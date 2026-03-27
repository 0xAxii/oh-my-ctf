"""Tool call repetition detection.

verialabs pattern: track recent tool signatures, warn/break on repetition.
Parameters are provisional — tune after empirical testing.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field


@dataclass
class LoopDetector:
    window: int = 12
    warn_threshold: int = 3
    break_threshold: int = 5
    _recent: deque[str] = field(init=False)

    def __post_init__(self) -> None:
        self._recent = deque(maxlen=self.window)

    def check(self, tool_name: str, args: dict | str | None = None) -> str | None:
        """Returns None (ok), 'warn', or 'break'."""
        if args:
            raw = json.dumps(args, sort_keys=True) if isinstance(args, dict) else str(args)
            sig = f"{tool_name}:{raw[:500]}"
        else:
            sig = tool_name
        self._recent.append(sig)

        count = sum(1 for s in self._recent if s == sig)
        if count >= self.break_threshold:
            return "break"
        if count >= self.warn_threshold:
            return "warn"
        return None

    @property
    def last_sig(self) -> str:
        return self._recent[-1] if self._recent else ""

    def reset(self) -> None:
        self._recent.clear()


LOOP_WARNING = (
    "You are stuck in a loop — same command run multiple times with identical results. "
    "STOP repeating. Try a completely different technique or tool."
)
