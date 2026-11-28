"""Shared fixtures for the parity harness tests.

The harness is tested the way it is used: real sides run v1 from the oracle
checkout on snapshots of a small checkout, and the tests read the differences
the harness reports. ``python -m parity oracle ensure`` builds that checkout
and its venvs; the suite fails without them.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from parity import oracle, runner, side

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
#: the instant every side's clock is fixed at
NOW = '2026-01-02T03:04:05Z'
#: a side that runs the command it is given and words the text of `re` its
#: own way, as a second engine may
WORDED = """
import subprocess, sys
result = subprocess.run(sys.argv[1:], capture_output=True)
stderr = result.stderr.replace(b'unterminated subpattern', b'unclosed group')
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(stderr)
sys.exit(result.returncode)
"""
#: the version the manifest of a checkout from :func:`_workspace` states
VERSION = '2.0.0-dev.1'
# a binary that leaves the identity record of its build, which a file beside
# it holds, and prints its version, as the Rust engine does
_BINARY = f"""#!{sys.executable}
import json, os, pathlib
record = json.loads(pathlib.Path(__file__).with_name('build.json').read_text())
with open(os.environ[{side.TRACE!r}], 'a') as handle:
    handle.write(json.dumps(record) + chr(10))
print(record['version'])
"""
# gates the tests that need git: the oracle checkout and the committed corpus
_needs_git = pytest.mark.skipif(shutil.which('git') is None, reason='requires git')
# gates the tests of names only a file system that keeps bytes holds: Linux
# has one, and a run there that cannot write them fails instead of skipping
_needs_bytes = pytest.mark.skipif(
    sys.platform != 'linux',
    reason='needs names that are no valid UTF-8',
)
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
    # git packs nothing behind a test's back: a commit would start a
    # maintenance run whose lock file comes and goes while a fixture copies
    # the repository
    quiet = ['-c', 'gc.auto=0', '-c', 'maintenance.auto=false']
    result = subprocess.run(
        ['git', '-C', f'{cwd}', *identity, *quiet, *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _declare(folder: pathlib.Path, corpus: runner.Corpus) -> pathlib.Path:
    """Write a corpora file that declares ``corpus`` and return its path."""
    path = folder / 'corpora.toml'
    text = f'[{corpus.label}]\ncheckout = "{corpus.checkout}"\nroot = "{corpus.root}"\n'
    path.write_text(text, encoding='utf-8')
    return path


def _workspace(folder: pathlib.Path) -> pathlib.Path:
    """Make a committed checkout shaped like the Rust workspace and return it.

    It holds a manifest that states :data:`VERSION`, one source file and one
    test file of a crate, and one embedded file. Its folder's name holds a
    space, which cargo escapes in the list of a binary's inputs.
    """
    repo = folder / 'a checkout'
    files = {
        'Cargo.toml': f'[workspace.package]\nversion = "{VERSION}"\n',
        '.gitignore': '/target\n',
        'crates/wiki-cli/src/lib.rs': '// the crate\n',
        'crates/wiki-cli/tests/it/main.rs': '// its tests\n',
        'wiki/skills/wiki/SKILL.md': 'The skill.\n',
    }
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    _git(repo, 'init', '-q', '-b', 'main')
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', 'workspace')
    return repo


def _build(repo: pathlib.Path) -> None:
    """Write the release binary of ``repo`` as cargo builds it at ``HEAD``.

    Beside the binary lies cargo's list of what it was built from: the
    crate's source file and the folder of embedded files, and no test file.
    """
    path = repo / 'target' / 'release' / 'wiki'
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        'engine': 'rust',
        'version': VERSION,
        'build': _git(repo, 'rev-parse', 'HEAD'),
    }
    path.with_name('build.json').write_text(json.dumps(record), encoding='utf-8')
    inputs = [repo / 'crates' / 'wiki-cli' / 'src' / 'lib.rs', repo / 'wiki' / 'skills']
    target, *listed = (f'{item}'.replace(' ', '\\ ') for item in (path, *inputs))
    text = f'{target}: {" ".join(listed)}\n'
    path.with_name('wiki.d').write_text(text, encoding='utf-8')
    # NOTE: the binary is written last, so no input is newer than it
    path.write_text(_BINARY, encoding='utf-8')
    path.chmod(0o755)


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
    built with the real CLI and then committed, as a wiki kept under git
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


@pytest.fixture(scope='session')
def history(
    tmp_path_factory: pytest.TempPathFactory,
    corpus: runner.Corpus,
    engine: side.Side,
) -> runner.Corpus:
    """Return a checkout whose history holds three merges of a small wiki.

    The first joins a branch that the other side did not move on from, so
    git calls no driver. The second joins two branches that each added a
    page to one folder, so both changed its index and git calls the driver.
    The third joins two edits of one page's body. The example wiki maps
    its pages to the driver too, whose text merge cannot settle them: the
    merge is left with a conflict, and ``update`` refuses the tree.
    Each merge commit keeps its first parent's tree: a replay reads the
    parents of a merge and never its tree. READ-ONLY: a replay runs in
    repositories of its own.
    """
    checkout = tmp_path_factory.mktemp('merges') / 'checkout'
    shutil.copytree(corpus.checkout, checkout, symlinks=True)
    root = checkout / corpus.root
    home = checkout.parent / 'home'
    home.mkdir()
    env = side.environment(engine, home, now=NOW)

    def page(relpath: str, text: str) -> None:
        """Write a page, converge the wiki and commit."""
        block = f'---\ndesc: About {relpath}.\n---\n\n'
        (root / relpath).write_text(f'{block}{text}\n', encoding='utf-8')
        args = ['update', '--path', f'{root}']
        updated = side.run(engine, args, cwd=home, env=env)
        assert updated.code == 0, updated.stderr
        _git(checkout, 'add', '-A')
        _git(checkout, 'commit', '-q', '-m', relpath)

    # map every index to the driver, as `wiki init` does
    attributes = '**/_index.md merge=wiki\n'
    (checkout / '.gitattributes').write_text(attributes, encoding='utf-8')
    _git(checkout, 'add', '-A')
    _git(checkout, 'commit', '-q', '-m', 'attributes')
    # a branch the main line did not move on from
    _git(checkout, 'checkout', '-q', '-b', 'one')
    page('guides/alpha.md', '# Alpha\n\nThe first page.')
    _git(checkout, 'checkout', '-q', 'main')
    _git(checkout, 'merge', '-q', '--no-ff', '-s', 'ours', '-m', 'one', 'one')
    # two branches that each add a page to one folder
    _git(checkout, 'checkout', '-q', '-b', 'two')
    page('guides/beta.md', '# Beta\n\nThe second page.')
    _git(checkout, 'checkout', '-q', 'main')
    page('guides/gamma.md', '# Gamma\n\nThe third page.')
    _git(checkout, 'merge', '-q', '--no-ff', '-s', 'ours', '-m', 'two', 'two')
    # two edits of one page's body
    _git(checkout, 'checkout', '-q', '-b', 'three')
    page('core/design.md', '# Design\n\nThe branch words it one way.')
    _git(checkout, 'checkout', '-q', 'main')
    page('core/design.md', '# Design\n\nThe main line words it another way.')
    _git(checkout, 'merge', '-q', '--no-ff', '-s', 'ours', '-m', 'three', 'three')
    return runner.Corpus(label='example', checkout=checkout, root=corpus.root)
