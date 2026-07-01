"""Branch-tree (git-like) session navigation: conversation history is a
navigable DAG, not a flat log. Jumping back to an earlier point and
continuing from there auto-summarizes the abandoned branch instead of
discarding it. Source: openclaw. See ARCHITECTURE.md section 7.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from aegis_core.context.engine import ContextEngine
from aegis_core.context.tiered_compression import default_token_counter
from aegis_core.state import AgentState, Message

Summarizer = Callable[[list[Message]], Awaitable[str]]


@dataclass
class MessageNode:
    node_id: str
    message: Message
    parent_id: str | None


@dataclass
class BranchSummary:
    branch_point_node_id: str
    abandoned_leaf_id: str
    summary: str
    message_count: int


@dataclass
class BranchTreeContextEngine(ContextEngine):
    """Every message appended to AgentState becomes a node in a tree keyed
    by node_id, rather than relying on flat list position. `track()` (called
    internally by every public method) keeps the tree in sync with
    state.messages by diffing on object identity.
    """

    token_budget: int = 100_000
    summarizer: Summarizer | None = None

    nodes: dict[str, MessageNode] = field(default_factory=dict)
    current_leaf_id: str | None = None
    root_id: str | None = None
    branch_summaries: list[BranchSummary] = field(default_factory=list)
    _seen: dict[int, str] = field(default_factory=dict)

    def track(self, state: AgentState) -> None:
        for msg in state.messages:
            key = id(msg)
            if key in self._seen:
                continue
            node_id = uuid.uuid4().hex[:12]
            node = MessageNode(node_id=node_id, message=msg, parent_id=self.current_leaf_id)
            self.nodes[node_id] = node
            self._seen[key] = node_id
            if self.root_id is None:
                self.root_id = node_id
            self.current_leaf_id = node_id

    def _path_to_root(self, node_id: str) -> list[MessageNode]:
        path: list[MessageNode] = []
        cur: str | None = node_id
        while cur is not None:
            node = self.nodes[cur]
            path.append(node)
            cur = node.parent_id
        path.reverse()
        return path

    def list_leaves(self) -> list[str]:
        parents = {n.parent_id for n in self.nodes.values() if n.parent_id}
        return [nid for nid in self.nodes if nid not in parents]

    @staticmethod
    def _common_ancestor_depth(path_a: list[MessageNode], path_b: list[MessageNode]) -> int:
        depth = 0
        for a, b in zip(path_a, path_b):
            if a.node_id != b.node_id:
                break
            depth += 1
        return depth

    async def navigate_to(self, node_id: str, state: AgentState) -> AgentState:
        """Jump to an earlier point in the tree and continue from there. The
        branch being abandoned (everything on the old leaf's path beyond the
        common ancestor with the new target) is summarized and recorded in
        `branch_summaries`, not silently discarded."""
        self.track(state)
        if node_id not in self.nodes:
            raise KeyError(f"no such branch node: {node_id}")

        old_leaf = self.current_leaf_id
        new_path = self._path_to_root(node_id)

        if old_leaf is not None and old_leaf != node_id:
            old_path = self._path_to_root(old_leaf)
            common_depth = self._common_ancestor_depth(new_path, old_path)
            abandoned = old_path[common_depth:]
            if abandoned:
                abandoned_messages = [n.message for n in abandoned]
                if self.summarizer is not None:
                    try:
                        summary_text = await self.summarizer(abandoned_messages)
                    except Exception:  # noqa: BLE001 — summarization must never block navigation
                        summary_text = f"[{len(abandoned)} messages on abandoned branch]"
                else:
                    summary_text = f"[{len(abandoned)} messages on abandoned branch]"
                self.branch_summaries.append(
                    BranchSummary(
                        branch_point_node_id=(
                            new_path[common_depth - 1].node_id if common_depth else ""
                        ),
                        abandoned_leaf_id=old_leaf,
                        summary=summary_text,
                        message_count=len(abandoned),
                    )
                )

        self.current_leaf_id = node_id
        state.messages = [n.message for n in new_path]
        self._seen = {id(n.message): n.node_id for n in new_path}
        return state

    async def should_compress(self, state: AgentState) -> bool:
        self.track(state)
        return default_token_counter(state.messages) >= self.token_budget

    async def compress(self, state: AgentState) -> AgentState:
        """Summarizes the *active* branch's older history while leaving the
        tree itself intact, so abandoned branches stay reachable via
        navigate_to() even after the live path has been compacted."""
        self.track(state)
        if len(state.messages) <= 6:
            return state
        head, tail = state.messages[:-6], state.messages[-6:]
        if self.summarizer is not None:
            try:
                summary_text = await self.summarizer(head)
            except Exception:  # noqa: BLE001
                summary_text = f"[{len(head)} earlier messages omitted]"
        else:
            summary_text = f"[{len(head)} earlier messages omitted]"
        summary_message = Message(
            role="system",
            content=f"[branch summary of {len(head)} earlier messages]\n{summary_text}",
            metadata={"compaction_summary": True, "branch_node_id": self.current_leaf_id},
        )
        state.messages = [summary_message, *tail]
        return state
