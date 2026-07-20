"""Behavioral tests for ``Wiki.search``.

Body and frontmatter-field search, and the body-region agreement
with ``read`` slicing and the word counts.
"""

from __future__ import annotations

import pathlib

from wiki.core.wiki import Wiki

from ._helpers import _make_wiki

__all__ = [
    'test_body_includes_h1_for_counts_and_search',
    'test_search_field_matches_value_only',
    'test_all_files_searches_non_markdown_whole',
]


def test_body_includes_h1_for_counts_and_search(
    tmp_path: pathlib.Path,
) -> None:
    """Only the frontmatter is special; the H1 is ordinary body content.

    Word count, search, and ``read`` slicing all cover everything below the
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
    # search matches the page's H1 line (frontmatter is skipped; prose lacks it)
    hits = wiki.search('topic')
    assert any(path == 'topic.md' and '# topic' in line for path, _, line in hits)
    # the index's auto-generated link block is body too, so it is matched as well
    assert any('_index.md' in path for path, _, _ in hits)


def test_search_field_matches_value_only(tmp_path: pathlib.Path) -> None:
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
    wiki.update()

    # a value anchor matches from the value's first character
    hits = wiki.search('^The design', field='desc')
    assert [relpath for relpath, _, _ in hits] == ['core/design.md']
    # ... including on a block scalar's continuation lines
    hits = wiki.search('^Multi-line', field='desc')
    assert [relpath for relpath, _, _ in hits] == ['core/block.md']
    # the key name itself is never part of the searched text
    assert wiki.search('desc', field='desc') == []
    # anchors see the unquoted value even when the wiki quotes it (format.quote)
    for anchored in ('^core/note', 'draft$', '^core/note: draft$'):
        hits = wiki.search(anchored, field='name')
        assert [relpath for relpath, _, _ in hits] == ['core/note: draft.md']


def test_all_files_searches_non_markdown_whole(tmp_path: pathlib.Path) -> None:
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
        hits = wiki.search(pattern, all_files=True)
        assert [(path, num) for path, num, _ in hits] == [('deploy.yaml', lineno)]
    # field mode searches frontmatter, which a non-md file never carries
    assert wiki.search('prod', field='host', all_files=True) == []
