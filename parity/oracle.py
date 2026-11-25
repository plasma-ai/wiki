"""Functions for the oracle: v1 at one commit, in a checkout the harness builds.

``oracle.toml`` names the commit, the branch it comes from, and each v1 side:
an interpreter and a constraints file that pins every package of its venv.
:func:`ensure` builds the checkout and the venvs, :func:`side` hands a side to
the harness after holding it against the file, and :func:`check` holds the
file level with v1.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import pathlib
import shutil
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from typing import Literal, Optional, overload

from . import facts, launcher
from .side import Side

__all__ = [
    'ORACLE',
    'CHECKS',
    'Build',
    'Oracle',
    'Verdict',
    'load',
    'ensure',
    'side',
    'check',
]

logger = logging.getLogger('parity')

#: the committed file that names the oracle
ORACLE = pathlib.Path(__file__).with_name('oracle.toml')
#: the checks of :func:`check`, in the order they run
CHECKS = ('base', 'level', 'removed')

# the remote whose branches hold v1
_REMOTE = 'origin'
# the folder of worktrees beside the main checkout
_WORKTREES = 'worktrees'
# the folder of the oracle checkout that holds one venv per side
_VENVS = '.venv'
# the folders of the oracle checkout that v1's answers come from
_SOURCES = ('wiki', 'tests')
# the list of v1 paths this branch has removed, beside the oracle file
_REMOVED = 'removed.txt'


@dataclasses.dataclass(frozen=True)
class Build:
    """One v1 side: what the oracle commit runs under.

    Attributes:
        name: Name of the side.
        python: Version of the uv-managed interpreter of its venv.
        unicode: Version of that interpreter's Unicode tables.
        libyaml: Version of the libyaml its PyYAML C loader is built on.
        constraints: The file that pins every package of its venv.

    """

    name: str
    python: str
    unicode: str
    libyaml: str
    constraints: pathlib.Path


@dataclasses.dataclass(frozen=True)
class Oracle:
    """What ``oracle.toml`` names.

    Attributes:
        path: The file itself.
        repo: The checkout that holds the file.
        checkout: Where the oracle checkout lives: a worktree beside the
            main checkout, named after the commit.
        commit: The oracle commit.
        version: What ``wiki --version`` prints at that commit.
        base: The branch v1 lives on.
        pending: The branch that holds the oracle commit until the base
            branch holds it, if there is one.
        builds: The v1 sides, by name.
        unmerged: The reason each listed v1 commit is not merged yet, by
            commit.

    """

    path: pathlib.Path
    repo: pathlib.Path
    checkout: pathlib.Path
    commit: str
    version: str
    base: str
    pending: Optional[str]
    builds: Mapping[str, Build]
    unmerged: Mapping[str, str]


@dataclasses.dataclass(frozen=True)
class Verdict:
    """The result of one check of the oracle file.

    Attributes:
        name: A name of :data:`CHECKS`.
        passed: Whether the check holds.
        detail: What the check found, as one line.

    """

    name: str
    passed: bool
    detail: str


def load(path: pathlib.Path = ORACLE) -> Oracle:
    """Return the oracle the TOML file at ``path`` names.

    Raises:
        ValueError: If the file lists an unmerged commit without a reason.

    """
    data = tomllib.loads(path.read_text(encoding='utf-8'))
    repo = pathlib.Path(_git(['rev-parse', '--show-toplevel'], cwd=path.parent))
    # every worktree shares the git folder of the main checkout
    shared = ['rev-parse', '--path-format=absolute', '--git-common-dir']
    main = pathlib.Path(_git(shared, cwd=repo)).parent
    folder = f'{main.name}-oracle-{data["commit"][:7]}'
    builds = {}
    for name, table in data['sides'].items():
        builds[name] = Build(
            name=name,
            python=table['python'],
            unicode=table['unicode'],
            libyaml=table['libyaml'],
            constraints=path.parent / table['constraints'],
        )
    unmerged = {}
    for row in data.get('unmerged', ()):
        if not row.get('reason'):
            raise ValueError(
                f'{path.name} lists the unmerged commit {row["commit"]}'
                f' without a reason.'
            )
        unmerged[row['commit']] = row['reason']
    return Oracle(
        path=path,
        repo=repo,
        checkout=main.parent / _WORKTREES / folder,
        commit=data['commit'],
        version=data['version'],
        base=data['base'],
        pending=data.get('pending'),
        builds=builds,
        unmerged=unmerged,
    )


def ensure(oracle: Oracle, *, names: Optional[Sequence[str]] = None) -> None:
    """Build the oracle checkout and the venvs of its sides.

    A checkout that exists is kept, and so is a venv that holds what its
    side names. A venv that does not is built again from nothing.

    Args:
        oracle: The oracle, from :func:`load`.
        names: The sides to build; ``None`` builds them all.

    Raises:
        ValueError: If the checkout is not the oracle commit as git holds
            it, or if a venv differs from what its side names after it is
            built.
        RuntimeError: If git or uv fails.

    """
    if not oracle.checkout.exists():
        oracle.checkout.parent.mkdir(parents=True, exist_ok=True)
        cmd = ['worktree', 'add', '--detach', f'{oracle.checkout}', oracle.commit]
        _git(cmd, cwd=oracle.repo)
        logger.info('checked out %s at %s', oracle.commit[:7], oracle.checkout)
    _hold(oracle)
    if names is None:
        names = list(oracle.builds)
    for name in names:
        build = oracle.builds[name]
        if not _differs(oracle, build):
            logger.info('%s: current', name)
            continue
        _build(oracle, build)
        problems = _differs(oracle, build)
        if problems:
            raise ValueError(_refusal(oracle, name, problems))
        logger.info('%s: built', name)


def side(oracle: Oracle, name: str) -> Side:
    """Return the v1 side ``name`` for the harness to run.

    The side runs the oracle checkout's package through the launcher, under
    the interpreter of its venv, and proves on every command that it is the
    oracle commit.

    Raises:
        ValueError: If the checkout is not the oracle commit as git holds
            it, or if the side's venv differs from what the side names.

    """
    _hold(oracle)
    build = oracle.builds[name]
    problems = _differs(oracle, build)
    if problems:
        raise ValueError(_refusal(oracle, name, problems))
    python = f'{_python(oracle, build)}'
    return Side(
        name=name,
        argv=(python, '-P', f'{launcher.__file__}'),
        describe=(python, '-P', f'{facts.__file__}'),
        identity={
            'engine': launcher.ENGINE,
            'version': oracle.version,
            'build': oracle.commit,
        },
    )


def check(oracle: Oracle, *, skip: Sequence[str] = ()) -> list[Verdict]:
    """Hold the oracle file level with v1.

    ``base``: the file names the merge base of ``HEAD`` and the branch that
    holds v1. ``level``: every commit of v1 is merged into ``HEAD``, or the
    file lists it with a reason. ``removed``: no v1 path that this branch
    has removed is tracked again. Branches are read as the remote-tracking
    references hold them, so fetch first.

    Args:
        oracle: The oracle, from :func:`load`.
        skip: Names of the checks to leave out.

    Returns:
        One verdict per check that ran, in the order of :data:`CHECKS`.

    """
    checks = {'base': _base, 'level': _level, 'removed': _removed}
    return [checks[name](oracle) for name in CHECKS if name not in skip]


# ------ helper functions


def _hold(oracle: Oracle) -> None:
    """Refuse an oracle checkout that is not the oracle commit as git holds it.

    Raises:
        ValueError: If the checkout is missing, if its ``HEAD`` is another
            commit, or if a file under ``wiki/`` or ``tests/`` is changed,
            added or deleted there.

    """
    checkout = oracle.checkout
    if not checkout.is_dir():
        raise ValueError(
            f'No oracle checkout at {checkout}; run `python -m parity oracle ensure`.'
        )
    head = _git(['rev-parse', 'HEAD'], cwd=checkout)
    if head != oracle.commit:
        raise ValueError(
            f'Oracle checkout {checkout} is at {head[:7]}, and'
            f' {oracle.path.name} names {oracle.commit[:7]}.'
        )
    cmd = ['--no-optional-locks', 'status', '--porcelain', '--', *_SOURCES]
    changed = _git(cmd, cwd=checkout)
    if changed:
        raise ValueError(f'Oracle checkout {checkout} has local changes: {changed!r}.')


def _python(oracle: Oracle, build: Build) -> pathlib.Path:
    """Return the interpreter of the venv of ``build``."""
    return oracle.checkout / _VENVS / build.name / 'bin' / 'python'


def _build(oracle: Oracle, build: Build) -> None:
    """Build the venv of ``build`` from nothing."""
    venv = oracle.checkout / _VENVS / build.name
    if venv.exists():
        shutil.rmtree(venv)
    # a uv-managed interpreter is the same build on every host
    cmd = ['venv', '--quiet', '--managed-python', '--python', build.python]
    _uv([*cmd, f'{venv}'])
    # the pinned packages, then the checkout itself in place
    cmd = ['pip', 'install', '--quiet', '--python', f'{_python(oracle, build)}']
    cmd += ['--requirements', f'{build.constraints}']
    cmd += ['--editable', f'{oracle.checkout}']
    _uv(cmd)


def _differs(oracle: Oracle, build: Build) -> list[str]:
    """Return each way the venv of ``build`` differs from what the side names.

    The venv must run the named interpreter, with the named Unicode tables
    and libyaml; import the oracle checkout's package, at the named version;
    and hold exactly the packages its constraints file pins, beside the
    checkout's own.
    """
    python = _python(oracle, build)
    if not python.is_file():
        return [f'no interpreter at {python}']
    # NOTE: nothing is inherited: a PYTHONPATH of the caller's would change
    #   what the interpreter imports
    result = subprocess.run(
        [f'{python}', '-P', f'{facts.__file__}'],
        env={},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return [f'its interpreter fails: {result.stderr.strip()!r}']
    found = json.loads(result.stdout)
    problems = []
    named = {
        'python': build.python,
        'unicode': build.unicode,
        'libyaml': build.libyaml,
    }
    for key, value in named.items():
        if found[key] != value:
            problems.append(f'{key} is {found[key] or "absent"}, not {value}')
    if found['wiki']['version'] != oracle.version:
        problems.append(f'wiki is {found["wiki"]["version"]}, not {oracle.version}')
    package = pathlib.Path(found['wiki']['package'])
    if package.resolve() != (oracle.checkout / 'wiki').resolve():
        problems.append(f'wiki is imported from {package}')
    # every package but the checkout's own is pinned, and every pin is installed
    pins = _pins(build.constraints)
    packages = dict(found['packages'])
    packages.pop(_project(oracle.checkout), None)
    for name in sorted(set(pins) | set(packages)):
        (installed, pinned) = (packages.get(name), pins.get(name))
        if installed != pinned:
            problems.append(
                f'{name} is {installed or "absent"}, pinned at {pinned or "nothing"}'
            )
    return problems


def _refusal(oracle: Oracle, name: str, problems: Sequence[str]) -> str:
    """Return the message that refuses the side ``name`` for ``problems``."""
    return (
        f'Side {name} is not what {oracle.path.name} names'
        f' ({"; ".join(problems)}); run `python -m parity oracle ensure`.'
    )


def _pins(constraints: pathlib.Path) -> dict[str, str]:
    """Return the version a constraints file pins each package at."""
    pins = {}
    for line in constraints.read_text(encoding='utf-8').splitlines():
        if (not line) or line.startswith('#'):
            continue
        (name, _, version) = line.partition('==')
        pins[facts.canonical(name)] = version
    return pins


def _project(checkout: pathlib.Path) -> str:
    """Return the name of the distribution the checkout at ``checkout`` builds."""
    text = (checkout / 'pyproject.toml').read_text(encoding='utf-8')
    return facts.canonical(tomllib.loads(text)['project']['name'])


def _base(oracle: Oracle) -> Verdict:
    """Check that the file names the merge base of ``HEAD`` and v1."""
    source = _sources(oracle)[-1]
    found = _git(['merge-base', 'HEAD', source], cwd=oracle.repo)
    detail = (
        f'{oracle.path.name} names {oracle.commit[:7]}, and the merge base of'
        f' HEAD and {source} is {found[:7]}'
    )
    return Verdict('base', found == oracle.commit, detail)


def _level(oracle: Oracle) -> Verdict:
    """Check that every commit of v1 is merged into ``HEAD``, or listed."""
    sources = _sources(oracle)
    unmerged: list[str] = []
    for source in sources:
        listing = _git(['rev-list', f'HEAD..{source}'], cwd=oracle.repo)
        unmerged.extend(commit for commit in listing.split() if commit not in unmerged)
    unlisted = [commit for commit in unmerged if commit not in oracle.unmerged]
    if unlisted:
        short = ', '.join(commit[:7] for commit in unlisted)
        detail = f'unmerged and not listed in {oracle.path.name}: {short}'
    elif unmerged:
        detail = f'{len(unmerged)} unmerged, each listed with a reason'
    else:
        detail = f'no commit of {" or ".join(sources)} is unmerged'
    return Verdict('level', not unlisted, detail)


def _removed(oracle: Oracle) -> Verdict:
    """Check that no v1 path this branch has removed is tracked again."""
    listing = oracle.path.with_name(_REMOVED)
    paths: list[str] = []
    if listing.is_file():
        text = listing.read_text(encoding='utf-8')
        paths = [line for line in text.splitlines() if line]
    if not paths:
        return Verdict('removed', True, f'{_REMOVED} lists no path')
    cmd = ['--no-optional-locks', '--literal-pathspecs', 'ls-files', '--', *paths]
    back = _git(cmd, cwd=oracle.repo).splitlines()
    if back:
        detail = f'tracked again: {", ".join(back)}'
    else:
        detail = f'none of the {len(paths)} listed paths is tracked'
    return Verdict('removed', not back, detail)


def _sources(oracle: Oracle) -> list[str]:
    """Return the references that hold v1: the base branch, then the pending one.

    The pending branch counts for as long as it holds a commit the base
    branch lacks. Once it is merged, or gone, the base branch alone holds
    v1.
    """
    base = f'{_REMOTE}/{oracle.base}'
    if oracle.pending is None:
        return [base]
    pending = f'{_REMOTE}/{oracle.pending}'
    cmd = ['rev-parse', '--verify', '--quiet', f'{pending}^{{commit}}']
    if _git(cmd, cwd=oracle.repo, check=False) is None:
        return [base]
    cmd = ['merge-base', '--is-ancestor', pending, base]
    if _git(cmd, cwd=oracle.repo, check=False) is not None:
        return [base]
    return [base, pending]


@overload
def _git(
    cmd: Sequence[str],
    *,
    cwd: pathlib.Path,
    check: Literal[True] = True,
) -> str: ...


@overload
def _git(
    cmd: Sequence[str],
    *,
    cwd: pathlib.Path,
    check: Literal[False],
) -> Optional[str]: ...


def _git(
    cmd: Sequence[str],
    *,
    cwd: pathlib.Path,
    check: bool = True,
) -> Optional[str]:
    """Run a git command and return its stripped stdout.

    Args:
        cmd: Git arguments (without the leading ``git``).
        cwd: Folder to run in (``git -C <cwd>``).
        check: Whether to raise on a non-zero exit, or return ``None``.

    Raises:
        RuntimeError: If git fails and ``check`` is set.

    """
    result = subprocess.run(
        ['git', '-C', f'{cwd}', *cmd],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if not check:
            return None
        error = result.stderr.strip()
        raise RuntimeError(f'git failed (exit {result.returncode}): {error!r}')
    return result.stdout.strip()


def _uv(cmd: Sequence[str]) -> None:
    """Run a uv command.

    Raises:
        RuntimeError: If uv fails.

    """
    result = subprocess.run(['uv', *cmd], capture_output=True, text=True)
    if result.returncode != 0:
        error = result.stderr.strip()
        raise RuntimeError(f'uv failed (exit {result.returncode}): {error!r}')
