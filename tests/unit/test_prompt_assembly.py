from __future__ import annotations

from pathlib import Path

import pytest

from aegisrun.harness import (
    ModelRequestEnvelope,
    PromptAssemblyError,
    PromptContext,
    PromptRegistry,
    PromptSection,
    RuntimeContextProjection,
    WorkspaceEventStore,
    default_invariants,
    derive_surface,
)


def test_prompt_registry_layers_orders_and_strictly_interpolates() -> None:
    root = PromptRegistry()
    root.section(PromptSection("identity", -100, "Harness {{model}}", "core"))
    root.section(PromptSection("persona", 0, "Global", "deployment"))
    root.context(PromptContext("sandbox", 110, "Mode: {{mode}}", "sandbox"))
    root.variable("model", "deepseek-v4-flash")
    root.variable("mode", "read-only")
    root.tool(
        {
            "name": "read_market_data",
            "description": "Read daily bars",
            "parameters": {"type": "object"},
        },
        source="marketdata",
    )
    child = root.child()
    child.section(PromptSection("persona", 0, "Child", "preset"))

    assembly = child.assemble()

    assert assembly.system == "Harness deepseek-v4-flash\n\nChild"
    assert assembly.runtime_context is not None
    assert assembly.runtime_context.endswith("Mode: read-only")
    assert [tool.name for tool in assembly.tools] == ["read_market_data"]
    assert assembly.to_dict()["sections"][1]["source"] == "preset"


def test_prompt_registry_complete_suppression_and_fail_loud_rules() -> None:
    root = PromptRegistry()
    root.variable("model", None)
    root.section(PromptSection("broken", 0, "{{model}}", "test"))
    with pytest.raises(PromptAssemblyError, match="undefined"):
        root.assemble()

    complete = PromptRegistry()
    complete.section(PromptSection("identity", -100, "ignored", "core"))
    complete.section(PromptSection("minimal", 0, "Exact prompt.", "preset", complete=True))
    complete.context(PromptContext("policy", 1, "hidden context", "policy"))
    complete.suppress_runtime_context()
    result = complete.assemble()
    assert result.system == "Exact prompt."
    assert result.runtime_context is None

    duplicate = complete.child()
    duplicate.section(PromptSection("other", 1, "Other", "test", complete=True))
    with pytest.raises(PromptAssemblyError, match="multiple complete"):
        duplicate.assemble()


def test_prompt_contributions_are_reversible_and_snapshotted() -> None:
    registry = PromptRegistry()
    registration = registry.section(PromptSection("one", 1, "first", "test"))
    schema = {"name": "echo", "parameters": {"type": "object"}}
    registry.tool(schema, source="test")
    schema["name"] = "mutated"

    assert registry.assemble().tools[0].name == "echo"
    registration.dispose()
    registration.dispose()
    assert registry.assemble().system is None


def test_model_request_envelope_rejects_embedded_credential_fields() -> None:
    prompt = PromptRegistry().assemble()
    with pytest.raises(ValueError, match="credential field"):
        ModelRequestEnvelope.create(
            provider="deepseek",
            model="test",
            prompt=prompt,
            messages=({"role": "user", "content": "hello"},),
            effective_config={},
            defaults={},
            request_body={
                "model": "test",
                "messages": [{"role": "user", "content": "hello"}],
                "api_key": "must-not-persist",
            },
        )


@pytest.mark.asyncio
async def test_runtime_context_is_durable_diffed_and_surface_replayable(tmp_path: Path) -> None:
    registry = PromptRegistry()
    registry.context(PromptContext("policy", 1, "read-only", "sandbox"))
    events = WorkspaceEventStore(
        tmp_path / "events.jsonl", run_id="run-1", invariants=default_invariants()
    )
    projection = RuntimeContextProjection()

    first = await projection.project(registry.assemble(), events, actor_id="agent-1")
    repeated = await projection.project(registry.assemble(), events, actor_id="agent-1")
    registry.close()
    empty = PromptRegistry()
    cleared = await projection.project(empty.assemble(), events, actor_id="agent-1")

    assert first is not None
    assert repeated is None
    assert cleared is not None
    surface = derive_surface(await events.load())
    assert len(surface) == 2
    assert "read-only" in surface[0].content[0]["text"]
    assert "none" in surface[1].content[0]["text"]
