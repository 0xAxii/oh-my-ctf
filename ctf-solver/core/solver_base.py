"""Solver result type, status constants, and protocol.

Shared across all solver backends.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


# Status constants
FLAG_FOUND = "flag_found"
GAVE_UP = "gave_up"
CANCELLED = "cancelled"
ERROR = "error"


@dataclass
class SolverResult:
    flag: str | None
    status: str
    findings_summary: str
    step_count: int
    log_path: str


class SolverProtocol(Protocol):
    model_spec: str

    async def start(self) -> None: ...
    async def run_until_done_or_gave_up(self) -> SolverResult: ...
    def bump(self, findings: str) -> None: ...
    async def stop(self) -> None: ...
