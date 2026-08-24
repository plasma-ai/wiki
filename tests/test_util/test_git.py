"""Test the ``wiki.util.git`` module.

The subprocess helper has no unit surface of its own; the fence probes
and merge-driver wiring it backs are covered end-to-end by the
gitignore-fence tests in ``test_core.test_exclude`` and
``test_core.test_lint`` against real repositories.
"""

from __future__ import annotations

__all__ = []
