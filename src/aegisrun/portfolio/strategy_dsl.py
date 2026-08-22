from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from aegisrun.portfolio.models import canonical_symbol
from aegisrun.research.advice import InvestmentAction
from aegisrun.research.signals import Direction
from aegisrun.skills.catalog import SkillPackage, SkillValidationError

STRATEGY_RESOURCE = "strategy.json"
STRATEGY_SCHEMA_VERSION = "aegisrun-candidate-strategy/v1"
_SAFE_ACTIONS = frozenset(
    {
        InvestmentAction.BUY.value,
        InvestmentAction.ADD.value,
        InvestmentAction.HOLD.value,
        InvestmentAction.WAIT.value,
    }
)
_DIRECTIONS = frozenset(item.value for item in Direction)


class StrategyValidationError(ValueError):
    """A declarative strategy is malformed or exceeds the safe screening DSL."""


@dataclass(frozen=True, slots=True)
class CandidateFilters:
    min_confidence: int = 0
    min_candidate_score: int = 0
    allowed_actions: frozenset[str] = _SAFE_ACTIONS
    allowed_directions: frozenset[str] = frozenset(
        {Direction.BULLISH.value, Direction.BEARISH.value, Direction.RANGE.value}
    )
    require_buy_gate_open: bool = False
    include_industries: tuple[str, ...] = ()
    exclude_industries: tuple[str, ...] = ()
    exclude_symbols: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class CandidateRanking:
    mode: Literal["legacy", "weighted"] = "legacy"
    confidence_weight: float = 1.0
    candidate_score_weight: float = 1.0
    market_adjustment_weight: float = 0.0
    macro_adjustment_weight: float = 0.0
    preferred_industries: tuple[str, ...] = ()
    preferred_industry_bonus: float = 0.0


@dataclass(frozen=True, slots=True)
class CandidateStrategy:
    name: str
    filters: CandidateFilters = CandidateFilters()
    ranking: CandidateRanking = CandidateRanking()
    max_results: int = 50
    schema_version: str = STRATEGY_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CandidateStrategy:
        _reject_unknown(payload, {"schema_version", "name", "filters", "ranking", "max_results"})
        schema_version = _required_string(payload, "schema_version", max_length=80)
        if schema_version != STRATEGY_SCHEMA_VERSION:
            raise StrategyValidationError(
                f"unsupported strategy schema_version: {schema_version}"
            )
        name = _required_string(payload, "name", max_length=100)
        filters = _filters(_mapping(payload.get("filters", {}), "filters"))
        ranking = _ranking(_mapping(payload.get("ranking", {}), "ranking"))
        max_results = _bounded_int(payload.get("max_results", 50), "max_results", 1, 50)
        return cls(name, filters, ranking, max_results, schema_version)

    def allows(
        self,
        *,
        symbol: str,
        industry: str,
        action: str,
        direction: str,
        confidence: int,
        candidate_score: int,
        buy_gate_open: bool | None,
    ) -> bool:
        filters = self.filters
        if canonical_symbol(symbol) in filters.exclude_symbols:
            return False
        if action not in filters.allowed_actions or direction not in filters.allowed_directions:
            return False
        if confidence < filters.min_confidence or candidate_score < filters.min_candidate_score:
            return False
        if filters.require_buy_gate_open and buy_gate_open is not True:
            return False
        if filters.include_industries and not _industry_matches(
            industry, filters.include_industries
        ):
            return False
        return not _industry_matches(industry, filters.exclude_industries)

    def score(
        self,
        *,
        industry: str,
        confidence: int,
        candidate_score: int,
        market_adjustment: int,
        macro_adjustment: int,
    ) -> float:
        if self.ranking.mode == "legacy":
            return float(confidence)
        ranking = self.ranking
        base_weight = ranking.confidence_weight + ranking.candidate_score_weight
        base = (
            confidence * ranking.confidence_weight
            + candidate_score * ranking.candidate_score_weight
        ) / base_weight
        score = (
            base
            + market_adjustment * ranking.market_adjustment_weight
            + macro_adjustment * ranking.macro_adjustment_weight
        )
        if _industry_matches(industry, ranking.preferred_industries):
            score += ranking.preferred_industry_bonus
        return round(max(0.0, min(100.0, score)), 4)

    def audit_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["filters"]["allowed_actions"] = sorted(self.filters.allowed_actions)
        value["filters"]["allowed_directions"] = sorted(self.filters.allowed_directions)
        value["filters"]["exclude_symbols"] = sorted(self.filters.exclude_symbols)
        return value


def candidate_strategy_from_skill(package: SkillPackage) -> CandidateStrategy:
    try:
        content = package.resources[STRATEGY_RESOURCE]
    except KeyError as error:
        raise SkillValidationError(
            f"skill {package.summary.name} does not declare {STRATEGY_RESOURCE}"
        ) from error
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as error:
        raise SkillValidationError(
            f"skill {package.summary.name} has invalid {STRATEGY_RESOURCE}: {error.msg}"
        ) from error
    if not isinstance(raw, dict):
        raise SkillValidationError(f"skill {package.summary.name} strategy must be an object")
    try:
        return CandidateStrategy.from_dict(raw)
    except StrategyValidationError as error:
        raise SkillValidationError(
            f"skill {package.summary.name} strategy is invalid: {error}"
        ) from error


