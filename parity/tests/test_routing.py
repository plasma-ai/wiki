"""Test the ``parity.routing`` module.

The committed table is read by every replay, which ``test_main`` covers;
this module carries the refusals that keep a row from taking a test out of
the replay with nothing behind it.
"""

from __future__ import annotations

import pathlib

import pytest

from parity import routing

__all__ = ['test_table_that_breaks_a_rule_is_refused']

# one row that keeps every rule, and the test it names
_TEST = 'tests/test_core/test_plan.py::test_update_survives'
_ROW = f'[[route]]\ntests = ["{_TEST}"]\nto = "rust"\nreason = "A race."\n'
# the same row with nothing to hold its test
_DROPPED = _ROW.replace('"rust"', '"dropped"')


@pytest.mark.parametrize(
    argnames=('text', 'message'),
    argvalues=[
        (f'{_ROW}rest = "v1"\n', 'unknown field'),
        (f'rows = 1\n{_ROW}', 'unknown key'),
        ('route = "all"\n', 'must be a list of tables'),
        ('route = ["all"]\n', 'must be a table'),
        ('[[route]]\nto = "rust"\nreason = "A race."\n', 'must be a list of test ids'),
        (_ROW.replace(f'["{_TEST}"]', '["test_plan"]'), 'must be a list of test ids'),
        (_ROW.replace('"A race."', '1'), 'reason must be a string'),
        (_ROW.replace('"rust"', '"elsewhere"'), 'to must be one of'),
        (_ROW.replace('"A race."', '" "'), 'gives no reason'),
        (_DROPPED, 'a dropped row cites'),
        (f'{_DROPPED}cite = "D99"\n', 'a dropped row cites'),
        (f'{_ROW}cite = "hooks"\n', 'only a dropped row cites'),
        (_ROW.replace('"rust"', '"replay"'), 'names the command'),
        (_ROW * 2, 'is routed twice'),
    ],
    ids=[
        'field',
        'key',
        'string',
        'no table',
        'no test',
        'no test id',
        'shape',
        'place',
        'reason',
        'uncited',
        'unknown entry',
        'cited',
        'waiting for nothing',
        'twice',
    ],
)
def test_table_that_breaks_a_rule_is_refused(
    tmp_path: pathlib.Path,
    text: str,
    message: str,
) -> None:
    """A row routes a test only with a place to send it and a reason.

    A dropped test is held by nothing, so its row cites what it rests on:
    a registry entry the registry holds, a part of v1 a second engine does
    not have, or the test that replaces it. A row that sends its tests to
    a check cites nothing, a row that waits for the replay names the
    command it waits for, and no test is routed twice. A table that keeps
    the rules comes back by test id, each test with its row, and a test
    is found by its own id or by the id of its function.
    """
    path = tmp_path / 'routing.toml'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError, match=message):
        routing.load(path, entries=['D6'])
    kept = _ROW + _DROPPED.replace(_TEST, f'{_TEST}[one]')
    for cite in ('D6', 'hooks', 'tests/test_cli/test_main.py::test_other'):
        path.write_text(f'{kept}cite = "{cite}"\n', encoding='utf-8')
        routes = routing.load(path, entries=['D6'])
        assert list(routes) == [_TEST, f'{_TEST}[one]']
        assert (routes[_TEST].to, routes[f'{_TEST}[one]'].cite) == ('rust', cite)
    waiting = _ROW.replace('"rust"', '"replay"') + 'target = "init"\n'
    path.write_text(waiting, encoding='utf-8')
    routes = routing.load(path)
    assert routing.find(routes, f'{_TEST}[one]') == routes[_TEST]
    assert (routes[_TEST].to, routes[_TEST].target) == ('replay', 'init')
    assert routing.find(routes, 'tests/test_core/test_plan.py::test_other') is None
    path.write_text('', encoding='utf-8')
    assert routing.load(path) == {}
