from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Sequence
from typing import TextIO
from urllib.parse import urlparse

from pre_commit.hook import Hook
from pre_commit.languages import helpers

ENVIRONMENT_DIR = None
install_environment = helpers.no_install
health_check = helpers.basic_health_check
get_default_version = helpers.basic_get_default_version

_ACTIVE_JSON_PATH = 'project/target/active.json'


def run_hook(
        hook: Hook,
        file_args: Sequence[str],
        color: bool,
) -> tuple[int, bytes]:
    if is_server_running(Path('.')):
        with open(port_file_path(Path('.')), encoding='UTF-8') as port_file, \
                connect_to_sbt_server(connection_details(port_file)) as _:
            # TODO: Improve impl to connect to run commands via SBT server
            return run_sbt_hook_via_commandline(hook, file_args, color)
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