def _filters(payload: dict[str, Any]) -> CandidateFilters:
    _reject_unknown(
        payload,
        {
            "min_confidence",
            "min_candidate_score",
            "allowed_actions",
            "allowed_directions",
            "require_buy_gate_open",
            "include_industries",
            "exclude_industries",
            "exclude_symbols",
        },
    )
    require_gate = payload.get("require_buy_gate_open", False)
    if not isinstance(require_gate, bool):
        raise StrategyValidationError("require_buy_gate_open must be a boolean")
    actions: frozenset[str] = frozenset(
        _string_list(
            payload.get("allowed_actions", sorted(_SAFE_ACTIONS)),
            "allowed_actions",
        )
    )
    if not actions or not actions <= _SAFE_ACTIONS:
        raise StrategyValidationError(
            f"allowed_actions must be a non-empty subset of {sorted(_SAFE_ACTIONS)}"
        )
    directions = frozenset(
        _string_list(
            payload.get(
                "allowed_directions",
                [Direction.BULLISH.value, Direction.BEARISH.value, Direction.RANGE.value],
            ),
            "allowed_directions",
        )
    )
    if not directions or not directions <= _DIRECTIONS:
        raise StrategyValidationError(
            f"allowed_directions must be a non-empty subset of {sorted(_DIRECTIONS)}"
        )
    symbols: set[str] = set()
    for value in _string_list(payload.get("exclude_symbols", []), "exclude_symbols"):
        try:
            symbols.add(canonical_symbol(value))
        except ValueError as error:
            raise StrategyValidationError(f"invalid excluded symbol: {value}") from error
    return CandidateFilters(
        min_confidence=_bounded_int(
            payload.get("min_confidence", 0), "min_confidence", 0, 100
        ),
        min_candidate_score=_bounded_int(
            payload.get("min_candidate_score", 0), "min_candidate_score", 0, 100
        ),
        allowed_actions=actions,
        allowed_directions=directions,
        require_buy_gate_open=require_gate,
        include_industries=_string_list(
            payload.get("include_industries", []), "include_industries"
        ),
        exclude_industries=_string_list(
            payload.get("exclude_industries", []), "exclude_industries"
        ),
        exclude_symbols=frozenset(symbols),
    )


def _ranking(payload: dict[str, Any]) -> CandidateRanking:
    _reject_unknown(
        payload,
        {
            "mode",
            "confidence_weight",
            "candidate_score_weight",
            "market_adjustment_weight",
            "macro_adjustment_weight",
            "preferred_industries",
            "preferred_industry_bonus",
        },
    )
    mode = payload.get("mode", "legacy")
    if mode not in {"legacy", "weighted"}:
        raise StrategyValidationError("ranking.mode must be legacy or weighted")
    confidence_weight = _bounded_float(
        payload.get("confidence_weight", 1), "confidence_weight", 0, 10
    )
    candidate_weight = _bounded_float(
        payload.get("candidate_score_weight", 1), "candidate_score_weight", 0, 10
    )
    if mode == "weighted" and confidence_weight + candidate_weight <= 0:
        raise StrategyValidationError("weighted ranking requires a positive base weight")
    return CandidateRanking(
        mode=mode,
        confidence_weight=confidence_weight,
        candidate_score_weight=candidate_weight,
        market_adjustment_weight=_bounded_float(
            payload.get("market_adjustment_weight", 0),
            "market_adjustment_weight",
            0,
            5,
        ),
        macro_adjustment_weight=_bounded_float(
            payload.get("macro_adjustment_weight", 0),
            "macro_adjustment_weight",
            0,
            5,
        ),
        preferred_industries=_string_list(
            payload.get("preferred_industries", []), "preferred_industries"
        ),
        preferred_industry_bonus=_bounded_float(
            payload.get("preferred_industry_bonus", 0),
            "preferred_industry_bonus",
            0,
            30,
        ),
    )


def _industry_matches(industry: str, patterns: tuple[str, ...]) -> bool:
    normalized = industry.strip().casefold()
    return bool(
        normalized
        and any(pattern.casefold() in normalized for pattern in patterns if pattern.strip())
    )


def _reject_unknown(payload: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise StrategyValidationError(f"unknown strategy fields: {sorted(unknown)}")


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StrategyValidationError(f"{field} must be an object")
    return {str(key): item for key, item in value.items()}


def _required_string(payload: dict[str, Any], field: str, *, max_length: int) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise StrategyValidationError(f"{field} must be a non-empty bounded string")
    return value.strip()


def _string_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 100:
        raise StrategyValidationError(f"{field} must be a list with at most 100 strings")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 80:
            raise StrategyValidationError(f"{field} contains an invalid string")
        if item.strip() not in normalized:
            normalized.append(item.strip())
    return tuple(normalized)


def _bounded_int(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise StrategyValidationError(f"{field} must be an integer in [{minimum}, {maximum}]")
    return value


def _bounded_float(value: object, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StrategyValidationError(f"{field} must be a number")
    normalized = float(value)
    if not minimum <= normalized <= maximum:
        raise StrategyValidationError(f"{field} must be in [{minimum}, {maximum}]")
    return normalized
