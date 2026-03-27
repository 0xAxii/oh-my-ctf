"""Manager Agent — conversational interface between user and swarm.

Uses GPT-5.4-mini/medium for on-demand Korean dialogue.
v1: terminal stdin/stdout. Phase 2: Discord.
"""

from __future__ import annotations

import asyncio
import logging

from core.app_server import AppServerClient

logger = logging.getLogger(__name__)

MANAGER_SYSTEM_PROMPT = """You are a CTF challenge management assistant.
You communicate with the user in Korean.
Your role is to be the interface between the user and the CTF solving pipeline.

IMPORTANT RULES:
- When the user sends a challenge file/description, ONLY summarize what you received (file name, category, brief description). Do NOT analyze the challenge or suggest solutions.
- Do NOT start solving until the user explicitly says to start (e.g. "풀어", "시작", "풀이 시작").
- Wait for user instructions. You are a manager, not a solver.

Your responsibilities:
- Receive and organize challenge info from the user
- Start/stop the solving pipeline when instructed
- Relay progress from solvers to the user
- Pass user hints to solvers
- Report flag discoveries
- Remember the flag format when the user tells you (e.g. "DH format", "flag{} format")

Keep responses concise (1-3 sentences).
"""


class Manager:
    """Conversational manager for CTF solving."""

    def __init__(self) -> None:
        self.client = AppServerClient()
        self.thread_id = ""
        self._ready = False

    async def init(self) -> None:
        await self.client.connect()
        self.thread_id = await self.client.start_thread(
            model="gpt-5.4-mini",
            cwd=".",
        )

        # Collect init response
        buf = []
        done = asyncio.Event()

        def _on_event(event):
            if event.method == "item/agentMessage/delta":
                buf.append(event.params.get("delta", ""))
            elif event.method == "turn/completed":
                done.set()

        self.client.on_event(_on_event)

        await self.client.start_turn(self.thread_id, [
            {"type": "text", "text": MANAGER_SYSTEM_PROMPT + "\n\n초기화 완료. 사용자 메시지를 기다립니다."}
        ])
        await asyncio.wait_for(done.wait(), timeout=30)
        self._ready = True
        logger.info("Manager initialized")

    async def handle_message(self, text: str, context: str = "") -> str:
        """Send user message to Manager LLM, return response."""
        if not self._ready:
            await self.init()

        buf = []
        done = asyncio.Event()

        def _on_event(event):
            if event.method == "item/agentMessage/delta":
                buf.append(event.params.get("delta", ""))
            elif event.method == "turn/completed":
                done.set()

        # Re-register handler (fresh for each turn)
        self.client._event_handlers.clear()
        self.client.on_event(_on_event)

        prompt = text
        if context:
            prompt = f"[시스템 컨텍스트] {context}\n\n[사용자] {text}"

        await self.client.start_turn(self.thread_id, [
            {"type": "text", "text": prompt}
        ])

        try:
            await asyncio.wait_for(done.wait(), timeout=60)
        except asyncio.TimeoutError:
            return "(Manager 응답 시간 초과)"

        return "".join(buf).strip()

    async def report_event(self, event: str, data: str = "") -> str:
        """Report a pipeline event to the manager for user-facing summary."""
        return await self.handle_message(
            f"[파이프라인 이벤트] {event}: {data}\n\n위 상황을 사용자에게 한국어로 간결하게 보고해주세요."
        )

    async def destroy(self) -> None:
        await self.client.destroy()
