"""Functions for running git commands."""

from __future__ import annotations

import os
import pathlib
import subprocess
import typing
from typing import Literal, Optional, Union

__all__ = []


@typing.overload
def _git(
    cmd: list[str],
    *,
    cwd: Optional[pathlib.Path] = None,
    check: Literal[True] = True,
    input: Optional[bytes] = None,
) -> str: ...


@typing.overload
def _git(
    cmd: list[str],
    *,
    cwd: Optional[pathlib.Path] = None,
    check: Literal[False],
    input: Optional[bytes] = None,
) -> Optional[str]: ...


@typing.overload
def _git(
    cmd: list[str],
    *,
    cwd: Optional[pathlib.Path] = None,
    check: bool = True,
    input: Optional[bytes] = None,
    raw: Literal[True],
) -> Optional[subprocess.CompletedProcess[bytes]]: ...


def _git(
    cmd: list[str],
    *,
    cwd: Optional[pathlib.Path] = None,
    check: bool = True,
    input: Optional[bytes] = None,
    raw: bool = False,
) -> Optional[Union[str, subprocess.CompletedProcess[bytes]]]:
    """Run a git command and return stripped stdout.

    Args:
        cmd: Git subcommand and arguments (without ``git`` prefix).
        cwd: Working directory for the command.
        check: Raise ``RuntimeError`` on non-zero exit.
        input: Bytes payload for git's stdin (a ``--stdin`` batch).
        raw: Return the ``CompletedProcess`` -- bytes stdout, returncode
            unjudged -- for callers that read exit codes themselves;
            ``check`` is not consulted.

    Returns:
        Stripped stdout string, or ``None`` on non-zero exit when
        ``check`` is ``False``. In ``raw`` mode, the completed process,
        or ``None`` when the git binary is missing.

    """
    # cwd rides `-C`, handed to git rather than the OS: an fsdecoded repo
    # path round-trips through argv, where a process working directory
    # would fail outright on a path the running locale cannot name
    full_cmd = ['git']
    if cwd:
        full_cmd.extend(['-C', f'{cwd}'])
    full_cmd.extend(cmd)
    # the repository a command answers from is the one enclosing cwd, never
    # one the caller's environment names: a git hook exports GIT_DIR
    # (relative, resolving against this cwd) and a caller may export one
    # pointing at another repo -- either would answer with, or mutate, a
    # foreign repository's state
    env = {
        name: value for name, value in os.environ.items() if not name.startswith('GIT_')
    }
    # a missing git binary is treated like a failed command, so callers that
    # pass check=False (or read the raw result) degrade to a clean no-op;
    # output is captured as bytes and fsdecoded -- text mode would decode with
    # the locale codec and raise on an undecodable repo path
    try:
        result = subprocess.run(
            full_cmd,
            input=input,
            capture_output=True,
            env=env,
        )
    except FileNotFoundError as e:
        if raw or not check:
            return None
        cmd_string = ' '.join(cmd)
        raise RuntimeError(f'git {cmd_string} failed: {e}') from e
    if raw:
        return result
    if result.returncode != 0:
        if check:
            cmd_string = ' '.join(cmd)
            error = os.fsdecode(result.stderr).strip()
            raise RuntimeError(
                f'git {cmd_string} failed (exit {result.returncode}): {error!r}'
            )
        return None
    output = os.fsdecode(result.stdout).strip()
    return output
