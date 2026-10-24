"""Silence handling for live voice calls (quotation 2.6).

A short silence is normal, so the agent never repeats itself at once. The
monitor is a small pure state machine (no I/O, injectable clock) driven by the
voice adapter:

    agent finishes speaking -> armed
    ``check_after`` seconds of caller silence  -> "check" (gentle "can you hear me?")
    after that check, ``check_after`` more     -> "check" again, up to ``max_checks``
    still silent after the last check          -> "connection_issue" (once)

Any sign of the caller (speech starting, a transcript) resets it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SilenceAction = Literal["check", "connection_issue"]


@dataclass
class SilenceMonitor:
    check_after: float = 15.0
    max_checks: int = 2
    checks: int = 0
    issue: bool = False
    _deadline: float | None = None

    def agent_finished(self, now: float, patience: float = 1.0) -> None:
        """The agent finished speaking (``now`` = when its audio stops playing). ``patience`` stretches the wait
        before the FIRST check, for a caller who asked for a moment to think."""
        if not self.issue:
            self._deadline = now + self.check_after * (patience if self.checks == 0 else 1.0)

    def caller_active(self) -> None:
        """The caller made a sound or said something: start over."""
        self.checks = 0
        self._deadline = None

    def poll(self, now: float, *, busy: bool) -> SilenceAction | None:
        """What to do right now, if anything. ``busy`` = the agent is speaking
        or a reply is on its way, so silence can't be judged."""
        if self.issue or self._deadline is None or busy or now < self._deadline:
            return None
        self._deadline = None  # re-armed by agent_finished once the prompt has played
        if self.checks < self.max_checks:
            self.checks += 1
            return "check"
        self.issue = True
        return "connection_issue"
