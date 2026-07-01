"""Verify the package can be imported."""

from __future__ import annotations

import importlib

__all__ = ['test_import']


def test_import() -> None:
    """Test that package imports successfully."""
    importlib.import_module('wiki')
