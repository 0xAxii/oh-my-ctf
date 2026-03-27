"""Terminal IO — v1 stdin/stdout interface for Manager.

Replaced by discord_bot.py in Phase 2.
"""

from __future__ import annotations

import asyncio
import sys


async def read_input(prompt: str = "> ") -> str:
    """Non-blocking stdin read."""
    loop = asyncio.get_event_loop()
    sys.stdout.write(prompt)
    sys.stdout.flush()
    return await loop.run_in_executor(None, sys.stdin.readline)


async def write_output(text: str) -> None:
    """Write to stdout."""
    print(text)
