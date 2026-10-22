"""Test the ``wiki.cli.cmd.wiki`` module.

The commands are covered end to end by ``test_wiki_commands``, which
drives the console script; this module pins the merge driver's index
rewrite in-process, where a file that resists the write can be set up.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from wiki.cli import cmd
from wiki.core.wiki import Wiki

__all__ = ['test_merge_rewrite_replaces_the_index_whole_or_not_at_all']

# a merged index as the driver's script leaves it: theirs' row below ours'
_MERGED = (
    '---\nname: core\ndesc: Core concepts.\n---\n\n# core\n\n'
    '[[_index|..]]\n\n[[core/zeta|zeta]]: The zeta page.\n\n'
    '[[core/alpha|alpha]]: The alpha page.\n\n***\n\nText.\n'
)


@pytest.mark.parametrize(
    argnames='case',
    argvalues=[
        # as root, a write to a read-only file succeeds either way, so the case
        # tells the whole-file replacement from an in-place write only without
        pytest.param(
            'read-only',
            marks=pytest.mark.skipif(os.geteuid() == 0, reason='needs a non-root user'),
        ),
        'failing-write',
    ],
)
def test_merge_rewrite_replaces_the_index_whole_or_not_at_all(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """The rewrite of a clean index merge lands whole or leaves it as merged.

    The rewrite runs after the merge script, whose exit code the driver
    passes to git. A read-only merged file is still replaced, and a
    failed write leaves the script's bytes in place without raising:
    raised, it would exit 2, and git would read a conflict into a file
    the write had cut short.
    """
    # the merged index git hands the driver, its rows out of update's order
    path = tmp_path / '.merge_file_a1b2c3'
    path.write_text(_MERGED, encoding='utf-8')
    engine = Wiki(tmp_path)
    canonical = engine.canonicalize_index(_MERGED)
    assert canonical != _MERGED

    # a read-only file is replaced whole
    if case == 'read-only':
        path.chmod(0o444)
        cmd.wiki._canonicalize_index(engine, path)
        assert path.read_text(encoding='utf-8') == canonical
    # a write that fails leaves the merged bytes and no temp file behind
    elif case == 'failing-write':

        def refuse(src: pathlib.Path, dst: pathlib.Path) -> None:
            """Fail the swap into place, as a full disk would."""
            raise OSError('replace refused')

        monkeypatch.setattr(os, 'replace', refuse)
        cmd.wiki._canonicalize_index(engine, path)
        assert path.read_text(encoding='utf-8') == _MERGED
        assert list(tmp_path.iterdir()) == [path]
