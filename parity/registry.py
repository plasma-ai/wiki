"""Functions for reading the registry of differences."""

from __future__ import annotations

import dataclasses
import pathlib
import tomllib
from collections.abc import Mapping
from typing import Optional

from . import classify

__all__ = [
    'PATH',
    'STATUSES',
    'CHECKS',
    'Entry',
    'load',
    'unready',
]

#: the registry file
PATH = pathlib.Path(__file__).parent / 'differences.toml'
#: what an entry's status may be
STATUSES = ('proposed', 'approved')
#: what checks an entry: a classifier, tests, or nothing
CHECKS = ('classifier', 'test', 'none')


@dataclasses.dataclass(frozen=True)
class Entry:
    """One registered difference.

    Attributes:
        id: Id of the entry.
        scope: What differs, and what must still match.
        reason: Why the difference is registered and not matched.
        status: ``proposed`` or ``approved``.
        question: Numbers of the questions whose Decision lines approve
            the entry; empty for a proposed one.
        check: ``classifier``, ``test`` or ``none``.
        tests: Ids of the tests that pin the entry, or a clause of it.
        unchecked: Why nothing checks an entry whose check is ``none``.

    """

    id: str
    scope: str
    reason: str
    status: str
    question: tuple[int, ...]
    check: str
    tests: tuple[str, ...] = ()
    unchecked: str = ''


def load(path: Optional[pathlib.Path] = None) -> dict[str, Entry]:
    """Return the entries of the registry file, by id.

    Raises:
        ValueError: If an entry lacks a field or holds a value outside
            the allowed ones, if an approved entry cites no question, or
            if the entries checked by a classifier are not exactly the
            ids :data:`classify.CLASSIFIERS` holds.

    """
    path = path or PATH
    data = tomllib.loads(path.read_text(encoding='utf-8'))
    result = {}
    for id, table in data.items():
        try:
            entry = Entry(
                id=id,
                scope=table['scope'],
                reason=table['reason'],
                status=table['status'],
                question=tuple(table.get('question', ())),
                check=table['check'],
                tests=tuple(table.get('tests', ())),
                unchecked=table.get('unchecked', ''),
            )
        except KeyError as e:
            raise ValueError(f'{path.name}: {id} has no {e.args[0]}.') from None
        if entry.status not in STATUSES:
            raise ValueError(f'{path.name}: {id} has the status {entry.status!r}.')
        if entry.check not in CHECKS:
            raise ValueError(f'{path.name}: {id} has the check {entry.check!r}.')
        if (entry.status == 'approved') and not entry.question:
            raise ValueError(f'{path.name}: {id} is approved and cites no question.')
        if (entry.check == 'none') and not entry.unchecked:
            raise ValueError(f'{path.name}: {id} does not say why nothing checks it.')
        result[id] = entry
    # every classifier excuses a registered difference, and no other
    named = {id for id, entry in result.items() if entry.check == 'classifier'}
    if named != set(classify.CLASSIFIERS):
        raise ValueError(
            f'{path.name} names the classifiers {sorted(named)!r}, and the'
            f' harness has {sorted(classify.CLASSIFIERS)!r}.'
        )
    return result


def unready(entries: Mapping[str, Entry]) -> list[str]:
    """Return what keeps the registry from a release, one line per finding.

    A release needs every entry approved, and every entry that tests check
    to name at least one.
    """
    result = []
    for id, entry in entries.items():
        if entry.status != 'approved':
            result.append(f'{id} is {entry.status}')
        if (entry.check == 'test') and not entry.tests:
            result.append(f'{id} names no test')
    return result
