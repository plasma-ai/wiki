"""Test the ``wiki.core.format`` module.

Functions over the on-disk page format, driven only through the engine;
the behavior-named core suites (``test_authoring``, ``test_update``,
``test_lint``) are the cover, exercising parse/render round-trips
exactly as the tool does.
"""

from __future__ import annotations

__all__ = []
