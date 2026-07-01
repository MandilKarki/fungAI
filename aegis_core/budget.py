"""Dual iteration/token budget with a "grace call" before hard cutoff.

Source: hermes-agent's max_iterations + iteration_budget split, with a grace
mechanism so a model that's about to be cut off gets one more call carrying an
explicit "wrap up now" nudge instead of being severed mid-thought.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IterationBudget:
    max_iterations: int = 50
    max_tokens: int | None = None
    grace_calls: int = 1

    spent_iterations: int = field(default=0, init=False)
    spent_tokens: int = field(default=0, init=False)
    grace_used: int = field(default=0, init=False)

    def record(self, tokens_used: int = 0) -> None:
        self.spent_iterations += 1
        self.spent_tokens += tokens_used

    def _hard_cap_hit(self) -> bool:
        if self.spent_iterations >= self.max_iterations:
            return True
        if self.max_tokens is not None and self.spent_tokens >= self.max_tokens:
            return True
        return False

    def should_continue(self) -> tuple[bool, bool]:
        """Returns (can_continue, is_grace_call).

        is_grace_call is True exactly once, the one call after the cap is hit,
        and the caller is expected to inject a "wrap up" nudge for that call.
        """
        if not self._hard_cap_hit():
            return True, False
        if self.grace_used < self.grace_calls:
            self.grace_used += 1
            return True, True
        return False, False

    def remaining_iterations(self) -> int:
        return max(0, self.max_iterations - self.spent_iterations)
