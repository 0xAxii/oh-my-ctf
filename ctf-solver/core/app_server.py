"""Codex App Server JSON-RPC 2.0 client over stdin/stdout.

Spawns `codex app-server` as asyncio subprocess and provides
typed methods for thread/turn lifecycle, event streaming, and error handling.

Reference: verialabs/ctf-agent codex_solver.py + blitz app-server-client.ts
Protocol: https://developers.openai.com/codex/app-server
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

_rpc_counter = itertools.count(1)


def _next_id() -> int:
    return next(_rpc_counter)


@dataclass
class AppServerEvent:
    """Parsed notification from the app server."""
    method: str
    params: dict[str, Any] = field(default_factory=dict)


ToolExecutor = Callable[[str, dict], Any]  # (tool_name, args) -> result string


# Default dynamic tools for CTF solving — model sees these as available tools
SANDBOX_TOOLS = [
    {
        "name": "bash",
        "description": "Execute a bash command in the sandbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "integer", "default": 60},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from the sandbox.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "write_file",
        "description": "Write a file into the sandbox.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    },
    {
        "name": "list_files",
        "description": "List files in a directory in the sandbox.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "default": "/challenge"}}},
    },
]


class AppServerClient:
    """Async JSON-RPC 2.0 client for Codex App Server.

    Usage:
        client = AppServerClient()
        await client.connect()
        thread_id = await client.start_thread(model="gpt-5.4", ...)
        turn_id = await client.start_turn(thread_id, [{"type": "text", "text": "..."}])
        # Events arrive via on_event callback
        await client.destroy()
    """

    def __init__(self, tool_executor: ToolExecutor | None = None) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[int | str, asyncio.Future] = {}
        self._buffer = ""
        self._reader_task: asyncio.Task | None = None
        self._event_handlers: list[Callable[[AppServerEvent], Any]] = []
        self._initialized = False
        self._spawn_count = 0
        self._max_respawns = 3
        self._tool_executor = tool_executor  # Called when model invokes a dynamic tool

    def on_event(self, handler: Callable[[AppServerEvent], Any]) -> None:
        """Register a callback for server notifications (turn/completed, item/*, etc)."""
        self._event_handlers.append(handler)

    async def connect(self) -> None:
        """Spawn codex app-server and perform initialize handshake."""
        self._process = await asyncio.create_subprocess_exec(
            "codex", "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=10 * 1024 * 1024,  # 10MB buffer — codex sends large JSON lines
        )
        self._spawn_count += 1
        self._reader_task = asyncio.create_task(self._read_loop())

        # Initialize handshake
        result = await self._request("initialize", {
            "clientInfo": {"name": "ctf-solver", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True},
        })
        logger.info("App Server initialized: %s", result.get("userAgent", "unknown"))
        self._initialized = True

        # Send initialized notification
        self._notify("initialized", {})

    async def start_thread(
        self,
        model: str = "gpt-5.4",
        cwd: str = "/challenge",
        dynamic_tools: list[dict] | None = None,
    ) -> str:
        """Create a new thread. Returns threadId."""
        params: dict[str, Any] = {"model": model, "cwd": cwd}
        if dynamic_tools is not None:
            params["dynamicTools"] = dynamic_tools

        result = await self._request("thread/start", params)
        thread = result.get("thread", result)
        thread_id = thread.get("id", "")
        logger.info("Thread started: %s (model=%s)", thread_id, model)
        return thread_id

    async def start_turn(
        self,
        thread_id: str,
        input_items: list[dict[str, str]],
        effort: str | None = None,
        sandbox: str | dict = "externalSandbox",
    ) -> str:
        """Start a turn. Returns turnId."""
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": input_items,
            "approvalPolicy": "never",
        }
        if isinstance(sandbox, dict):
            params["sandboxPolicy"] = sandbox
        elif sandbox == "externalSandbox":
            params["sandboxPolicy"] = {
                "type": "externalSandbox",
                "networkAccess": "enabled",
            }
        else:
            params["sandboxPolicy"] = {"type": sandbox}
        if effort:
            params["effort"] = effort

        result = await self._request("turn/start", params)
        turn_id = result.get("turnId", result.get("turn", {}).get("id", ""))
        logger.debug("Turn started: %s on thread %s", turn_id, thread_id)
        return turn_id

    async def steer(
        self,
        thread_id: str,
        turn_id: str,
        input_items: list[dict[str, str]],
    ) -> None:
        """Inject additional input into a running turn."""
        await self._request("turn/steer", {
            "threadId": thread_id,
            "input": input_items,
            "expectedTurnId": turn_id,
        })

    async def interrupt(self, thread_id: str, turn_id: str) -> None:
        """Cancel a running turn."""
        await self._request("turn/interrupt", {
            "threadId": thread_id,
            "turnId": turn_id,
        })

    async def _handle_tool_call(self, request_id: int | str, params: dict) -> None:
        """Handle item/tool/call — execute via tool_executor and respond."""
        tool_name = params.get("tool", "")
        args_raw = params.get("arguments", {})
        if isinstance(args_raw, str):
            try:
                args_raw = json.loads(args_raw)
            except json.JSONDecodeError:
                args_raw = {"command": args_raw}

        try:
            result = self._tool_executor(tool_name, args_raw)
            if asyncio.iscoroutine(result):
                result = await result
            result_str = str(result) if result else ""
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_name, e)
            result_str = f"Error: {e}"

        response = json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "contentItems": [{"type": "inputText", "text": result_str[:50000]}],
                "success": "Error" not in result_str,
            },
        })
        self._process.stdin.write((response + "\n").encode())  # type: ignore
        await self._process.stdin.drain()  # type: ignore

    async def destroy(self) -> None:
        """Shut down the app server process."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self._process and self._process.returncode is None:
            self._process.stdin.close()  # type: ignore
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()

        # Reject all pending requests
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("App server destroyed"))
        self._pending.clear()
        self._initialized = False
        logger.info("App Server destroyed")

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def ensure_alive(self) -> None:
        """Respawn if the process died. Max 3 respawns."""
        if self.is_alive:
            return
        if self._spawn_count >= self._max_respawns:
            raise RuntimeError(f"App server died {self._spawn_count} times, giving up")
        logger.warning("App server died, respawning (%d/%d)", self._spawn_count, self._max_respawns)
        await self.connect()

    # --- JSON-RPC internals ---

    async def _request(self, method: str, params: dict) -> dict:
        """Send a request and await the response."""
        if not self._process or not self._process.stdin:
            raise ConnectionError("Not connected")

        msg_id = _next_id()
        message = json.dumps({"jsonrpc": "2.0", "method": method, "id": msg_id, "params": params})

        fut: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut

        self._process.stdin.write((message + "\n").encode())
        await self._process.stdin.drain()

        return await fut  # No timeout — wait until response arrives

    def _notify(self, method: str, params: dict) -> None:
        """Send a notification (no response expected)."""
        if not self._process or not self._process.stdin:
            return
        message = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        self._process.stdin.write((message + "\n").encode())

    async def _read_loop(self) -> None:
        """Read stdout line by line and dispatch responses/notifications."""
        assert self._process and self._process.stdout
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break  # Process exited
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    logger.debug("Non-JSON from app-server: %s", text[:200])
                    continue

                if "id" in msg and ("result" in msg or "error" in msg):
                    # Response to a request
                    msg_id = msg["id"]
                    fut = self._pending.pop(msg_id, None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(
                                RuntimeError(f"RPC error: {msg['error']}")
                            )
                        else:
                            fut.set_result(msg.get("result", {}))
                elif "method" in msg and "id" not in msg:
                    # Notification (no id = not a request from server)
                    event = AppServerEvent(
                        method=msg["method"],
                        params=msg.get("params", {}),
                    )
                    for handler in self._event_handlers:
                        try:
                            result = handler(event)
                            if asyncio.iscoroutine(result):
                                asyncio.create_task(result)
                        except Exception:
                            logger.exception("Event handler error for %s", event.method)
                elif "method" in msg and "id" in msg:
                    # Server request
                    request_id = msg["id"]
                    method = msg["method"]
                    logger.debug("Server request: %s (id=%s)", method, request_id)

                    # Dynamic tool call — route to tool executor
                    if method == "item/tool/call" and self._tool_executor:
                        await self._handle_tool_call(request_id, msg.get("params", {}))
                        continue

                    if "requestApproval" in method:
                        # Auto-approve all tool executions
                        # Per docs: result is a string, not an object
                        response = json.dumps({
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": "acceptForSession",
                        })
                    elif "requestUserInput" in method:
                        # Auto-accept user input requests
                        response = json.dumps({
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {},
                        })
                    else:
                        # Unknown server request — respond with empty result to not block
                        logger.info("Auto-accepting server request: %s", method)
                        response = json.dumps({
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {},
                        })
                    self._process.stdin.write((response + "\n").encode())  # type: ignore
                    await self._process.stdin.drain()  # type: ignore

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Read loop error")
        finally:
            logger.info("Read loop exited (process alive: %s)", self.is_alive)
