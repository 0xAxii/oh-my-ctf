"""AppServerSolver — drives one Codex App Server instance for a single challenge.

Implements SolverProtocol: start, run_until_done_or_gave_up, bump, stop.
Uses BumpEngine internally for fresh-session restarts.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from core.app_server import AppServerClient, AppServerEvent, SANDBOX_TOOLS
from core.loop_detector import LoopDetector, LOOP_WARNING
from core.message_bus import ChallengeMessageBus
from core.solver_base import (
    CANCELLED, ERROR, FLAG_FOUND, GAVE_UP, SolverResult,
)
from core.tracer import SolverTracer

logger = logging.getLogger(__name__)

# Flag candidate regex — Manager/LightCritic does real verification
FLAG_PATTERN = re.compile(r"(flag|FLAG|DH|CTF|GoN)\{[^}]+\}")


@dataclass
class AppServerSolver:
    """One solver instance = one model racing on one challenge."""

    model_spec: str                     # e.g. "gpt-5.4"
    effort: str                         # e.g. "xhigh"
    challenge_dir: str
    system_prompt: str                  # category-specific, from Recon
    cancel_event: asyncio.Event
    message_bus: ChallengeMessageBus
    flag_format: str = r"(flag|FLAG|DH|CTF|GoN)\{[^}]+\}"

    container_id: str = ""  # Docker container ID — if set, tools run via docker exec

    # Internal state
    client: AppServerClient = field(init=False)
    tracer: SolverTracer | None = None
    thread_id: str = ""
    turn_id: str = ""
    step_count: int = 0
    findings_summary: str = ""
    _response_buf: str = ""
    _turn_done: asyncio.Event = field(default_factory=asyncio.Event)
    _turn_status: str = ""
    _turn_error: str = ""
    _flag: str | None = None
    _loop_detector: LoopDetector = field(default_factory=LoopDetector)
    _bump_findings: str = ""

    def __post_init__(self) -> None:
        self.client = AppServerClient(tool_executor=self._exec_tool)

    async def _exec_tool(self, tool_name: str, args: dict) -> str:
        """Execute a tool — routes through docker exec if container_id is set.

        All subprocess calls are async to avoid blocking the event loop.
        """

        async def _run_cmd(cmd: list[str], timeout: float = 60, input_data: str = "") -> tuple[int, str, str]:
            """Run a command asynchronously, return (returncode, stdout, stderr)."""
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if input_data else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(input_data.encode() if input_data else None),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return -1, "", "Error: command timed out"
            return proc.returncode, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")

        if tool_name == "bash":
            cmd = args.get("command", "")
            timeout = args.get("timeout_seconds", 60)
            if self.container_id:
                full_cmd = ["docker", "exec", self.container_id, "bash", "-c", cmd]
            else:
                full_cmd = ["bash", "-c", cmd]
            rc, stdout, stderr = await _run_cmd(full_cmd, timeout=timeout)
            if rc == -1:
                return stderr
            return (stdout + stderr)[:50000]

        elif tool_name == "read_file":
            path = args.get("path", "")
            if self.container_id:
                rc, stdout, stderr = await _run_cmd(["docker", "exec", self.container_id, "cat", path], timeout=10)
                return stdout[:50000] if rc == 0 else stderr
            else:
                try:
                    with open(path) as f:
                        return f.read()[:50000]
                except Exception as e:
                    return f"Error: {e}"

        elif tool_name == "write_file":
            path = args.get("path", "")
            content = args.get("content", "")
            if self.container_id:
                rc, stdout, stderr = await _run_cmd(
                    ["docker", "exec", "-i", self.container_id, "bash", "-c", f"cat > {path}"],
                    timeout=10, input_data=content,
                )
                return "OK" if rc == 0 else stderr
            else:
                try:
                    with open(path, "w") as f:
                        f.write(content)
                    return "OK"
                except Exception as e:
                    return f"Error: {e}"

        elif tool_name == "list_files":
            path = args.get("path", "/challenge")
            if self.container_id:
                rc, stdout, stderr = await _run_cmd(["docker", "exec", self.container_id, "ls", "-la", path], timeout=10)
            else:
                rc, stdout, stderr = await _run_cmd(["ls", "-la", path], timeout=10)
            return stdout[:50000] if rc == 0 else stderr

        return f"Unknown tool: {tool_name}"

    async def start(self) -> None:
        self.tracer = SolverTracer(
            challenge_name=self.challenge_dir.split("/")[-1],
            model_id=self.model_spec,
        )
        self.client.on_event(self._handle_event)
        await self.client.connect()
        self.thread_id = await self.client.start_thread(
            model=self.model_spec,
            cwd=self.challenge_dir,
            dynamic_tools=SANDBOX_TOOLS,
        )

    async def run_until_done_or_gave_up(self) -> SolverResult:
        """Run one turn. Returns when turn completes or is cancelled."""
        self._turn_done.clear()
        self._turn_status = ""
        self._turn_error = ""
        self._response_buf = ""
        self._flag = None

        # Build input: system prompt + bump findings if any
        input_items = [{"type": "text", "text": self.system_prompt}]
        if self._bump_findings:
            input_items.append({
                "type": "text",
                "text": f"## Previous verified findings\n\n{self._bump_findings}",
            })
            self._bump_findings = ""

        # Check MessageBus for cross-solver insights
        unread = await self.message_bus.check(self.model_spec)
        if unread:
            formatted = self.message_bus.format_unread(unread)
            input_items.append({"type": "text", "text": formatted})

        try:
            self.turn_id = await self.client.start_turn(
                self.thread_id, input_items, effort=self.effort,
            )
        except Exception as e:
            logger.error("[%s] start_turn failed: %s", self.model_spec, e)
            return SolverResult(
                flag=None, status=ERROR,
                findings_summary=str(e),
                step_count=self.step_count,
                log_path=self.tracer.path if self.tracer else "",
            )

        # Wait for turn to complete or cancellation
        cancel_task = asyncio.create_task(self.cancel_event.wait())
        done_task = asyncio.create_task(self._turn_done.wait())

        done, pending = await asyncio.wait(
            [cancel_task, done_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for p in pending:
            p.cancel()

        if self.cancel_event.is_set():
            # Interrupted by swarm (another solver found flag)
            try:
                await self.client.interrupt(self.thread_id, self.turn_id)
            except Exception:
                pass
            return SolverResult(
                flag=None, status=CANCELLED,
                findings_summary=self.findings_summary,
                step_count=self.step_count,
                log_path=self.tracer.path if self.tracer else "",
            )

        # Capture response as findings summary for bump injection
        if self._response_buf:
            self.findings_summary = self._response_buf[:2000]

        # Turn completed normally
        if self._flag:
            return SolverResult(
                flag=self._flag, status=FLAG_FOUND,
                findings_summary=self.findings_summary,
                step_count=self.step_count,
                log_path=self.tracer.path if self.tracer else "",
            )

        if self._turn_error:
            self.tracer.event("turn_error", error=self._turn_error) if self.tracer else None
            return SolverResult(
                flag=None, status=ERROR,
                findings_summary=self._turn_error,
                step_count=self.step_count,
                log_path=self.tracer.path if self.tracer else "",
            )

        # No flag, no error — gave up or stopped early
        return SolverResult(
            flag=None, status=GAVE_UP,
            findings_summary=self.findings_summary,
            step_count=self.step_count,
            log_path=self.tracer.path if self.tracer else "",
        )

    def bump(self, findings: str) -> None:
        """Inject verified findings for the next fresh session."""
        self._bump_findings = findings
        self._loop_detector.reset()

    async def stop(self) -> None:
        await self.client.destroy()
        if self.tracer:
            self.tracer.close()

    # --- Event handling ---

    def _handle_event(self, event: AppServerEvent) -> None:
        method = event.method
        params = event.params

        if method == "turn/completed":
            turn = params.get("turn", {})
            self._turn_status = turn.get("status", "completed")
            error = params.get("error", {})
            if error:
                codex_error = error.get("codexErrorInfo", "")
                self._turn_error = codex_error or error.get("message", "")
            self._turn_done.set()

        elif method == "item/agentMessage/delta":
            delta = params.get("delta", "")
            self._response_buf += delta
            self._check_for_flag(delta)
            self.step_count += 1
            if self.tracer and delta:
                self.tracer.model_response(delta, self.step_count)

        elif method == "item/commandExecution/outputDelta":
            output = params.get("delta", "")
            if self.tracer:
                tool = params.get("tool", "bash")
                self.tracer.tool_result(tool, output[:2000], self.step_count)
            self._check_for_flag(output)

        elif method == "item/started":
            item_type = params.get("item", {}).get("type", "")
            if item_type == "tool_call":
                tool_name = params.get("item", {}).get("name", "unknown")
                tool_args = params.get("item", {}).get("arguments", "")
                if self.tracer:
                    self.tracer.tool_call(tool_name, str(tool_args)[:2000], self.step_count)
                # Loop detection
                loop_status = self._loop_detector.check(tool_name, tool_args)
                if loop_status == "break":
                    logger.warning("[%s] Loop detected, breaking", self.model_spec)
                    self.findings_summary += f"\n[LOOP BREAK] {LOOP_WARNING}"

    def _check_for_flag(self, text: str) -> None:
        """Check text for flag candidates."""
        if not text:
            return
        pattern = re.compile(self.flag_format)
        match = pattern.search(text)
        if match:
            candidate = match.group(0)
            if self.tracer:
                self.tracer.flag_candidate(candidate, f"step_{self.step_count}")
            # Store as candidate — LightCritic will verify
            self._flag = candidate
            logger.info("[%s] Flag candidate: %s", self.model_spec, candidate)
