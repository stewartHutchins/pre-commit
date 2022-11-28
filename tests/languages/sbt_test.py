from __future__ import annotations

from itertools import product
from pathlib import Path
from typing import Any
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from pre_commit.hook import Hook
from pre_commit.languages import sbt
from pre_commit.languages.sbt import is_server_running
from pre_commit.languages.sbt import port_file_path
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
