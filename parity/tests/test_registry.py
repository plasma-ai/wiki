"""Test the ``parity.registry`` module and the registry check.

The registry is the one list of differences a comparison may excuse. The
file is held to the classifiers the harness has, and each classifier is
proved the way the comparison rules are: v1 against a stand-in that prints
what a second engine may print, once inside the entry's scope and once just
outside it.
"""

from __future__ import annotations

import dataclasses
import pathlib
import shutil
import sys

import pytest

from parity import classify, registry, runner, selfcheck, side, standin

from .conftest import NOW

__all__ = [
    'test_registry_file_is_held_to_the_classifiers',
    'test_registry_file_that_breaks_a_rule_is_refused',
    'test_release_waits_for_every_entry',
    'test_every_classifier_has_fixtures_on_both_sides_of_its_scope',
    'test_fixtures_are_excused_or_reported',
    'test_reference_without_a_judge_excuses_no_judged_difference',
    'test_stand_in_that_changes_nothing_is_an_error',
]

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='requires git')

# one well-formed entry, as the registry file spells it
_ENTRY = """
[D1]
scope = "A library's text."
reason = "It belongs to the library."
status = "approved"
question = [7]
check = "classifier"
"""


def test_registry_file_is_held_to_the_classifiers() -> None:
    """The committed file loads, and names exactly the classifiers there are.

    A classifier with no entry would excuse a difference nobody registered,
    and an entry that claims a classifier the harness lacks would pass as
    checked.
    """
    entries = registry.load()
    named = {id for id, entry in entries.items() if entry.check == 'classifier'}
    assert named == set(classify.CLASSIFIERS)
    assert all(entry.status in registry.STATUSES for entry in entries.values())


@pytest.mark.parametrize(
    argnames=('old', 'new', 'message'),
    argvalues=[
        ('status = "approved"', 'status = "agreed"', 'has the status'),
        ('question = [7]\n', '', 'cites no question'),
        ('reason = "It belongs to the library."\n', '', 'has no reason'),
        ('check = "classifier"', 'check = "manual"', 'has the check'),
        ('check = "classifier"', 'check = "none"', 'does not say why'),
        ('check = "classifier"', 'check = "test"', 'names the classifiers'),
        ('[D1]', '[D11]', 'names the classifiers'),
    ],
    ids=['status', 'question', 'field', 'check', 'unchecked', 'missing', 'unknown'],
)
def test_registry_file_that_breaks_a_rule_is_refused(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    old: str,
    new: str,
    message: str,
) -> None:
    """An entry outside the registry's rules stops the harness before any run.

    Each file differs from a well-formed one in one thing: a status or a
    check the registry does not have, an approval that cites no question,
    a missing field, an unchecked entry that does not say why, and a set
    of classifier entries that is not the set of classifiers.
    """
    monkeypatch.setattr(classify, 'CLASSIFIERS', {'D1': classify.library_text})
    path = tmp_path / 'differences.toml'
    path.write_text(_ENTRY, encoding='utf-8')
    assert list(registry.load(path)) == ['D1']
    path.write_text(_ENTRY.replace(old, new), encoding='utf-8')
    with pytest.raises(ValueError, match=message):
        registry.load(path)


def test_release_waits_for_every_entry() -> None:
    """A proposed entry, or one whose tests are not written, holds a release.

    An approved entry with a classifier or a named test holds nothing.
    """
    entries = registry.load()
    ready = dataclasses.replace(entries['D4'], tests=('refusal',))
    proposed = dataclasses.replace(entries['D1'], status='proposed', question=())
    untested = dataclasses.replace(entries['D4'], tests=())
    waits = registry.unready({'D1': proposed, 'D2': entries['D2'], 'D4': untested})
    assert waits == ['D1 is proposed', 'D4 names no test']
    assert registry.unready({'D2': entries['D2'], 'D4': ready}) == []


def test_every_classifier_has_fixtures_on_both_sides_of_its_scope() -> None:
    """Each classifier is checked on a difference it excuses and on one it must not.

    Without the second a classifier that excuses everything would pass.
    """
    for id in classify.CLASSIFIERS:
        chosen = [fixture for fixture in selfcheck.FIXTURES if fixture.entry == id]
        assert any(not fixture.shows for fixture in chosen), id
        assert any(fixture.shows for fixture in chosen), id
    assert {fixture.tree for fixture in selfcheck.FIXTURES} == set(selfcheck.TREES)


@pytest.mark.parametrize('tree', list(selfcheck.TREES))
def test_fixtures_are_excused_or_reported(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
    tree: str,
) -> None:
    """A registered difference is excused under its entry, and no other is.

    The stand-in prints another text where a library wrote v1's, another
    layout for a usage error and a help page, and a refusal where v1 ran.
    Each is excused only inside its entry's scope: a changed wiki-written
    prefix or parameter message and a refusal of supported input are still
    reported, with exactly the differences they show.
    """
    plan = runner.prepare(corpus, work=tmp_path / 'work')
    check = selfcheck.fixtures(plan, engine, tree, now=NOW)
    assert check.registered
    assert sorted(check.excused) == sorted(check.registered)
    assert sorted(check.found) == sorted(check.expected)
    assert check.passed
    # a command excused where none is expected fails a check
    assert not dataclasses.replace(check, registered=()).passed


def test_reference_without_a_judge_excuses_no_judged_difference(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
) -> None:
    """Without the reference's interpreter to ask, a refusal stays a difference.

    Whether an input is outside the supported set is the oracle's to say.
    A reference side with no judge leaves that unsaid, so the refusals of
    the stand-in are reported, the one of a supported construct among
    them, while the layout of a usage error needs no judge and stays
    excused.
    """
    plan = runner.prepare(corpus, work=tmp_path / 'work')
    unjudged = dataclasses.replace(engine, judge=())
    check = selfcheck.fixtures(plan, unjudged, 'snapshot', now=NOW)
    excused = {item.case: item.entry for item in check.excused}
    assert excused == {
        'query text': 'D1',
        'parser message': 'D2',
        'parameter message': 'D2',
        'help page': 'D2',
    }
    reported = {difference.case for difference in check.found}
    assert {'pattern text', 'regex construct', 'supported construct'} <= reported


def test_stand_in_that_changes_nothing_is_an_error(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """An edit whose expression finds nothing fails the stand-in.

    A stand-in that prints the real side's words would compare equal, and
    a fixture built on it would prove nothing.
    """
    real = [sys.executable, '-c', 'print("text")']
    edits = '[[[], {"stdout": ["absent", "other"]}]]'
    with pytest.raises(ValueError, match='matches nothing'):
        standin.main([edits, f'{len(real)}', *real])
    edits = '[[[], {"stdout": ["text", "other"], "exit": 3}]]'
    assert standin.main([edits, f'{len(real)}', *real]) == 3
    assert capfd.readouterr().out == 'other\n'
