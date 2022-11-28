from __future__ import annotations

import asyncio
import subprocess
from asyncio.subprocess import Process
from pathlib import Path

from pre_commit.languages.sbt import is_server_running

_TIMEOUT = 30


async def start_sbt_server(
        root_dir: Path,
        shutdown_timeout: int = _TIMEOUT,
) -> Process:
    process = await asyncio.create_subprocess_shell(
        'sbt',
        cwd=root_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        shell=True,
    )
    await asyncio.wait_for(
        _wait_until_server_started(root_dir),
        timeout=shutdown_timeout,
    )
    return process


async def shutdown_sbt_server(
        proc: Process,
        *,
        timeout: int = _TIMEOUT,
) -> None:
    try:
        await asyncio.wait_for(
            proc.communicate(b'shutdown\n'), timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()


async def _wait_until_server_started(root_dir: Path) -> None:
    while not is_server_running(root_dir):
        await asyncio.sleep(1)
