"""Functions for reading the corpus and side files."""

from __future__ import annotations

import pathlib
import tomllib

from .runner import Corpus
from .side import Side

__all__ = []


def load_corpora(path: pathlib.Path) -> dict[str, Corpus]:
    """Return the corpora a TOML file declares, by label.

    Each table is one corpus: ``checkout`` is the folder to snapshot, read
    against the file's own folder when relative, and ``root`` the wiki
    root below it.

    Raises:
        ValueError: If the file is not TOML, if a top-level value is not a
            table, if a ``checkout`` or a ``root`` is not a string, or if a
            ``root`` is not a plain path below its checkout.

    """
    data = tomllib.loads(path.read_text(encoding='utf-8'))
    result = {}
    for label, table in data.items():
        if not isinstance(table, dict):
            raise ValueError(f'Corpus {label!r}: must be a table, got {table!r}.')
        checkout = table.get('checkout')
        if not isinstance(checkout, str):
            raise ValueError(
                f'Corpus {label!r}: checkout must be a string, got {checkout!r}.'
            )
        relpath = table.get('root')
        if not isinstance(relpath, str):
            raise ValueError(
                f'Corpus {label!r}: root must be a string, got {relpath!r}.'
            )
        root = pathlib.PurePosixPath(relpath)
        if root.is_absolute() or (not root.parts) or ('..' in root.parts):
            raise ValueError(
                f'Corpus {label!r}: root must be a folder below the checkout,'
                f' got {relpath!r}.'
            )
        result[label] = Corpus(
            label=label,
            checkout=path.parent / checkout,
            root=f'{root}',
        )
    return result


def load_sides(path: pathlib.Path) -> dict[str, Side]:
    """Return the sides a TOML file declares, by name.

    Each table is one side: ``argv`` is the command prefix, ``env`` an
    optional table of variables for that side only, ``describe`` an
    optional whole command that prints what the side is made of, and
    ``notice`` whether the side prints the ``WIKI_FREEZE_TIME`` notice
    (true unless stated).

    Raises:
        ValueError: If the file is not TOML, if a top-level value is not a
            table, or if a side's ``argv`` is not a non-empty list of
            strings, its ``env`` not a table of strings, its ``describe``
            not a list of strings, or its ``notice`` not a boolean. Every
            side of the file is checked, whether or not the run names it.

    """
    data = tomllib.loads(path.read_text(encoding='utf-8'))
    result = {}
    for name, table in data.items():
        if not isinstance(table, dict):
            raise ValueError(f'Side {name!r}: must be a table, got {table!r}.')
        argv = table.get('argv')
        if (not _is_string_list(argv)) or (not argv):
            raise ValueError(
                f'Side {name!r}: argv must be a non-empty list of strings,'
                f' got {argv!r}.'
            )
        env = table.get('env', {})
        if (not isinstance(env, dict)) or (not _is_string_list(list(env.values()))):
            raise ValueError(
                f'Side {name!r}: env must be a table of strings, got {env!r}.'
            )
        describe = table.get('describe', [])
        if not _is_string_list(describe):
            raise ValueError(
                f'Side {name!r}: describe must be a list of strings, got {describe!r}.'
            )
        notice = table.get('notice', True)
        if not isinstance(notice, bool):
            raise ValueError(
                f'Side {name!r}: notice must be true or false, got {notice!r}.'
            )
        result[name] = Side(
            name=name,
            argv=tuple(argv),
            env=dict(env),
            describe=tuple(describe),
            notice=notice,
        )
    return result


# ------ helper functions


def _is_string_list(value: object) -> bool:
    """Return whether ``value`` is a list that holds only strings."""
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
