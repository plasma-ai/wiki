"""Functions for giving every test of v1 one home.

A test of v1 has a home when a part of the harness holds a second engine to
it: the CLI suite, which runs the test's own commands on the engine; the
replay, which runs the test's calls again as command lines; the format
calls, which are made again from their records; or a row of the routing
table, which names the check that holds the test in their place.
:func:`collect` lists the tests v1 has, and :func:`check` finds the home of
each.
"""

from __future__ import annotations

import collections
import pathlib
import subprocess
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from . import dispatch, routing
from .dispatch import Dispatcher
from .record import Recording
from .replay import Finding
from .routing import Route
from .suite import Source

__all__ = []

#: the homes a test can have, in the order a test is given one
HOMES = ('routed', 'suite', 'replay', 'format')

# the harness's own tests as v1's branch holds them: tests of the harness,
# not of v1
_OWN = 'tests/test_parity'
# what separates a test's file from its name in a test id
_SCOPE = '::'
# the outcomes of a test that the host did not let run to its end
_UNRUN = ('skipped', 'xfailed')
# seconds the collection may take -- a run this long has hung
_TIMEOUT = 600
# how many lines of pytest's output an error of the collection quotes
_TAIL = 20


def collect(
    source: Source,
    dispatcher: Dispatcher,
    *,
    work: pathlib.Path,
) -> tuple[str, ...]:
    """Return the id of every test v1 has, as its own pytest settings select them.

    pytest collects from the root of v1's checkout and runs nothing. The
    harness's own tests on v1's branch are left out.

    Args:
        source: v1's tests, from :func:`parity.suite.source`.
        dispatcher: A dispatcher, for the environment its callers run
            under.
        work: New folder for a scratch home.

    Raises:
        RuntimeError: If pytest does not end the collection cleanly, or
            collects nothing.

    """
    work.mkdir(parents=True)
    (work / '.gitconfig').write_bytes(b'')
    cmd = [f'{source.python}', '-P', '-m', 'pytest', '--collect-only', '-q']
    # one process collects, and nothing is written into the checkout
    cmd += ['-n', '0', '-p', 'no:cacheprovider', f'--ignore={_OWN}']
    done = subprocess.run(
        cmd,
        cwd=source.checkout,
        env=dispatch.environment(dispatcher, work),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=_TIMEOUT,
    )
    lines = done.stdout.decode('utf-8', 'replace').splitlines()
    tests = tuple(line for line in lines if _SCOPE in line)
    if (done.returncode != 0) or not tests:
        raise RuntimeError(
            f'pytest did not collect the tests (exit {done.returncode}):'
            f' {lines[-_TAIL:]!r}'
        )
    return tests


def check(
    collected: Collection[str],
    recording: Recording,
    report: Mapping[str, Any],
    formats: Mapping[str, Any],
    routes: Mapping[str, Route],
    *,
    folder: str,
    ported: Sequence[str] = (),
) -> tuple[list[Finding], dict[str, Any]]:
    """Find the home of every test of v1, and hold the routing table to the tests.

    A test the routing table names has its row for a home. The CLI suite
    holds a test of its folder that ran a command, by itself or through a
    shared fixture. The replay holds a test with a call that replays as
    its command. The format calls hold a test that made one which gives
    its outcome again. A test with none of these is homeless, unless the
    host skipped it: such a test shows its home on a host that runs it.

    The table is held to the tests in turn: a row names tests v1 has, a
    dropped row that cites a test cites one the CLI suite holds, and a
    row that waits for the replay waits for a command that is not ported
    yet.

    Args:
        collected: The id of every test v1 has, from :func:`collect`.
        recording: A recording of every test, from
            :func:`parity.record.run`.
        report: The report of a replay of every call, from
            :func:`parity.replay.run`.
        formats: The format calls as JSON data, from
            :func:`parity.replay.formats`.
        routes: The routing table, from :func:`parity.routing.load`.
        folder: The folder of the CLI suite's tests, relative to v1's
            checkout.
        ported: The ported commands.

    Returns:
        The findings, and the homes as JSON data: how many tests have
        each home, how many the table sends to each place, the tests the
        host skipped before they showed a home, and the tests with none.

    """
    # gather what each part of the harness holds
    commanded = {row['id'] for row in recording.tests.report['tests'] if row['calls']}
    commanded.update(test for test, fixtures in recording.shared.items() if fixtures)
    held = {test for test in commanded if test.startswith(f'{folder}/')}
    replayed = {
        row['test']
        for row in report['calls']
        if not (row['shared'] or row['unformed'] or row['problems'])
    }
    differing = set(formats['differing'])
    formatted = {
        test
        for entry in recording.formats
        if not (entry.get('unformed') or entry['outcome'].get('unformed'))
        for test in entry['tests']
        if test not in differing
    }
    unrun = {
        row['id'] for row in recording.tests.report['tests'] if row['outcome'] in _UNRUN
    }
    # give each test the first home that holds it
    homes: collections.Counter[str] = collections.Counter()
    places: collections.Counter[str] = collections.Counter()
    skipped = []
    homeless = []
    for test in collected:
        route = routing.find(routes, test)
        if route is not None:
            homes['routed'] += 1
            places[route.to] += 1
        elif test in held:
            homes['suite'] += 1
        elif test in replayed:
            homes['replay'] += 1
        elif test in formatted:
            homes['format'] += 1
        elif test in unrun:
            skipped.append(test)
        else:
            homeless.append(test)
    findings = [Finding('homeless', test) for test in homeless]
    # hold each row to the tests v1 has
    functions = {routing.function(test) for test in collected}
    held.update([routing.function(test) for test in held])
    cited = set()
    for test, route in routes.items():
        if (test not in collected) and (test not in functions):
            findings.append(Finding('absent', test, 'v1 has no such test'))
        dropped = (route.to == 'dropped') and (_SCOPE in route.cite)
        if dropped and (route.cite not in held):
            cited.add(route.cite)
        if (route.to == 'replay') and (route.target in ported):
            detail = f'{route.target} is ported, and the replay does not hold the test'
            findings.append(Finding('waiting', test, detail))
    findings.extend(
        Finding('cite', cite, 'a row cites a test that the CLI suite does not hold')
        for cite in sorted(cited)
    )
    result = {
        'tests': len(collected),
        'homes': {home: homes[home] for home in HOMES},
        'routed': dict(sorted(places.items())),
        'skipped': skipped,
        'homeless': homeless,
    }
    return findings, result
