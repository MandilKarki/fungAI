from aegis_core.context.branch_tree import BranchTreeContextEngine
from aegis_core.context.engine import ContextEngine
from aegis_core.context.quarantine import QuarantineContextEngine
from aegis_core.context.tiered_compression import TieredCompressionEngine
from aegis_core.state import AgentState, Message


async def test_tiered_compression_protects_tail_and_summarizes_head():
    # protected_tail_tokens=0 forces the tail boundary to be governed purely
    # by protected_tail_floor rather than token budget (irrelevant at this
    # message size, since the default 20k-token budget would otherwise never
    # trigger a split for a handful of short messages).
    engine = TieredCompressionEngine(protected_tail_tokens=0, protected_tail_floor=2)
    state = AgentState()
    for i in range(10):
        state.append(Message(role="user", content=f"message {i}"))

    compressed = await engine.compress(state)
    assert compressed.messages[0].metadata.get("compaction_summary") is True
    # the last two original messages must survive untouched
    assert compressed.messages[-1].content == "message 9"
    assert compressed.messages[-2].content == "message 8"


def test_tiered_compression_dedupes_identical_tool_results():
    engine = TieredCompressionEngine()
    head = [
        Message(role="tool", content="same output", name="x", tool_call_id="1"),
        Message(role="tool", content="same output", name="x", tool_call_id="2"),
        Message(role="tool", content="different output", name="x", tool_call_id="3"),
    ]
    deduped = engine._dedupe_tool_results(head)
    assert deduped[0].content == "same output"  # first occurrence kept verbatim
    assert "duplicate" in deduped[1].content  # second, identical, replaced with a stub
    assert deduped[2].content == "different output"  # distinct content untouched


async def test_branch_tree_navigate_back_summarizes_abandoned_branch():
    engine = BranchTreeContextEngine()
    state = AgentState()

    state.append(Message(role="user", content="m1"))
    engine.track(state)
    leaf1 = engine.current_leaf_id

    state.append(Message(role="assistant", content="m2"))
    engine.track(state)
    state.append(Message(role="user", content="m3"))
    engine.track(state)

    await engine.navigate_to(leaf1, state)

    assert [m.content for m in state.messages] == ["m1"]
    assert len(engine.branch_summaries) == 1
    assert engine.branch_summaries[0].message_count == 2


async def test_quarantine_falls_back_after_max_failures():
    class Flaky(ContextEngine):
        async def should_compress(self, state):
            raise RuntimeError("boom")

        async def compress(self, state):
            raise RuntimeError("boom")

    quarantine = QuarantineContextEngine(inner=Flaky(), max_failures=1)
    state = AgentState()

    assert quarantine.is_quarantined is False
    result = await quarantine.should_compress(state)
    assert result is False  # fallback (PassthroughContextEngine) never compresses
    assert quarantine.is_quarantined is True

    # subsequent calls go straight to the fallback, no more exceptions raised
    result2 = await quarantine.compress(state)
    assert result2 is state
