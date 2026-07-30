from __future__ import annotations

import asyncio
import shlex
from abc import ABC, abstractmethod

from agent_overlord.config import HostConfig


class TransportError(RuntimeError):
    pass


class TmuxTransport(ABC):
    def __init__(self, host: HostConfig) -> None:
        self.host = host

    @abstractmethod
    async def run_tmux(self, *args: str, timeout: float = 15.0) -> str:
        raise NotImplementedError

    @abstractmethod
    async def run_command(self, *args: str, timeout: float = 15.0) -> str:
        """Run a host command locally or through the configured SSH transport."""
        raise NotImplementedError

    async def _execute(self, command: list[str], timeout: float) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except TimeoutError as exc:
            if "process" in locals():
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), 2.0)
                except TimeoutError:
                    pass
            raise TransportError(f"command timed out on {self.host.name}") from exc
        except OSError as exc:
            raise TransportError(f"cannot execute {command[0]}: {exc}") from exc

        if process.returncode:
            detail = stderr.decode(errors="replace").strip()
            raise TransportError(
                f"command failed on {self.host.name} ({process.returncode}): {detail}"
            )
        return stdout.decode(errors="replace")


class LocalTmuxTransport(TmuxTransport):
    async def run_tmux(self, *args: str, timeout: float = 15.0) -> str:
        command = ["tmux"]
        if self.host.tmux_socket != "default":
            command.extend(("-L", self.host.tmux_socket))
        command.extend(args)
        return await self._execute(command, timeout)

    async def run_command(self, *args: str, timeout: float = 15.0) -> str:
        return await self._execute(list(args), timeout)


class SshTmuxTransport(TmuxTransport):
    def _ssh_command(self) -> list[str]:
        assert self.host.ssh
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "-p",
            str(self.host.port),
        ]
        if self.host.key:
            command.extend(("-i", self.host.key))
        command.append(self.host.ssh)
        return command

    async def run_tmux(self, *args: str, timeout: float = 15.0) -> str:
        command = self._ssh_command()
        remote = ["tmux"]
        if self.host.tmux_socket != "default":
            remote.extend(("-L", self.host.tmux_socket))
        remote.extend(args)
        command.append(shlex.join(remote))
        return await self._execute(command, timeout)

    async def run_command(self, *args: str, timeout: float = 15.0) -> str:
        command = self._ssh_command()
        command.append(shlex.join(args))
        return await self._execute(command, timeout)


def transport_for(host: HostConfig) -> TmuxTransport:
    if host.local:
        return LocalTmuxTransport(host)
    return SshTmuxTransport(host)
