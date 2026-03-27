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

        self._channel: discord.TextChannel | None = None
        self._message_queue: asyncio.Queue[str] = asyncio.Queue()
        self._ready = asyncio.Event()
        self._bot_task: asyncio.Task | None = None

        self._setup_handlers()

    def _setup_handlers(self) -> None:
        @self.client.event
        async def on_ready():
            logger.info("Discord bot connected as %s", self.client.user)
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

            # Handle file attachments — zip → auto-extract to challenges/
            for attachment in message.attachments:
                dl_path = f"/tmp/discord_{attachment.filename}"
                await attachment.save(dl_path)
                logger.info("Downloaded attachment: %s", attachment.filename)

                if attachment.filename.endswith(".zip"):
                    challenge_dir = self._extract_challenge(dl_path, attachment.filename)
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

    def _extract_challenge(self, zip_path: str, filename: str) -> str:
        """Extract zip to challenges/{name}/ and return the path."""
        project_root = Path(__file__).parent.parent.parent
        name = Path(filename).stem  # strip .zip
        challenge_dir = project_root / "challenges" / name
        challenge_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(challenge_dir)

        # If zip contains a single top-level directory, flatten it
        entries = list(challenge_dir.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            single_dir = entries[0]
            for item in single_dir.iterdir():
                item.rename(challenge_dir / item.name)
            single_dir.rmdir()

        file_count = sum(1 for _ in challenge_dir.rglob("*") if _.is_file())
        logger.info("Extracted %s → %s (%d files)", filename, challenge_dir, file_count)
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
