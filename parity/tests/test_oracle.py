"""Test the ``parity.oracle`` module.

A v1 side that runs is covered by every matrix test, which goes through
the oracle checkout; this module carries the refusals that keep a side
from being anything but what the oracle file names, the making of a
checkout, and the checks that hold the file level with v1.
"""

from __future__ import annotations

import dataclasses
import pathlib
import shutil

import pytest

from parity import oracle

from .conftest import _git

__all__ = [
    'test_venv_that_differs_from_its_side_is_refused',
    'test_checkout_that_is_not_the_oracle_commit_is_refused',
    'test_ensure_makes_a_deleted_checkout_again',
    'test_check_follows_the_oracle_from_the_pending_branch_to_a_sync',
]

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='requires git')


@pytest.mark.parametrize(
    argnames=('changed', 'message'),
    argvalues=[
        # the oracle file names another interpreter, loader or version
        ('python', 'python is'),
        ('unicode', 'unicode is'),
        ('libyaml', 'libyaml is'),
        ('version', 'wiki is'),
        # the venv imports the package of another checkout, at the same version
        ('package', 'wiki is imported from'),
        # the constraints file moves a pin, drops one, or pins one more
        ('moved', 'pyyaml is'),
        ('dropped', 'pinned at nothing'),
        ('added', 'parity-example is absent'),
    ],
)
def test_venv_that_differs_from_its_side_is_refused(
    tmp_path: pathlib.Path,
    changed: str,
    message: str,
) -> None:
    """A side is handed out only when its venv holds exactly what is named.

    The built venv is held against an oracle file and a constraints file
    that each differ from it in one thing, and against a checkout of
    another commit: there it imports ``wiki`` at the named version from
    elsewhere, as a venv that installed a release would. Any of them would
    let a result be read as the oracle's when another interpreter, loader
    or package produced it.
    """
    named = oracle.load()
    build = named.builds['oracle']
    if changed == 'package':
        # a scratch checkout that holds the oracle's venvs, not its package
        checkout = tmp_path / 'checkout'
        checkout.mkdir()
        shutil.copy(named.checkout / 'pyproject.toml', checkout)
        (checkout / '.venv').symlink_to(named.checkout / '.venv')
        _git(checkout, 'init', '-q', '-b', 'main')
        _git(checkout, 'add', 'pyproject.toml')
        _git(checkout, 'commit', '-q', '-m', 'project')
        commit = _git(checkout, 'rev-parse', 'HEAD')
        named = dataclasses.replace(named, checkout=checkout, commit=commit)
    lines = build.constraints.read_text(encoding='utf-8').splitlines()
    if changed == 'moved':
        lines = [
            'pyyaml==0.0' if line.startswith('pyyaml==') else line for line in lines
        ]
    elif changed == 'dropped':
        lines = [line for line in lines if not line.startswith('pyyaml==')]
    elif changed == 'added':
        lines.append('parity-example==1.0')
    constraints = tmp_path / 'constraints.txt'
    constraints.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    build = dataclasses.replace(build, constraints=constraints)
    if changed in ('python', 'unicode', 'libyaml'):
        build = dataclasses.replace(build, **{changed: '0.0'})
    if changed == 'version':
        named = dataclasses.replace(named, version='0.0')
    named = dataclasses.replace(named, builds={'oracle': build})
    with pytest.raises(ValueError, match=message):
        oracle.side(named, 'oracle')


@pytest.mark.parametrize(
    argnames='changed',
    argvalues=['missing', 'moved', 'edited', 'added', 'hidden'],
)
def test_checkout_that_is_not_the_oracle_commit_is_refused(
    tmp_path: pathlib.Path,
    changed: str,
) -> None:
    """A checkout is refused unless git holds it at the oracle commit, unchanged.

    The checkout here is a scratch repository: one that is missing, one
    whose ``HEAD`` moved past the named commit, one with an edited engine
    file, one with a new test file, and one whose git config keeps new
    files out of ``git status``. Each is refused before the side's venv is
    looked at.
    """
    checkout = tmp_path / 'checkout'
    (checkout / 'wiki').mkdir(parents=True)
    (checkout / 'tests').mkdir()
    (checkout / 'wiki' / 'engine.py').write_text('VALUE = 1\n', encoding='utf-8')
    (checkout / 'tests' / 'test_engine.py').write_text('', encoding='utf-8')
    _git(checkout, 'init', '-q', '-b', 'main')
    _git(checkout, 'add', '-A')
    _git(checkout, 'commit', '-q', '-m', 'engine')
    commit = _git(checkout, 'rev-parse', 'HEAD')
    message = 'local changes'
    if changed == 'missing':
        shutil.rmtree(checkout)
        message = 'No oracle checkout'
    elif changed == 'moved':
        _git(checkout, 'commit', '-q', '--allow-empty', '-m', 'later')
        message = f'names {commit[:7]}'
    elif changed == 'edited':
        (checkout / 'wiki' / 'engine.py').write_text('VALUE = 2\n', encoding='utf-8')
    elif changed in ('added', 'hidden'):
        (checkout / 'tests' / 'test_more.py').write_text('', encoding='utf-8')
        if changed == 'hidden':
            _git(checkout, 'config', 'status.showUntrackedFiles', 'no')
    named = dataclasses.replace(oracle.load(), checkout=checkout, commit=commit)
    with pytest.raises(ValueError, match=message):
        oracle.side(named, 'oracle')


