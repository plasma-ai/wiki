"""Pytest plugin that runs v1's CLI tests through the dispatcher.

usage: <pytest of a v1 venv> -p parity_suite --parity-plan PLAN <tests>

v1's CLI tests take the console script from one helper module, as its
``WIKI`` path and its ``VENV_BIN`` folder. The plugin points both at the
dispatcher of the run as the helper is imported, before any module takes a
name from it, so every call a test makes, by the path or by the bare name,
goes where the dispatcher sends it. It gives each test a trace file of its
own through ``WIKI_TRACE``, and writes one result file: every test with its
outcome and its trace file, every test file that was not loaded, and every
test left out.

``PLAN`` is a JSON object: ``helper`` is the helper module's name, ``wiki``
the dispatcher, ``traces`` the folder for the trace files and ``result`` the
result file. The plugin runs inside a v1 side's interpreter, so it imports
nothing from ``parity``, and its folder holds nothing else.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import json
import os
import pathlib
import sys
from collections.abc import Sequence
from types import ModuleType
from typing import Any, Optional, Union

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
_POINTED = pytest.StashKey[bool]()
_LEFT = pytest.StashKey[list[str]]()
_COUNT = pytest.StashKey[int]()


class _Finder:
    """Finds the helper module, to point it at the dispatcher as it is imported.

    v1's test packages import their test modules, so the first import of
    any module of the tests imports them all, the helper among them. An
    import finder is the one place that runs between the helper's body and
    the first module that takes a name from it.
    """

    def __init__(self: _Finder, config: pytest.Config) -> None:
        """Initialize the finder for the run of ``config``."""
        self.config = config

    def find_spec(
        self: _Finder,
        fullname: str,
        path: Optional[Sequence[str]],
        target: Optional[ModuleType] = None,
    ) -> Optional[importlib.machinery.ModuleSpec]:
        """Return the helper's spec as the next finder that knows it gives it.

        The loader of the spec is wrapped, so pytest's own loader still
        rewrites the helper's assertions.
        """
        if fullname != _plan(self.config)['helper']:
            return None
        for finder in sys.meta_path:
            if finder is self:
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is not None:
                spec.loader = _Loader(spec.loader, self.config)
                return spec
        return None


class _Loader(importlib.abc.Loader):
    """Loads the helper module as its own loader does, then points it."""

    def __init__(self: _Loader, loader: Any, config: pytest.Config) -> None:
        """Initialize the loader around the helper's own ``loader``."""
        self.loader = loader
        self.config = config

    def __getattr__(self: _Loader, name: str) -> Any:
        """Return what the helper's own loader holds under ``name``."""
        return getattr(self.loader, name)

    def create_module(
        self: _Loader,
        spec: importlib.machinery.ModuleSpec,
    ) -> Optional[ModuleType]:
        """Create the module as the helper's own loader does."""
        return self.loader.create_module(spec)

    def exec_module(self: _Loader, module: ModuleType) -> None:
        """Run the helper's body, then set its two names to the dispatcher.

        A helper without both names is left as it is: the run is refused
        once the tests are collected.
        """
        self.loader.exec_module(module)
        if hasattr(module, _WIKI) and hasattr(module, _BIN):
            wiki = _plan(self.config)['wiki']
            setattr(module, _WIKI, wiki)
            setattr(module, _BIN, pathlib.Path(wiki).parent)
            self.config.stash[_POINTED] = True


class _Collector:
    """Gathers every test's outcome in the process that reports the run."""

    def __init__(self: _Collector, config: pytest.Config) -> None:
        """Initialize the collector for the run of ``config``."""
        self.config = config
        self.rows: dict[str, dict[str, str]] = {}
        self.deselected: set[str] = set()

    def pytest_collectreport(self: _Collector, report: pytest.CollectReport) -> None:
        """Handle the report of one collected file: note one that was not loaded.

        A file that fails while it is imported, or skips itself there, has
        no test that runs, so no report of a test names it. Where workers
        run the tests, pytest goes on with the other files.
        """
        if not report.passed:
            row = {'id': report.nodeid, 'outcome': _outcome(report), 'trace': ''}
            self.rows[report.nodeid] = row

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
        path = pathlib.Path(_plan(self.config)['result'])
        path.write_text(json.dumps(document, indent=1) + '\n', encoding='utf-8')


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the option that names the plan of the run."""
    group = parser.getgroup('parity')
    plan_help = 'JSON file that names the dispatcher and the files of the run.'
    group.addoption('--parity-plan', type=pathlib.Path, required=True, help=plan_help)


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config: pytest.Config) -> None:
    """Watch for the helper module before pytest imports any module of the tests.

    Whatever a module takes from the helper is then the dispatcher: a name
    it binds, a command it builds while it is imported, a parameter of a
    test.
    """
    sys.meta_path.insert(0, _Finder(early_config))


def pytest_configure(config: pytest.Config) -> None:
    """Gather outcomes in the process that reports them."""
    config.stash[_LEFT] = []
    config.stash[_COUNT] = 0
    if not hasattr(config, 'workerinput'):
        config.pluginmanager.register(_Collector(config), 'parity-suite-collector')


def pytest_deselected(items: Sequence[pytest.Item]) -> None:
    """Note the tests the run leaves out."""
    for item in items:
        item.config.stash[_LEFT].append(item.nodeid)


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Refuse to run tests that were not pointed at the dispatcher.

    Raises:
        pytest.UsageError: If the helper module was not imported, or does
            not hold the two names: the tests would then call the console
            script of their own venv, and the run would prove nothing.

    """
    if items and not config.stash.get(_POINTED, False):
        helper = _plan(config)['helper']
        raise pytest.UsageError(
            f'{helper} does not name the console script as {_WIKI} and'
            f' {_BIN}; the tests cannot be pointed at the dispatcher.'
        )


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
    os.environ[TRACE] = os.path.join(_plan(config)['traces'], name)
    item.user_properties.append((_PROPERTY, name))


# ------ helper functions


def _plan(config: pytest.Config) -> dict[str, Any]:
    """Return the plan of the run, read once.

    The helper module is imported before pytest configures the run, so
    the plan is read from the options pytest has parsed by then.
    """
    if _PLAN not in config.stash:
        path: pathlib.Path = config.known_args_namespace.parity_plan
        config.stash[_PLAN] = json.loads(path.read_text(encoding='utf-8'))
    return config.stash[_PLAN]


def _outcome(report: Union[pytest.TestReport, pytest.CollectReport]) -> str:
    """Return what one phase of a test says about the test's outcome.

    The report of a collected file reads as a phase around the tests.
    """
    expected = hasattr(report, 'wasxfail')
    if report.failed:
        if getattr(report, 'when', None) == 'call':
            return 'failed'
        return 'error'
    if report.skipped:
        if expected:
            return 'xfailed'
        return 'skipped'
    if expected:
        return 'xpassed'
    return 'passed'
