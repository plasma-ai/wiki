"""Shared fixtures for the core suites."""

from __future__ import annotations

from typing import Any

import pytest
import yaml


@pytest.fixture(params=['c', 'pure'])
def _vary_loader(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the test under the C loader and again under the pure-Python loader.

    The C loader is a build-time option of the PyYAML wheel, so the reader
    falls back to the pure loader where it is missing; the ``pure`` axis
    removes it the same way.
    """
    if request.param == 'pure':
        monkeypatch.delattr(yaml, 'CSafeLoader', raising=False)


@pytest.fixture
def compositions(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Return a fresh list that collects every block body the strict reader composes.

    The reader's one PyYAML call is the boundary a memo hides behind: a
    block composed once per run shows up here once per run, whatever
    holds the memo. Drain the list between phases the way
    ``capsys.readouterr()`` drains a stream.
    """
    real = yaml.compose
    result: list[str] = []

    def recording(stream: str, Loader: type = yaml.Loader) -> Any:
        """Record the composed body, then compose it."""
        result.append(stream)
        return real(stream, Loader=Loader)

    monkeypatch.setattr(yaml, 'compose', recording)
    return result
