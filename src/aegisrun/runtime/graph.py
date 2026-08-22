from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph


class HarnessGraphState(TypedDict, total=False):
    phase: str
    steps: int


def build_checkpoint_graph(checkpointer: Any) -> Any:
    async def persist(state: HarnessGraphState) -> HarnessGraphState:
        return {
            "phase": state.get("phase", "created"),
            "steps": state.get("steps", 0),
        }

    graph = StateGraph(HarnessGraphState)
    graph.add_node("persist", persist)
    graph.add_edge(START, "persist")
    graph.add_edge("persist", END)
    return graph.compile(checkpointer=checkpointer)
