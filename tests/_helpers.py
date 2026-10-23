"""Shared helpers for the ``wiki`` test suite."""

from __future__ import annotations

import os

import pytest

__all__ = ['_needs_unprivileged']

# gates the tests that rely on a permission denial
_needs_unprivileged = pytest.mark.skipif(
    os.geteuid() == 0,
    reason='needs a non-root user',
)
