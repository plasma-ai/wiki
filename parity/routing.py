"""Functions for reading the routing table.

The routing table lists the tests of v1 that neither the CLI suite nor the
replay holds, with the check that holds each one in their place.
"""

from __future__ import annotations

import dataclasses
import pathlib
import tomllib
from collections.abc import Collection, Mapping
from typing import Optional

__all__ = []

#: the committed file that holds the routing table
PATH = pathlib.Path(__file__).parent / 'routing.toml'
#: where a row sends its tests: to a test the second engine brings, to a
#: function of its probe, to the replay once the recorder has a form for
#: their command, or nowhere
TARGETS = ('rust', 'probe', 'replay', 'dropped')
#: the parts of v1 that a second engine does not have, which a dropped row
#: may cite
MECHANISMS = ('trust', 'hooks', 'frontmatter memo', 'loader choice', 'Python API')

# the one key of the file, and the fields of a row: its list and its texts
_KEY = 'route'
_LIST = 'tests'
_TEXTS = ('to', 'target', 'cite', 'reason')
# what separates a test's file from its name in a test id
_SCOPE = '::'


@dataclasses.dataclass(frozen=True)
class Route:
    """One row of the routing table.

    Attributes:
        tests: The ids of the tests the row routes. The id of a test
            function routes every parameter set of it.
        to: One of :data:`TARGETS`.
        target: The test or the probe function that holds the tests in
            the replay's place; empty until it exists. For a row that
            waits for the replay, the command it waits for.
        cite: What a dropped row rests on: the id of a registry entry,
            one of :data:`MECHANISMS`, or the id of the test that
            replaces the dropped ones.
        reason: Why the replay cannot hold the tests.

    """

    tests: tuple[str, ...]
    to: str
    target: str
    cite: str
    reason: str


def load(
    path: Optional[pathlib.Path] = None,
    *,
    entries: Collection[str] = (),
) -> dict[str, Route]:
    """Return the routing table of the TOML file at ``path``, by test id.

    Args:
        path: The file; the committed one when ``None``.
        entries: The ids of the registry's entries, which a dropped row
            may cite.

    Raises:
        ValueError: If the file holds another key than ``route``, if a row
            is no table, lacks a field or holds one of the wrong shape,
            if a row names no test, sends its tests to an unknown place
            or gives no reason, if a dropped row cites nothing a dropped
            row may cite, if another row cites anything, if a row that
            waits for the replay names no command, or if two rows name
            one test.

    """
    path = path or PATH
    data = tomllib.loads(path.read_text(encoding='utf-8'))
    unknown = sorted(set(data) - {_KEY})
    if unknown:
        raise ValueError(f'{path.name} has the unknown key {unknown[0]!r}.')
    rows = data.get(_KEY, [])
    if not isinstance(rows, list):
        raise ValueError(f'{path.name}: {_KEY} must be a list of tables.')
    result: dict[str, Route] = {}
    for number, row in enumerate(rows, start=1):
        where = f'{path.name}, row {number}'
        if not isinstance(row, dict):
            raise ValueError(f'{where}: must be a table, got {row!r}.')
        unknown = sorted(set(row) - {_LIST, *_TEXTS})
        if unknown:
            raise ValueError(f'{where}: has the unknown field {unknown[0]!r}.')
        tests = row.get(_LIST)
        if (
            (not isinstance(tests, list))
            or (not tests)
            or not all(isinstance(test, str) and (_SCOPE in test) for test in tests)
        ):
            raise ValueError(
                f'{where}: {_LIST} must be a list of test ids, got {tests!r}.'
            )
        texts = {name: row.get(name, '') for name in _TEXTS}
        wrong = sorted(
            name for name, text in texts.items() if not isinstance(text, str)
        )
        if wrong:
            raise ValueError(f'{where}: {wrong[0]} must be a string.')
        if texts['to'] not in TARGETS:
            raise ValueError(
                f'{where}: to must be one of {TARGETS!r}, got {texts["to"]!r}.'
            )
        if not texts['reason'].strip():
            raise ValueError(f'{where}: gives no reason.')
        cite = texts['cite']
        cited = (cite in entries) or (cite in MECHANISMS) or (_SCOPE in cite)
        if (texts['to'] == 'dropped') and not cited:
            raise ValueError(
                f'{where}: a dropped row cites a registry entry, one of'
                f' {MECHANISMS!r} or a test, got {cite!r}.'
            )
        if (texts['to'] != 'dropped') and cite:
            raise ValueError(f'{where}: only a dropped row cites, got {cite!r}.')
        if (texts['to'] == 'replay') and not texts['target'].strip():
            raise ValueError(
                f'{where}: a row that waits for the replay names the command'
                f' it waits for as its target.'
            )
        route = Route(tests=tuple(tests), **texts)
        for test in tests:
            if test in result:
                raise ValueError(f'{where}: {test} is routed twice.')
            result[test] = route
    return result


def find(routes: Mapping[str, Route], test: str) -> Optional[Route]:
    """Return the row of ``routes`` that names ``test`` or its test function, if any."""
    return routes.get(test) or routes.get(function(test))


def function(test: str) -> str:
    """Return the id of a test without its parameters: the test function's."""
    return test.partition('[')[0]
