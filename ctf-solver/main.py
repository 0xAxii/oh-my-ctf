"""CTF Auto-Solver — terminal-based conversational interface.

Usage:
    python main.py
    python main.py --challenge ./path/to/challenge --category pwn
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Load .env from project root
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from manager.manager import Manager
from manager.terminal_io import read_input as terminal_read, write_output as terminal_write
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
    recon_facts = await run_recon(challenge_dir, category)
    if recon_facts:
        logger.info("Recon facts (first 300 chars): %s", recon_facts[:300])
    swarm = ChallengeSwarm(
        challenge_dir=challenge_dir,
        recon_facts=recon_facts,
        category=category,
        flag_format=flag_format or r"(flag|FLAG|DH|CTF|GoN)\{[^}]+\}",
    )
    return swarm


async def run_interactive(
    challenge_dir: str = "",
    category: str = "",
    flag_format: str = "",
    read_input=terminal_read,
    write_output=terminal_write,
) -> None:
    """Main interactive loop — Manager talks to user, spawns and monitors swarm."""
    manager = Manager()
    remote = ""
    # Multi-challenge support: {challenge_name: {swarm, task, flag_queue}}
    active_swarms: dict[str, dict] = {}

    await write_output("CTF Auto-Solver v0.1")
    await write_output("Manager 초기화 중...")

    try:
        await manager.init()
        await write_output("준비 완료. 챌린지를 알려주세요.\n")

        while True:
            # --- Check all active swarms (non-blocking) ---
            finished = []
            for name, info in active_swarms.items():
                swarm = info["swarm"]
                task = info["task"]
                fq = info["flag_queue"]

                # Check if LightCritic found a verified flag
                if fq is not None:
                    try:
                        flag = fq.get_nowait()
                        msg = await manager.report_event("FLAG_FOUND", f"{name}: {flag}")
                        await write_output(f"manager> {msg}\n")
                        swarm.kill()
                        finished.append(name)
                        continue
                    except asyncio.QueueEmpty:
                        pass

                # Check tool installation requests
                try:
                    tool_req = swarm.tool_request_queue.get_nowait()
                    await write_output(
                        f"manager> [{name}] Solver [{tool_req['model']}] 도구 설치 요청: {tool_req['request']}\n"
                        f"manager> 설치 후 '설치완료' 입력해주세요.\n"
                    )
                except asyncio.QueueEmpty:
                    pass

                # Check if swarm task finished
                if task is not None and task.done():
                    try:
                        result = task.result()
                    except Exception as e:
                        logger.error("[%s] Swarm task raised: %s", name, e, exc_info=True)
                        result = None

                    if result and result.flag:
                        msg = await manager.report_event("FLAG_FOUND", f"{name}: {result.flag}")
                        await write_output(f"manager> {msg}\n")
                    else:
                        summary = result.findings_summary[:200] if result else ""
                        msg = await manager.report_event("SWARM_DONE_NO_FLAG", f"{name}: {summary}")
                        await write_output(f"manager> {msg}\n")
                    finished.append(name)

            for name in finished:
                active_swarms.pop(name, None)

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

            # Handle /reset — new Manager session
            if user_input == "[리셋]":
                for info in active_swarms.values():
                    info["swarm"].kill()
                active_swarms.clear()
                await manager.destroy()
                manager = Manager()
                await manager.init()
                challenge_dir = ""
                category = ""
                flag_format = ""
                remote = ""
                await write_output("manager> 새 세션 시작. 챌린지를 알려주세요.\n")
                continue

            # Auto-set challenge_dir from Discord zip upload
            if "[챌린지]" in user_input:
                for line in user_input.split("\n"):
                    if "[챌린지]" in line:
                        challenge_dir = line.split("]", 1)[1].strip().split(" ")[0]
                        break
                await write_output(f"manager> 챌린지 파일 수신: {challenge_dir}\n")
                user_input = user_input.replace(line, "").strip()
                user_input += f"\n\n챌린지 파일이 {challenge_dir}에 준비되었습니다."

            # Parse /challenge slash command registration
            if "[등록]" in user_input:
                for line in user_input.split("\n"):
                    if "카테고리:" in line:
                        category = line.split(":", 1)[1].strip()
                    if "리모트:" in line:
                        remote = line.split(":", 1)[1].strip()

            # Parse /solve command — set challenge_dir, let Manager decide
            if user_input.startswith("풀이 시작"):
                for line in user_input.split("\n"):
                    if "경로:" in line:
                        challenge_dir = line.split(":", 1)[1].strip()
                    elif "문제:" in line:
                        solve_name = line.split(":", 1)[1].strip()
                        solve_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "challenges", solve_name)
                        if os.path.isdir(solve_dir):
                            challenge_dir = solve_dir

                # Read description.md and pass to Manager
                desc = ""
                desc_path = os.path.join(challenge_dir, "description.md") if challenge_dir else ""
                if desc_path and os.path.exists(desc_path):
                    desc = open(desc_path).read()
                    for dl in desc.split("\n"):
                        if dl.startswith("Category:"):
                            category = dl.split(":", 1)[1].strip()
                            break

                # Pass to Manager for decision
                user_input = f"풀이 시작 요청: {os.path.basename(challenge_dir)}\n경로: {challenge_dir}\n\n{desc}"

            # Build context string for Manager
            context = ""
            if active_swarms:
                parts = []
                for sname, sinfo in active_swarms.items():
                    st = sinfo["swarm"].get_status()
                    solvers_info = ", ".join(
                        f"{m}: {d['findings'][:80]}" for m, d in st["solvers"].items() if d["findings"]
                    )
                    parts.append(f"[{sname}] winner={st['winner']}" + (f" ({solvers_info})" if solvers_info else ""))
                context = f"활성 풀이 {len(active_swarms)}개: " + "; ".join(parts)

            response = await manager.handle_message(user_input, context=context)
            await write_output(f"manager> {response}\n")

            # --- Interpret Manager response for action hooks ---

            # 1) Spawn swarm if Manager signals start
            if any(kw in response for kw in _SPAWN_KEYWORDS) and challenge_dir:
                chal_name = os.path.basename(challenge_dir)
                MAX_CONCURRENT = 5
                if chal_name in active_swarms:
                    pass  # already running
                elif len(active_swarms) >= MAX_CONCURRENT:
                    await write_output(f"manager> 동시 풀이 {MAX_CONCURRENT}개 제한 초과. 기존 풀이가 끝나길 기다려주세요.\n")
                else:
                    # Hardware check for 2nd+ concurrent swarm
                    if len(active_swarms) >= 1:
                        import shutil, psutil
                        cpu_pct = psutil.cpu_percent(interval=1)
                        mem = psutil.virtual_memory()
                        hw_msg = f"현재 CPU {cpu_pct}%, 메모리 {mem.percent}% ({mem.available // (1024**3)}GB 여유). 동시 풀이 {len(active_swarms)+1}개 진행할까요?"
                        hw_response = await manager.handle_message(
                            f"[시스템] {hw_msg}\n사용자에게 하드웨어 상태를 알려주고, 진행 여부를 확인해주세요."
                        )
                        await write_output(f"manager> {hw_response}\n")
                        # Wait for user confirmation
                        try:
                            confirm = await asyncio.wait_for(read_input(""), timeout=30.0)
                        except asyncio.TimeoutError:
                            await write_output("manager> 시간 초과. 풀이 취소.\n")
                            continue
                        if confirm.strip() not in ("ㅇㅇ", "ㅇ", "응", "yes", "y", "확인", "진행"):
                            await write_output("manager> 풀이 취소.\n")
                            continue

                    # Ask Manager for flag format
                    ff_response = await manager.handle_message(
                        "flag format이 뭐였지? 접두사만 한 단어로 답해. 예: DH, flag, CTF. 모르면 unknown"
                    )
                    ff_word = ff_response.strip().split()[0].rstrip("{").rstrip(".")
                    if ff_word and ff_word.lower() != "unknown":
                        flag_format = ff_word + r"\{[^}]+\}"
                        logger.info("Flag format from Manager: %s", flag_format)

                    chdir = os.path.join(challenge_dir, "files") if os.path.isdir(os.path.join(challenge_dir, "files")) else challenge_dir
                    await write_output(f"manager> {chal_name} Recon 시작 중...\n")
                    try:
                        swarm = await _spawn_swarm(chdir, category, flag_format)
                        fq = asyncio.Queue()
                        swarm.light_critic = LightCritic(challenge_dir=chdir, on_flag_found=fq)
                        task = asyncio.create_task(swarm.run(), name=f"swarm-{chal_name}")
                        active_swarms[chal_name] = {"swarm": swarm, "task": task, "flag_queue": fq}
                        await write_output(f"manager> {chal_name} 풀이 시작 (category={category or 'auto'})\n")
                    except Exception as e:
                        logger.error("Failed to spawn swarm for %s: %s", chal_name, e, exc_info=True)
                        await write_output(f"manager> {chal_name} 시작 실패: {e}\n")

            # 2) Relay user hint to all active solvers via MessageBus
            if _HINT_PREFIX in response and active_swarms:
                hint_start = response.find(_HINT_PREFIX) + len(_HINT_PREFIX)
                hint_text = response[hint_start:].strip()
                for sinfo in active_swarms.values():
                    await sinfo["swarm"].message_bus.broadcast(hint_text, source="manager")
                logger.info("Hint broadcast to %d swarms: %s", len(active_swarms), hint_text[:100])

            # 3) User confirms tool installation → resume all waiting solvers
            if user_input in ("설치완료", "설치 완료", "installed", "done") and active_swarms:
                for sinfo in active_swarms.values():
                    sinfo["swarm"].tool_installed_event.set()
                await write_output("manager> 도구 설치 확인. Solver 재개 중...\n")

    except KeyboardInterrupt:
        await write_output("\n중단됨.")
    finally:
        for sinfo in active_swarms.values():
            sinfo["swarm"].kill()
            if sinfo["task"] and not sinfo["task"].done():
                sinfo["task"].cancel()
            await asyncio.gather(swarm_task, return_exceptions=True)
        await manager.destroy()


async def run_direct(challenge_dir: str, category: str = "", flag_format: str = "", remote: str = "", use_docker: bool = True, read_input=terminal_read, write_output=terminal_write) -> None:
    """Direct mode — skip Manager conversation, run recon+swarm immediately."""
    logger.info("Direct mode: %s (category=%s, remote=%s)", challenge_dir, category or "auto", remote or "none")

    await write_output(f"Recon 시작: {challenge_dir}")
    recon_facts = await run_recon(challenge_dir, category)

    if recon_facts:
        await write_output(f"Recon facts:\n{recon_facts[:500]}")

    # Inject remote target info into recon facts
    if remote:
        recon_facts += f"\n\n## REMOTE TARGET\nThe real flag is on the remote server, NOT in local files.\nRemote: {remote}\nLocal flag files are FAKE. You MUST get the flag from the remote server."

    swarm = ChallengeSwarm(
        challenge_dir=challenge_dir,
        recon_facts=recon_facts,
        category=category,
        flag_format=flag_format or r"(flag|FLAG|DH|CTF|GoN)\{[^}]+\}",
        use_docker=use_docker,
    )

    mode = "Docker" if use_docker else "host"
    await write_output(f"Solver swarm 시작 (5.4 + 5.2 병렬, {mode})...")

    # Run swarm with tool-request monitoring for direct mode
    swarm_task = asyncio.create_task(swarm.run(), name="swarm-direct")

    while not swarm_task.done():
        # Check for tool installation requests
        try:
            tool_req = swarm.tool_request_queue.get_nowait()
            await write_output(
                f"\n[!] Solver [{tool_req['model']}] 도구 설치 요청: {tool_req['request']}"
            )
            raw = await read_input("설치 후 Enter (또는 '설치완료'): ")
            swarm.tool_installed_event.set()
            await write_output("도구 설치 확인. Solver 재개 중...\n")
        except asyncio.QueueEmpty:
            pass

        # Brief sleep to avoid busy-wait
        try:
            await asyncio.wait_for(asyncio.shield(swarm_task), timeout=2.0)
            break
        except asyncio.TimeoutError:
            continue

    try:
        result = swarm_task.result()
    except Exception as e:
        logger.error("Swarm error: %s", e, exc_info=True)
        result = None

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
    parser.add_argument("--no-docker", action="store_true", help="Run solvers on host instead of Docker")
    parser.add_argument("--discord", action="store_true", help="Use Discord bot instead of terminal")
    parser.add_argument("--channel", type=int, default=0, help="Discord channel ID (0 = auto-detect)")
    args = parser.parse_args()

    async def _run():
        read_input = terminal_read
        write_output = terminal_write
        dio = None

        if args.discord:
            from manager.discord_bot import DiscordIO
            dio = DiscordIO(channel_id=args.channel)
            await dio.start()
            read_input = dio.read_input
            write_output = dio.write_output

        try:
            if args.challenge:
                await run_direct(
                    args.challenge, args.category or "", args.flag_format or "",
                    args.remote or "", use_docker=not args.no_docker,
                    read_input=read_input, write_output=write_output,
                )
            else:
                await run_interactive(
                    read_input=read_input, write_output=write_output,
                )
        finally:
            if dio:
                await dio.stop()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
