"""Functions for reading the corpus and side files."""

from __future__ import annotations

import pathlib
import tomllib

from .runner import Corpus
from .side import Side

__all__ = [
    'load_corpora',
    'load_sides',
]


def load_corpora(path: pathlib.Path) -> dict[str, Corpus]:
    """Return the corpora a TOML file declares, by label.

    Each table is one corpus: ``checkout`` is the folder to snapshot, read
    against the file's own folder when relative, and ``root`` the wiki
    root below it.

    Raises:
        ValueError: If a ``root`` is not a plain path below its checkout.

    """
    data = tomllib.loads(path.read_text(encoding='utf-8'))
    result = {}
    for label, table in data.items():
        root = pathlib.PurePosixPath(table['root'])
        if root.is_absolute() or (not root.parts) or ('..' in root.parts):
            raise ValueError(
                f'Corpus {label}: root must be a folder below the checkout,'
                f' got {table["root"]!r}.'
            )
        result[label] = Corpus(
            label=label,
            checkout=path.parent / table['checkout'],
            root=f'{root}',
        )
    return result


def load_sides(path: pathlib.Path) -> dict[str, Side]:
    """Return the sides a TOML file declares, by name.

    Each table is one side: ``argv`` is the command prefix, ``env`` an
    optional table of variables for that side only, ``notice`` whether
    the side prints the ``WIKI_NOW`` notice (true unless stated),
    ``describe`` an optional whole command that prints what the side is
    made of, and ``judge`` an optional whole command that answers what the
    side's interpreter makes of an input.
    """
    data = tomllib.loads(path.read_text(encoding='utf-8'))
    result = {}
    for name, table in data.items():
        result[name] = Side(
            name=name,
            argv=tuple(table['argv']),
            env=dict(table.get('env', {})),
            notice=table.get('notice', True),
            describe=tuple(table.get('describe', ())),
            judge=tuple(table.get('judge', ())),
        )
    return result
