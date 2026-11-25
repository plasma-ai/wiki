"""Test the ``parity.runner`` module.

The matrix runs themselves are covered through the self-check and the
command line; this module carries the planning refusals and the rules
that hold for one side by itself.
"""

from __future__ import annotations

import pathlib
import shutil
import sys

import pytest

from parity import runner, side

from .conftest import NOW

__all__ = [
    'test_work_folder_below_a_wiki_is_refused',
    'test_unknown_stage_is_refused',
    'test_sides_that_fail_alike_are_reported',
]

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='requires git')


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


def test_unknown_stage_is_refused(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """A stage name the plan does not hold is an error, not an empty run.

    A run of no stage finds no difference, so a mistyped name would read
    as a pass.
    """
    plan = runner.prepare(corpus, work=tmp_path / 'work')
    with pytest.raises(ValueError, match='No stage is named'):
        runner.run(plan, [], now=NOW, only=['query', 'wirte'])


def test_sides_that_fail_alike_are_reported(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """Two sides that run no wiki command do not pass by agreeing.

    Both sides exit 1 with no output on every command, so no stream, exit
    code or tree differs between them. Each command that is built to
    succeed, or to fail as an error, is still reported on each side.
    """
    argv = (sys.executable, '-c', 'raise SystemExit(1)')
    broken = [side.Side(name=name, argv=argv, notice=False) for name in ('one', 'two')]
    plan = runner.prepare(corpus, work=tmp_path / 'work')
    result = runner.run(plan, broken, now=NOW, only=['query'])
    assert {difference.aspect for difference in result.differences} == {'unexpected'}
    reported = {(difference.run, difference.case) for difference in result.differences}
    for name in ('one', 'two'):
        assert {(name, 'map'), (name, 'match (')} <= reported
