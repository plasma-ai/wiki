"""Pytest plugin that runs v1's CLI tests through the dispatcher.

usage: <pytest of a v1 venv> -p parity_suite --parity-plan PLAN <tests>

v1's CLI tests take the console script from one helper module, as its
``WIKI`` path and its ``VENV_BIN`` folder. The plugin points both at the
dispatcher of the run, so every call a test makes, by the path or by the
bare name, goes where the dispatcher sends it. It gives each test a trace
file of its own through ``WIKI_TRACE``, and writes one result file: every
test with its outcome and its trace file, and every test left out.

``PLAN`` is a JSON object: ``helper`` is the helper module's name, ``wiki``
the dispatcher, ``traces`` the folder for the trace files and ``result`` the
result file. The plugin runs inside a v1 side's interpreter, so it imports
nothing from ``parity``, and its folder holds nothing else.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from collections.abc import Sequence
from typing import Any

import pytest

__all__ = []

#: the variable that names the file a side appends its identity record to
TRACE = 'WIKI_TRACE'

# the names the helper module holds the console script under
_WIKI = 'WIKI'
_BIN = 'VENV_BIN'
# the property a test carries the name of its trace file under
_PROPERTY = 'parity_trace'
# the key a worker hands the tests it left out to the controller under
_DESELECTED = 'parity_deselected'
# the name of the process in a trace file's name, where no worker runs
_MAIN = 'main'

_PLAN = pytest.StashKey[dict[str, Any]]()
_LEFT = pytest.StashKey[list[str]]()
_COUNT = pytest.StashKey[int]()


class _Collector:
    """Gathers every test's outcome in the process that reports the run."""

    def __init__(self: _Collector, config: pytest.Config) -> None:
        """Initialize the collector for the run of ``config``."""
        self.config = config
        self.rows: dict[str, dict[str, str]] = {}
        self.deselected: set[str] = set()

    def pytest_runtest_logreport(self: _Collector, report: pytest.TestReport) -> None:
        """Handle the report of one phase of one test."""
        blank = {'id': report.nodeid, 'outcome': 'passed', 'trace': ''}
        row = self.rows.setdefault(report.nodeid, blank)
        for name, value in report.user_properties:
            if name == _PROPERTY:
                row['trace'] = f'{value}'
        # the first phase that does not pass names the outcome
        if row['outcome'] == 'passed':
            row['outcome'] = _outcome(report)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self: _Collector, node: Any) -> None:
        """Handle the end of a worker: take the tests it left out."""
        output = getattr(node, 'workeroutput', {})
        self.deselected.update(output.get(_DESELECTED, ()))

    def pytest_sessionfinish(self: _Collector) -> None:
        """Write the result file."""
        self.deselected.update(self.config.stash[_LEFT])
        document = {
            'tests': [self.rows[id] for id in sorted(self.rows)],
            'deselected': sorted(self.deselected),
        }
        path = pathlib.Path(self.config.stash[_PLAN]['result'])
        path.write_text(json.dumps(document, indent=1) + '\n', encoding='utf-8')


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the option that names the plan of the run."""
    group = parser.getgroup('parity')
    plan_help = 'JSON file that names the dispatcher and the files of the run.'
    group.addoption('--parity-plan', type=pathlib.Path, required=True, help=plan_help)


def pytest_configure(config: pytest.Config) -> None:
    """Read the plan, and gather outcomes in the process that reports them."""
    plan: pathlib.Path = config.option.parity_plan
    config.stash[_PLAN] = json.loads(plan.read_text(encoding='utf-8'))
    config.stash[_LEFT] = []
    config.stash[_COUNT] = 0
    if not hasattr(config, 'workerinput'):
        config.pluginmanager.register(_Collector(config), 'parity-suite-collector')


def pytest_deselected(items: Sequence[pytest.Item]) -> None:
    """Note the tests the run leaves out."""
    for item in items:
        item.config.stash[_LEFT].append(item.nodeid)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Point the helper module and the collected test modules at the dispatcher.

    A test module binds the helper's ``WIKI`` under its own name when it
    imports it, so the name is set in every module that holds the helper's
    value.

    Raises:
        pytest.UsageError: If the helper module is not imported, or does not
            hold the two names: the tests would then call the console script
            of their own venv, and the run would prove nothing.

    """
    if not items:
        return
    plan = config.stash[_PLAN]
    helper = sys.modules.get(plan['helper'])
    if (helper is None) or not (hasattr(helper, _WIKI) and hasattr(helper, _BIN)):
        raise pytest.UsageError(
            f'{plan["helper"]} does not name the console script as {_WIKI}'
            f' and {_BIN}; the tests cannot be pointed at the dispatcher.'
        )
    found = getattr(helper, _WIKI)
    modules = {getattr(item, 'module', None) for item in items}
    for module in (helper, *modules):
        if (module is not None) and (getattr(module, _WIKI, None) == found):
            setattr(module, _WIKI, plan['wiki'])
    setattr(helper, _BIN, pathlib.Path(plan['wiki']).parent)


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Hand the tests a worker left out to the process that reports the run."""
    config = session.config
    output = getattr(config, 'workeroutput', None)
    if output is not None:
        output[_DESELECTED] = list(config.stash[_LEFT])


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Give the test a trace file of its own, before its first fixture runs.

    The file stays in place until the next test gets its own, so a fixture
    torn down behind the test still counts for it.
    """
    config = item.config
    worker = getattr(config, 'workerinput', {}).get('workerid', _MAIN)
    config.stash[_COUNT] += 1
    name = f'{worker}-{config.stash[_COUNT]}'
    os.environ[TRACE] = os.path.join(config.stash[_PLAN]['traces'], name)
    item.user_properties.append((_PROPERTY, name))


# ------ helper functions


def _outcome(report: pytest.TestReport) -> str:
    """Return what one phase of a test says about the test's outcome."""
    expected = hasattr(report, 'wasxfail')
    if report.failed:
        if report.when == 'call':
            return 'failed'
        return 'error'
    if report.skipped:
        if expected:
            return 'xfailed'
        return 'skipped'
    if expected:
        return 'xpassed'
    return 'passed'
