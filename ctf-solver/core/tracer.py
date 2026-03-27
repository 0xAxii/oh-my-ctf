"""JSONL append-only event tracer.

One file per solver, streamable via tail -f.
Provides input for LightCritic trace-matching.
"""

from __future__ import annotations

import atexit
import json
import time
from pathlib import Path


def _sanitize(s: str) -> str:
    return s.replace("/", "_").replace(" ", "_")


class SolverTracer:
    def __init__(self, challenge_name: str, model_id: str, log_dir: str = "logs") -> None:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        self.path = str(
            Path(log_dir) / f"trace-{_sanitize(challenge_name)}-{_sanitize(model_id)}-{ts}.jsonl"
        )
        self._fh = open(self.path, "a")
        atexit.register(self._close)

    def close(self) -> None:
        if not self._fh.closed:
            try:
                self._fh.close()
            except Exception:
                pass

    _close = close

    def _write(self, event: dict) -> None:
        try:
            self._fh.write(json.dumps({"ts": time.time(), **event}) + "\n")
            self._fh.flush()
        except Exception:
            pass

    def tool_call(self, tool_name: str, args: str, step: int) -> None:
        self._write({"type": "tool_call", "tool": tool_name, "args": args[:2000], "step": step})

    def tool_result(self, tool_name: str, result: str, step: int) -> None:
        self._write({"type": "tool_result", "tool": tool_name, "result": result[:2000], "step": step})

    def model_response(self, text: str, step: int) -> None:
        self._write({"type": "model_response", "text": text[:1000], "step": step})

    def finding(self, key: str, value: str, source: str) -> None:
        self._write({"type": "finding", "key": key, "value": value, "source": source})

    def flag_candidate(self, flag: str, source: str) -> None:
        self._write({"type": "flag_candidate", "flag": flag, "source": source})

    def event(self, kind: str, **kwargs) -> None:
        self._write({"type": kind, **kwargs})
