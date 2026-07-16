"""Shared helpers for the ``wiki`` CLI subprocess tests.

The CLI suite drives the real ``wiki`` console script as a subprocess;
test modules pull the hermetic runner in with ``from .conftest import
_wiki`` and skip themselves when the script is not installed.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
from typing import Optional

from wiki.constants import OFFLINE_MODE

# prefer the console script beside the running interpreter (this checkout's
# venv) over PATH, which may resolve a different install or a broken shim
VENV_BIN = pathlib.Path(sys.executable).parent
WIKI = shutil.which('wiki', path=VENV_BIN) or shutil.which('wiki')
GIT = shutil.which('git')


def _env() -> dict[str, str]:
    """Build the hermetic base environment for suite subprocesses."""
    env = dict(os.environ)
    # drop color-forcing vars: typer force-enables ANSI when any is set (e.g.
    # GITHUB_ACTIONS in CI), and the escapes it injects inside option names
    # break plain-substring assertions on captured output
    for var in ('GITHUB_ACTIONS', 'FORCE_COLOR', 'PY_COLORS'):
        env.pop(var, None)
    # the site-packages install is a frozen copy, so PYTHONPATH puts this
    # worktree first and the console script imports the edited package
    worktree = pathlib.Path(__file__).resolve().parents[2]
    env['PYTHONPATH'] = os.pathsep.join(
        part for part in (str(worktree), env.get('PYTHONPATH', '')) if part
    )
    # the console script's directory leads PATH so nested invocations by bare
    # name (a `git merge` running the registered `wiki _merge` driver) resolve
    # this checkout's install
    env['PATH'] = f'{VENV_BIN}{os.pathsep}{env.get("PATH", "")}'
    return env


def _wiki(
    cwd: pathlib.Path,
    *args: str,
    allow_download: bool = False,
    home: Optional[pathlib.Path] = None,
) -> subprocess.CompletedProcess:
    """Run the ``wiki`` CLI in ``cwd`` and capture text output.

    Plugin downloads are skipped by default so the suite stays offline;
    pass ``allow_download=True`` to exercise the real network fetch.
    ``HOME`` is always isolated -- to ``home`` when given, else ``cwd``
    -- so commands that write under the home directory (install
    targets) never touch or depend on the developer's real one.
    """
    env = _env()
    env[OFFLINE_MODE] = 'false' if allow_download else 'true'
    env['HOME'] = str(home if home is not None else cwd)
    return subprocess.run(
        [WIKI, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


def _git(cwd: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in ``cwd``, capturing text output.

    Runs under the suite's hermetic environment (see ``_env``), whose
    PATH prepend lets a ``git merge`` resolve the registered
    ``wiki _merge`` driver by bare name. No ``check``: the merge tests
    assert on conflict exit codes.
    """
    return subprocess.run(
        [GIT, '-C', f'{cwd}', *args],
        capture_output=True,
        text=True,
        env=_env(),
    )
