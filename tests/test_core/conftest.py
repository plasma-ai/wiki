"""Shared fixtures for the core suites."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import yaml

from wiki.core import format


@pytest.fixture(params=['c', 'pure'])
def _vary_loader(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Run the test under the C loader and again under the pure-Python loader.

    The C loader is a build-time option of the PyYAML wheel, so the reader
    falls back to the pure loader where it is missing; the ``pure`` axis
    removes it the same way. The compose memo is keyed by block text alone,
    so it is cleared on both axes -- before, so this loader recomposes, and
    after, so the next test's loader does.
    """
    format._compose_cached.cache_clear()
    if request.param == 'pure':
        monkeypatch.delattr(yaml, 'CSafeLoader', raising=False)
    yield
    format._compose_cached.cache_clear()
