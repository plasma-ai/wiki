"""Functions for the dispatcher: one ``wiki`` that sends each call to a side.

A test suite calls one executable named ``wiki``, by its path or through
``PATH``. The dispatcher is that executable. It sends a call of a ported
command to the second engine and every other call to v1, and it notes each
call in the file ``WIKI_TRACE`` names, beside the identity record the side
leaves there. :func:`tally` reads the file back.
"""

from __future__ import annotations

import collections
import dataclasses
import json
import pathlib
import shlex
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from . import side
from .side import TRACE, Side

__all__ = []

#: the file name of the dispatcher, the name every caller looks for
NAME = 'wiki'

# the keys of the line the dispatcher leaves for a call: the side the call
# went to, and the ported command it names
_ROUTE = 'route'
_COMMAND = 'command'
# the variable that fixes an engine's clock
_CLOCK = 'WIKI_FREEZE_TIME'


@dataclasses.dataclass(frozen=True)
class Dispatcher:
    """One ``wiki`` executable in front of the sides of a run.

    Attributes:
        path: The executable. Its folder holds nothing else, so it can lead
            the ``PATH`` of a caller that runs ``wiki`` by its bare name.
        sides: The sides it sends calls to, by name: v1 first, then the
            second engine if the run has one.
        commands: The ported commands, whose calls go to the second engine.
        untraced: The file that takes the note of a call made with no
            ``WIKI_TRACE``.

    """

    path: pathlib.Path
    sides: Mapping[str, Side]
    commands: tuple[str, ...]
    untraced: pathlib.Path


@dataclasses.dataclass(frozen=True)
class Tally:
    """The calls one trace file holds.

    Attributes:
        calls: How many calls each side took, by name.
        commands: How many calls of each ported command the second engine
            took.
        identified: Whether the file holds one identity record for every
            call of a side that leaves one, each the record of the side
            the call went to, and no other line.

    """

    calls: Mapping[str, int]
    commands: Mapping[str, int]
    identified: bool


def build(
    folder: pathlib.Path,
    *,
    reference: Side,
    engine: Optional[Side] = None,
    commands: Sequence[str] = (),
) -> Dispatcher:
    """Write the dispatcher into the new folder ``folder``.

    Args:
        folder: New folder for the executable, and for nothing else.
        reference: The v1 side, which takes every call the second engine
            does not.
        engine: The second engine, which takes the calls of ``commands``;
            ``None`` sends every call to v1.
        commands: The ported commands: the first arguments of the calls
            the second engine takes.

    Returns:
        The dispatcher, for :func:`tally` to read its notes by.

    Raises:
        ValueError: If the two sides share a name.

    """
    if (engine is not None) and (engine.name == reference.name):
        raise ValueError(f'Sides must have different names, got {engine.name!r}.')
    sides = {reference.name: reference}
    lines = ['#!/bin/sh']
    untraced = folder.with_name(f'{folder.name}.untraced')
    # NOTE: a call made with no WIKI_TRACE is noted all the same, in a file of
    #   the run's own, so no call passes the dispatcher unseen
    lines.append(f'trace=${{{TRACE}:-}}')
    lines.append('if [ -z "$trace" ]; then')
    lines.append(f'    trace={shlex.quote(f"{untraced}")}')
    lines.append('fi')
    if (engine is not None) and commands:
        sides[engine.name] = engine
        # one branch per ported command, so the note names the command
        lines.append('case "${1-}" in')
        for command in commands:
            note = {_ROUTE: engine.name, _COMMAND: command}
            lines.append(f'{shlex.quote(command)})')
            lines.extend(f'    {line}' for line in _send(engine, note))
            lines.append('    ;;')
        lines.append('esac')
    lines.extend(_send(reference, {_ROUTE: reference.name}))
    folder.mkdir(parents=True)
    path = folder / NAME
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    path.chmod(0o755)
    return Dispatcher(
        path=path,
        sides=sides,
        commands=tuple(commands) if engine is not None else (),
        untraced=untraced,
    )


def route(dispatcher: Dispatcher, command: str) -> Side:
    """Return the side that takes a call whose first argument is ``command``."""
    reference, *engines = dispatcher.sides.values()
    if command in dispatcher.commands:
        return engines[0]
    return reference


def environment(dispatcher: Dispatcher, home: pathlib.Path) -> dict[str, str]:
    """Return the full environment the callers of ``dispatcher`` run under.

    It is the built environment of the v1 side with no clock fixed: the
    callers are tests and terminal cases that bring their own clock or read
    none, and a fixed one would open every command's stderr with the
    notice.

    Args:
        dispatcher: The dispatcher, from :func:`build`.
        home: Scratch home folder; it holds the global git config.

    Returns:
        The variables to run a caller of the dispatcher under.

    """
    reference = next(iter(dispatcher.sides.values()))
    env = side.environment(reference, home, now='')
    del env[_CLOCK]
    return env


def tally(dispatcher: Dispatcher, trace: pathlib.Path) -> Tally:
    """Return the calls the file ``trace`` holds, and whether each is identified.

    The dispatcher leaves one note per call, and a side that has an identity
    leaves its record on each call it takes. So the records a file holds
    must be exactly those its notes ask for: a record too few is a call
    that did not run the side it went to, and a record too many is an
    engine that ran without passing the dispatcher. A file that does not
    exist holds no call.
    """
    calls: collections.Counter[str] = collections.Counter()
    commands: collections.Counter[str] = collections.Counter()
    records: collections.Counter[str] = collections.Counter()
    owed: collections.Counter[str] = collections.Counter()
    readable = True
    if trace.is_file():
        for line in trace.read_bytes().splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                readable = False
                continue
            name = entry.get(_ROUTE) if isinstance(entry, dict) else None
            if (not isinstance(name, str)) or (name not in dispatcher.sides):
                records[_canonical(entry)] += 1
                continue
            calls[name] += 1
            if _COMMAND in entry:
                commands[entry[_COMMAND]] += 1
            identity = dispatcher.sides[name].identity
            if identity:
                owed[_canonical(dict(identity))] += 1
    return Tally(
        calls=dict(calls),
        commands=dict(commands),
        identified=readable and (records == owed),
    )


# ------ helper functions


def _send(runner: Side, note: Mapping[str, str]) -> list[str]:
    """Return the shell lines that note a call and hand it to ``runner``.

    The side replaces the shell, so the caller's streams, signals and exit
    code are the side's own. A side with no identity gets no ``WIKI_TRACE``,
    as in every other run of the harness.
    """
    lines = [f'printf \'%s\\n\' {shlex.quote(json.dumps(note))} >>"$trace"']
    lines.extend(
        f'export {name}={shlex.quote(value)}' for name, value in runner.env.items()
    )
    if not runner.identity:
        lines.append(f'unset {TRACE}')
    argv = ' '.join(shlex.quote(part) for part in runner.argv)
    lines.append(f'exec {argv} "$@"')
    return lines


def _canonical(entry: Any) -> str:
    """Return one JSON value in the form two equal values share."""
    return json.dumps(entry, sort_keys=True)
