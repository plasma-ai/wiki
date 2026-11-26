"""Functions for the Linux container.

macOS keeps no two names that differ in case only, and no name that is no
valid UTF-8, so the trees that hold such names exist only on Linux. The
container is the Linux of the CI runners on a host that is none: a clone of
the repository at one commit, on the container's own file system, with the
harness's environment installed. Whatever runs there runs as in CI.
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
from collections.abc import Sequence
from typing import Optional

__all__ = []

#: the file the image is built from
DOCKERFILE = pathlib.Path(__file__).with_name('Dockerfile')

# the name of the image; its tag is a digest of the Dockerfile
_IMAGE = 'wiki-parity'
# the volume that holds what uv downloads, so a second container starts fast
_CACHE = 'wiki-parity-cache'
# where a container finds the repository, the clone, the cache and the output
_SOURCE = '/source.git'
_CHECKOUT = '/work/wiki'
_CACHED = '/cache'
_OUT = '/out'
# what a container runs: it clones the repository to its own file system,
# takes the remote-tracking branches as the host holds them, checks out the
# commit, installs the harness's environment and becomes the command
_ENTER = f"""\
set -e
git config --global --add safe.directory {_SOURCE}
git clone --quiet --no-checkout {_SOURCE} {_CHECKOUT}
cd {_CHECKOUT}
git fetch --quiet origin '+refs/remotes/origin/*:refs/remotes/origin/*'
git checkout --quiet --detach "$1"
shift
uv sync --quiet --only-group parity
exec "$@"
"""


def run(
    command: Sequence[str],
    *,
    commit: Optional[str] = None,
    out: Optional[pathlib.Path] = None,
) -> int:
    """Run ``command`` in a fresh container, in a clone of the repository.

    The clone is made inside the container from the repository this
    harness lies in, which the container reads and never writes. It holds
    what is committed: an edit that is not committed does not reach it.
    The command runs from the clone's root, as the user ``parity``, with
    the streams of the caller.

    Args:
        command: The command and its arguments.
        commit: The commit to check out. ``None`` takes ``HEAD`` of the
            checkout the harness lies in.
        out: A folder of the host that the container writes as ``/out``.

    Returns:
        The exit code of the command.

    Raises:
        ValueError: If ``command`` is empty, if Docker is not installed or
            does not answer, or if ``commit`` names no commit.
        RuntimeError: If the image cannot be built.

    """
    if not command:
        raise ValueError('The container needs a command to run.')
    here = pathlib.Path(__file__).parent
    named = commit or 'HEAD'
    resolved = _git(
        ['rev-parse', '--verify', '--quiet', f'{named}^{{commit}}'], cwd=here
    )
    if not resolved:
        raise ValueError(f'{named!r} names no commit of the repository.')
    source = _git(['rev-parse', '--path-format=absolute', '--git-common-dir'], cwd=here)
    image = ensure()
    mounts = ['--volume', f'{source}:{_SOURCE}:ro', '--volume', f'{_CACHE}:{_CACHED}']
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        mounts += ['--volume', f'{out.resolve()}:{_OUT}']
    argv = ['docker', 'run', '--rm', '--init', *mounts, image]
    argv += ['bash', '-c', _ENTER, 'bash', resolved, *command]
    return subprocess.run(argv).returncode


def ensure() -> str:
    """Build the image, unless one of this Dockerfile exists; return its name.

    Raises:
        ValueError: If Docker is not installed, or its daemon does not
            answer.
        RuntimeError: If the build fails.

    """
    if shutil.which('docker') is None:
        raise ValueError('The container needs Docker, and no docker is on PATH.')
    answer = subprocess.run(
        ['docker', 'version', '--format', '{{.Server.Version}}'],
        capture_output=True,
        text=True,
    )
    if answer.returncode != 0:
        error = answer.stderr.strip()
        raise ValueError(f'The Docker daemon does not answer: {error!r}')
    text = DOCKERFILE.read_bytes()
    image = f'{_IMAGE}:{hashlib.sha256(text).hexdigest()[:12]}'
    held = subprocess.run(['docker', 'image', 'inspect', image], capture_output=True)
    if held.returncode == 0:
        return image
    # the Dockerfile copies nothing from the host, so it is the whole context
    built = subprocess.run(
        ['docker', 'build', '--tag', image, '-'],
        input=text,
        capture_output=True,
    )
    if built.returncode != 0:
        error = built.stderr.decode('utf-8', 'replace').strip()
        raise RuntimeError(f'docker build failed (exit {built.returncode}): {error!r}')
    return image


# ------ helper functions


def _git(cmd: Sequence[str], *, cwd: pathlib.Path) -> str:
    """Run a git command in ``cwd`` and return its stripped stdout; empty if it fails."""
    result = subprocess.run(
        ['git', '-C', f'{cwd}', *cmd],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ''
    return result.stdout.strip()
