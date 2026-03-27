"""CTF Auto-Solver — terminal-based conversational interface.

Usage:
    python main.py
    python main.py --challenge ./path/to/challenge --category pwn
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from manager.manager import Manager
from manager.terminal_io import read_input, write_output
from core.recon import run_recon
from core.swarm import ChallengeSwarm
from core.light_critic import LightCritic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
# Flush logs immediately (nohup buffers otherwise)
import sys
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
logger = logging.getLogger("main")

# Patterns Manager uses in responses to signal intent
_SPAWN_KEYWORDS = ("풀이 시작", "swarm 시작", "solve 시작", "시작할게요", "분석 시작", "풀기 시작")
_HINT_PREFIX = "[힌트]"


async def _spawn_swarm(challenge_dir: str, category: str, flag_format: str) -> ChallengeSwarm:
    """Run recon then create and return a ChallengeSwarm (not yet started)."""
    findings, prompt_a, prompt_b = await run_recon(challenge_dir, category)
    if findings:
        logger.info("Recon findings (first 300 chars): %s", findings[:300])
    swarm = ChallengeSwarm(
        challenge_dir=challenge_dir,
        system_prompt_a=prompt_a,
        system_prompt_b=prompt_b,
        flag_format=flag_format or r"(flag|FLAG|DH|CTF|GoN)\{[^}]+\}",
    )
    return swarm


async def run_interactive(
    challenge_dir: str = "",
    category: str = "",
    flag_format: str = "",
) -> None:
    """Main interactive loop — Manager talks to user, spawns and monitors swarm."""
    manager = Manager()
    active_swarm: ChallengeSwarm | None = None
    swarm_task: asyncio.Task | None = None
    # flag_queue is set on the swarm's LightCritic; we keep a reference here
    flag_queue: asyncio.Queue | None = None

    await write_output("CTF Auto-Solver v0.1")
    await write_output("Manager 초기화 중...")

    try:
        await manager.init()
        await write_output("준비 완료. 챌린지를 알려주세요.\n")

        while True:
            # --- Check swarm status (non-blocking) ---
            if active_swarm:
                status = active_swarm.get_status()

                # Check if LightCritic already found a verified flag
                if flag_queue is not None:
                    try:
                        flag = flag_queue.get_nowait()
                        msg = await manager.report_event("FLAG_FOUND", flag)
                        await write_output(f"manager> {msg}\n")
                        active_swarm.kill()
                        active_swarm = None
                        swarm_task = None
                        flag_queue = None
                        continue
                    except asyncio.QueueEmpty:
                        pass

                # Check if swarm task finished
                if swarm_task is not None and swarm_task.done():
                    try:
                        result = swarm_task.result()
                    except Exception as e:
                        logger.error("Swarm task raised: %s", e, exc_info=True)
                        result = None

                    if result and result.flag:
                        msg = await manager.report_event("FLAG_FOUND", result.flag)
                        await write_output(f"manager> {msg}\n")
                    else:
                        summary = result.findings_summary[:200] if result else ""
                        msg = await manager.report_event("SWARM_DONE_NO_FLAG", summary)
                        await write_output(f"manager> {msg}\n")

                    active_swarm = None
                    swarm_task = None
                    flag_queue = None

            # --- Read user input with timeout so we can keep polling swarm ---
            try:
                raw = await asyncio.wait_for(read_input("you> "), timeout=5.0)
            except asyncio.TimeoutError:
                continue  # No input yet — loop back to check swarm

            user_input = raw.strip()
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "종료"):
                break

            # Build context string for Manager
            context = ""
            if active_swarm:
                st = active_swarm.get_status()
                solvers_info = ", ".join(
                    f"{m}: {d['findings'][:80]}" for m, d in st["solvers"].items() if d["findings"]
                )
                context = (
                    f"현재 활성 swarm 실행 중. 챌린지: {st['challenge']}. "
                    f"취소됨: {st['cancelled']}. "
                    f"Winner: {st['winner']}. "
                    + (f"Solver 진행 상황: {solvers_info}" if solvers_info else "")
                )

            response = await manager.handle_message(user_input, context=context)
            await write_output(f"manager> {response}\n")

            # --- Interpret Manager response for action hooks ---

            # 1) Spawn swarm if Manager signals start and no swarm running
            if active_swarm is None and any(kw in response for kw in _SPAWN_KEYWORDS):
                # Use challenge_dir from CLI arg or fall back to current dir
                chdir = challenge_dir or "."
                await write_output("manager> Recon 시작 중...\n")
                try:
                    active_swarm = await _spawn_swarm(chdir, category, flag_format)
                    # Grab the flag queue from LightCritic after swarm.run() sets it up
                    # (run() creates LightCritic internally; we set up our own queue here
                    #  and pass it in so we can monitor it)
                    flag_queue = asyncio.Queue()
                    active_swarm.light_critic = LightCritic(
                        challenge_dir=chdir,
                        on_flag_found=flag_queue,
                    )
                    swarm_task = asyncio.create_task(
                        active_swarm.run(), name="swarm"
                    )
                    start_msg = await manager.report_event(
                        "SWARM_STARTED",
                        f"challenge_dir={chdir}, category={category or 'auto'}",
                    )
                    await write_output(f"manager> {start_msg}\n")
                except Exception as e:
                    logger.error("Failed to spawn swarm: %s", e, exc_info=True)
                    err_msg = await manager.report_event("SWARM_START_ERROR", str(e))
                    await write_output(f"manager> {err_msg}\n")

            # 2) Relay user hint to solvers via MessageBus broadcast
            elif active_swarm is not None and _HINT_PREFIX in response:
                # Manager decided the user sent a hint — broadcast it
                hint_start = response.find(_HINT_PREFIX) + len(_HINT_PREFIX)
                hint_text = response[hint_start:].strip()
                await active_swarm.message_bus.broadcast(hint_text, source="manager")
                logger.info("Hint broadcast to solvers: %s", hint_text[:100])

            # TODO: "도구 부족" handling
            # When solver output contains tool-not-found errors:
            # 1. Manager asks user for install permission
            # 2. User approves → install tool (e.g. subprocess.run(["pip", "install", ...]))
            # 3. Inject a message into the solver's current thread via message_bus to retry
            #    (same session continuation, NOT a bump/new thread)
            # This requires steer-or-same-thread support from AppServerSolver.

    except KeyboardInterrupt:
        await write_output("\n중단됨.")
    finally:
        if active_swarm:
            active_swarm.kill()
        if swarm_task and not swarm_task.done():
            swarm_task.cancel()
            await asyncio.gather(swarm_task, return_exceptions=True)
        await manager.destroy()


async def run_direct(challenge_dir: str, category: str = "", flag_format: str = "", remote: str = "") -> None:
    """Direct mode — skip Manager conversation, run recon+swarm immediately."""
    logger.info("Direct mode: %s (category=%s, remote=%s)", challenge_dir, category or "auto", remote or "none")

    await write_output(f"Recon 시작: {challenge_dir}")
    findings, prompt_a, prompt_b = await run_recon(challenge_dir, category)

    if findings:
        await write_output(f"Recon findings:\n{findings[:500]}")

    # Inject remote target info into solver prompts
    if remote:
        remote_info = f"\n\n## REMOTE TARGET\nThe real flag is on the remote server, NOT in local files.\nRemote: {remote}\nLocal flag files are FAKE. You MUST get the flag from the remote server.\n"
        prompt_a += remote_info
        prompt_b += remote_info

    swarm = ChallengeSwarm(
        challenge_dir=challenge_dir,
        system_prompt_a=prompt_a,
        system_prompt_b=prompt_b,
        flag_format=flag_format or r"(flag|FLAG|DH|CTF|GoN)\{[^}]+\}",
    )

    await write_output("Solver swarm 시작 (5.4 + 5.2 병렬)...")
    result = await swarm.run()

    if result and result.flag:
        await write_output(f"\n FLAG FOUND: {result.flag}")
    else:
        await write_output("\n Flag를 찾지 못했습니다.")
        if result:
            await write_output(f"Status: {result.status}")
            await write_output(f"Findings: {result.findings_summary[:300]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="CTF Auto-Solver")
    parser.add_argument("--challenge", "-c", help="Challenge directory path")
    parser.add_argument("--category", help="Category hint (pwn/rev/crypto)")
    parser.add_argument("--flag-format", help="Flag regex override")
    parser.add_argument("--remote", "-r", help="Remote target (e.g. host:port or http://host:port)")
    args = parser.parse_args()

    if args.challenge:
        asyncio.run(run_direct(args.challenge, args.category or "", args.flag_format or "", args.remote or ""))
    else:
        asyncio.run(run_interactive())


if __name__ == "__main__":
    main()
