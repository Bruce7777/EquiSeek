from __future__ import annotations


class AegisRunError(Exception):
    code = "aegisrun_error"


class NotFoundError(AegisRunError):
    code = "not_found"


class ConflictError(AegisRunError):
    code = "conflict"


class InvalidTransitionError(AegisRunError):
    code = "invalid_transition"


class PolicyDeniedError(AegisRunError):
    code = "policy_denied"


class ApprovalRequiredError(AegisRunError):
    code = "approval_required"


class BudgetExceededError(AegisRunError):
    code = "budget_exhausted"


class SandboxViolationError(AegisRunError):
    code = "sandbox_violation"


class UnknownOutcomeError(AegisRunError):
    code = "unknown_external_outcome"
