from __future__ import annotations

import socket
from asyncio import open_unix_connection
from itertools import product
from pathlib import Path
from socket import SocketType
from typing import Any
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from pre_commit.hook import Hook
from pre_commit.languages import sbt
from pre_commit.languages.sbt import connect_to_sbt_server
from pre_commit.languages.sbt import connection_details
from pre_commit.languages.sbt import create_exec_request
from pre_commit.languages.sbt import is_server_running
from pre_commit.languages.sbt import port_file_path
from pre_commit.languages.sbt import read_until_complete_message
from testing.sbt_test_utils import shutdown_sbt_server
from testing.sbt_test_utils import start_sbt_server
from testing.util import cwd
from testing.util import skipif_cant_run_sbt


@skipif_cant_run_sbt
def test_active_json_is_file(sbt_project_with_server: Path) -> None:
    """The port file of a running sbt server can be found"""

    # act
    port_file = port_file_path(sbt_project_with_server)

    # assert
    assert port_file.exists()


def test_active_json_is_not_file(sbt_project_without_server: Path) -> None:
    """The port file is not present if there is no running sbt server"""

    # act
    port_file = port_file_path(sbt_project_without_server)

    # assert
    assert not port_file.exists()


@skipif_cant_run_sbt
def test_is_server_running_true(sbt_project_with_server: Path) -> None:
    """is_server_running should return true if SBT server is running"""

    # act
    actual = is_server_running(sbt_project_with_server)

    # assert
    assert actual is True


def test_is_server_running_false(sbt_project_without_server: Path) -> None:
    """is_server_running should return false if SBT server is not running"""

    # act
    actual = is_server_running(sbt_project_without_server)

    # assert
    assert actual is False


@skipif_cant_run_sbt
def test_connection_details_port_file_is_readable(
        sbt_project_with_server: Path,
) -> None:
    """connection_details can read a running SBT server's port file"""

    # arrange
    port_file = port_file_path(sbt_project_with_server)

    # act
    socket_path: Path = connection_details(port_file.open('r'))

    # assert
    assert socket_path.exists()


def test_connect_to_sbt_server(tmp_path: Path) -> None:
    """connect_to_sbt_server, should connect to an existing socket"""
    # arrange & act
    sock_file = tmp_path.joinpath('socket.sock')
    with _create_listening_socket(sock_file) as sock_listen,\
            connect_to_sbt_server(sock_file) as sock_under_test:
        expected = 'sample text'
        conn, _ = sock_listen.accept()
        sock_under_test.send(expected.encode('UTF-8'))

        # assert
        actual = conn.recv(len(expected)).decode('UTF-8')
        assert actual == expected


@skipif_cant_run_sbt
@pytest.mark.asyncio
async def test_valid_lsp_request(
        sbt_project_with_touch_command_and_socket: tuple[Path, SocketType],
) -> None:
    """A valid request can be sent to SBT server, and we can determine when
    the SBT command has complete"""
    # arrange
    project_path, sbt_conn = sbt_project_with_touch_command_and_socket
    task_id = 10
    file_to_create = 'sample_file.txt'

    reader, writer = await open_unix_connection(sock=sbt_conn)

    # act
    rpc = create_exec_request(task_id, rf"""touch "{file_to_create}" """)
    writer.write(rpc.encode('UTF-8'))
    _rc, _ = await read_until_complete_message(reader, task_id)

    # assert
    expected_file = project_path.joinpath(file_to_create)
    assert expected_file.exists()
    assert _rc == 0


@skipif_cant_run_sbt
@pytest.mark.parametrize(
    ['args', 'files'],
    product(
        [
            [], ['argfile1.txt'], ['argfile1.txt', 'argfile2.txt'],
            ['\"arg file1.txt\"'], ['\"arg file1.txt\"', '\"arg file2.txt\"'],
        ],
        [
            [], ['filesfile1.txt'], ['filesfile1.txt', 'filesfile2.txt'],
            ['files file1.txt'], ['files file1.txt', 'files file2.txt'],
        ],
    ),
)
def test_sbt_hook(
        sbt_project_with_touch_command: Path,
        args: list[str],
        files: list[str],
) -> None:
    # arrange
    project_root = sbt_project_with_touch_command
    hook = _create_hook(
        language='sbt',
        entry='touch',
        args=args,
    )

    # act
    with cwd(project_root):
        ret, out = sbt.run_hook(hook, files, False)

    # assert
    output = out.decode('UTF-8')
    assert ret == 0
    for file in args + files:
        unquoted_file = _unquote(file)
        expected_file = project_root.joinpath(unquoted_file).absolute()
        assert expected_file.exists()
        assert f'Creating file: {expected_file}' in output


def _unquote(s: str) -> str:
    return s.strip("\"")


def _create_hook(**kwargs: Any) -> Hook:
    default_values = {field: None for field in Hook._fields}
    actual_values = {**default_values, **kwargs}
    return Hook(**actual_values)  # type: ignore


def _create_listening_socket(path: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    sock.listen()
    return sock


@pytest_asyncio.fixture
async def sbt_project_with_server(
        tmp_path: Path,
) -> AsyncGenerator[Path, None]:
    server_process = await start_sbt_server(tmp_path)
    yield tmp_path
    await shutdown_sbt_server(server_process)


@pytest.fixture
def sbt_project_without_server(
        tmp_path: Path,
) -> Path:
    return tmp_path


@pytest_asyncio.fixture
async def sbt_project_with_touch_command_and_socket(
        sbt_project_with_touch_command_no_server: Path,
) -> AsyncGenerator[tuple[Path, SocketType], None]:
    project_root = sbt_project_with_touch_command_no_server
    server_process = await start_sbt_server(project_root)
    with open(port_file_path(project_root), encoding='UTF-8') as port_file,\
            connect_to_sbt_server(connection_details(port_file)) as conn:
        yield project_root, conn
        await shutdown_sbt_server(server_process)
