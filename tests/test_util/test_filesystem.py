"""Test the ``wiki.util.filesystem`` module."""

from __future__ import annotations

import os
import pathlib

import pytest

from wiki.util.filesystem import write_atomic

__all__ = [
    'test_write_atomic_writes_lf_bytes',
    'test_write_atomic_preserves_mode',
    'test_write_atomic_failure_discards_temp',
]


def test_write_atomic_writes_lf_bytes(tmp_path: pathlib.Path) -> None:
    """Written text lands byte-exact, with LF line endings on every platform."""
    target = tmp_path / 'page.md'
    write_atomic(target, 'alpha\nbeta\n')
    assert target.read_bytes() == b'alpha\nbeta\n'


def test_write_atomic_preserves_mode(tmp_path: pathlib.Path) -> None:
    """A rewrite keeps the target's permission bits."""
    target = tmp_path / 'page.md'
    write_atomic(target, 'first\n')
    os.chmod(target, 0o600)
    write_atomic(target, 'second\n')
    assert target.read_text(encoding='utf-8') == 'second\n'
    assert target.stat().st_mode & 0o777 == 0o600


def test_write_atomic_failure_discards_temp(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed swap keeps the old contents and leaves no temp file behind.

    The staging temp is dot-prefixed, so even mid-write it never shows
    up in a directory walk of the wiki tree.
    """
    target = tmp_path / 'page.md'
    write_atomic(target, 'original\n')

    # fail the swap after the temp file is fully staged
    staged = []

    def refuse(src: str, dst: pathlib.Path) -> None:
        """Record the staged temp name and fail as if the disk refused."""
        staged.append(pathlib.Path(src).name)
        raise OSError('replace refused')

    monkeypatch.setattr(os, 'replace', refuse)
    with pytest.raises(OSError, match='replace refused'):
        write_atomic(target, 'updated\n')
    # the staged temp was dot-prefixed and is discarded; the target survives
    assert staged[0].startswith('.page.md.')
    assert target.read_text(encoding='utf-8') == 'original\n'
    assert list(tmp_path.iterdir()) == [target]
