"""Test the ``parity.runner`` module.

The matrix runs themselves are covered through the self-check and the
command line; this module carries the planning refusals.
"""

from __future__ import annotations

import pathlib

import pytest

from parity import runner

__all__ = ['test_work_folder_below_a_wiki_is_refused']


def test_work_folder_below_a_wiki_is_refused(tmp_path: pathlib.Path) -> None:
    """A work folder under a declared wiki is refused before any snapshot.

    Every side runs under a scratch home, so a ``.wiki/settings.json``
    above the work folder -- the user's own config home included -- reads
    as an enclosing wiki, and each command run from inside the snapshot's
    wiki would fail the same way on both sides and compare equal.
    """
    (tmp_path / '.wiki').mkdir()
    (tmp_path / '.wiki' / 'settings.json').write_text('{}\n')
    corpus = runner.Corpus(label='example', checkout=tmp_path / 'checkout', root='wiki')
    with pytest.raises(ValueError, match='lies below'):
        runner.prepare(corpus, work=tmp_path / 'work')
    assert not (tmp_path / 'work').exists()
