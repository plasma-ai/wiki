"""Test the ``wiki.core.event`` module.

Payload-only ``Event`` base with no unit surface of its own; the typed
notice events built on it are exercised through the engine suites,
which capture and assert on emitted events via ``_helpers``.
"""

from __future__ import annotations

__all__ = []
