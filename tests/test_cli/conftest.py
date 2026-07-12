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


def _wiki(
    cwd: pathlib.Path,
    *args: str,
    allow_download: bool = False,
    home: Optional[pathlib.Path] = None,
) -> subprocess.CompletedProcess:
    """Run the ``wiki`` CLI in ``cwd`` and capture text output.

    Plugin downloads are skipped by default so the suite stays offline;
    pass ``allow_download=True`` to exercise the real network fetch, and
    ``home`` to isolate commands that write under the home directory.
    """
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
    env[OFFLINE_MODE] = 'false' if allow_download else 'true'
    if home is not None:
        env['HOME'] = str(home)
    return subprocess.run(
        [WIKI, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )
