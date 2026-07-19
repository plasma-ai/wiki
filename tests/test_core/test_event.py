"""Test the ``wiki.core.event`` module.

Payload-only ``Event`` base; the typed notice events built on it are
exercised through the engine suites, which capture and assert on
emitted events via ``_helpers``. Only the payload-extraction boundary
is checked here.
"""

from __future__ import annotations

import pytest

from wiki.core.event import Event

__all__ = ['test_event_rejects_unknown_payload_field']


def test_event_rejects_unknown_payload_field() -> None:
    """A misspelled payload kwarg fails at the emit site, not at render."""
    with pytest.raises(TypeError, match='pathh'):
        Event('context', pathh='x')
