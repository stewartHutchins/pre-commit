from __future__ import annotations

import asyncio
import json
import socket
from asyncio import StreamReader
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
from pre_commit.languages.sbt import get_next_message
from pre_commit.languages.sbt import is_server_running
from pre_commit.languages.sbt import JsonType
from pre_commit.languages.sbt import port_file_path
from pre_commit.languages.sbt import read_until_complete_message
from pre_commit.languages.sbt import return_code
from pre_commit.languages.sbt import run_via_lsp
from testing.fixtures import make_repo
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
    with _create_listening_socket(sock_file) as sock_listen, \
            connect_to_sbt_server(sock_file) as sock_under_test:
        expected = 'sample text'
        conn, _ = sock_listen.accept()
        sock_under_test.send(expected.encode('UTF-8'))

        # assert
        actual = conn.recv(len(expected)).decode('UTF-8')
        assert actual == expected


@pytest.mark.parametrize(
    ['body', 'expected'],
    [
        [{'result': {'exitCode': 10}}, 10],
        [{'error': {'code': 11}}, 11],
    ],
)
def test_return_code(body: JsonType, expected: int) -> None:
    """The return code can be retrieved from a json message"""
    # act
    actual = return_code(body)

    # assert
    assert actual == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ['completion_msg', 'task_id', 'expected_return_code'],
    [
        [{'id': 10, 'result': {'exitCode': 10}}, 10, 10],
        [{'id': 10, 'error': {'code': 11}}, 10, 11],
    ],
)
async def test_read_until_complete_message(
        completion_msg: JsonType,
        task_id: int,
        expected_return_code: int,
) -> None:
    """read_until_complete_message should wait for the right task id and get
    the return code"""
    # arrange
    reader = StreamReader()
    junk_msg: JsonType = {'junk': 'data'}

    intermediate_message = _create_sbt_response(junk_msg)
    complete_message = _create_sbt_response(completion_msg)

    reader.feed_data(intermediate_message)
    reader.feed_data(intermediate_message)
    reader.feed_data(intermediate_message)
    reader.feed_data(complete_message)

    # act
    rc, messages = await read_until_complete_message(reader, task_id)

    # assert
    assert rc == expected_return_code
    expected_output = json.dumps(junk_msg) + '\n' +\
        json.dumps(junk_msg) + '\n' +\
        json.dumps(junk_msg) + '\n' +\
        json.dumps(completion_msg) + '\n'
    assert messages.decode('UTF-8') == expected_output


@pytest.mark.asyncio
async def test_get_next_message() -> None:
    # arrange
    reader = StreamReader()
    arbitrary_msg_1: JsonType = {'value': 1}
    arbitrary_msg_2: JsonType = {'value': 2}
    arbitrary_msg_3: JsonType = {'value': 3}

    message1 = _create_sbt_response(arbitrary_msg_1)
    reader.feed_data(message1)
    message2 = _create_sbt_response(arbitrary_msg_2)
    reader.feed_data(message2)
    message3 = _create_sbt_response(arbitrary_msg_3)
    reader.feed_data(message3)

    # act
    first = await get_next_message(reader)
    second = await get_next_message(reader)
    third = await get_next_message(reader)

    # assert
    assert first['value'] == 1
    assert second['value'] == 2
    assert third['value'] == 3


@skipif_cant_run_sbt
@pytest.mark.asyncio
async def test_run_via_lsp(
        sbt_project_with_touch_command_and_socket: tuple[Path, SocketType],
) -> None:
    """A valid request can be sent to SBT server, and we can determine when
    the SBT command has complete"""
    # arrange
    project_path, sbt_conn = sbt_project_with_touch_command_and_socket
    file_to_create = 'sample_file.txt'

    ret, _ = await run_via_lsp(
        f'touch {file_to_create}',
        sbt_conn,
    )

    # assert
    expected_file = project_path.joinpath(file_to_create)
    assert expected_file.exists()
    assert ret == 0


@skipif_cant_run_sbt
@pytest.mark.asyncio
async def test_run_via_lsp_invalid_command(
        sbt_project_with_socket: tuple[Path, SocketType],
) -> None:
    """A valid request can be sent to SBT server and we can determine when the
    SBT command has complete"""
    # arrange
    project_path, sbt_conn = sbt_project_with_socket

    ret, _ = await run_via_lsp(
        'some non-existent command',
        sbt_conn,
    )

    # assert
    assert ret != 0


@skipif_cant_run_sbt
@pytest.mark.asyncio
async def test_run_via_lsp_timeout(
        sbt_project_with_sleep_command_and_socket: tuple[Path, SocketType],
) -> None:
    """A valid request can be sent to SBT server and we can determine when the
    SBT command has complete"""
    # arrange
    project_path, sbt_conn = sbt_project_with_sleep_command_and_socket

    # act & assert
    with pytest.raises(asyncio.TimeoutError):
        await run_via_lsp(
            'sleep 10',
            sbt_conn,
            timeout=1,
        )


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
    assert ret == 0
    for file in args + files:
        unquoted_file = _unquote(file)
        expected_file = project_root.joinpath(unquoted_file).absolute()
        assert expected_file.exists()


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


def _create_sbt_response(body: JsonType) -> bytes:
    msg = json.dumps(body)
    return f"""\
Content-Length: {len(msg)}\r\n\
Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n\
\r\n\
{msg}\
""".encode()


@pytest_asyncio.fixture
async def sbt_project_with_server(
        tmp_path: Path,
) -> AsyncGenerator[Path, None]:
    server_process = await start_sbt_server(tmp_path)
    yield tmp_path
    await shutdown_sbt_server(server_process)


@pytest_asyncio.fixture
async def sbt_project_with_socket(
        sbt_project_with_server: Path,
) -> AsyncGenerator[tuple[Path, SocketType], None]:
    project_repo = sbt_project_with_server
    server_process = await start_sbt_server(project_repo)
    with open(port_file_path(project_repo), encoding='UTF-8') as port_file, \
            connect_to_sbt_server(connection_details(port_file)) as conn:
        yield project_repo, conn
        await shutdown_sbt_server(server_process)


@pytest.fixture
def sbt_project_without_server(
        tmp_path: Path,
) -> Path:
    return tmp_path


@pytest_asyncio.fixture
async def sbt_project_with_sleep_command_and_socket(
        tempdir_factory: Path,
) -> AsyncGenerator[tuple[Path, SocketType], None]:
    project_repo = make_repo(tempdir_factory, 'sbt_repo_with_sleep_command')
    project_repo = Path(project_repo)
    server_process = await start_sbt_server(project_repo)
    with open(port_file_path(project_repo), encoding='UTF-8') as port_file, \
            connect_to_sbt_server(connection_details(port_file)) as conn:
        yield project_repo, conn
        await shutdown_sbt_server(server_process)


@pytest_asyncio.fixture
async def sbt_project_with_touch_command_and_socket(
        sbt_project_with_touch_command_no_server: Path,
) -> AsyncGenerator[tuple[Path, SocketType], None]:
    project_root = sbt_project_with_touch_command_no_server
    server_process = await start_sbt_server(project_root)
    with open(port_file_path(project_root), encoding='UTF-8') as port_file, \
            connect_to_sbt_server(connection_details(port_file)) as conn:
        yield project_root, conn
        await shutdown_sbt_server(server_process)
