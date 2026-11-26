"""Functions for the kind list.

The kind list gives every notice kind and every issue kind of v1 one home:
a test of v1 whose replayed call shows the kind, or the perturbation, which
puts the kind into the trees it changes. :func:`load` reads the list, and
:func:`check` holds it to a recording of v1's tests and its replay.
"""

from __future__ import annotations

import pathlib
import tomllib
from collections.abc import Mapping
from typing import Any, Optional

from . import routing
from .record import Recording
from .replay import Finding
from .routing import Route

__all__ = []

#: the committed file that holds the kind list
PATH = pathlib.Path(__file__).parent / 'kinds.toml'
#: the two families of kinds, each a table of the file
FAMILIES = ('notices', 'issues')
#: the home of a kind that the perturbation puts into its trees
PERTURBATION = 'perturbation'

# what separates a test's file from its name in a test id
_SCOPE = '::'
# where the routing table sends a test that a test of the second engine holds
_HELD = 'rust'


def load(path: Optional[pathlib.Path] = None) -> dict[str, dict[str, str]]:
    """Return the kind list of the TOML file at ``path``: each kind's home, by family.

    Raises:
        ValueError: If the file holds another key than the two families,
            if a family is no table, or if a home is neither the
            perturbation nor a test id.

    """
    path = path or PATH
    data = tomllib.loads(path.read_text(encoding='utf-8'))
    unknown = sorted(set(data) - set(FAMILIES))
    if unknown:
        raise ValueError(f'{path.name} has the unknown key {unknown[0]!r}.')
    result: dict[str, dict[str, str]] = {}
    for family in FAMILIES:
        homes = data.get(family, {})
        if not isinstance(homes, dict):
            raise ValueError(f'{path.name}: {family} must be a table.')
        for kind, home in homes.items():
            named = isinstance(home, str) and (
                (home == PERTURBATION) or (_SCOPE in home)
            )
            if not named:
                raise ValueError(
                    f'{path.name}: the home of {kind} must be {PERTURBATION!r} or'
                    f' a test id, got {home!r}.'
                )
        result[family] = dict(homes)
    return result


def check(
    homes: Mapping[str, Mapping[str, str]],
    recording: Recording,
    report: Mapping[str, Any],
    routes: Mapping[str, Route],
) -> tuple[list[Finding], dict[str, Any]]:
    """Hold the kind list to v1's own kinds and to a replay of every call.

    The list must name exactly the kinds v1 has. A kind whose home is a
    test is held in one of two ways. The replay holds it when a call of
    the test replays as its command and the command shows the kind. A
    test of the second engine holds it when the routing table sends the
    home there and the test met the kind in a call. The perturbation
    answers for the kinds it is the home of.

    Args:
        homes: The kind list, from :func:`load`.
        recording: A recording of every test, from
            :func:`parity.record.run`.
        report: The report of a replay of every call, from
            :func:`parity.replay.run`.
        routes: The routing table, from :func:`parity.routing.load`.

    Returns:
        The findings, and the list as JSON data: how many kinds of each
        family have each sort of home.

    """
    findings = []
    counts: dict[str, dict[str, int]] = {}
    for family in FAMILIES:
        label = family.removesuffix('s')
        listed = homes.get(family, {})
        known = set(recording.known.get(family, ()))
        count = counts.setdefault(family, {'replay': 0, _HELD: 0, PERTURBATION: 0})
        for kind in sorted(known - set(listed)):
            findings.append(
                Finding('kind', f'{label} {kind}', 'the list gives it no home')
            )
        for kind, home in sorted(listed.items()):
            subject = f'{label} {kind}'
            if kind not in known:
                findings.append(Finding('kind', subject, 'v1 has no such kind'))
            elif home == PERTURBATION:
                count[PERTURBATION] += 1
            elif routing.find(routes, home) is None:
                if _shows(report, home, family, kind):
                    count['replay'] += 1
                else:
                    detail = f'no replayed call of {home} shows it'
                    findings.append(Finding('kind', subject, detail))
            elif _met(recording, routes, home, family, kind):
                count[_HELD] += 1
            else:
                detail = f'{home} is routed, and no test of the second engine holds it'
                findings.append(Finding('kind', subject, detail))
    return findings, counts


# ------ helper functions


def _shows(report: Mapping[str, Any], test: str, family: str, kind: str) -> bool:
    """Return whether a call of ``test`` replays as its command and shows ``kind``."""
    for row in report['calls']:
        own = (row['test'] == test) and not row['shared']
        if own and not (row['unformed'] or row['problems']):
            if kind in row['kinds'].get(family, ()):
                return True
    return False


def _met(
    recording: Recording,
    routes: Mapping[str, Route],
    test: str,
    family: str,
    kind: str,
) -> bool:
    """Return whether ``test`` met ``kind`` in a call and a test of the second engine holds it."""
    route = routing.find(routes, test)
    if (route is None) or (route.to != _HELD):
        return False
    return any(
        (call.test == test) and (kind in call.kinds.get(family, ()))
        for call in recording.calls
    )
