"""Verify the package can be imported."""

from __future__ import annotations

import importlib
import subprocess
import sys

__all__ = [
    'test_import',
    'test_cli_subpackage_imports_fresh',
]


def test_import() -> None:
    """Test that package imports successfully."""
    importlib.import_module('wiki')


def test_cli_subpackage_imports_fresh() -> None:
    """``import wiki.cli.utils`` resolves in a fresh interpreter.

    ``wiki.cli`` must stay bound to the CLI subpackage: rebinding the
    attribute (e.g. re-exporting the Typer app runner over it) breaks
    submodule imports for library consumers, so the guard runs in a
    fresh interpreter where no earlier import can mask the rebind.
    """
    code = (
        'import types\n'
        'import wiki.cli.utils\n'
        'import wiki\n'
        'assert isinstance(wiki.cli, types.ModuleType)\n'
        'assert callable(wiki.cli.utils.resolve_wiki_root)\n'
    )
    subprocess.run([sys.executable, '-c', code], check=True)
