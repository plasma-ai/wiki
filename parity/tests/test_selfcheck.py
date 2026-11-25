"""Test the ``parity.selfcheck`` module.

The self-check is the proof that a result of the harness counts: one
engine against itself must show nothing, and a side doctored in one way
must show exactly that way. Each comparison rule has one doctored check.
"""

from __future__ import annotations

import pathlib
import shutil

import pytest

from parity import matrix, runner, selfcheck, side

from .conftest import NOW

__all__ = [
    'test_one_engine_against_itself_shows_no_difference',
    'test_doctored_side_shows_exactly_its_difference',
]

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='requires git')


def test_one_engine_against_itself_shows_no_difference(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
) -> None:
    """The whole matrix is identical between two hash seeds of one engine.

    A difference here is the harness comparing something that is not the
    engine's to decide, or the engine answering by hash order; either
    way no other result of the harness could be trusted.
    """
    plan = runner.prepare(corpus, work=tmp_path / 'work')
    check = selfcheck.clean(plan, engine, now=NOW)
    assert check.passed, check.found
    # every stage ran, and the commands did real work on the sampled wiki
    stages = {stage['name']: stage for stage in check.report['stages']}
    assert list(stages) == list(matrix.STAGES)
    codes = {
        run['exit']
        for stage in stages.values()
        for case in stage['cases']
        for run in case['runs'].values()
    }
    assert codes == {0, 1, 2}
    # every search and match that is built to find something found it
    assert [stage['no_hit'] for stage in stages.values()] == [[]] * len(stages)


@pytest.mark.parametrize(
    argnames='name',
    argvalues=[name for name, _, _ in selfcheck.CHECKS],
)
def test_doctored_side_shows_exactly_its_difference(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
    name: str,
) -> None:
    """One changed byte, mode, code, file, cache entry or record is reported, alone.

    A missing report is a rule that does not fire; an extra one is a
    difference the harness invents. Both would make a clean run
    meaningless.
    """
    plan = runner.prepare(corpus, work=tmp_path / 'work')
    check = selfcheck.doctored(plan, engine, name, now=NOW)
    assert check.expected, 'every doctored check expects a difference'
    assert sorted(check.found) == sorted(check.expected)
