from aegis_core.checkpoints import CheckpointMiddleware, restore_checkpoint
from aegis_core.memory.state_backend import StateBackend
from aegis_core.state import AgentState


async def test_checkpoint_and_restore_roundtrip():
    memory = StateBackend({"/notes.txt": "original content"})
    middleware = CheckpointMiddleware(memory=memory)
    state = AgentState()

    await middleware.before_tool_call(
        tool_name="write_file", arguments={"path": "/notes.txt", "content": "new"}, state=state
    )
    checkpoint_id = state.scratch["checkpoints"][0]

    # simulate the tool call actually happening
    await memory.write("/notes.txt", "mutated content")
    assert await memory.read("/notes.txt") == "mutated content"

    restored = await restore_checkpoint(memory, checkpoint_id)
    assert restored is True
    assert await memory.read("/notes.txt") == "original content"


async def test_checkpoint_of_nonexistent_file_restores_by_deleting():
    memory = StateBackend()
    middleware = CheckpointMiddleware(memory=memory)
    state = AgentState()

    await middleware.before_tool_call(
        tool_name="write_file", arguments={"path": "/new.txt", "content": "x"}, state=state
    )
    checkpoint_id = state.scratch["checkpoints"][0]

    await memory.write("/new.txt", "it exists now")
    await restore_checkpoint(memory, checkpoint_id)

    try:
        await memory.read("/new.txt")
        assert False, "expected the file to be deleted on restore since it never existed before"
    except Exception:
        pass


async def test_restore_unknown_checkpoint_returns_false():
    memory = StateBackend()
    assert await restore_checkpoint(memory, "does-not-exist") is False
