"""Sides: an argv prefix plus the full environment a command runs under."""

from __future__ import annotations

import dataclasses
import os
import pathlib
import shutil
import subprocess
from collections.abc import Mapping, Sequence

__all__ = [
    'NOTICE',
    'Side',
    'Outcome',
    'environment',
    'run',
]

#: the stderr line an engine prints first when ``WIKI_FREEZE_TIME`` is set
NOTICE = b'WIKI_FREEZE_TIME is set: timestamps use its fixed instant, not the clock.\n'

# the locale and zone every side runs under: a UTF-8 locale that macOS and
# Linux both ship, and a POSIX zone string that needs no zone files and sits
# off UTC by an odd offset, so a stamp taken in local time shows
_LANG = 'C.UTF-8'
_TZ = '<+0545>-05:45'
# the folders after git's own on every side's PATH
_SYSTEM_PATH = ('/usr/bin', '/bin', '/usr/sbin', '/sbin')
# a side that runs this long has hung
_TIMEOUT = 3600


@dataclasses.dataclass(frozen=True)
class Side:
    """One engine under comparison.

    Attributes:
        name: Label of the side in results.
        argv: Command prefix; the harness appends the wiki arguments.
        env: Variables set for this side only, over the fixed environment.
        notice: Whether the side prints :data:`NOTICE` when
            ``WIKI_FREEZE_TIME`` is set. A side that fixes its clock through
            a wrapper prints none.
        describe: A whole command whose stdout says what the side is made
            of (its interpreter, its dependencies, its commit), for the
            result file. Empty for a side that has none.

    """

    name: str
    argv: tuple[str, ...]
    env: Mapping[str, str] = dataclasses.field(default_factory=dict)
    notice: bool = True
    describe: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Outcome:
    """What one command printed and returned.

    Attributes:
        stdout: The bytes written to stdout.
        stderr: The bytes written to stderr, without the notice line.
        code: The exit code.
        notice: Whether stderr opened with the notice, on a side that
            prints it; always true on a side that prints none.

    """

    stdout: bytes
    stderr: bytes
    code: int
    notice: bool = True


def environment(side: Side, home: pathlib.Path, *, now: str) -> dict[str, str]:
    """Return the full environment of ``side``; nothing else is inherited.

    Args:
        side: The side to run.
        home: Scratch home folder; it holds the global git config.
        now: RFC 3339 instant for ``WIKI_FREEZE_TIME``.

    Returns:
        The variables to run every command of the side under.

    """
    # git first, so both sides call the git the harness resolved
    folders: list[str] = list(_SYSTEM_PATH)
    git = shutil.which('git')
    if git and (os.path.dirname(git) not in folders):
        folders.insert(0, os.path.dirname(git))
    config = f'{home / ".gitconfig"}'
    # NOTE: the Python engine drops GIT_CONFIG_GLOBAL and GIT_CONFIG_NOSYSTEM
    #   from its git children, which then read the same file as $HOME/.gitconfig
    #   and still read the system config
    env = {
        'PATH': os.pathsep.join(folders),
        'HOME': f'{home}',
        'WIKI_CONFIG_DIR': f'{home / "wiki_config"}',
        'OFFLINE_MODE': 'true',
        'GIT_CONFIG_GLOBAL': config,
        'GIT_CONFIG_NOSYSTEM': '1',
        'LANG': _LANG,
        'TZ': _TZ,
        'WIKI_FREEZE_TIME': now,
    }
    env.update(side.env)
    return env


def run(
    side: Side,
    args: Sequence[str],
    *,
    cwd: pathlib.Path,
    env: Mapping[str, str],
) -> Outcome:
    """Run one wiki command on ``side`` and capture it.

    Args:
        side: The side to run.
        args: Wiki arguments, appended to the side's prefix.
        cwd: Working directory of the command.
        env: Full environment, from :func:`environment`.

    Returns:
        The streams and the exit code, with the notice line removed from
        the head of stderr on a side that prints it.

    """
    result = subprocess.run(
        [*side.argv, *args],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_TIMEOUT,
    )
    stderr = result.stderr
    notice = True
    if side.notice:
        notice = stderr.startswith(NOTICE)
        if notice:
            stderr = stderr[len(NOTICE) :]
    return Outcome(
        stdout=result.stdout,
        stderr=stderr,
        code=result.returncode,
        notice=notice,
    )
