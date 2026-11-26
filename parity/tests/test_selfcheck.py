"""Test the ``parity.selfcheck`` module.

The self-check is the proof that a result of the harness counts: one
engine against itself must show nothing, and a side doctored in one way
must show exactly that way. Each doctored check proves one comparison
rule; ``test_cache``, ``test_compare`` and ``test_manifest`` carry the
rules no doctored check reaches.
"""

from __future__ import annotations

import dataclasses
import pathlib
import sys
from typing import Optional

import pytest

from parity import matrix, runner, selfcheck, side
from parity.matrix import Case

from .conftest import NOW, _needs_git

__all__ = [
    'test_one_engine_against_itself_shows_no_difference',
    'test_doctored_side_shows_exactly_its_difference',
    'test_excused_command_fails_a_check',
]

pytestmark = _needs_git

# a side that runs the command it is given and, under the hash seed 2, words
# the text of `re` its own way
_WORDED = """
import os, subprocess, sys
result = subprocess.run(sys.argv[1:], capture_output=True)
stderr = result.stderr
if os.environ['PYTHONHASHSEED'] == '2':
    stderr = stderr.replace(b'unterminated subpattern', b'unclosed group')
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(stderr)
sys.exit(result.returncode)
"""


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


@pytest.mark.parametrize(
    argnames='name',
    argvalues=[None, 'stdout'],
    ids=['clean', 'doctored'],
)
def test_excused_command_fails_a_check(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
    name: Optional[str],
) -> None:
    """A command the registry excuses fails a check of one engine against itself.

    The side words a library's text by its hash seed, which the registry
    excuses in a second engine. One engine has nothing registered against
    itself: the clean check fails though it reports no difference, and a
    doctored check fails though it reports exactly its own.
    """
    plan = runner.prepare(corpus, work=tmp_path / 'work')
    # put an invalid pattern before the commands of the stage a check doctors
    (full,) = (stage for stage in plan.stages if stage.name == 'full')
    invalid = Case(name='match (', args=('match', '('), codes=frozenset({2}))
    full = dataclasses.replace(full, cases=(invalid, *full.cases))
    staged = dataclasses.replace(plan, stages=(full,))
    worded = dataclasses.replace(
        engine,
        argv=(sys.executable, '-c', _WORDED, *engine.argv),
    )
    if name is None:
        check = selfcheck.clean(staged, worded, now=NOW)
    else:
        check = selfcheck.doctored(staged, worded, name, now=NOW)
    assert sorted(check.found) == sorted(check.expected)
    assert [(item.case, item.entry) for item in check.excused] == [('match (', 'D1')]
    assert not check.passed
