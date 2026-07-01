import asyncio

from aegis_core.tools.base import Tool
from aegis_core.tools.registry import ToolRegistry
from aegis_core.tools.search import ToolSearchIndex


async def test_dispatch_converts_exceptions_to_failure_result():
    def boom():
        raise RuntimeError("kaboom")

    registry = ToolRegistry()
    registry.register(Tool(name="boom", description="", input_schema={}, handler=boom))
    result = await registry.dispatch("boom", {})
    assert result.ok is False
    assert "kaboom" in result.error


async def test_availability_probe_flake_suppression():
    calls = {"n": 0}

    def flaky_check():
        calls["n"] += 1
        return calls["n"] != 2  # second probe fails, should be suppressed by grace window

    registry = ToolRegistry()
    tool = Tool(name="flaky", description="", input_schema={}, handler=lambda: "ok", check_fn=flaky_check)
    registry.register(tool)

    # Force TTL to expire between calls by manipulating internal state directly.
    state = registry._probe_state["flaky"]
    assert await registry._probe_available(tool) is True  # first probe: True
    state.last_checked = 0  # expire cache
    ok = await registry._probe_available(tool)  # second probe: False, but within grace window
    assert ok is True
    assert "suppressed" in (state.reason or "")


async def test_availability_report_surfaces_reason_for_unavailable_tool():
    registry = ToolRegistry()
    registry.register(
        Tool(name="down", description="", input_schema={}, handler=lambda: "x", check_fn=lambda: (False, "service unreachable"))
    )
    report = await registry.availability_report()
    entry = next(r for r in report if r["name"] == "down")
    # First probe always succeeds the grace window check trivially since there's no prior success;
    # after enough time (simulated via last_success default 0.0) it should be unavailable with a reason.
    assert entry["reason"] is not None


async def test_dispatch_batch_preserves_order():
    registry = ToolRegistry()
    registry.register(Tool(name="a", description="", input_schema={}, handler=lambda: "A", concurrency_safe=True))
    registry.register(Tool(name="b", description="", input_schema={}, handler=lambda: "B", concurrency_safe=False))
    results = await registry.dispatch_batch([("a", {}), ("b", {}), ("a", {})])
    assert [r.content for r in results] == ["A", "B", "A"]


def test_tool_search_defers_and_resolves():
    registry = ToolRegistry()
    for i in range(45):
        registry.register(
            Tool(
                name=f"tool_{i}",
                description="d",
                input_schema={},
                handler=lambda: "x",
                deferred=(i != 0),
            )
        )
    index = ToolSearchIndex(registry, defer_threshold=40)
    assert index.should_defer_catalog() is True
    visible_names = {t.name for t in index.visible_tools()}
    assert "tool_0" in visible_names
    assert "tool_7" not in visible_names

    resolved = index.resolve(["tool_7"])
    assert len(resolved) == 1
    visible_names_after = {t.name for t in index.visible_tools()}
    assert "tool_7" in visible_names_after
