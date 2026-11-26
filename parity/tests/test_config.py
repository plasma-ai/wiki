"""Test the ``parity.config`` module.

A run reads its sides and corpora from two TOML files; this module
carries what the loaders refuse, so a file of the wrong shape ends a run
before it compares anything.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable

import pytest

from parity import config, runner

__all__ = [
    'test_relative_checkout_is_read_against_the_file',
    'test_root_outside_the_checkout_is_refused',
    'test_file_of_the_wrong_shape_is_refused',
    'test_key_outside_a_table_is_refused',
]

# a valid command prefix, for the tables that break another key
_ARGV = 'argv = ["wiki"]'


def test_relative_checkout_is_read_against_the_file(tmp_path: pathlib.Path) -> None:
    """A relative ``checkout`` names a folder beside the corpora file."""
    path = tmp_path / 'corpora.toml'
    text = '[example]\ncheckout = "checkout"\nroot = "wiki"\n'
    path.write_text(text, encoding='utf-8')
    corpora = config.load_corpora(path)
    expected = runner.Corpus(
        label='example',
        checkout=tmp_path / 'checkout',
        root='wiki',
    )
    assert corpora == {'example': expected}


@pytest.mark.parametrize(
    argnames='root',
    argvalues=['/wiki', '', '../wiki'],
    ids=['absolute', 'empty', 'parent'],
)
def test_root_outside_the_checkout_is_refused(
    tmp_path: pathlib.Path,
    root: str,
) -> None:
    """A ``root`` that is no plain path below its checkout is refused.

    The harness joins the root to each clone of the checkout, so an
    absolute path, an empty one or one that climbs out would name a
    folder outside the tree a side runs on.
    """
    path = tmp_path / 'corpora.toml'
    text = f'[example]\ncheckout = "checkout"\nroot = "{root}"\n'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError, match='root must be a folder below the checkout'):
        config.load_corpora(path)


@pytest.mark.parametrize(
    argnames=('load', 'table', 'message'),
    argvalues=[
        (config.load_sides, 'argv = "wiki"', 'argv must be a non-empty list'),
        (config.load_sides, 'argv = []', 'argv must be a non-empty list'),
        (config.load_sides, f'{_ARGV}\nenv = {{ A = 1 }}', 'env must be a table'),
        (config.load_sides, f'{_ARGV}\ndescribe = "wiki"', 'describe must be a list'),
        (config.load_sides, f'{_ARGV}\nnotice = "false"', 'notice must be true or'),
        (config.load_sides, f'{_ARGV}\njudge = "python"', 'judge must be a list'),
        (config.load_corpora, 'root = "wiki"', 'checkout must be a string'),
        (config.load_corpora, 'checkout = "checkout"', 'root must be a string'),
    ],
    ids=[
        'argv-string',
        'argv-empty',
        'env',
        'describe',
        'notice',
        'judge',
        'checkout',
        'root',
    ],
)
def test_file_of_the_wrong_shape_is_refused(
    tmp_path: pathlib.Path,
    load: Callable[[pathlib.Path], dict],
    table: str,
    message: str,
) -> None:
    """A key of the wrong type, or a missing one, is refused by name.

    A string ``argv`` would be split into its characters, and a quoted
    ``notice`` would read as true. Each key is checked as the file is
    read, and the error names the table and the key.
    """
    path = tmp_path / 'file.toml'
    path.write_text(f'[example]\n{table}\n', encoding='utf-8')
    with pytest.raises(ValueError, match=f"'example': {message}"):
        load(path)


@pytest.mark.parametrize(
    argnames='load',
    argvalues=[config.load_sides, config.load_corpora],
    ids=['sides', 'corpora'],
)
def test_key_outside_a_table_is_refused(
    tmp_path: pathlib.Path,
    load: Callable[[pathlib.Path], dict],
) -> None:
    """A top-level key that is no table is refused by name.

    A file whose ``[name]`` header was left out holds its keys at the top
    level, where each reads as a side or a corpus that is no table. The
    error names the key, as it does for a key of the wrong type.
    """
    path = tmp_path / 'file.toml'
    path.write_text('example = ["wiki"]\n', encoding='utf-8')
    with pytest.raises(ValueError, match="'example': must be a table"):
        load(path)
