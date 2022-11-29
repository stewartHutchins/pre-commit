from __future__ import annotations

import io
import json
import socket
from asyncio import StreamReader
from pathlib import Path
from typing import AsyncIterable
from typing import Sequence
from typing import TextIO
from typing import TypeAlias
from typing import Union
from urllib.parse import urlparse

from pre_commit.hook import Hook
from pre_commit.languages import helpers

ENVIRONMENT_DIR = None
install_environment = helpers.no_install
health_check = helpers.basic_health_check
get_default_version = helpers.basic_get_default_version

_ACTIVE_JSON_PATH = 'project/target/active.json'

JsonType: TypeAlias = dict[str, Union[str, Union[int, str, dict[str, object]]]]
_CONTENT_LENGTH = 'Content-Length'


def run_hook(
        hook: Hook,
        file_args: Sequence[str],
        color: bool,
) -> tuple[int, bytes]:
    if is_server_running(Path('.')):
        return run_sbt_hook_via_lsp(hook, file_args, color)
    else:
        return run_sbt_hook_via_commandline(hook, file_args, color)


def run_sbt_hook_via_commandline(
        hook: Hook,
        file_args: Sequence[str],
        color: bool,
) -> tuple[int, bytes]:
    """
    Run an SBT hook, via the commandline. The command to be run is:
        sbt ${entry} ${args} ${files}
    The entry and args will not be quoted (so should be wrapped in quotes as
    appropriate by the hook author),however files will be quoted, so any
    filenames with spaces will be interpreted as a single argument by SBT
    """
    entry_part = hook.entry
    args_part = ' '.join(hook.args)
    files_part = ' '.join(_quote(file) for file in file_args)
    sbt_command = f'{entry_part} {args_part} {files_part}'
    shell_cmd = ('sbt', sbt_command)
    return helpers.run_xargs(hook, shell_cmd, [], color=color)


def _quote(s: str) -> str:
    return f"\"{s}\""


def run_sbt_hook_via_lsp(
        hook: Hook,
        file_args: Sequence[str],
        color: bool,
) -> tuple[int, bytes]:
    with open(port_file_path(Path('.')), encoding='UTF-8') as port_file, \
            connect_to_sbt_server(connection_details(port_file)) as _:
        # TODO: Improve impl to connect to run commands via SBT server
        return run_sbt_hook_via_commandline(hook, file_args, color)


def is_server_running(root_dir: Path) -> bool:
    """
    Determine whether the server is running, based on the presence or lack
    there of an SBT port file
    :param root_dir: The root directory of the project
    :return: True if SBT server is running in this directory, else False
    """
    return port_file_path(root_dir).exists()


def port_file_path(root_dir: Path) -> Path:
    """
    Get the location of a port file, given the directory an SBT server is
    running in
    :param root_dir: The root directory of an SBT server
    :return: The path to the port file
    """
    return root_dir.joinpath(_ACTIVE_JSON_PATH)


def connection_details(active_json_io: TextIO) -> Path:
    """
    Get the location of the unix socket, from the opened port file
    :param active_json_io: An opened port file
    :return: The path to the unix socket
    """
    parsed_json: dict[str, str] = json.load(active_json_io)
    uri = parsed_json['uri']
    return Path(urlparse(uri).path)


def connect_to_sbt_server(socket_file: Path) -> socket.socket:
    """
    Create a connection to a unix socket
    :param socket_file: The path to the socket
    :return: A socket connection
    """
    sbt_connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sbt_connection.connect(str(socket_file))
    return sbt_connection


def create_exec_request(command_id: int, sbt_command: str) -> str:
    """
    Create an exec request for SBT server
    :param command_id: A unique ID for the task
    :param sbt_command: The command to be run in SBT
    :return: A request which (when sent to SBT server) will invoke the
    provided command
    """
    # TODO: do not reload project (experimentation needed)
    rpc_body = _body(command_id, f'reload;{sbt_command}')
    bsp_header = _header(len(rpc_body) + 2)
    return bsp_header + '\r\n' + rpc_body + '\r\n'


def _header(length: int) -> str:
    return f"""Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n""" \
           f"""Content-Length: {length}\r\n"""


def _body(command_id: int, sbt_command: str) -> str:
    return json.dumps(
        {
            'jsonrpc': '2.0',
            'id': command_id,
            'method': 'sbt/exec',
            'params': {
                'commandLine': sbt_command,
            },
        },
    )


async def read_until_complete_message(
        reader: StreamReader,
        task_id: int,
) -> tuple[int, bytes]:
    """
    Read from the stream reader until the final message (indicating the
    command has completed) has been received
    :param reader: The stream reader, reading from the SBT server
    :param task_id: The task ID of the SBT command
    :return: The return code of the command and the read data read from the
    server
    """
    buffer = io.BytesIO()
    async for message in _message_iterator(reader):
        buffer.write(json.dumps(message).encode('UTF-8') + b'\n')
        if is_completion_message(message, task_id):
            return return_code(message), buffer.getvalue()
    raise ValueError('Completion message not found')  # probably not reachable


async def _message_iterator(reader: StreamReader) -> AsyncIterable[JsonType]:
    while not reader.at_eof():
        yield await get_next_message(reader)


async def get_next_message(reader: StreamReader) -> JsonType:
    """
    Read the next message sent by SBT server
    :param reader: A stream reader connected to the socket
    :return: The next message
    """
    headers = _parse_headers(await _read_headers(reader))
    content_length: int = headers[_CONTENT_LENGTH]  # type: ignore
    body = _parse_body(await _read_body(content_length, reader))
    return body


def _parse_headers(headers: list[str]) -> JsonType:
    return dict(_parse_header(header) for header in headers)


def _parse_header(header: str) -> tuple[str, str | int]:
    key, value = header.split(':')
    if key == _CONTENT_LENGTH:
        return key, int(value.strip())
    else:
        return key, value.strip()


async def _read_headers(reader: StreamReader) -> list[str]:
    headers: list[str] = []
    while True:
        line = (await reader.readline()).decode('UTF-8')
        if line == '\r\n':
            break
        headers = headers + [line]
    return headers


async def _read_body(content_length: int, reader: StreamReader) -> str:
    return (await reader.readexactly(content_length)).decode('UTF-8')


def _parse_body(content: str) -> JsonType:
    body: JsonType = json.loads(content)
    return body


def is_completion_message(message: JsonType, task_id: int) -> bool:
    """
    Determine whether the message sent indicates whether the SBT command has
    completed
    :param message: A message from sbt server
    :param task_id: The task ID of the message
    :return: True if the message sent indicates completion of the command,
    else False
    """
    return message.get('id') == task_id


def return_code(completion_msg: JsonType) -> int:
    """
    Get the return code from the final response message
    :param completion_msg: The final response
    :return: The return code
    """
    if 'result' in completion_msg:  # pylint: disable=no-else-return
        return completion_msg['result']['exitCode']  # type: ignore
    else:
        return completion_msg['error']['code']  # type: ignore
