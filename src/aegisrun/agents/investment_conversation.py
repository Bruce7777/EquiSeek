from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from aegisrun.orchestration.models import SAFE_ID

InvestmentIntent = Literal[
    "screen_candidates",
    "design_strategy",
    "analyze_security",
    "manage_skills",
    "explain_holding",
    "manage_portfolio",
    "general_research",
]
_INVESTMENT_INTENTS = frozenset(
    {
        "screen_candidates",
        "design_strategy",
        "analyze_security",
        "manage_skills",
        "explain_holding",
        "manage_portfolio",
        "general_research",
    }
)
MAX_CONVERSATION_STATE_BYTES = 2 * 1024 * 1024
MAX_STORED_TURNS = 256


class ConversationTurnLike(Protocol):
    @property
    def role(self) -> str: ...

    @property
    def content(self) -> str: ...


@dataclass(frozen=True, slots=True)
class RoutedInvestmentTurn:
    intent: InvestmentIntent
    prompt: str
    requested_tools: tuple[str, ...]
    symbol: str | None = None


class InvestmentIntentRouter:
    """Small deterministic router in front of the language model.

    It does not make an investment decision. It only selects the bounded product
    capability that should handle a user turn.
    """

    _symbol = re.compile(r"(?<!\d)(?:[03689]\d{5})(?:\.(?:SH|SZ|BJ))?(?!\d)", re.I)

    def route(self, text: str) -> RoutedInvestmentTurn:
        prompt = " ".join(text.strip().split())
        if not prompt:
            raise ValueError("投资研究问题不能为空")
        symbol_match = self._symbol.search(prompt)
        symbol = symbol_match.group(0).upper() if symbol_match else None
        lowered = prompt.casefold()
        skill_marker = any(word in lowered for word in ("skill", "技能", "策略包"))
        skill_management = any(
            word in prompt
            for word in (
                "管理",
                "查看",
                "列出",
                "有哪些",
                "安装",
                "替换",
                "删除",
                "导入",
                "启用",
                "禁用",
            )
        )
        if skill_marker and skill_management and symbol is None:
            return RoutedInvestmentTurn("manage_skills", prompt, ("skills.list",), symbol)
        portfolio_marker = any(
            word in prompt for word in ("持仓", "仓位", "自选", "添加个股", "新增个股", "记录个股")
        )
        portfolio_management = any(
            word in prompt
            for word in (
                "添加",
                "新增",
                "记录",
                "修改",
                "更新",
                "调整",
                "删除",
                "移除",
                "查询",
                "查看",
                "列出",
                "有哪些",
            )
        )
        if portfolio_marker and portfolio_management:
            return RoutedInvestmentTurn(
                "manage_portfolio",
                prompt,
                ("portfolio.list", "portfolio.upsert", "portfolio.remove"),
                symbol,
            )
        if any(word in prompt for word in ("筛选", "选股", "扫描", "候选池", "候选股")):
            return RoutedInvestmentTurn(
                "screen_candidates", prompt, ("portfolio.load", "research.screen"), symbol
            )
        if any(word in prompt for word in ("策略", "回测", "规则", "指标组合")):
            return RoutedInvestmentTurn(
                "design_strategy", prompt, ("strategy.compose", "backtest.run"), symbol
            )
        if symbol is not None or any(word in prompt for word in ("分析这只", "研究这只", "行情")):
            return RoutedInvestmentTurn(
                "analyze_security", prompt, ("market.load", "research.analyze"), symbol
            )
        if any(word in prompt for word in ("持仓", "卖出", "减仓", "加仓", "止损", "止盈")):
            return RoutedInvestmentTurn("explain_holding", prompt, ("portfolio.read",), symbol)
        return RoutedInvestmentTurn("general_research", prompt, (), symbol)


