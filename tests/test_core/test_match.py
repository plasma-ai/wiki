"""Behavioral tests for ``Wiki.match``.

Body and frontmatter-field matching, and the body-region agreement
with ``read`` slicing and the word counts.
"""

from __future__ import annotations

import pathlib

import pytest

from wiki.core.wiki import Wiki

from ._helpers import _make_wiki, _set_exclude_patterns

__all__ = [
    'test_body_includes_h1_for_counts_and_match',
    'test_match_field_matches_value_only',
    'test_all_files_matches_non_markdown_whole',
    'test_match_skips_excluded_paths',
    'test_match_survives_page_deleted_mid_scan',
]


def test_body_includes_h1_for_counts_and_match(
    tmp_path: pathlib.Path,
) -> None:
    """Only the frontmatter is special; the H1 is ordinary body content.

    Word count, match, and ``read`` slicing all cover everything below the
    frontmatter -- the H1 heading and an index's auto-generated link block
    alike -- so a query matches the H1 line and the count includes it.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root')
    (tmp_path / 'topic.md').write_text(
        '---\nname: topic\ndesc: d\n---\n\n# topic\n\nbody prose words\n',
        encoding='utf-8',
    )
    wiki.update()
    # the count covers the H1 ("# topic" = 2) plus the prose (3)
    assert 'topic (5)' in wiki.map()
    # match hits the page's H1 line (frontmatter is skipped; prose lacks it)
    hits = wiki.match('topic')
    assert any(path == 'topic.md' and '# topic' in line for path, _, line in hits)
    # the index's auto-generated link block is body too, so it is matched as well
    assert any('_index.md' in path for path, _, _ in hits)


def test_match_field_matches_value_only(tmp_path: pathlib.Path) -> None:
    """``field`` patterns match the field's VALUE, never the ``key:`` prefix.

    Matching the raw line would mean a value anchor (``^...``) could
    never hit and a pattern naming the key (``desc``) would hit every
    line of that field; the match runs against the value alone --
    block-scalar continuation lines included, surrounding YAML quotes
    stripped -- while the reported line text stays raw.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'core' / 'block.md').write_text(
        '---\nname: block\ndesc: |\n  Multi-line summary.\n---\n\n# block\n\nBody.\n',
        encoding='utf-8',
    )
    # a ': ' in the page name makes update write the name quoted
    (tmp_path / 'core' / 'note: draft.md').write_text(
        '---\nname: note: draft\ndesc: d\n---\n\n# note: draft\n\nBody.\n',
        encoding='utf-8',
    )
    # a custom key may carry a hyphen (the field grammar is [\w-]+)
    (tmp_path / 'core' / 'tracked.md').write_text(
        '---\nname: tracked\ndesc: d\nreview-status: approved\n---\n\n# t\n\nBody.\n',
        encoding='utf-8',
    )
    # a dotted key is a field of its own and ends its neighbor
    (tmp_path / 'core' / 'foreign.md').write_text(
        '---\nname: foreign\ndesc: d\ncom.example: |\n  needle body\n---\n'
        '\n# f\n\nBody.\n',
        encoding='utf-8',
    )
    # a column-0 sequence item holding a colon belongs to its field
    (tmp_path / 'core' / 'cited.md').write_text(
        '---\nname: cited\ndesc: d\nsources:\n- https://doi.org/10.1/x\n---\n'
        '\n# c\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()

    # a value anchor matches from the value's first character
    hits = wiki.match('^The design', field='desc')
    assert [relpath for relpath, _, _ in hits] == ['core/design.md']
    # ... including on a block scalar's continuation lines
    hits = wiki.match('^Multi-line', field='desc')
    assert [relpath for relpath, _, _ in hits] == ['core/block.md']
    # the key name itself is never part of the searched text
    assert wiki.match('desc', field='desc') == []
    # anchors see the unquoted value even when the wiki quotes it (format.quote)
    for anchored in ('^core/note', 'draft$', '^core/note: draft$'):
        hits = wiki.match(anchored, field='name')
        assert [relpath for relpath, _, _ in hits] == ['core/note: draft.md']
    # a hyphenated custom key is a field like any other: the value anchor
    # hits and the key name stays out of the searched text
    hits = wiki.match('^approved', field='review-status')
    assert [relpath for relpath, _, _ in hits] == ['core/tracked.md']
    assert wiki.match('review', field='review-status') == []
    # a dotted key's line and block body never attribute to the field
    # before it: field-scoped search must not hit foreign-key content
    assert wiki.match('needle', field='desc') == []
    # a URL item at column 0 is part of its sequence field, colon and all
    hits = wiki.match('doi.org', field='sources')
    assert [relpath for relpath, _, _ in hits] == ['core/cited.md']


def test_all_files_matches_non_markdown_whole(tmp_path: pathlib.Path) -> None:
    """Frontmatter is a markdown concept; non-md files are searched whole.

    ``read`` slices non-markdown files whole, so a non-md file whose
    first lines form a ``---`` pair (a multi-document YAML, say) has no
    frontmatter to skip -- body search matches inside the leading block
    and ``field`` search never reads it as frontmatter.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root')
    (tmp_path / 'deploy.yaml').write_text(
        '---\nhost: prod.example.com\nport: 443\n---\nhost: staging.example.com\n',
        encoding='utf-8',
    )
    wiki.update()
    # body search matches inside the leading '---' pair and below it alike
    for pattern, lineno in [(r'prod\.example', 2), (r'staging\.example', 5)]:
        hits = wiki.match(pattern, all_files=True)
        assert [(path, num) for path, num, _ in hits] == [('deploy.yaml', lineno)]
    # field mode searches frontmatter, which a non-md file never carries
    assert wiki.match('prod', field='host', all_files=True) == []


def test_match_skips_excluded_paths(tmp_path: pathlib.Path) -> None:
    """Excluded files never surface in a match, ``all_files`` included.

    Match enumerates through the same walk update indexes with, so an
    ``exclude.patterns`` subtree is invisible to body matching and to the
    ``all_files`` sweep over non-markdown files alike.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'vendor').mkdir()
    (tmp_path / 'vendor' / 'lib.md').write_text(
        '---\nname: lib\ndesc: A page.\n---\n\n# lib\n\nneedle prose\n',
        encoding='utf-8',
    )
    (tmp_path / 'vendor' / 'raw.txt').write_text('needle raw\n', encoding='utf-8')
    (tmp_path / 'core' / 'keep.md').write_text(
        '---\nname: keep\ndesc: A page.\n---\n\n# keep\n\nneedle kept\n',
        encoding='utf-8',
    )
    _set_exclude_patterns(tmp_path, ['vendor'])
    wiki = Wiki(tmp_path)

    # only the indexed sibling matches, with or without all_files
    hits = wiki.match('needle')
    assert [relpath for relpath, _, _ in hits] == ['core/keep.md']
    hits = wiki.match('needle', all_files=True)
    assert [relpath for relpath, _, _ in hits] == ['core/keep.md']


def test_match_survives_page_deleted_mid_scan(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page deleted after enumeration never crashes match.

    Match reads each enumerated file directly, so a page vanishing
    between the walk and its read (a concurrent delete) must match as
    absent from the walk while the surviving pages still report hits.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['keep', 'doomed']})
    doomed = tmp_path / 'core' / 'doomed.md'
    real = Wiki._search_files

    def racy(
        self: Wiki,
        folder: pathlib.Path,
        **kwargs: bool,
    ) -> list[pathlib.Path]:
        """Delete the doomed page right after the walk lists it."""
        result = real(self, folder, **kwargs)
        if doomed.exists():
            doomed.unlink()
        return result

    # the mid-scan deletion is handled, not crashed on
    monkeypatch.setattr(Wiki, '_search_files', racy)
    hits = wiki.match('Content for')
    paths = [relpath for relpath, _, _ in hits]
    assert 'core/keep.md' in paths
    assert 'core/doomed.md' not in paths
