"""Regression tests pinning corrected ``wiki`` CLI behaviors."""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

__all__ = [
    'test_update_adds_missing_name',
    'test_init_guards_existing_wiki',
    'test_not_found_message_is_clean',
    'test_update_path_joins_name',
    'test_fresh_wiki_lints_clean',
]

WIKI = shutil.which('wiki')
pytestmark = pytest.mark.skipif(
    WIKI is None,
    reason='wiki console script not on PATH',
)


# ------ fixed bugs


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
