"""Regression tests pinning ``wiki`` CLI behaviors."""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from .conftest import WIKI, _wiki

__all__ = [
    'test_not_found_message_is_clean',
    'test_broken_pipe_is_quiet',
]

pytestmark = pytest.mark.skipif(
    WIKI is None,
    reason='wiki console script not installed',
)


# ------ output behaviors


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


# ------ helpers


def _new_wiki(tmp_path: pathlib.Path) -> pathlib.Path:
    """Initialize an empty wiki under ``tmp_path`` and return its root."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    return root
