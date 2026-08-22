"""Event-sourced, capability-scoped Agent Harness kernel."""

from aegisrun.harness.capabilities import (
    CapabilityDescriptor,
    CapabilityError,
    CapabilityRegistry,
    TrustLevel,
)
from aegisrun.harness.events import (
    AgentEvent,
    EventCorruptionError,
    EventError,
    EventSource,
    EventStore,
    WorkspaceEventStore,
)
from aegisrun.harness.invariants import InvariantError, InvariantRegistry, default_invariants
from aegisrun.harness.projections import HarnessProjection, project_events
from aegisrun.harness.prompt import (
    PromptAssembly,
    PromptAssemblyError,
    PromptContext,
    PromptRegistry,
    PromptSection,
    ToolSchemaSnapshot,
)
from aegisrun.harness.requests import ModelRequestEnvelope
from aegisrun.harness.scopes import ResourceScope
from aegisrun.harness.surface import RuntimeContextProjection, SurfaceMessage, derive_surface

__all__ = [
    "AgentEvent",
    "CapabilityDescriptor",
    "CapabilityError",
    "CapabilityRegistry",
    "EventCorruptionError",
    "EventError",
    "EventSource",
    "EventStore",
    "HarnessProjection",
    "InvariantError",
    "InvariantRegistry",
    "ModelRequestEnvelope",
    "PromptAssembly",
    "PromptAssemblyError",
    "PromptContext",
    "PromptRegistry",
    "PromptSection",
    "ResourceScope",
    "RuntimeContextProjection",
    "SurfaceMessage",
    "ToolSchemaSnapshot",
    "TrustLevel",
    "WorkspaceEventStore",
    "default_invariants",
    "derive_surface",
    "project_events",
]