def test_ensure_makes_a_deleted_checkout_again(tmp_path: pathlib.Path) -> None:
    """A checkout whose folder was deleted is made again, not refused.

    Git still lists a worktree whose folder is gone, and adds none at its
    path unless it is told to. ``ensure`` is the only way a checkout is
    made, so it takes the path back by itself. No side is built here.
    """
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q', '-b', 'main')
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'engine')
    commit = _git(repo, 'rev-parse', 'HEAD')
    checkout = tmp_path / 'worktrees' / 'oracle'
    named = dataclasses.replace(
        oracle.load(),
        repo=repo,
        checkout=checkout,
        commit=commit,
    )
    oracle.ensure(named, names=[])
    shutil.rmtree(checkout)
    oracle.ensure(named, names=[])
    assert _git(checkout, 'rev-parse', 'HEAD') == commit


def test_check_follows_the_oracle_from_the_pending_branch_to_a_sync(
    tmp_path: pathlib.Path,
) -> None:
    """The three checks hold the oracle file level with v1 as v1 moves.

    A scratch repository walks the life of the file. It is cut from a
    pending branch that the base branch does not hold yet; the base branch
    merges that branch and then gains a fix; the branch lists the new
    commits, then syncs with the base branch and moves the oracle to the
    merged commit; and a path it has removed comes back.
    """
    repo = tmp_path / 'repo'
    repo.mkdir()
    path = repo / 'parity' / 'oracle.toml'

    def commit(name: str) -> str:
        (repo / name).write_text(f'{name}\n', encoding='utf-8')
        _git(repo, 'add', '-A')
        _git(repo, 'commit', '-q', '-m', name)
        return _git(repo, 'rev-parse', 'HEAD')

    def name(sha: str, *unmerged: str) -> None:
        rows = ''.join(
            f'[[unmerged]]\ncommit = "{item}"\nreason = "waits for the next sync"\n'
            for item in unmerged
        )
        text = f'commit = "{sha}"\nversion = "1"\nbase = "dev"\n'
        path.write_text(
            f'{text}pending = "stopgap"\n[sides]\n{rows}',
            encoding='utf-8',
        )
        _git(repo, 'add', '-A')
        _git(repo, 'commit', '-q', '-m', 'name the oracle')

    def verdicts() -> dict[str, bool]:
        return {
            verdict.name: verdict.passed for verdict in oracle.check(oracle.load(path))
        }

    def publish(branch: str) -> None:
        _git(repo, 'update-ref', f'refs/remotes/origin/{branch}', branch)

    # v1 on dev, the pending branch on top of it, and v2 cut from that branch
    _git(repo, 'init', '-q', '-b', 'dev')
    commit('engine.py')
    _git(repo, 'checkout', '-q', '-b', 'stopgap')
    pending = commit('speedup.py')
    publish('dev')
    publish('stopgap')
    _git(repo, 'checkout', '-q', '-b', 'v2')
    (repo / 'parity').mkdir()
    name(pending)
    passing = {'base': True, 'level': True, 'removed': True}
    assert verdicts() == passing
    # the base branch alone does not hold the oracle yet, so naming its tip fails
    name(_git(repo, 'rev-parse', 'origin/dev'))
    assert verdicts() == {**passing, 'base': False}
    # dev merges the pending branch, then gains a fix: v2 is two commits behind
    _git(repo, 'checkout', '-q', 'dev')
    _git(repo, 'merge', '-q', '--no-ff', '-m', 'merge', 'stopgap')
    merged = _git(repo, 'rev-parse', 'HEAD')
    fix = commit('fix.py')
    publish('dev')
    _git(repo, 'checkout', '-q', 'v2')
    name(pending)
    assert verdicts() == {**passing, 'level': False}
    name(pending, merged)
    assert verdicts() == {**passing, 'level': False}
    name(pending, merged, fix)
    assert verdicts() == passing
    # a sync merges dev into v2: the oracle is then the merged commit
    _git(repo, 'merge', '-q', '--no-ff', '-m', 'sync', 'origin/dev')
    assert verdicts() == {**passing, 'base': False}
    name(fix)
    assert verdicts() == passing
    # the branch removes a v1 path and lists it; the path may not come back
    _git(repo, 'rm', '-q', 'engine.py')
    path.with_name('removed.txt').write_text('engine.py\n', encoding='utf-8')
    commit('removal.txt')
    assert verdicts() == passing
    commit('engine.py')
    assert verdicts() == {**passing, 'removed': False}
