"""Behavioral tests for ``Wiki.read``.

The resolution ladder (folder index, file, ``.md`` extension),
slice units, the path-escape refusal shared by every name-taking
operation, and the unique-leaf suggestion.
"""

from __future__ import annotations

import pathlib

import pytest

from wiki.core.wiki import Wiki

from ._helpers import _make_wiki

__all__ = [
    'test_read_resolution',
    'test_read_line_slicing',
    'test_operations_refuse_paths_outside_root',
    'test_read_slice_units',
    'test_read_suggests_unique_leaf_match',
]


@pytest.mark.parametrize(
    ('name', 'path_suffix'),
    [
        ('core', 'core/_index.md'),
        ('core/design', 'core/design.md'),
    ],
    ids=['folder', 'page'],
)
def test_read_resolution(
    tmp_path: pathlib.Path,
    name: str,
    path_suffix: str,
) -> None:
    """Read resolves names to folder indexes and pages."""
    _make_wiki(tmp_path, folders={'core': ['design']})
    wiki = Wiki(tmp_path)
    content = wiki.read(name)
    expected = (tmp_path / path_suffix).read_text(encoding='utf-8')
    assert content == expected


def test_read_line_slicing(tmp_path: pathlib.Path) -> None:
    """Read with start/stop slices by lines (the default), preserving frontmatter."""
    # init a wiki and author a multi-line page, then update
    wiki = Wiki(tmp_path)
    wiki.init()
    (tmp_path / 'long.md').write_text(
        '---\nname: long\ndesc: A long page.\n---\n\n# long\n\n'
        'line one\nline two\nline three\nline four\n',
        encoding='utf-8',
    )
    wiki.update()

    # read with stop slices by line index (the default), keeping frontmatter
    content = wiki.read('long', stop=3)
    assert 'line one' in content
    assert 'line four' not in content
    assert 'name: long' in content


@pytest.mark.parametrize(
    ('operation', 'name'),
    [
        ('read', '../outside/secret'),
        ('read', '{outside}/secret.md'),
        ('search', '..'),
        ('map', '../outside'),
        ('update', '..'),
        ('lint', '../outside'),
    ],
    ids=[
        'read-relative',
        'read-absolute',
        'search-parent',
        'map-sibling',
        'update-parent',
        'lint-sibling',
    ],
)
def test_operations_refuse_paths_outside_root(
    tmp_path: pathlib.Path,
    operation: str,
    name: str,
) -> None:
    """Name-taking operations refuse to resolve targets outside the wiki root.

    Wiki names are agent-supplied input: a relative or absolute name whose
    target escapes the root must be rejected -- never read, searched, mapped,
    or rewritten -- even when the target exists.
    """
    # build a wiki beside a sibling file it must never reach
    root = tmp_path / 'wiki'
    wiki = _make_wiki(root)
    outside = tmp_path / 'outside'
    outside.mkdir()
    secret = outside / 'secret.md'
    secret.write_text('Secret content.\n', encoding='utf-8')

    # bind the escaping name to the operation under test
    name = name.format(outside=outside)
    calls = {
        'read': lambda: wiki.read(name),
        'search': lambda: wiki.search('Secret', name=name),
        'map': lambda: wiki.map(name),
        'update': lambda: wiki.update(name),
        'lint': lambda: wiki.lint(name),
    }

    # the escaping name is refused ...
    with pytest.raises(ValueError, match='outside wiki root'):
        calls[operation]()
    # ... and the outside file is untouched
    assert secret.read_text(encoding='utf-8') == 'Secret content.\n'


def test_read_slice_units(tmp_path: pathlib.Path) -> None:
    """``read`` slices by words/lines/chars; words keep original spacing.

    Only the frontmatter is special: the H1 leads the body, so it occupies the
    first word/line/char positions and is sliced alongside the prose. An
    unknown unit is rejected loudly rather than returning unsliced content.
    """
    wiki = Wiki(tmp_path)
    body = 'Alpha   beta gamma\ndelta epsilon.'
    (tmp_path / 'p.md').write_text(
        f'---\nname: P\ndesc: A page.\n---\n\n# P\n\n{body}\n',
        encoding='utf-8',
    )
    # the H1 leads the body: words 2:4 reach the prose, keeping original spacing
    assert 'Alpha   beta' in wiki.read('p', start=2, stop=4, on='words')
    # the first body line is the H1 heading, not the prose
    out = wiki.read('p', start=0, stop=1, on='lines')
    assert '# P' in out
    assert 'Alpha' not in out
    # chars slice by character, reaching the prose past the leading H1
    sliced = wiki.read('p', start=5, stop=10, on='chars')
    assert sliced.strip().endswith('Alpha')
    # an unknown unit is rejected loudly
    with pytest.raises(ValueError, match="must be 'lines', 'words', or 'chars'"):
        wiki.read('p', start=0, stop=1, on='paragraphs')


def test_read_suggests_unique_leaf_match(tmp_path: pathlib.Path) -> None:
    """A failed read of a bare leaf suggests the unique nested page's read key."""
    _make_wiki(tmp_path, folders={'team/eng': ['oncall']})
    wiki = Wiki(tmp_path)
    # the bare leaf misses, but the error names the path-joined key that resolves
    with pytest.raises(FileNotFoundError, match=r'did you mean team/eng/oncall'):
        wiki.read('oncall')
