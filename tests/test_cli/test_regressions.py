"""Regression tests pinning ``wiki`` CLI behaviors."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

__all__ = [
    'test_update_adds_missing_name',
    'test_init_guards_existing_wiki',
    'test_not_found_message_is_clean',
    'test_broken_pipe_is_quiet',
    'test_update_path_joins_name',
    'test_fresh_wiki_lints_clean',
]

# prefer the console script beside the running interpreter (this checkout's
# venv) over PATH, which may resolve a different install or a broken shim
VENV_BIN = pathlib.Path(sys.executable).parent
WIKI = shutil.which('wiki', path=VENV_BIN) or shutil.which('wiki')
pytestmark = pytest.mark.skipif(
    WIKI is None,
    reason='wiki console script not installed',
)


# ------ update, init, and output behaviors


def test_update_adds_missing_name(tmp_path: pathlib.Path) -> None:
    """Update adds a ``name:`` field to a page that lacks one."""
    root = _new_wiki(tmp_path)
    core = root / 'core'
    core.mkdir()
    page = core / 'design.md'
    page.write_text(
        '---\ndesc: A design doc.\n---\n# Design\n\nBody text here.\n',
        encoding='utf-8',
    )
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    assert 'name:' in page.read_text(encoding='utf-8')


def test_init_guards_existing_wiki(tmp_path: pathlib.Path) -> None:
    """Re-running init on an existing wiki reports, not silently updates."""
    root = _new_wiki(tmp_path)
    result = _wiki(tmp_path, 'init', '--path', str(root))
    assert 'already initialized' in result.stdout.lower()


def test_not_found_message_is_clean(tmp_path: pathlib.Path) -> None:
    """The no-wiki message has no mid-path repr quotes."""
    result = _wiki(tmp_path, 'map')
    combined = result.stdout + result.stderr
    assert "wiki/'_index.md'" not in combined
    assert 'wiki/_index.md' in combined


def test_broken_pipe_is_quiet(tmp_path: pathlib.Path) -> None:
    """A downstream reader closing early (``read ... | head``) stays quiet.

    Piping into ``head``/``less`` is the default way to skim large output, so
    the closed pipe must end the command successfully instead of spilling
    'Error: [Errno 32] Broken pipe' and failing the pipeline.
    """
    root = _new_wiki(tmp_path)
    # a page far past the 64KB pipe buffer, so the write outlives the reader
    body = 'filler prose line\n' * 30_000
    (root / 'big.md').write_text(
        f'---\nname: big\ndesc: Big.\n---\n\n# big\n\n{body}',
        encoding='utf-8',
    )
    # pipefail surfaces the wiki side's exit status through the pipeline
    script = f'set -o pipefail; "{WIKI}" read big --path "{root}" | head -n 2'
    result = subprocess.run(
        ['bash', '-c', script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert 'name: big' in result.stdout
    assert 'Broken pipe' not in result.stderr
    assert 'BrokenPipeError' not in result.stderr


# ------ update name and fresh-wiki lint behavior


def test_update_path_joins_name(tmp_path: pathlib.Path) -> None:
    """Update intentionally sets name/H1 to the path-joined name."""
    root = _new_wiki(tmp_path)
    core = root / 'core'
    core.mkdir()
    page = core / 'mytitle.md'
    page.write_text(
        '---\nname: MyTitle\ndesc: A page.\n---\n# MyTitle\n\nBody.\n',
        encoding='utf-8',
    )
    _wiki(root, 'update', '--path', str(root))
    text = page.read_text(encoding='utf-8')
    assert 'name: core/mytitle' in text
    assert '# core/mytitle' in text


def test_fresh_wiki_lints_clean(tmp_path: pathlib.Path) -> None:
    """A just-initialized wiki passes lint (soft notes go to stderr)."""
    root = _new_wiki(tmp_path)
    assert _wiki(root, 'lint', '--path', str(root)).returncode == 0


# ------ helpers


def _wiki(cwd: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """Run the ``wiki`` CLI in ``cwd`` and capture output."""
    return subprocess.run(
        [WIKI, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _new_wiki(tmp_path: pathlib.Path) -> pathlib.Path:
    """Initialize an empty wiki under ``tmp_path`` and return its root."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    return root
