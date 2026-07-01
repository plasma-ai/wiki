"""Top-level pytest config for the ``wiki`` suite (subprocess-coverage bridge)."""

from __future__ import annotations

import os
import pathlib
import sys

# the CLI suite drives the installed ``wiki`` console script as a subprocess,
# which coverage's in-process tracer cannot see; under ``--cov``, point both the
# parent and (via coverage's startup hook) every subprocess at one config + data
# file, so the CLI lines are measured and combined instead of reading near-zero
if any(arg == '--cov' or arg.startswith('--cov=') for arg in sys.argv):
    _cov_root = pathlib.Path(__file__).resolve().parent.parent
    os.environ.setdefault('COVERAGE_PROCESS_START', str(_cov_root / 'pyproject.toml'))
    os.environ.setdefault('COVERAGE_FILE', str(_cov_root / '.coverage'))