@dataclass(slots=True)
class InvestmentMemory:
    """Durable, user-approved investment preferences only.

    Raw positions, quantities, cost prices, credentials and one-off instructions are
    intentionally outside this memory object.
    """

    risk_profile: str | None = None
    horizon: str | None = None
    max_drawdown_pct: float | None = None
    preferred_sectors: list[str] = field(default_factory=list)
    avoided_sectors: list[str] = field(default_factory=list)
    strategy_preferences: list[str] = field(default_factory=list)
    updated_at: str | None = None

    def update_from_explicit_statement(self, text: str) -> bool:
        normalized = " ".join(text.strip().split())
        changed = False
        if re.search(
            r"(?:我是|我的风险偏好(?:是|为)?|风险承受(?:是|为)?)\s*"
            r"(保守|稳健|平衡|成长|激进)",
            normalized,
        ):
            profile = re.search(r"(保守|稳健|平衡|成长|激进)", normalized)
            if profile and self.risk_profile != profile.group(1):
                self.risk_profile = profile.group(1)
                changed = True
        horizon = re.search(
            r"(?:我|本人)?(?:偏好|主要做|投资周期(?:是|为)?)\s*(短线|中线|长线)",
            normalized,
        )
        if horizon and self.horizon != horizon.group(1):
            self.horizon = horizon.group(1)
            changed = True
        drawdown = re.search(
            r"(?:最大|最多|可接受)(?:回撤|亏损)[不超过为是:\s]*"
            r"(\d+(?:\.\d+)?)\s*%",
            normalized,
        )
        if drawdown:
            value = min(float(drawdown.group(1)), 100.0)
            if self.max_drawdown_pct != value:
                self.max_drawdown_pct = value
                changed = True
        changed |= self._capture_list(
            normalized,
            ("偏好行业", "偏好板块", "看好板块"),
            self.preferred_sectors,
        )
        changed |= self._capture_list(
            normalized,
            ("回避行业", "回避板块", "不碰板块"),
            self.avoided_sectors,
        )
        if any(marker in normalized for marker in ("我偏好", "我的策略", "策略偏好")):
            indicators = [
                name
                for name in ("MACD", "WR", "KDJ", "RSI", "BOLL", "均线", "基本面", "低波动")
                if name.casefold() in normalized.casefold()
            ]
            for indicator in indicators:
                if indicator not in self.strategy_preferences:
                    self.strategy_preferences.append(indicator)
                    changed = True
        if changed:
            self.updated_at = datetime.now(UTC).isoformat()
        return changed

    @staticmethod
    def _capture_list(text: str, markers: tuple[str, ...], target: list[str]) -> bool:
        changed = False
        for marker in markers:
            match = re.search(rf"{marker}[是为:\s]*([^。；，,]{{1,24}})", text)
            if not match:
                continue
            value = match.group(1).strip()
            if value and value not in target:
                target.append(value)
                changed = True
        return changed

    def prompt_payload(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "risk_profile": self.risk_profile,
                "horizon": self.horizon,
                "max_drawdown_pct": self.max_drawdown_pct,
                "preferred_sectors": self.preferred_sectors,
                "avoided_sectors": self.avoided_sectors,
                "strategy_preferences": self.strategy_preferences,
            }.items()
            if value not in (None, [], "")
        }


@dataclass(frozen=True, slots=True)
class StoredAttachment:
    name: str
    mime_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class StoredConversationTurn:
    role: Literal["user", "assistant"]
    content: str
    intent: InvestmentIntent | None = None
    run_id: str | None = None
    attachments: tuple[StoredAttachment, ...] = ()


@dataclass(slots=True)
class InvestmentThreadState:
    thread_id: str
    turns: list[StoredConversationTurn] = field(default_factory=list)
    summary: str = ""
    compressed_turn_count: int = 0
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class InvestmentContextPolicy:
    trigger_chars: int = 16_000
    keep_recent_turns: int = 8
    max_summary_chars: int = 3_500
    max_turn_chars_in_summary: int = 280

    def __post_init__(self) -> None:
        if self.trigger_chars < 1 or self.keep_recent_turns < 1:
            raise ValueError("context policy limits must be positive")


