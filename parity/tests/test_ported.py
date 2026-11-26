"""Test the ``parity.ported`` module.

The committed list is read by every run of the CLI suite, which
``test_main`` covers; this module carries the refusals that keep a list
from routing calls by a rule nobody wrote.
"""

from __future__ import annotations

import pathlib

import pytest

from parity import ported

__all__ = ['test_list_that_breaks_a_rule_is_refused']


@pytest.mark.parametrize(
    argnames=('text', 'message'),
    argvalues=[
        ('commands = ["read"]\nengine = "other"\n', 'unknown key'),
        ('', 'must be a list of strings'),
        ('commands = "read"\n', 'must be a list of strings'),
        ('commands = ["read", 1]\n', 'must be a list of strings'),
        ('commands = ["read", ""]\n', 'holds an empty string'),
        ('commands = ["read", "lint", "read"]\n', "lists 'read' twice"),
    ],
    ids=['key', 'missing', 'string', 'number', 'empty', 'twice'],
)
def test_list_that_breaks_a_rule_is_refused(
    tmp_path: pathlib.Path,
    text: str,
    message: str,
) -> None:
    """A list is read only when it is a list of commands, each named once.

    A string read as a list would port one command per letter, an empty
    name would take the calls that have no argument, and a key the harness
    does not know would be a rule it does not apply. A well-formed list
    comes back in its own order.
    """
    path = tmp_path / 'ported.toml'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError, match=message):
        ported.load(path)
    path.write_text('commands = ["read", "--version"]\n', encoding='utf-8')
    assert ported.load(path) == ('read', '--version')
