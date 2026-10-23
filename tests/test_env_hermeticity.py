"""Tests that the suite is hermetic against a live deployment's env.

An offline host exports ``OFFLINE_MODE`` for every tool that reads it (a
CI job, or a shell set up for fractal, which honors the same switch). If
the suite inherits it, ``true`` skips the stubbed downloads the config
tests assert on, and a value outside true/false makes every in-process
``init``/``update_config`` call raise ``ValueError``. The autouse
fixture in ``tests/conftest.py`` strips the var; this test guards that
contract.
"""

from __future__ import annotations

import os

from .conftest import _AMBIENT_ENV_VARS

__all__ = ['test_ambient_env_neutralized']


def test_ambient_env_neutralized() -> None:
    """The inherited deployment env must not leak into any test."""
    leaked = {var: os.environ[var] for var in _AMBIENT_ENV_VARS if var in os.environ}
    assert not leaked, f'ambient env leaked into the suite: {leaked}'
