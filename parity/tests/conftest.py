"""Shared fixtures for the parity harness tests.

The harness is tested the way it is used: real sides run v1 from the oracle
checkout on snapshots of a small checkout, and the tests read the differences
the harness reports. ``python -m parity oracle ensure`` builds that checkout
and its venvs; the suite fails without them.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

from parity import oracle, runner, side

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
#: the instant every side's clock is fixed at
NOW = '2026-01-02T03:04:05Z'
# gates the tests that need git: the oracle checkout and the committed corpus
_needs_git = pytest.mark.skipif(shutil.which('git') is None, reason='requires git')
# gates the tests that rely on a permission denial
_needs_unprivileged = pytest.mark.skipif(
    os.geteuid() == 0,
    reason='needs a non-root user',
)


def _git(cwd: pathlib.Path, *args: str) -> str:
    """Run git in ``cwd`` under no config but the repository's own."""
    env = dict(os.environ)
    # the developer's ~/.gitconfig (commit.gpgsign, core.hooksPath, ...) must
    # not perturb the commits and merges a test makes
    env['GIT_CONFIG_GLOBAL'] = os.devnull
    env['GIT_CONFIG_SYSTEM'] = os.devnull
    identity = ['-c', 'user.name=t', '-c', 'user.email=t@t']
    result = subprocess.run(
        ['git', '-C', f'{cwd}', *identity, *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture(scope='session')
def engine() -> side.Side:
    """Return the oracle side: v1 at the oracle commit, under its own venv."""
    return oracle.side(oracle.load(), 'oracle')


@pytest.fixture(scope='session')
def corpus(
    tmp_path_factory: pytest.TempPathFactory,
    engine: side.Side,
) -> runner.Corpus:
    """Return a committed checkout that holds a small converged wiki.

    The wiki is the repository's example plus two indexed folders, one
    nested folder and a non-Markdown file, so the matrix has folders to
    scope to and pages to read. Every page has a desc and one has tags,
    so each search and match of the matrix has something to find. It is
    built with the real CLI and then committed, as a campaign checkout
    is. READ-ONLY: the harness snapshots the checkout and runs only in
    its clones.
    """
    # copy the example wiki
    checkout = tmp_path_factory.mktemp('parity') / 'checkout'
    root = checkout / 'hello'
    ignore = shutil.ignore_patterns('cache', '.obsidian')
    shutil.copytree(REPO_ROOT / 'examples' / 'hello', root, ignore=ignore)
    # add the folders through the CLI
    home = checkout.parent / 'home'
    home.mkdir()
    env = side.environment(engine, home, now=NOW)
    for folder in ('guides', 'guides/deep', 'core'):
        args = ['new', folder, '--desc', f'About {folder}.', '--content', 'Text.']
        made = side.run(engine, [*args, '--path', f'{root}'], cwd=home, env=env)
        assert made.code == 0, made.stderr
    # write the pages and a non-Markdown file
    pages = {
        'guides/setup.md': (
            'desc: Installing the widget.\ntags: [install, widget]',
            '# Setup\n\nRun the installer to set up the widget.\n',
        ),
        'guides/deep/notes.md': (
            'desc: Notes on render order.',
            '# Notes\n\nWidgets render after the installer.\n',
        ),
        'core/design.md': (
            'desc: The widget subsystem.',
            '# Design\n\nThe widget subsystem handles rendering.\n',
        ),
    }
    for relpath, (fields, body) in pages.items():
        text = f'---\n{fields}\n---\n\n{body}'
        (root / relpath).write_text(text, encoding='utf-8')
    snippet = 'widget appears in plain code here\n'
    (root / 'core' / 'snippet.txt').write_text(snippet, encoding='utf-8')
    # converge the wiki
    updated = side.run(engine, ['update', '--path', f'{root}'], cwd=home, env=env)
    assert updated.code == 0, updated.stderr
    # commit under no config but the repository's own
    _git(checkout, 'init', '-q', '-b', 'main')
    _git(checkout, 'add', '-A')
    _git(checkout, 'commit', '-q', '-m', 'wiki')
    return runner.Corpus(label='example', checkout=checkout, root='hello')
