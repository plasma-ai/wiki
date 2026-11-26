"""Functions for reading the list of ported commands."""

from __future__ import annotations

import pathlib
import tomllib
from typing import Optional

__all__ = []

#: the committed file that lists the ported commands
PATH = pathlib.Path(__file__).parent / 'ported.toml'

# the one key of the file
_KEY = 'commands'


def load(path: Optional[pathlib.Path] = None) -> tuple[str, ...]:
    """Return the ported commands the TOML file at ``path`` lists, in its order.

    Raises:
        ValueError: If the file holds another key than ``commands``, if
            ``commands`` is not a list of strings, or if it holds an empty
            string or one string twice.

    """
    path = path or PATH
    data = tomllib.loads(path.read_text(encoding='utf-8'))
    unknown = sorted(set(data) - {_KEY})
    if unknown:
        raise ValueError(f'{path.name} has the unknown key {unknown[0]!r}.')
    commands = data.get(_KEY)
    if (not isinstance(commands, list)) or not all(
        isinstance(command, str) for command in commands
    ):
        raise ValueError(
            f'{path.name}: {_KEY} must be a list of strings, got {commands!r}.'
        )
    if '' in commands:
        raise ValueError(f'{path.name}: {_KEY} holds an empty string.')
    twice = sorted({command for command in commands if commands.count(command) > 1})
    if twice:
        raise ValueError(f'{path.name}: {_KEY} lists {twice[0]!r} twice.')
    return tuple(commands)