@dataclass(frozen=True, slots=True)
class InvestmentContextBundle:
    summary: str
    recent_turns: tuple[StoredConversationTurn, ...]
    compressed_turn_count: int
    approximate_tokens: int

    def durable_payload(
        self,
        memory: InvestmentMemory | None = None,
        skill_references: Sequence[dict[str, str]] = (),
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.summary:
            payload["conversation_summary"] = self.summary
        if memory is not None and memory.prompt_payload():
            payload["user_investment_preferences"] = memory.prompt_payload()
        if skill_references:
            payload["active_skill_references"] = list(skill_references)
        return payload


class InvestmentContextEngine:
    """Business-aware compaction that keeps facts and decisions out of prose history."""

    def __init__(self, policy: InvestmentContextPolicy | None = None) -> None:
        self.policy = policy or InvestmentContextPolicy()

    def compact(
        self,
        turns: Sequence[ConversationTurnLike],
        *,
        previous_summary: str = "",
        previously_compressed: int = 0,
        force: bool = False,
    ) -> InvestmentContextBundle:
        preserved = tuple(
            StoredConversationTurn(
                "assistant" if turn.role == "assistant" else "user",
                turn.content.strip(),
                getattr(turn, "intent", None),
                getattr(turn, "run_id", None),
                tuple(getattr(turn, "attachments", ())),
            )
            for turn in turns
            if turn.content.strip()
        )
        chars = len(previous_summary) + sum(len(turn.content) for turn in preserved)
        if not force and chars <= self.policy.trigger_chars:
            return InvestmentContextBundle(
                previous_summary,
                preserved,
                previously_compressed,
                max(1, chars // 3),
            )
        split = max(0, len(preserved) - self.policy.keep_recent_turns)
        older, recent = preserved[:split], preserved[split:]
        if not older:
            return InvestmentContextBundle(
                previous_summary,
                recent,
                previously_compressed,
                max(1, chars // 3),
            )
        entries: list[str] = [previous_summary] if previous_summary else []
        for turn in older:
            label = "用户目标/约束" if turn.role == "user" else "既有研究结论"
            intent = f"[{turn.intent}] " if turn.intent else ""
            normalized_content = " ".join(turn.content.strip().split())
            entries.append(
                f"- {label}: {intent}{normalized_content[: self.policy.max_turn_chars_in_summary]}"
            )
        summary = "\n".join(item for item in entries if item).strip()
        if len(summary) > self.policy.max_summary_chars:
            summary = summary[-self.policy.max_summary_chars :]
            summary = "[更早内容已压缩]\n" + summary
        total_chars = len(summary) + sum(len(turn.content) for turn in recent)
        return InvestmentContextBundle(
            summary,
            recent,
            previously_compressed + len(older),
            max(1, total_chars // 3),
        )


class InvestmentConversationStore:
    """Local JSON store with per-user memory and per-thread conversation state."""

    def __init__(
        self,
        root: Path,
        *,
        context_engine: InvestmentContextEngine | None = None,
    ) -> None:
        self.root = root.resolve()
        self.context_engine = context_engine or InvestmentContextEngine()

    def load_memory(self, user_id: str = "local-user") -> InvestmentMemory:
        path = self._user_root(user_id) / "memory.json"
        if not path.is_file():
            return InvestmentMemory()
        raw = self._read_json(path)
        try:
            risk_profile = self._optional_string(raw.get("risk_profile"), "risk_profile")
            horizon = self._optional_string(raw.get("horizon"), "horizon")
            updated_at = self._optional_string(raw.get("updated_at"), "updated_at")
            drawdown_raw = raw.get("max_drawdown_pct")
            if drawdown_raw is None:
                drawdown = None
            elif isinstance(drawdown_raw, bool) or not isinstance(drawdown_raw, (int, float)):
                raise ValueError("max_drawdown_pct must be numeric")
            else:
                drawdown = float(drawdown_raw)
                if not math.isfinite(drawdown) or not 0 <= drawdown <= 100:
                    raise ValueError("max_drawdown_pct is out of range")
            return InvestmentMemory(
                risk_profile=risk_profile,
                horizon=horizon,
                max_drawdown_pct=drawdown,
                preferred_sectors=self._string_values(raw.get("preferred_sectors")),
                avoided_sectors=self._string_values(raw.get("avoided_sectors")),
                strategy_preferences=self._string_values(raw.get("strategy_preferences")),
                updated_at=updated_at,
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"conversation state is invalid: {path.name}") from error

    def capture_memory(self, text: str, user_id: str = "local-user") -> InvestmentMemory:
        memory = self.load_memory(user_id)
        if memory.update_from_explicit_statement(text):
            self._write_json(self._user_root(user_id) / "memory.json", asdict(memory))
        return memory

    def load_thread(self, thread_id: str, user_id: str = "local-user") -> InvestmentThreadState:
        path = self._thread_path(user_id, thread_id)
        if not path.is_file():
            return InvestmentThreadState(thread_id)
        raw = self._read_json(path)
        try:
            turns_raw = raw.get("turns", [])
            if not isinstance(turns_raw, list) or len(turns_raw) > MAX_STORED_TURNS:
                raise ValueError("turns must be a list")
            turns: list[StoredConversationTurn] = []
            for item in turns_raw:
                if not isinstance(item, dict):
                    raise ValueError("turn must be an object")
                role = item.get("role")
                content = item.get("content")
                intent = item.get("intent")
                run_id = item.get("run_id")
                attachments_raw = item.get("attachments", [])
                if (
                    role not in {"user", "assistant"}
                    or not isinstance(content, str)
                    or not content.strip()
                    or len(content) > (100_000 if role == "assistant" else 8_000)
                ):
                    raise ValueError("turn role or content is invalid")
                if intent is not None and intent not in _INVESTMENT_INTENTS:
                    raise ValueError("turn intent is invalid")
                if run_id is not None and (
                    not isinstance(run_id, str) or not SAFE_ID.fullmatch(run_id)
                ):
                    raise ValueError("turn run_id is invalid")
                if not isinstance(attachments_raw, list) or len(attachments_raw) > 4:
                    raise ValueError("turn attachments are invalid")
                attachments: list[StoredAttachment] = []
                for attachment in attachments_raw:
                    if not isinstance(attachment, dict):
                        raise ValueError("turn attachment must be an object")
                    name = attachment.get("name")
                    mime_type = attachment.get("mime_type")
                    size_bytes = attachment.get("size_bytes")
                    if (
                        not isinstance(name, str)
                        or not name.strip()
                        or len(name) > 200
                        or not isinstance(mime_type, str)
                        or len(mime_type) > 100
                        or isinstance(size_bytes, bool)
                        or not isinstance(size_bytes, int)
                        or not 0 <= size_bytes <= 10 * 1024 * 1024
                    ):
                        raise ValueError("turn attachment metadata is invalid")
                    attachments.append(StoredAttachment(name, mime_type, size_bytes))
                turns.append(
                    StoredConversationTurn(
                        cast(Literal["user", "assistant"], role),
                        content,
                        cast(InvestmentIntent | None, intent),
                        run_id,
                        tuple(attachments),
                    )
                )
            summary = raw.get("summary", "")
            compressed = raw.get("compressed_turn_count", 0)
            updated_at = self._optional_string(raw.get("updated_at"), "updated_at")
            if not isinstance(summary, str) or len(summary) > 100_000:
                raise ValueError("summary must be a string")
            if isinstance(compressed, bool) or not isinstance(compressed, int) or compressed < 0:
                raise ValueError("compressed_turn_count must be a non-negative integer")
            return InvestmentThreadState(
                thread_id=thread_id,
                turns=turns,
                summary=summary,
                compressed_turn_count=compressed,
                updated_at=updated_at,
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"conversation state is invalid: {path.name}") from error

    def create_thread(self, thread_id: str, user_id: str = "local-user") -> InvestmentThreadState:
        state = InvestmentThreadState(
            thread_id=thread_id,
            updated_at=datetime.now(UTC).isoformat(),
        )
        self._write_json(self._thread_path(user_id, thread_id), self._state_dict(state))
        return state

    def list_threads(self, user_id: str = "local-user") -> list[dict[str, Any]]:
        root = self._user_root(user_id) / "threads"
        if not root.is_dir() or root.is_symlink():
            return []
        result: list[dict[str, Any]] = []
        for path in list(root.glob("*.json"))[:MAX_STORED_TURNS]:
            if path.is_symlink() or not path.is_file():
                continue
            try:
                state = self.load_thread(path.stem, user_id)
            except ValueError:
                continue
            first_user = next(
                (turn.content for turn in state.turns if turn.role == "user"),
                "新对话",
            )
            last_turn = state.turns[-1].content if state.turns else "还没有消息"
            result.append(
                {
                    "threadId": state.thread_id,
                    "title": first_user[:36],
                    "preview": last_turn[:72],
                    "turnCount": state.compressed_turn_count + len(state.turns),
                    "updatedAt": state.updated_at,
                }
            )
        return sorted(
            result,
            key=lambda item: str(item.get("updatedAt") or ""),
            reverse=True,
        )[:50]

    def append(
        self,
        thread_id: str,
        role: Literal["user", "assistant"],
        content: str,
        *,
        intent: InvestmentIntent | None = None,
        run_id: str | None = None,
        attachments: Sequence[StoredAttachment] = (),
        user_id: str = "local-user",
    ) -> InvestmentThreadState:
        cleaned = content.strip()
        if not cleaned:
            raise ValueError("conversation turn cannot be empty")
        state = self.load_thread(thread_id, user_id)
        if run_id is not None:
            self._validate_id(run_id)
        if len(attachments) > 4:
            raise ValueError("conversation turn accepts at most four attachments")
        content_limit = 100_000 if role == "assistant" else 8_000
        state.turns.append(
            StoredConversationTurn(
                role, cleaned[:content_limit], intent, run_id, tuple(attachments)
            )
        )
        compacted = self.context_engine.compact(
            state.turns,
            previous_summary=state.summary,
            previously_compressed=state.compressed_turn_count,
        )
        state.turns = list(compacted.recent_turns)
        state.summary = compacted.summary
        state.compressed_turn_count = compacted.compressed_turn_count
        state.updated_at = datetime.now(UTC).isoformat()
        self._write_json(self._thread_path(user_id, thread_id), self._state_dict(state))
        if role == "user":
            self.capture_memory(cleaned, user_id)
        return state

    def clear_thread(self, thread_id: str, user_id: str = "local-user") -> None:
        path = self._thread_path(user_id, thread_id)
        if path.exists():
            path.unlink()

    def _user_root(self, user_id: str) -> Path:
        self._validate_id(user_id)
        return self.root / "users" / user_id

    def _thread_path(self, user_id: str, thread_id: str) -> Path:
        self._validate_id(thread_id)
        return self._user_root(user_id) / "threads" / f"{thread_id}.json"

    @staticmethod
    def _validate_id(value: str) -> None:
        if not SAFE_ID.fullmatch(value) or value in {".", ".."}:
            raise ValueError("conversation identifiers must be safe path segments")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if path.is_symlink():
            raise ValueError(f"conversation state symlinks are not allowed: {path.name}")
        try:
            data = path.read_bytes()
            if len(data) > MAX_CONVERSATION_STATE_BYTES:
                raise ValueError(f"conversation state is too large: {path.name}")
            value = json.loads(data.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"conversation state is unreadable: {path.name}") from error
        if not isinstance(value, dict):
            raise ValueError(f"conversation state must be an object: {path.name}")
        return value

    @staticmethod
    def _string_values(value: object) -> list[str]:
        if value is None:
            return []
        if (
            not isinstance(value, list)
            or len(value) > 100
            or any(not isinstance(item, str) or len(item) > 100 for item in value)
        ):
            raise ValueError("memory list fields must contain strings")
        return [item for item in value if item]

    @staticmethod
    def _optional_string(value: object, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 200:
            raise ValueError(f"{field} must be a string")
        return value

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        temporary = path.with_suffix(path.suffix + ".tmp")
        if temporary.is_symlink():
            raise OSError(f"conversation temporary file cannot be a symlink: {temporary.name}")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)

    @staticmethod
    def _state_dict(state: InvestmentThreadState) -> dict[str, Any]:
        return {
            "thread_id": state.thread_id,
            "turns": [asdict(turn) for turn in state.turns],
            "summary": state.summary,
            "compressed_turn_count": state.compressed_turn_count,
            "updated_at": state.updated_at,
        }
