from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from aegisrun.tools.spec import ToolSpec

PlanStatus = Literal["pending", "in_progress", "completed", "blocked", "skipped"]


@dataclass(slots=True)
class HarnessPlanItem:
    id: str
    title: str
    status: PlanStatus = "pending"
    tool: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ToolCallGuardDecision:
    allowed: bool
    code: str = ""
    reason: str = ""


class AgentLoopHarness:
    """Durable control state around an otherwise small model/tool loop.

    The class deliberately does not call models or execute tools. It owns the parts
    that must remain deterministic and inspectable: plan state, deferred tool
    promotion, repeated-call detection, and model-facing observation budgets.
    """

    _PLAN_STATUSES = {"pending", "in_progress", "completed", "blocked", "skipped"}

    def __init__(
        self,
        *,
        goal: str,
        intent: str,
        tool_specs: Sequence[ToolSpec],
        allowed_tool_names: Sequence[str],
        state_directory: Path,
        observation_budget_chars: int = 24_000,
        observation_item_chars: int = 8_000,
        repeat_reminder_thresholds: Sequence[int] = (3, 5, 8),
    ) -> None:
        if observation_budget_chars < 2_000:
            raise ValueError("observation budget must be at least 2000 characters")
        if observation_item_chars < 500:
            raise ValueError("observation item budget must be at least 500 characters")
        thresholds = tuple(sorted(repeat_reminder_thresholds))
        if (
            not thresholds
            or len(set(thresholds)) != len(thresholds)
            or any(not isinstance(value, int) or value < 2 for value in thresholds)
        ):
            raise ValueError("repeat reminder thresholds must be unique integers >= 2")
        self.goal = goal
        self.intent = intent
        self.state_directory = state_directory
        self.observation_budget_chars = observation_budget_chars
        self.observation_item_chars = observation_item_chars
        self.repeat_reminder_thresholds = thresholds
        allowed = set(allowed_tool_names)
        self._catalog = {spec.name: spec for spec in tool_specs if spec.name in allowed}
        self._catalog_hash = self._compute_catalog_hash(tuple(self._catalog.values()))
        self._promoted = self._initial_tools(goal, intent, allowed)
        self._last_call_fingerprint = ""
        self._consecutive_repeat_count = 0
        self._repeat_reminders = 0
        self._guard_violations = 0
        self._externalized_results = 0
        self._plan_revision = 1
        self._plan_owner: Literal["runtime", "model"] = "runtime"
        self._plan = self._bootstrap_plan(goal, intent)

    @property
    def catalog_hash(self) -> str:
        return self._catalog_hash

    @property
    def promoted_tool_names(self) -> tuple[str, ...]:
        return tuple(name for name in self._catalog if name in self._promoted)

    @property
    def deferred_tool_names(self) -> tuple[str, ...]:
        return tuple(name for name in self._catalog if name not in self._promoted)

    def is_promoted(self, name: str) -> bool:
        return name in self._promoted

    def promote(self, name: str) -> bool:
        if name not in self._catalog:
            return False
        changed = name not in self._promoted
        self._promoted.add(name)
        return changed

    def discover(self, query: str, *, limit: int = 6) -> list[dict[str, Any]]:
        selected = self._selected_tool_names(query)
        terms = self._search_terms(query)
        ranked: list[tuple[int, str, ToolSpec]] = []
        for name, spec in self._catalog.items():
            if (
                name in {"tools.search", "plan.update", "tool_results.read"}
                or name in self._promoted
            ):
                continue
            if selected:
                if name in selected:
                    ranked.append((10_000, name, spec))
                continue
            haystack = f"{name} {spec.description}".lower()
            score = sum(3 if term in name.lower() else 1 for term in terms if term in haystack)
            if score:
                ranked.append((score, name, spec))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        matches: list[dict[str, Any]] = []
        effective_limit = 10 if selected else max(1, min(limit, 10))
        for _, name, spec in ranked[:effective_limit]:
            self.promote(name)
            matches.append(
                {
                    "name": name,
                    "description": spec.description,
                    "risk": spec.risk.value,
                    "side_effect": spec.side_effect,
                }
            )
        return matches

    def replace_plan(self, raw_items: Sequence[dict[str, Any]]) -> None:
        if not raw_items or len(raw_items) > 8:
            raise ValueError("plan must contain between 1 and 8 items")
        items: list[HarnessPlanItem] = []
        active_count = 0
        seen: set[str] = set()
        for index, raw in enumerate(raw_items, start=1):
            item_id = str(raw.get("id") or f"task-{index}")[:64]
            title = " ".join(str(raw.get("title", "")).split())[:160]
            status = str(raw.get("status", "pending"))
            tool = str(raw.get("tool", ""))[:96]
            if not title:
                raise ValueError("each plan item requires a title")
            if item_id in seen:
                raise ValueError(f"duplicate plan item id: {item_id}")
            if status not in self._PLAN_STATUSES:
                raise ValueError(f"unknown plan item status: {status}")
            if status == "in_progress":
                active_count += 1
            seen.add(item_id)
            items.append(
                HarnessPlanItem(
                    id=item_id,
                    title=title,
                    status=status,  # type: ignore[arg-type]
                    tool=tool,
                    detail=str(raw.get("detail", ""))[:500],
                )
            )
        if active_count > 1:
            raise ValueError("at most one plan item may be in_progress")
        self._plan = items
        self._plan_owner = "model"
        self._plan_revision += 1

    def before_tool(self, name: str, arguments: dict[str, Any]) -> ToolCallGuardDecision:
        if name not in self._catalog:
            self._guard_violations += 1
            return ToolCallGuardDecision(False, "UNKNOWN_TOOL", f"未知工具：{name}")
        if name not in self._promoted:
            self._guard_violations += 1
            return ToolCallGuardDecision(
                False,
                "DEFERRED_TOOL",
                f"工具 {name} 尚未晋升；先调用 tools.search 发现并加载它",
            )
        fingerprint = self._fingerprint(name, arguments)
        if fingerprint == self._last_call_fingerprint:
            self._consecutive_repeat_count += 1
        else:
            self._last_call_fingerprint = fingerprint
            self._consecutive_repeat_count = 1
        if self._plan_owner == "runtime":
            self._start_matching_plan_item(name)
        if self._consecutive_repeat_count in self.repeat_reminder_thresholds:
            self._repeat_reminders += 1
            if self._consecutive_repeat_count == self.repeat_reminder_thresholds[0]:
                message = (
                    f"你已连续 {self._consecutive_repeat_count} 次使用完全相同的工具和参数。"
                    "请先重新检查上一轮结果；如果任务尚未完成，请调整方法或参数，"
                    "否则直接收束回答。"
                )
            else:
                canonical = self._canonical_arguments(arguments)
                preview = canonical[:500]
                if len(canonical) > 500:
                    preview += f"…（省略 {len(canonical) - 500} 个字符）"
                message = (
                    "检测到重复工具调用：\n"
                    f"- 工具：{name}\n"
                    f"- 连续次数：{self._consecutive_repeat_count}\n"
                    f"- 参数：{preview}\n"
                    "这些调用没有体现新的方法。请改用其他动作、调整参数，"
                    "或者在证据已经足够时结束任务。"
                )
            return ToolCallGuardDecision(
                True,
                "REPEATED_TOOL_CALL_REMINDER",
                message,
            )
        return ToolCallGuardDecision(True)

    def after_tool(self, name: str, *, ok: bool, detail: str) -> None:
        if name in {"tools.search", "plan.update"} or self._plan_owner == "model":
            return
        matching = next(
            (item for item in self._plan if item.tool == name and item.status == "in_progress"),
            None,
        )
        if matching is None:
            matching = next(
                (item for item in self._plan if not item.tool and item.status == "in_progress"),
                None,
            )
        if matching is not None:
            matching.status = "completed" if ok else "blocked"
            matching.detail = detail[:500]
        if ok:
            self._start_next_pending_plan_item()
        self._plan_revision += 1

    def project_observations(self, observations: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        projected_reversed: list[dict[str, Any]] = []
        remaining = self.observation_budget_chars - 2  # JSON array brackets
        for index in range(len(observations) - 1, -1, -1):
            item_budget = remaining - (1 if projected_reversed else 0)  # item separator
            if item_budget <= 0:
                break
            projected = self._project_observation(index + 1, observations[index])
            encoded = self._json(projected)
            if len(encoded) > item_budget:
                fitted = self._fit_projection(projected, item_budget)
                if fitted is None:
                    break
                projected = fitted
                encoded = self._json(projected)
            projected_reversed.append(projected)
            remaining = item_budget - len(encoded)
            if remaining <= 0:
                break
        projected_reversed.reverse()
        return projected_reversed

    def model_context(self) -> dict[str, Any]:
        return {
            "plan": self.plan_snapshot(),
            "tool_discovery": {
                "catalog_hash": self.catalog_hash,
                "promoted": list(self.promoted_tool_names),
                "deferred_count": len(self.deferred_tool_names),
                "deferred_catalog": self.deferred_catalog(),
                "instruction": (
                    "需要其他能力时调用 tools.search；可按用途检索，或使用 "
                    "select:tool.name,other.tool 精确晋升；未晋升工具不能执行。"
                ),
            },
            "loop_guard": {
                "mode": "advisory-only",
                "repeat_reminder_thresholds": list(self.repeat_reminder_thresholds),
                "consecutive_repeat_count": self._consecutive_repeat_count,
                "reminders": self._repeat_reminders,
                "violations": self._guard_violations,
            },
            "observation_budget": {
                "total_chars": self.observation_budget_chars,
                "per_item_chars": self.observation_item_chars,
                "externalized_results": self._externalized_results,
            },
        }

    def plan_snapshot(self) -> dict[str, Any]:
        return {
            "revision": self._plan_revision,
            "owner": self._plan_owner,
            "items": [asdict(item) for item in self._plan],
        }

    def deferred_catalog(self) -> list[dict[str, str]]:
        return [
            {
                "name": name,
                "description": " ".join(self._catalog[name].description.split())[:160],
                "risk": self._catalog[name].risk.value,
            }
            for name in self.deferred_tool_names
        ]

    def snapshot(self) -> dict[str, Any]:
        return {
            **self.model_context(),
            "deferred_tools": list(self.deferred_tool_names),
        }

    def read_externalized_result(
        self,
        path: str,
        *,
        offset: int = 0,
        limit: int = 5_000,
    ) -> dict[str, Any]:
        if offset < 0:
            raise ValueError("tool result offset must be non-negative")
        if limit < 1 or limit > 5_000:
            raise ValueError("tool result limit must be between 1 and 5000")
        if not re.fullmatch(
            r"\.state/tool-results/[A-Za-z0-9][A-Za-z0-9_.-]{0,191}\.json", path
        ):
            raise ValueError("tool result path is not an externalized result reference")
        root = (self.state_directory / "tool-results").resolve()
        candidate = self.state_directory / path.removeprefix(".state/")
        try:
            target = candidate.resolve(strict=True)
        except OSError as error:
            raise ValueError("externalized tool result does not exist") from error
        if (
            not target.is_relative_to(root)
            or candidate.is_symlink()
            or not target.is_file()
        ):
            raise ValueError("tool result path is not a regular file inside the result store")
        content = target.read_text(encoding="utf-8")
        end = min(len(content), offset + limit)
        return {
            "path": path,
            "offset": offset,
            "next_offset": end if end < len(content) else None,
            "total_chars": len(content),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content": content[offset:end],
            "eof": end >= len(content),
        }

    def _project_observation(self, index: int, observation: dict[str, Any]) -> dict[str, Any]:
        encoded = self._json(observation)
        if len(encoded) <= self.observation_item_chars:
            return observation
        stored = json.dumps(observation, ensure_ascii=False, indent=2, sort_keys=True)
        digest = hashlib.sha256(stored.encode("utf-8")).hexdigest()
        relative = Path("tool-results") / f"observation-{index}-{digest[:12]}.json"
        target = self.state_directory / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(stored, encoding="utf-8")
            self._externalized_results += 1
        data = observation.get("data")
        preview = self._json(data)[: max(200, self.observation_item_chars // 3)]
        return {
            **self._summary_projection(observation),
            "data_preview": preview,
            "externalized": {
                "path": f".state/{relative.as_posix()}",
                "sha256": digest,
                "size_chars": len(stored),
            },
        }

    @staticmethod
    def _summary_projection(observation: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "tool": str(observation.get("tool", "")),
            "ok": bool(observation.get("ok")),
        }
        for key in ("summary", "error", "artifact_id", "harness_notice"):
            value = observation.get(key)
            if value not in {None, ""}:
                result[key] = str(value)[:1_000]
        return result

    @classmethod
    def _fit_projection(
        cls,
        projection: dict[str, Any],
        budget: int,
    ) -> dict[str, Any] | None:
        compact: dict[str, Any] = {
            "tool": str(projection.get("tool", ""))[:96],
            "ok": bool(projection.get("ok")),
        }
        externalized = projection.get("externalized")
        if isinstance(externalized, dict):
            compact["externalized"] = externalized
        if len(cls._json(compact)) > budget:
            return None
        for key in ("summary", "error", "harness_notice", "artifact_id", "data_preview"):
            value = projection.get(key)
            if value in {None, ""}:
                continue
            text = str(value)
            candidate = {**compact, key: text}
            while text and len(cls._json(candidate)) > budget:
                excess = len(cls._json(candidate)) - budget
                text = text[: max(0, len(text) - max(1, excess))]
                candidate = {**compact, key: f"{text}…"} if text else compact
            if text:
                compact = candidate
        return compact

    @staticmethod
    def _fingerprint(name: str, arguments: dict[str, Any]) -> str:
        normalized = AgentLoopHarness._canonical_arguments(arguments)
        return hashlib.sha256(f"{name}\n{normalized}".encode()).hexdigest()

    @staticmethod
    def _canonical_arguments(arguments: dict[str, Any]) -> str:
        return json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @staticmethod
    def _search_terms(query: str) -> tuple[str, ...]:
        aliases = {
            "宏观": ("macro", "宏观"),
            "行情": ("market", "bars", "行情"),
            "股票": ("market", "research", "证券"),
            "证券": ("market", "research", "证券"),
            "筛选": ("screen", "筛选"),
            "选股": ("screen", "筛选"),
            "网页": ("web", "search", "联网"),
            "搜索": ("search", "搜索"),
            "持仓": ("portfolio", "持仓"),
            "文件": ("file", "workspace", "read", "write", "文件"),
            "命令": ("bash", "shell", "命令"),
            "技能": ("skill", "技能"),
        }
        raw = tuple(part.lower() for part in re.findall(r"[\w.\-\u4e00-\u9fff]+", query))
        expanded = list(raw)
        for key, values in aliases.items():
            if key in query:
                expanded.extend(values)
        return tuple(dict.fromkeys(item for item in expanded if item))

    @staticmethod
    def _selected_tool_names(query: str) -> set[str]:
        match = re.fullmatch(r"\s*select\s*:\s*(.+?)\s*", query, re.IGNORECASE)
        if match is None:
            return set()
        return {
            name.strip()
            for name in match.group(1).split(",")
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", name.strip())
        }

    @classmethod
    def _compute_catalog_hash(cls, specs: Sequence[ToolSpec]) -> str:
        catalog = [
            {
                "name": spec.name,
                "version": spec.version,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "risk": spec.risk.value,
                "side_effect": spec.side_effect,
                "timeout_seconds": spec.timeout_seconds,
                "required_capabilities": sorted(spec.required_capabilities),
                "concurrency_safe": spec.concurrency_safe,
            }
            for spec in sorted(specs, key=lambda item: item.name)
        ]
        return hashlib.sha256(cls._json(catalog).encode("utf-8")).hexdigest()

    @staticmethod
    def _initial_tools(goal: str, intent: str, allowed: set[str]) -> set[str]:
        names = {
            "tools.search",
            "plan.update",
            "tool_results.read",
            "skills.list",
            "skills.activate",
        }
        intent_tools = {
            "manage_portfolio": {"portfolio.list", "portfolio.upsert", "portfolio.remove"},
            "screen_candidates": {"portfolio.snapshot", "research.screen"},
            "analyze_security": {"market.analyze", "market.bars", "evidence.current"},
            "explain_holding": {"evidence.current", "market.analyze", "market.bars"},
            "manage_skills": {"skills.list", "skills.activate"},
        }
        names.update(intent_tools.get(intent, set()))
        if re.search(r"宏观|资本三流|成本转嫁", goal):
            names.add("macro.snapshot")
        if re.search(r"最新|新闻|公告|联网|搜索|检索|今天|近期", goal):
            names.add("web.search")
        if re.search(r"文件|目录|工作区|代码|命令|shell|bash", goal, re.IGNORECASE):
            names.update({"list_files", "read", "write", "edit", "bash"})
        if intent == "general_research":
            names.add("market.bars")
        return names & allowed

    @staticmethod
    def _bootstrap_plan(goal: str, intent: str) -> list[HarnessPlanItem]:
        tool = {
            "manage_portfolio": "portfolio.list",
            "screen_candidates": "portfolio.snapshot",
            "analyze_security": "market.analyze",
            "explain_holding": "evidence.current",
            "manage_skills": "skills.list",
        }.get(intent, "")
        if re.search(r"宏观|资本三流|成本转嫁", goal):
            tool = "macro.snapshot"
        elif re.search(r"最新|新闻|公告|联网|搜索|检索|今天|近期", goal):
            tool = "web.search"
        elif re.search(r"文件|目录|工作区|代码|命令|shell|bash", goal, re.IGNORECASE):
            tool = "list_files"
        title = "收集完成目标所需的可追溯证据"
        if tool:
            title = f"调用 {tool} 收集可追溯证据"
        return [
            HarnessPlanItem("evidence", title, "in_progress", tool),
            HarnessPlanItem("synthesis", "核对证据边界并形成回答", "pending"),
        ]

    def _start_matching_plan_item(self, name: str) -> None:
        matching = next(
            (item for item in self._plan if item.tool == name and item.status == "pending"),
            None,
        )
        if matching is None:
            return
        for item in self._plan:
            if item.status == "in_progress":
                item.status = "pending"
        matching.status = "in_progress"
        self._plan_revision += 1

    def _start_next_pending_plan_item(self) -> None:
        if any(item.status == "in_progress" for item in self._plan):
            return
        pending = next((item for item in self._plan if item.status == "pending"), None)
        if pending is not None:
            pending.status = "in_progress"
