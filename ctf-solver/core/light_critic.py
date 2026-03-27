"""LightCritic — findings verification via GPT-5.3-codex-spark.

Watches solver findings_raw files, verifies against JSONL trace,
promotes verified findings to findings_verified, detects flag candidates.

Flow:
  Solver writes findings_raw_{model}.md
    → LightCritic (spark) reads raw + trace
    → Verified? → findings_verified.json
    → Flag detected? → notify Manager
    → Fake flag? → record in findings_verified as "fake"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.app_server import AppServerClient

logger = logging.getLogger(__name__)

CRITIC_SYSTEM_PROMPT = """You are a CTF findings verifier. You receive:
1. Raw findings from a solver (free-form text)
2. JSONL trace of the solver's tool calls and outputs

Your job: cross-reference each finding against the trace.

For each finding, determine:
- Is this claim supported by actual tool output in the trace?
- If it's an address/offset: does it appear in GDB/objdump/readelf output?
- If it's a vulnerability type: does the relevant function exist in the binary?
- If it's a protection status: does it match checksec output?
- If it's a flag candidate: is it from actual exploit output or just strings/placeholder?

Output ONLY valid JSON array. Each element:
{
  "finding": "original finding text",
  "verified": true/false,
  "reason": "brief explanation",
  "is_flag": true/false,
  "is_fake_flag": true/false
}

Be strict. If a finding has no supporting evidence in the trace, mark verified=false.
Flag candidates that appear in strings output or are placeholder format (test, fake, example) are fake.
"""


@dataclass
class VerifiedFinding:
    finding: str
    verified: bool
    reason: str
    is_flag: bool = False
    is_fake_flag: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class LightCritic:
    """Verifies solver findings against trace using spark."""

    challenge_dir: str
    on_flag_found: asyncio.Queue | None = None  # notify Manager
    _client: AppServerClient | None = None
    _thread_id: str = ""
    _verified: list[VerifiedFinding] = field(default_factory=list)
    _fake_flags: list[str] = field(default_factory=list)
    _verified_path: str = ""

    async def start(self) -> None:
        self._verified_path = os.path.join(self.challenge_dir, "findings_verified.json")
        self._client = AppServerClient()
        await self._client.connect()
        self._thread_id = await self._client.start_thread(
            model="gpt-5.3-codex-spark",
            cwd=self.challenge_dir,
        )
        logger.info("LightCritic started (spark)")

    async def verify(self, raw_findings: str, trace_path: str) -> list[VerifiedFinding]:
        """Verify raw findings against trace. Returns newly verified findings."""
        if not self._client or not self._thread_id:
            logger.warning("LightCritic not started, using fallback")
            return await self._verify_fallback(raw_findings, trace_path)

        # Read last N lines of trace
        trace_content = self._read_trace_tail(trace_path, max_lines=100)
        if not trace_content:
            trace_content = "(no trace available)"

        prompt = f"""## Raw Findings
{raw_findings}

## Trace (last 100 events)
{trace_content}

Verify each finding. Output JSON array only."""

        # Run verification turn
        buf: list[str] = []
        done = asyncio.Event()

        def _on_event(event):
            if event.method == "item/agentMessage/delta":
                buf.append(event.params.get("delta", ""))
            elif event.method == "turn/completed":
                done.set()

        self._client._event_handlers.clear()
        self._client.on_event(_on_event)

        try:
            await self._client.start_turn(self._thread_id, [
                {"type": "text", "text": CRITIC_SYSTEM_PROMPT},
                {"type": "text", "text": prompt},
            ])
            await done.wait()
        except Exception as e:
            logger.warning("LightCritic spark failed: %s, using fallback", e)
            return await self._verify_fallback(raw_findings, trace_path)

        response = "".join(buf)
        new_findings = self._parse_response(response)

        # Process results
        for f in new_findings:
            self._verified.append(f)
            if f.is_fake_flag:
                self._fake_flags.append(f.finding)
                logger.info("Fake flag detected: %s", f.finding)
            if f.is_flag and not f.is_fake_flag and f.verified:
                logger.info("VERIFIED FLAG: %s", f.finding)
                if self.on_flag_found:
                    await self.on_flag_found.put(f.finding)

        self._save_verified()
        return new_findings

    async def _verify_fallback(self, raw_findings: str, trace_path: str) -> list[VerifiedFinding]:
        """Fallback: GPT-5.4 low when spark is unavailable."""
        if self._client:
            await self._client.destroy()

        self._client = AppServerClient()
        await self._client.connect()
        self._thread_id = await self._client.start_thread(
            model="gpt-5.4",
            cwd=self.challenge_dir,
        )
        logger.info("LightCritic fallback to 5.4 low")

        # Same flow as above
        trace_content = self._read_trace_tail(trace_path, max_lines=100)
        prompt = f"""## Raw Findings
{raw_findings}

## Trace (last 100 events)
{trace_content or '(no trace)'}

Verify each finding. Output JSON array only."""

        buf: list[str] = []
        done = asyncio.Event()

        def _on_event(event):
            if event.method == "item/agentMessage/delta":
                buf.append(event.params.get("delta", ""))
            elif event.method == "turn/completed":
                done.set()

        self._client._event_handlers.clear()
        self._client.on_event(_on_event)

        try:
            await self._client.start_turn(self._thread_id, [
                {"type": "text", "text": CRITIC_SYSTEM_PROMPT},
                {"type": "text", "text": prompt},
            ])
            await done.wait()
        except Exception as e:
            logger.error("LightCritic fallback also failed: %s", e)
            return []

        response = "".join(buf)
        return self._parse_response(response)

    def get_verified_summary(self) -> str:
        """Get all verified findings as text for bump injection."""
        verified_only = [f for f in self._verified if f.verified]
        if not verified_only:
            return ""
        lines = []
        for f in verified_only:
            tag = "[FLAG]" if f.is_flag else "[FACT]"
            lines.append(f"{tag} {f.finding}")
        if self._fake_flags:
            lines.append("")
            lines.append("## Known fake flags (DO NOT reuse)")
            for ff in self._fake_flags:
                lines.append(f"- {ff}")
        return "\n".join(lines)

    async def stop(self) -> None:
        if self._client:
            await self._client.destroy()

    def _read_trace_tail(self, trace_path: str, max_lines: int = 100) -> str:
        try:
            with open(trace_path, "r") as f:
                lines = f.readlines()
            return "".join(lines[-max_lines:])
        except Exception:
            return ""

    def _parse_response(self, text: str) -> list[VerifiedFinding]:
        """Parse JSON array from critic response."""
        # Extract JSON from response (might have markdown wrapping)
        json_match = re.search(r"\[.*\]", text, re.DOTALL)
        if not json_match:
            logger.warning("LightCritic returned no JSON array")
            return []
        try:
            items = json.loads(json_match.group())
        except json.JSONDecodeError:
            logger.warning("LightCritic returned invalid JSON")
            return []

        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            results.append(VerifiedFinding(
                finding=item.get("finding", ""),
                verified=item.get("verified", False),
                reason=item.get("reason", ""),
                is_flag=item.get("is_flag", False),
                is_fake_flag=item.get("is_fake_flag", False),
            ))
        return results

    def _save_verified(self) -> None:
        """Persist verified findings to JSON file."""
        data = [
            {
                "finding": f.finding,
                "verified": f.verified,
                "reason": f.reason,
                "is_flag": f.is_flag,
                "is_fake_flag": f.is_fake_flag,
                "timestamp": f.timestamp,
            }
            for f in self._verified
        ]
        try:
            with open(self._verified_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error("Failed to save verified findings: %s", e)
