"""Recon — fast fact-gathering for challenge analysis.

Uses GPT-5.4 medium to quickly run checksec/file/strings etc.
Returns raw facts only — no strategy or solver prompts.
Solver decides its own approach based on facts + category knowledge.
"""

from __future__ import annotations

import logging

from core.app_server import AppServerClient

logger = logging.getLogger(__name__)

RECON_SYSTEM_PROMPT = """You are a FAST CTF recon agent. Gather facts only — do NOT plan strategy.

Spend under 60 seconds. Run these checks and NOTHING else:

1. `find . -type f | head -40` — list challenge files
2. `file *` on binaries/executables
3. If binary: `checksec`, `readelf -h`, `strings | head -50`
4. If source code: identify language, framework, key entrypoints
5. If web: glance at docker-compose.yml, identify tech stack and routes
6. If crypto: identify primitives, parameters, key sizes

Output ONE section only:

## RECON_FACTS
List every verified fact, one per line. Only facts confirmed by tool output.
Include:
- File types and structure
- Binary protections (NX, PIE, canary, RELRO)
- Language/framework/libraries
- Key function names and file locations
- Crypto parameters (key sizes, algorithms, modes)
- Service ports and protocols
- Any hardcoded strings, credentials, or interesting constants
- Challenge category (pwn/rev/crypto/web/forensics/web3/misc/ai)

Do NOT include:
- Strategy recommendations
- Attack suggestions
- "Next steps" or plans
- Opinions about difficulty
"""

MAX_RECON_RETRIES = 3


def _load_category_prompt(category: str) -> str:
    """Load category-specific prompt from categories/{category}.md."""
    from pathlib import Path
    project_root = Path(__file__).parent.parent
    prompt_path = project_root / "categories" / f"{category}.md"
    try:
        return prompt_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


async def run_recon(
    challenge_dir: str,
    category: str = "",
) -> str:
    """Run recon via LLM and return facts string.

    Recon only gathers facts (checksec, file, strings, etc).
    Strategy is left to the solver.
    """
    for attempt in range(1, MAX_RECON_RETRIES + 1):
        client = AppServerClient()
        try:
            await client.connect()
            thread_id = await client.start_thread(
                model="gpt-5.4",
                cwd=challenge_dir,
            )

            response_buf = []
            turn_done = asyncio.Event()
            event_count = [0]

            def _on_event(event):
                event_count[0] += 1
                if event.method == "item/agentMessage/delta":
                    response_buf.append(event.params.get("delta", ""))
                elif event.method == "turn/completed":
                    logger.info("Recon turn completed (%d events, %d chars response)", event_count[0], len("".join(response_buf)))
                    turn_done.set()
                elif event.method == "item/started":
                    item = event.params.get("item", {})
                    if item.get("type") == "tool_call":
                        logger.info("Recon tool call: %s", item.get("name", "unknown"))

            client.on_event(_on_event)

            prompt = RECON_SYSTEM_PROMPT
            if category:
                prompt = f"Category hint: {category}\n\n" + prompt

            await client.start_turn(thread_id, [{"type": "text", "text": prompt}], effort="medium")
            await turn_done.wait()

            full_response = "".join(response_buf)
            facts = _parse_recon_facts(full_response)
            if facts:
                logger.info("Recon produced facts (%d chars)", len(facts))
                return facts

            logger.warning("Recon attempt %d/%d: no usable facts, retrying...", attempt, MAX_RECON_RETRIES)

        except Exception as e:
            logger.error("Recon attempt %d/%d failed: %s", attempt, MAX_RECON_RETRIES, e)
        finally:
            await client.destroy()

    raise RuntimeError(f"Recon failed after {MAX_RECON_RETRIES} attempts")


def _parse_recon_facts(text: str) -> str:
    """Extract RECON_FACTS section from recon output.

    Falls back to full response if section header not found.
    """
    current = None
    facts_lines = []

    for line in text.split("\n"):
        stripped = line.strip().upper().replace("#", "").strip()
        if stripped == "RECON_FACTS":
            current = "RECON_FACTS"
            continue
        if current == "RECON_FACTS":
            # Stop if we hit another section header
            if stripped and stripped.startswith("##"):
                break
            facts_lines.append(line)

    facts = "\n".join(facts_lines).strip()

    # Fallback: if no section found, use the whole response as facts
    if not facts and text.strip():
        logger.warning("No RECON_FACTS section found, using full response as facts")
        facts = text.strip()

    return facts


# Make asyncio available at module level for run_recon
import asyncio
