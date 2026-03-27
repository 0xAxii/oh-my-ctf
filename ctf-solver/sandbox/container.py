"""Docker container lifecycle manager for CTF solver.

Each challenge runs in an isolated container built from a category-specific
Dockerfile (e.g. Dockerfile.pwn → ctf-pwn:latest).  A writable /workspace is
created at container start; the challenge directory is bind-mounted read-only
at /challenge.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Maps category name → Docker image tag.
# Images are expected to have been built before the solver runs.
CATEGORY_IMAGES: dict[str, str] = {
    "pwn":       "ctf-pwn:latest",
    "rev":       "ctf-rev:latest",
    "crypto":    "ctf-crypto:latest",
    "web":       "ctf-web:latest",
    "forensics": "ctf-forensics:latest",
    "web3":      "ctf-web3:latest",
    "misc":      "ctf-misc:latest",
    "ai":        "ctf-ai:latest",
}

_FALLBACK_IMAGE = "ctf-base:latest"


def _image_for(category: str) -> str:
    return CATEGORY_IMAGES.get(category.lower(), _FALLBACK_IMAGE)


def _sanitize_model(model_name: str) -> str:
    """Strip characters that are invalid in Docker container names."""
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in model_name)


async def _run_docker(*args: str) -> tuple[int, str, str]:
    """Run a docker sub-command, returning (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "docker", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    return proc.returncode, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")


class ContainerManager:
    """Manages Docker container lifecycles for CTF challenge solvers.

    Usage::

        cm = ContainerManager()
        cid = await cm.create("pwn", "/path/to/challenge", "gpt-4o")
        output = await cm.exec(cid, "ls /challenge")
        await cm.destroy(cid)
        # or at shutdown:
        await cm.destroy_all()
    """

    def __init__(self, *, network_enabled: bool = True) -> None:
        self._network_enabled = network_enabled
        # container_id → container_name  (docker assigns the full id; we store name too)
        self._active: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create(
        self,
        category: str,
        challenge_dir: str | Path,
        model_name: str,
    ) -> str:
        """Create and start a new solver container.

        Parameters
        ----------
        category:
            Challenge category (pwn, rev, crypto, …).
        challenge_dir:
            Host path that will be bind-mounted read-only at /challenge.
        model_name:
            Model identifier; used in the container name for traceability.

        Returns
        -------
        str
            The full Docker container ID.
        """
        image = _image_for(category)
        short_uuid = uuid.uuid4().hex[:8]
        container_name = f"ctf-solver-{_sanitize_model(model_name)}-{short_uuid}"
        challenge_path = str(Path(challenge_dir).resolve())

        cmd: list[str] = [
            "run",
            "--detach",
            "--name", container_name,
            # Read-only challenge mount
            "--mount", f"type=bind,source={challenge_path},target=/challenge,readonly",
            # Writable workspace (anonymous volume)
            "--mount", "type=volume,destination=/workspace",
            # Resource limits (sensible defaults for CTF work)
            "--memory", "2g",
            "--cpus", "2",
        ]

        if not self._network_enabled:
            cmd += ["--network", "none"]

        cmd.append(image)

        logger.info("Creating container %s from image %s", container_name, image)
        returncode, stdout, stderr = await _run_docker(*cmd)

        if returncode != 0:
            raise RuntimeError(
                f"docker run failed for {container_name!r}: {stderr.strip()}"
            )

        container_id = stdout.strip()
        self._active[container_id] = container_name
        logger.info("Container started: %s (%s)", container_name, container_id[:12])
        return container_id

    async def destroy(self, container_id: str) -> None:
        """Stop and remove a container.

        Silently ignores containers that are no longer tracked or already gone.
        """
        name = self._active.pop(container_id, container_id[:12])
        logger.info("Destroying container %s", name)

        returncode, _, stderr = await _run_docker("rm", "--force", container_id)
        if returncode != 0:
            logger.warning("docker rm failed for %s: %s", name, stderr.strip())

    async def destroy_all(self) -> None:
        """Destroy all tracked containers (graceful shutdown)."""
        if not self._active:
            return
        ids = list(self._active.keys())
        logger.info("Destroying %d container(s)…", len(ids))
        await asyncio.gather(*(self.destroy(cid) for cid in ids), return_exceptions=True)

    async def exec(
        self,
        container_id: str,
        command: str,
        timeout: float = 60,
    ) -> str:
        """Execute a shell command inside a running container.

        Parameters
        ----------
        container_id:
            Full container ID returned by :meth:`create`.
        command:
            Shell command string; executed via ``sh -c``.
        timeout:
            Maximum seconds to wait for the command to complete.

        Returns
        -------
        str
            Combined stdout of the command.

        Raises
        ------
        TimeoutError
            If the command exceeds *timeout* seconds.
        RuntimeError
            If docker exec returns a non-zero exit code.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container_id, "sh", "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            # Best-effort kill so we don't leave a hanging process
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise TimeoutError(
                f"Command timed out after {timeout}s in container {container_id[:12]!r}"
            )

        stdout = stdout_b.decode(errors="replace")
        if proc.returncode != 0:
            stderr = stderr_b.decode(errors="replace")
            raise RuntimeError(
                f"Command exited with code {proc.returncode} "
                f"in {container_id[:12]!r}: {stderr.strip()}"
            )

        return stdout

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    @property
    def active_containers(self) -> dict[str, str]:
        """Return a shallow copy of {container_id: container_name}."""
        return dict(self._active)
