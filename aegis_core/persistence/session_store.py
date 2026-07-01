"""Durable session persistence: save/restore a full AgentState (the
conversation itself) across process restarts.

Distinct from the BackendProtocol memory backends (those persist *files* the
agent reads/writes); this persists the transcript so a CLI session, or a
gateway-routed conversation, can resume after a restart. See ROADMAP.md.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

from aegis_core.state import AgentState, Message, StopReason, ToolCall

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


def _message_to_dict(m: Message) -> dict:
    return asdict(m)


def _message_from_dict(d: dict) -> Message:
    d = dict(d)
    tool_calls = [ToolCall(**tc) for tc in d.pop("tool_calls", [])]
    return Message(tool_calls=tool_calls, **d)


class SQLiteSessionStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _save_sync(self, session_id: str, state: AgentState) -> None:
        payload = {
            "messages": [_message_to_dict(m) for m in state.messages],
            "iteration": state.iteration,
            "stop_reason": state.stop_reason.value if state.stop_reason else None,
            "scratch": state.scratch,
        }
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO sessions (session_id, state_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET state_json = excluded.state_json, "
                "updated_at = excluded.updated_at",
                (session_id, json.dumps(payload, default=str), time.time()),
            )
            conn.commit()
        finally:
            conn.close()

    def _load_sync(self, session_id: str) -> AgentState | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT state_json FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        payload = json.loads(row["state_json"])
        state = AgentState()
        state.messages = [_message_from_dict(m) for m in payload["messages"]]
        state.iteration = payload["iteration"]
        state.stop_reason = StopReason(payload["stop_reason"]) if payload["stop_reason"] else None
        state.scratch = payload.get("scratch", {})
        return state

    def _list_sync(self) -> list[str]:
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT session_id FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def _delete_sync(self, session_id: str) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()
        finally:
            conn.close()

    async def save(self, session_id: str, state: AgentState) -> None:
        await asyncio.to_thread(self._save_sync, session_id, state)

    async def load(self, session_id: str) -> AgentState | None:
        return await asyncio.to_thread(self._load_sync, session_id)

    async def list_sessions(self) -> list[str]:
        return await asyncio.to_thread(self._list_sync)

    async def delete(self, session_id: str) -> None:
        await asyncio.to_thread(self._delete_sync, session_id)
