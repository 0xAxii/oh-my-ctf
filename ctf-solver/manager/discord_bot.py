"""Discord IO — replaces terminal_io.py for Manager ↔ user communication.

Bot listens on a designated channel. User messages go to Manager,
Manager responses come back to Discord.

Usage:
    from manager.discord_bot import DiscordIO
    dio = DiscordIO(token=os.environ["DISCORD_BOT_TOKEN"])
    await dio.start()
    # dio.read_input() / dio.write_output() same interface as terminal_io
"""

from __future__ import annotations

import asyncio
import logging
import os
import zipfile
from pathlib import Path

import discord
from discord import app_commands

logger = logging.getLogger(__name__)


class DiscordIO:
    """Discord-based IO adapter for Manager.

    Provides the same read_input/write_output interface as terminal_io,
    but routes through a Discord text channel.
    """

    def __init__(self, token: str = "", channel_id: int = 0) -> None:
        self.token = token or os.environ.get("DISCORD_BOT_TOKEN", "")
        self.channel_id = channel_id  # 0 = auto-detect first message channel

        intents = discord.Intents.default()
        intents.message_content = True
        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)

        self._channel: discord.TextChannel | None = None
        self._message_queue: asyncio.Queue[str] = asyncio.Queue()
        self._ready = asyncio.Event()
        self._bot_task: asyncio.Task | None = None
        self._pending_challenge: dict | None = None  # waiting for zip upload
        self._challenge_list: list[str] = []       # /solve listing for number selection

        self._setup_handlers()
        self._setup_commands()

    def _setup_commands(self) -> None:
        @self.tree.command(name="challenge", description="챌린지 등록 (파일은 메시지로 첨부)")
        @app_commands.describe(
            name="문제 이름",
            description="문제 설명 (원격 서버 주소 포함 가능)",
            category="카테고리 (pwn/rev/crypto/web/forensics/web3/misc/ai)",
        )
        async def challenge_cmd(
            interaction: discord.Interaction,
            name: str,
            description: str,
            category: str = "",
        ):
            self._channel = interaction.channel
            self.channel_id = interaction.channel_id

            self._pending_challenge = {
                "name": name,
                "category": category,
                "description": description,
            }

            await interaction.response.send_message(
                f"챌린지 `{name}` 등록." + (f" ({category})" if category else "") + " zip 파일을 올려주세요."
            )

        @self.tree.command(name="solve", description="챌린지 풀이 시작 — 목록에서 번호로 선택")
        async def solve_cmd(interaction: discord.Interaction):
            self._channel = interaction.channel
            self.channel_id = interaction.channel_id

            # List available challenges
            project_root = Path(__file__).parent.parent.parent
            challenges_dir = project_root / "challenges"
            if not challenges_dir.exists():
                await interaction.response.send_message("challenges/ 디렉토리가 없습니다.")
                return

            dirs = sorted([d for d in challenges_dir.iterdir() if d.is_dir()], key=lambda d: d.name)
            if not dirs:
                await interaction.response.send_message("등록된 챌린지가 없습니다. /challenge로 먼저 등록하세요.")
                return

            # Read category from description.md for display
            entries = []
            for d in dirs:
                cat = ""
                desc_file = d / "description.md"
                if desc_file.exists():
                    for line in desc_file.read_text(encoding="utf-8").split("\n"):
                        if line.startswith("Category:"):
                            cat = line.split(":", 1)[1].strip()
                            break
                label = f"{cat}_{d.name}" if cat else d.name
                entries.append((d.name, label))

            self._challenge_list = [e[0] for e in entries]
            listing = "\n".join(f"`{i+1}` - {label}" for i, (_, label) in enumerate(entries))
            self._challenge_list = dirs
            await interaction.response.send_message(
                f"**챌린지 목록:**\n{listing}\n\n"
                f"번호를 입력하세요. 원격 서버가 있으면 쉼표 뒤에 붙여주세요.\n"
                f"예: `1,http://host.dreamhack.games:12345/`"
            )

        @self.tree.command(name="status", description="현재 풀이 상태 확인")
        async def status_cmd(interaction: discord.Interaction):
            await self._message_queue.put("상태")
            await interaction.response.send_message("상태 확인 중...")

        @self.tree.command(name="clear", description="채널 대화내역 삭제 (채널 복제 방식)")
        async def clear_cmd(interaction: discord.Interaction):
            await interaction.response.send_message("채널 초기화 중...")
            old_channel = interaction.channel
            new_channel = await old_channel.clone(reason="CTF Manager /clear")
            await old_channel.delete(reason="CTF Manager /clear")
            self._channel = new_channel
            self.channel_id = new_channel.id
            await new_channel.send("채널 초기화 완료.")

        @self.tree.command(name="reset", description="챌린지 디렉토리 초기화 + 매니저 새 세션")
        async def reset_cmd(interaction: discord.Interaction):
            self._channel = interaction.channel
            self.channel_id = interaction.channel_id

            # Clean challenges directory
            project_root = Path(__file__).parent.parent.parent
            challenges_dir = project_root / "challenges"
            import shutil
            removed = []
            if challenges_dir.exists():
                for d in challenges_dir.iterdir():
                    if d.is_dir():
                        removed.append(d.name)
                        shutil.rmtree(d)

            # Signal Manager reset
            await self._message_queue.put("[리셋]")

            msg = f"초기화 완료."
            if removed:
                msg += f" 삭제된 챌린지: {', '.join(removed)}"
            msg += " 매니저 새 세션 시작."
            await interaction.response.send_message(msg)

    def _setup_handlers(self) -> None:
        @self.client.event
        async def on_ready():
            logger.info("Discord bot connected as %s", self.client.user)
            # Sync to each guild for instant availability
            for guild in self.client.guilds:
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                logger.info("Slash commands synced to %s", guild.name)
            if self.channel_id:
                self._channel = self.client.get_channel(self.channel_id)
            self._ready.set()

        @self.client.event
        async def on_message(message: discord.Message):
            # Ignore own messages
            if message.author == self.client.user:
                return

            # Auto-detect channel from first message
            if self._channel is None:
                self._channel = message.channel
                self.channel_id = message.channel.id
                logger.info("Auto-detected channel: #%s (%d)", message.channel.name, self.channel_id)

            # Only listen to the designated channel
            if message.channel.id != self.channel_id:
                return

            text = message.content.strip()
            parts = []

            # Handle /solve number selection (e.g. "1,http://host:port/")
            if self._challenge_list and text and text[0].isdigit():
                split = text.split(",", 1)
                try:
                    idx = int(split[0].strip()) - 1
                    if 0 <= idx < len(self._challenge_list):
                        chosen = self._challenge_list[idx]
                        extra = split[1].strip() if len(split) > 1 else ""
                        project_root = Path(__file__).parent.parent.parent
                        challenge_dir = str(project_root / "challenges" / chosen)

                        # Append extra info (remote etc) to description.md
                        if extra:
                            desc_path = Path(challenge_dir) / "description.md"
                            if desc_path.exists():
                                with open(desc_path, "a", encoding="utf-8") as f:
                                    f.write(f"\n\n## Remote\n{extra}\n")
                            else:
                                desc_path.write_text(f"# {chosen}\n\n## Remote\n{extra}\n", encoding="utf-8")

                        msg = f"풀이 시작\n문제: {chosen}\n경로: {challenge_dir}"
                        self._challenge_list = []
                        await self._message_queue.put(msg)
                        await message.channel.send(f"`{chosen}` 풀이 시작." + (f"\n{extra}" if extra else ""))
                        return
                except ValueError:
                    pass

            # Handle file attachments — zip → auto-extract to challenges/
            for attachment in message.attachments:
                dl_path = f"/tmp/discord_{attachment.filename}"
                await attachment.save(dl_path)
                logger.info("Downloaded attachment: %s", attachment.filename)

                if attachment.filename.endswith(".zip"):
                    # Use pending challenge name if available
                    override_name = ""
                    if self._pending_challenge:
                        override_name = self._pending_challenge["name"]

                    challenge_dir = self._extract_challenge(
                        dl_path, attachment.filename, override_name=override_name,
                    )

                    if self._pending_challenge:
                        pc = self._pending_challenge
                        # Save description to challenge directory
                        desc_path = Path(challenge_dir) / "description.md"
                        desc_parts = [f"# {pc['name']}"]
                        if pc.get("category"):
                            desc_parts.append(f"\nCategory: {pc['category']}")
                        if pc.get("description"):
                            desc_parts.append(f"\n## Description\n{pc['description']}")
                        desc_path.write_text("\n".join(desc_parts) + "\n", encoding="utf-8")
                        parts.append(
                            f"[챌린지] {challenge_dir}\n"
                            f"[등록] {pc['name']}\n"
                            f"카테고리: {pc['category']}\n"
                            f"설명: {pc.get('description', '')}"
                        )
                        self._pending_challenge = None
                    else:
                        parts.append(f"[챌린지] {challenge_dir} ({attachment.filename})")
                else:
                    parts.append(f"[파일] {dl_path} ({attachment.filename})")

            # Combine text + file info into single message
            if text:
                parts.insert(0, text)

            if parts:
                combined = "\n".join(parts)
                logger.info("Discord message from %s: %s", message.author, combined[:200])
                await self._message_queue.put(combined)

    def _extract_challenge(self, zip_path: str, filename: str, override_name: str = "") -> str:
        """Extract zip to challenges/{name}/files/ and return the challenge dir path."""
        project_root = Path(__file__).parent.parent.parent
        name = override_name or Path(filename).stem  # strip .zip
        challenge_dir = project_root / "challenges" / name
        files_dir = challenge_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(files_dir)

        # If zip contains a single top-level directory, flatten it
        entries = list(files_dir.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            single_dir = entries[0]
            for item in single_dir.iterdir():
                item.rename(files_dir / item.name)
            single_dir.rmdir()

        file_count = sum(1 for _ in files_dir.rglob("*") if _.is_file())
        logger.info("Extracted %s → %s (%d files)", filename, files_dir, file_count)
        return str(challenge_dir)

    async def start(self) -> None:
        """Start the Discord bot in the background."""
        if not self.token:
            raise RuntimeError("DISCORD_BOT_TOKEN not set")

        self._bot_task = asyncio.create_task(
            self.client.start(self.token), name="discord-bot"
        )
        await self._ready.wait()
        logger.info("Discord IO ready")

    async def stop(self) -> None:
        """Shut down the bot."""
        if self.client and not self.client.is_closed():
            await self.client.close()
        if self._bot_task:
            self._bot_task.cancel()
            try:
                await self._bot_task
            except asyncio.CancelledError:
                pass

    async def read_input(self, prompt: str = "") -> str:
        """Wait for next user message from Discord."""
        return await self._message_queue.get()

    async def write_output(self, text: str) -> None:
        """Send message to Discord channel."""
        if not self._channel:
            logger.warning("No Discord channel set, dropping message: %s", text[:100])
            return

        # Discord has 2000 char limit per message
        for i in range(0, len(text), 1900):
            chunk = text[i:i + 1900]
            await self._channel.send(chunk)
