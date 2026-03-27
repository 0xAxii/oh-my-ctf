"""Recon — initial challenge exploration + custom solver prompt generation.

Uses GPT-5.4-mini/medium to quickly analyze the challenge,
then generates tailored prompts for each solver (different approaches).
Findings are verified by LightCritic before being passed to solvers.

Pattern: D-CIPHER AutoPrompter + blitz Recon/Scout
"""

from __future__ import annotations

import logging

from core.app_server import AppServerClient

logger = logging.getLogger(__name__)

RECON_SYSTEM_PROMPT = """You are a FAST CTF recon agent. Be quick — spend under 60 seconds.

DO NOT read every file. DO NOT do deep analysis. Just:
1. Run: find . -type f | head -30
2. Run: file on any binaries
3. If web: glance at docker-compose.yml and the main app entrypoint only
4. If binary: run checksec
5. Identify the challenge TYPE and likely vulnerability in 1-2 sentences

After this QUICK look, output THREE things:

## FINDINGS
List every verified fact you discovered (addresses, protections, vuln type, etc).
One fact per line. Only facts you confirmed with tool output.

## SOLVER_PROMPT_A
Write a detailed starting prompt for Solver A (GPT-5.4).
This prompt MUST follow GPT-5.4 prompt guidance:
- Include a <completeness_contract>: "Treat the task as incomplete until all required items are covered. Maintain an internal checklist."
- Include <tool_persistence_rules>: "Continue tool calls until they materially improve accuracy or completeness. Do not skip prerequisites."
- Include <verification_loop>: "Before finishing, verify all requirements are met and all claims are backed by tool output."
- Keep user updates minimal: "Result in 1 sentence + next step in 1 sentence. Do not explain routine tool calls."
- Encourage parallel independent tool calls where possible.
- Focus on a STATIC ANALYSIS FIRST approach.

Include in the prompt:
- Specific vulnerability identified and where
- Recommended approach/strategy
- Key addresses/offsets from recon
- Which tools to use first

## SOLVER_PROMPT_B
Write a detailed starting prompt for Solver B (GPT-5.2).
This prompt MUST follow GPT-5.2 prompt guidance:
- Leverage 5.2's conservative grounding bias: emphasize explicit reasoning and evidence-based claims.
- Use structured output specs: overview paragraph + ≤5 tagged bullets (changes, location, risk, next_step, open).
- Keep verbosity low: 3-6 sentences or ≤5 bullets per update.
- Include uncertainty handling: "If ambiguous, state assumptions explicitly before proceeding."
- Focus on a DYNAMIC ANALYSIS FIRST approach (GDB, runtime testing, fuzzing).

Include same recon facts but frame for dynamic-first exploration.

Make the two prompts attack the problem from DIFFERENT angles.
Both prompts must include:
- "Write all discoveries to findings_raw.md as you go."
- "If output exceeds 100 lines, save to file and note key findings only."
- "Every address/offset MUST come from actual tool output, not from memory."
- "DO NOT describe what you will do. EXECUTE immediately."
- "NEVER brute-force passwords, logins, or tokens. NEVER send mass requests or flood the server. Find the vulnerability through code analysis and exploit it surgically."
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
) -> tuple[str, str, str]:
    """Run recon via LLM and return (findings, prompt_a, prompt_b).

    Returns fallback prompts if recon fails or times out.
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

            category_knowledge = _load_category_prompt(category) if category else ""
            if category_knowledge:
                prompt = f"## Category Knowledge\n{category_knowledge}\n\n{prompt}"

            await client.start_turn(thread_id, [{"type": "text", "text": prompt}], effort="medium")
            await turn_done.wait()

            full_response = "".join(response_buf)
            if full_response:
                findings, prompt_a, prompt_b = _parse_recon_output(full_response)
                if prompt_a and prompt_b:
                    logger.info("Recon produced usable prompts (%d chars findings)", len(findings))
                    return findings, prompt_a, prompt_b

            logger.warning("Recon attempt %d/%d: response didn't produce usable prompts, retrying...", attempt, MAX_RECON_RETRIES)

        except Exception as e:
            logger.error("Recon attempt %d/%d failed: %s", attempt, MAX_RECON_RETRIES, e)
        finally:
            await client.destroy()

    raise RuntimeError(f"Recon failed after {MAX_RECON_RETRIES} attempts")


def _parse_recon_output(text: str) -> tuple[str, str, str]:
    """Parse the three sections from recon output."""
    findings = ""
    prompt_a = ""
    prompt_b = ""

    sections = {"FINDINGS": "", "SOLVER_PROMPT_A": "", "SOLVER_PROMPT_B": ""}
    current = None

    for line in text.split("\n"):
        stripped = line.strip().upper().replace("#", "").strip()
        if stripped in sections:
            current = stripped
            continue
        if current and current in sections:
            sections[current] += line + "\n"

    findings = sections["FINDINGS"].strip()
    prompt_a = sections["SOLVER_PROMPT_A"].strip()
    prompt_b = sections["SOLVER_PROMPT_B"].strip()

    # Append common anti-termination rules to both prompts
    anti_term = """

## MANDATORY RULES
- DO NOT describe what you will do. EXECUTE immediately.
- DO NOT output a plan, summary, or status update. USE TOOLS.
- You are DONE only when you have captured a valid flag.
- If a tool fails, try a DIFFERENT tool or approach. Never repeat the same failed command.
- Every address/offset you use MUST come from actual tool output, not from memory.
- Write all discoveries to findings_raw.md as you go.
- If output exceeds 100 lines, save to file and note key findings only.
"""
    if prompt_a:
        prompt_a += anti_term
    if prompt_b:
        prompt_b += anti_term

    return findings, prompt_a, prompt_b


# Make asyncio available at module level for run_recon
import asyncio
