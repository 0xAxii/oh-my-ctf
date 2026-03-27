"""ChallengeSwarm — parallel model racing on one challenge.

Two solvers (5.4 xhigh + 5.2 xhigh) race simultaneously.
First flag wins, rest cancelled. MessageBus shares findings.
BumpEngine restarts solvers on premature termination.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field

from core.light_critic import LightCritic
from core.message_bus import ChallengeMessageBus
from core.solver import AppServerSolver
from core.solver_base import (
    CANCELLED, ERROR, FLAG_FOUND, GAVE_UP, SolverResult,
)

logger = logging.getLogger(__name__)

BASE_BACKOFF = 15  # seconds
MAX_BACKOFF = 300


def _effort_for_bump(bump_count: int) -> str:
    """Return effort level based on bump count."""
    if bump_count <= 2:
        return "medium"
    if bump_count <= 5:
        return "high"
    return "xhigh"


def _backoff(bump_count: int) -> float:
    """Exponential backoff with jitter."""
    import random
    delay = min(BASE_BACKOFF * (2 ** bump_count), MAX_BACKOFF)
    jitter = random.uniform(-5, 5)
    return max(0, delay + jitter)


@dataclass
class ChallengeSwarm:
    """Parallel solvers racing on one challenge."""

    challenge_dir: str
    system_prompt_a: str          # Solver 1 prompt (from Recon)
    system_prompt_b: str          # Solver 2 prompt (from Recon, different approach)
    flag_format: str = r"(flag|FLAG|DH|CTF|GoN)\{[^}]+\}"

    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    message_bus: ChallengeMessageBus = field(default_factory=ChallengeMessageBus)
    solvers: dict[str, AppServerSolver] = field(default_factory=dict)
    findings: dict[str, str] = field(default_factory=dict)
    winner: SolverResult | None = None
    light_critic: LightCritic | None = None

    async def run(self) -> SolverResult | None:
        """Run both solvers in parallel. Returns winner's result or None."""
        self.light_critic = LightCritic(challenge_dir=self.challenge_dir, on_flag_found=asyncio.Queue())
        await self.light_critic.start()

        model_configs = [
            ("gpt-5.4", "medium", self.system_prompt_a),
            ("gpt-5.2", "medium", self.system_prompt_b),
        ]

        tasks = [
            asyncio.create_task(
                self._run_solver(model, effort, prompt),
                name=f"solver-{model}",
            )
            for model, effort, prompt in model_configs
        ]

        try:
            while tasks:
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    try:
                        result = task.result()
                    except Exception:
                        continue
                    if result and result.status == FLAG_FOUND:
                        self.cancel_event.set()
                        for p in pending:
                            p.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        self.winner = result
                        return result

                tasks = list(pending)

            self.cancel_event.set()
            return self.winner

        except Exception as e:
            logger.error("Swarm error: %s", e, exc_info=True)
            self.cancel_event.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return None

        finally:
            if self.light_critic:
                await self.light_critic.stop()

    async def _run_solver(
        self, model: str, effort: str, system_prompt: str,
    ) -> SolverResult | None:
        """Run one solver with bump loop."""
        solver = AppServerSolver(
            model_spec=model,
            effort=effort,
            challenge_dir=self.challenge_dir,
            system_prompt=system_prompt,
            cancel_event=self.cancel_event,
            message_bus=self.message_bus,
            flag_format=self.flag_format,
        )
        self.solvers[model] = solver

        try:
            await solver.start()
            return await self._bump_loop(solver, model)
        except Exception as e:
            logger.error("[%s] Fatal: %s", model, e, exc_info=True)
            return None
        finally:
            await solver.stop()

    async def _bump_loop(
        self, solver: AppServerSolver, model: str,
    ) -> SolverResult:
        """BumpEngine: fresh session + verified findings on each premature stop."""
        bump_count = 0
        consecutive_errors = 0

        while not self.cancel_event.is_set():
            result = await solver.run_until_done_or_gave_up()

            # Broadcast useful findings to other solvers
            if result.findings_summary and result.status not in (ERROR,):
                self.findings[model] = result.findings_summary
                await self.message_bus.post(model, result.findings_summary[:500])

            # Verify findings through LightCritic
            if self.light_critic and result.findings_summary:
                trace_path = solver.tracer.path if solver.tracer else ""
                await self.light_critic.verify(result.findings_summary, trace_path)

            # Check if LightCritic found a verified flag
            if self.light_critic and self.light_critic.on_flag_found:
                try:
                    flag = self.light_critic.on_flag_found.get_nowait()
                    return SolverResult(flag=flag, status=FLAG_FOUND, findings_summary=result.findings_summary, step_count=result.step_count, log_path=result.log_path)
                except asyncio.QueueEmpty:
                    pass

            if result.status == FLAG_FOUND:
                self.winner = result
                logger.info("[%s] Flag found: %s", model, result.flag)
                return result

            if result.status == CANCELLED:
                return result

            if result.status == ERROR:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    logger.warning("[%s] 3 consecutive errors, giving up", model)
                    return result
            else:
                consecutive_errors = 0

            # Bump: fresh session with accumulated findings
            bump_count += 1
            new_effort = _effort_for_bump(bump_count)
            solver.effort = new_effort

            # Cooldown with cancellation check
            cooldown = _backoff(bump_count)
            try:
                await asyncio.wait_for(
                    self.cancel_event.wait(),
                    timeout=cooldown,
                )
                return result  # Cancelled during cooldown
            except asyncio.TimeoutError:
                pass

            # Use LightCritic's verified findings for bump injection instead of raw insights
            if self.light_critic:
                verified = self.light_critic.get_verified_summary()
                insights = verified if verified else self._gather_insights(model)
            else:
                insights = self._gather_insights(model)

            # Fresh session: new thread, verified findings only
            logger.info("[%s] Bump %d (effort=%s), starting fresh session", model, bump_count, new_effort)

            # Destroy old client, create new one
            await solver.client.destroy()
            from core.app_server import AppServerClient
            solver.client = AppServerClient(tool_executor=solver._exec_tool)
            solver.client.on_event(solver._handle_event)
            await solver.client.connect()
            from core.app_server import SANDBOX_TOOLS
            solver.thread_id = await solver.client.start_thread(
                model=solver.model_spec,
                cwd=solver.challenge_dir,
                dynamic_tools=SANDBOX_TOOLS,
            )

            # Inject verified findings into next session
            solver.bump(insights)

        return SolverResult(
            flag=None, status=CANCELLED,
            findings_summary="", step_count=0,
            log_path=solver.tracer.path if solver.tracer else "",
        )

    def _gather_insights(self, exclude_model: str) -> str:
        """Collect findings from other solvers for bump injection."""
        parts = []
        for model, finding in self.findings.items():
            if model != exclude_model and finding:
                parts.append(f"[{model}]: {finding}")
        return "\n\n".join(parts) if parts else ""

    def kill(self) -> None:
        self.cancel_event.set()

    def get_status(self) -> dict:
        return {
            "challenge": self.challenge_dir,
            "cancelled": self.cancel_event.is_set(),
            "winner": self.winner.flag if self.winner else None,
            "solvers": {
                spec: {"findings": self.findings.get(spec, "")}
                for spec in self.solvers
            },
        }
