"""Top-level fixtures and pytest config for the ``wiki`` suite."""

from __future__ import annotations

import os
import pathlib
import sys
from collections.abc import Iterator

import pytest

from wiki.constants import OFFLINE_MODE

# the CLI suite drives the installed ``wiki`` console script as a subprocess,
# which coverage's in-process tracer cannot see; under ``--cov``, point both the
# parent and (via coverage's startup hook) every subprocess at one config + data
# file, so the CLI lines are measured and combined instead of reading near-zero
if any(arg == '--cov' or arg.startswith('--cov=') for arg in sys.argv):
    _cov_root = pathlib.Path(__file__).resolve().parent.parent
    os.environ.setdefault('COVERAGE_PROCESS_START', str(_cov_root / 'pyproject.toml'))
    os.environ.setdefault('COVERAGE_FILE', str(_cov_root / '.coverage'))

# env vars a live wiki deployment may export -- tests must not inherit them:
# an ambient OFFLINE_MODE=true silently skips the download paths the config
# tests exercise, and core validates the var fail-loud, so any other ambient
# value breaks every in-process init/update_config test (see _isolate_ambient_env)
_AMBIENT_ENV_VARS = [OFFLINE_MODE]


@pytest.fixture(autouse=True)
def _isolate_trust_store(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point the user-global trust store at a throwaway dir per test.

    The ``.wiki/wiki.py`` trust gate reads ``~/.wiki/settings.json``;
    without this a hook-loading test would read (and a ``wiki trust`` test
    would mutate) the real user's trusted-wiki list. ``WIKI_CONFIG_DIR``
    redirects it to a fresh dir, so every test starts trusting nothing and
    the CLI subprocesses inherit the same isolated store.
    """
    home = tmp_path_factory.mktemp('wiki_config')
    monkeypatch.setenv('WIKI_CONFIG_DIR', str(home))


@pytest.fixture(scope='session', autouse=True)
def _isolate_ambient_env() -> Iterator[None]:
    """Strip a live deployment's exported env for the whole session.

    A wiki host (fractal's node loop, a CI job) exports ``OFFLINE_MODE``
    for its own ``wiki`` invocations. Inherited by the suite, ``true``
    skips the stubbed downloads the config tests assert on, and a value
    outside true/false makes every in-process ``init``/``update_config``
    call raise ``ValueError``. Tests that need the var set it themselves
    with the function-scoped ``monkeypatch``, which runs later and is
    undone per test; the CLI suite is unaffected -- its ``_wiki`` runner
    pins the var per subprocess.
    """
    monkeypatch = pytest.MonkeyPatch()
    for var in _AMBIENT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield
    monkeypatch.undo()
