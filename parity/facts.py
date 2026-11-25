"""Print what a v1 side's interpreter is made of, as one JSON object.

usage: <a v1 venv's python> -P facts.py

The object holds the interpreter's version, the versions of its Unicode
tables and of its SQLite, the libyaml that PyYAML's C loader is built on
(empty when the loader is absent), every installed distribution with its
version, and the version and folder of the ``wiki`` package it imports. The
harness holds these against what ``oracle.toml`` and the side's constraints
file name, and a result file records them.

Run with ``-P`` so the script's folder stays off ``sys.path``.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import pathlib
import platform
import re
import sqlite3
import sys
import unicodedata
from typing import Any

__all__ = [
    'canonical',
    'main',
]


def canonical(name: str) -> str:
    """Return a distribution name in the form two spellings of it share.

    >>> canonical('Typing_Extensions')
    'typing-extensions'
    """
    return re.sub(r'[-_.]+', '-', name).lower()


def main() -> None:
    """Print the facts of the running interpreter."""
    import yaml

    import wiki

    libyaml = ''
    if yaml.__with_libyaml__:
        libyaml = importlib.import_module('yaml._yaml').get_version_string()
    facts: dict[str, Any] = {
        'python': platform.python_version(),
        'implementation': sys.implementation.name,
        'unicode': unicodedata.unidata_version,
        'sqlite': sqlite3.sqlite_version,
        'libyaml': libyaml,
        'packages': {
            canonical(item.metadata['Name']): item.version
            for item in importlib.metadata.distributions()
        },
        'wiki': {
            'version': wiki.__version__,
            'package': f'{pathlib.Path(wiki.__file__).parent}',
        },
    }
    print(json.dumps(facts, indent=1, sort_keys=True))


if __name__ == '__main__':
    main()
