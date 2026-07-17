"""Behavioral tests for ``Wiki.map``.

Tree rendering, category labels and filters (including matches
beyond the depth cutoff), word labels, presentation knobs, and the
counts cache -- damage survival included.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil

import pytest

from wiki.core.wiki import Wiki

from ._helpers import (
    CategorizedWiki,
    _capture_notices,
    _make_category_folder,
    _make_wiki,
)

__all__ = [
    'test_update_creates_self_ignoring_cache',
    'test_update_announces_recreated_cache',
    'test_map_survives_cache_damage',
    'test_quoted_category_labels_and_filters',
    'test_map_output',
    'test_map_folds_multiline_desc',
    'test_map_unindexed',
    'test_map_word_counts',
    'test_map_handles_dotted_markdown_stem',
    'test_map_presentation_configurable',
    'test_map_rejects_malformed_settings',
    'test_map_names_undecodable_index',
    'test_map_descs_unbounded_by_default',
    'test_markerless_index_warns_in_map_and_flags_in_lint',
    'test_map_survives_binary_attachment',
    'test_non_word_category_labels_filters_and_resolves',
    'test_map_category_shows_matches_beyond_depth',
    'test_map_marks_copied_subtree_links_broken',
]


# ------ counts cache


def test_update_creates_self_ignoring_cache(tmp_path: pathlib.Path) -> None:
    """Update materializes ``.wiki/cache/`` with counts and a self-ignoring rule.

    Derived counts live in one git-ignored cache file rather than every
    page's frontmatter; the ``.wiki/cache/`` directory carries its own
    ``.gitignore`` (``*``) so no host repo configuration is needed.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    gitignore = (tmp_path / '.wiki' / 'cache' / '.gitignore').read_text(
        encoding='utf-8'
    )
    assert gitignore.strip() == '*'
    # entries carry each page's body word count keyed by relative path
    counts = json.loads(
        (tmp_path / '.wiki' / 'cache' / 'word_counts.json').read_text(encoding='utf-8')
    )
    # the body is the H1 ('# core/design' = 2 words) plus three words of prose
    assert counts['core/design.md']['words'] == 5


def test_update_announces_recreated_cache(
    tmp_path: pathlib.Path,
) -> None:
    """A deleted ``.wiki/cache/`` is recreated with a notice, never silently.

    The cache is pure derived state, so recreating it is always safe --
    but a deletion undone without a word reads as if the delete never
    happened.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    shutil.rmtree(tmp_path / '.wiki' / 'cache')
    notices = _capture_notices(wiki)

    # the recreation is announced and the cache is materialized again
    wiki.update()
    descriptions = '\n'.join(event.description for event in notices)
    assert 'Recreated .wiki/cache/' in descriptions
    assert (tmp_path / '.wiki' / 'cache' / 'word_counts.json').is_file()

    # an ordinary refresh stays quiet
    notices.clear()
    wiki.update()
    descriptions = '\n'.join(event.description for event in notices)
    assert 'Recreated .wiki/cache/' not in descriptions


@pytest.mark.parametrize('damage', ['missing', 'corrupt'], ids=['missing', 'corrupt'])
def test_map_survives_cache_damage(tmp_path: pathlib.Path, damage: str) -> None:
    """A missing or corrupt counts cache is rebuilt, never an error.

    The cache is pure derived state: the worst case for any damage is a
    full recompute.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    cache = tmp_path / '.wiki' / 'cache' / 'word_counts.json'
    if damage == 'missing':
        cache.unlink()
    else:
        cache.write_text('{not json', encoding='utf-8')

    # map still renders real counts, and the cache is rebuilt behind it
    output = wiki.map()
    assert re.search(r'design \(\d+\)', output)
    counts = json.loads(cache.read_text(encoding='utf-8'))
    assert 'core/design.md' in counts
    # serialization is key-sorted, so the cache is stable and diffable
    assert list(counts) == sorted(counts)


# ------ rendering and labels


def test_quoted_category_labels_and_filters(tmp_path: pathlib.Path) -> None:
    """A quoted category still labels, filters, and resolves its folder.

    The category reader must strip YAML quotes: a quoted value would
    otherwise leak into the label, the filter, and the folder path.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root')
    _make_category_folder(tmp_path, 'store', '"backend"', 'The store layer.')
    wiki.update()

    # the parent label carries the unquoted category prefix
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    assert '[[store/_index|[backend] store/]]' in root_index

    # the category filter matches, and the folder resolves (never unindexed)
    filtered = wiki.map(category=['backend'])
    assert 'store/' in filtered
    assert '(unindexed)' not in wiki.map()


def test_map_output(tmp_path: pathlib.Path) -> None:
    """Map renders an indented tree with category prefixes, words, and filters."""
    # build a category-ordered wiki with a categorized folder, an
    # uncategorized folder, a page, and a non-markdown file
    wiki = CategorizedWiki(tmp_path)
    wiki.init(name='root')
    _make_category_folder(tmp_path, 'cache', 'node', 'The cache layer.')
    _make_category_folder(tmp_path, 'notes', '', 'Free-form notes.')
    (tmp_path / 'cache' / 'design.md').write_text(
        '# design\n\nseveral words of body content here now.\n',
        encoding='utf-8',
    )
    (tmp_path / 'cache' / 'data.csv').write_text('a,b,c\n', encoding='utf-8')
    wiki.update()

    # category prefix and indented children
    output = wiki.map()
    assert '[node] cache/' in output
    assert 'notes/' in output
    assert '  design' in output
    assert '  data.csv' in output

    # depth=0 limits to top-level entries
    assert 'design' not in wiki.map(depth=0)

    # category filter applies at all depths: -c=node surfaces node
    # entries; -c='' surfaces uncategorized entries even nested under a
    # categorized folder (which appears as the path to them)
    node_only = wiki.map(category=['node'])
    assert '[node] cache/' in node_only
    assert 'notes/' not in node_only
    uncategorized_only = wiki.map(category=[])
    assert 'notes/' in uncategorized_only
    assert '  design' in uncategorized_only

    # markdown filter: True keeps .md pages, False keeps other files
    assert 'design' in wiki.map(markdown=True)
    assert 'data.csv' not in wiki.map(markdown=True)
    assert 'data.csv' in wiki.map(markdown=False)
    assert 'design' not in wiki.map(markdown=False)

    # words=False drops the (count) annotations
    assert '(' not in wiki.map(words=False)

    # desc_limit truncates long descriptions
    assert 'The cache layer.' not in wiki.map(desc_limit=4)


def test_map_folds_multiline_desc(tmp_path: pathlib.Path) -> None:
    """Map shows the full desc with newlines folded to spaces."""
    # author a block-scalar desc whose breaks land in the index row
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'core' / 'layers.md').write_text(
        '---\nname: layers\ndesc: |\n  Layered architecture with strict\n'
        '  dependency direction.\n---\n\n# layers\n\nText.\n',
        encoding='utf-8',
    )
    wiki.update()

    # the whole desc renders on the map line, folded to one line
    output = wiki.map()
    assert 'Layered architecture with strict dependency direction.' in output


def test_map_unindexed(tmp_path: pathlib.Path) -> None:
    """Map marks un-indexed folders instead of crashing."""
    # build a populated wiki with one folder and page
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    # a folder created after update has no _index.md
    (tmp_path / 'bare').mkdir()
    # top-level target with no index -> single (unindexed) line, no crash
    assert wiki.map(name='bare') == 'bare/ (unindexed)'
    # a linked child whose index is deleted is marked in-tree; the rest still renders
    (tmp_path / 'core' / '_index.md').unlink()
    output = wiki.map()
    assert 'core/ (unindexed)' in output


def test_map_word_counts(tmp_path: pathlib.Path) -> None:
    """Folders render ``(page/tree)``; pages render a single ``(page)``.

    The tree total includes the folder's own index prose, so a leaf folder
    shows equal halves -- ``(N/N)`` -- rather than ``(N/0)``, and a
    parent's tree total exceeds its own page count once a child is added.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root')
    # leaf folder: five words of index prose, no pages or subfolders
    leaf = tmp_path / 'leaf'
    leaf.mkdir()
    (leaf / '_index.md').write_text(
        '---\nname: leaf\ndesc: A leaf.\n---\n\n# leaf\n\n***\n\n'
        'one two three four five\n',
        encoding='utf-8',
    )
    # parent folder with a child page so its subtree exceeds its own prose
    parent = tmp_path / 'parent'
    parent.mkdir()
    (parent / '_index.md').write_text(
        '---\nname: parent\ndesc: A parent.\n---\n\n# parent\n\n***\n\nalpha beta\n',
        encoding='utf-8',
    )
    (parent / 'child.md').write_text(
        '---\nname: child\ndesc: A child.\n---\n\n# child\n\nsome words here\n',
        encoding='utf-8',
    )
    wiki.update()
    output = wiki.map()

    # leaf: tree includes its own index prose -- a childless folder shows equal
    # halves (page == tree), not (N/0)
    leaf_match = re.search(r'leaf/ \((\d+)/(\d+)\)', output)
    assert leaf_match is not None
    assert leaf_match.group(1) == leaf_match.group(2)
    assert int(leaf_match.group(1)) > 0
    # parent: a folder always shows the ratio, and the tree exceeds its own page
    match = re.search(r'parent/ \((\d+)/(\d+)\)', output)
    assert match is not None
    page_count, tree_count = int(match.group(1)), int(match.group(2))
    assert tree_count > page_count
    # page: a single count, no ratio slash
    assert re.search(r'child \(\d+\)', output)


def test_map_handles_dotted_markdown_stem(tmp_path: pathlib.Path) -> None:
    """A dotted markdown stem (``my.notes.md``) counts words and filters as md.

    Resolving such a page by a name test (``'.' in name``) would read its
    word count from a missing file (0) and invert the ``--markdown`` filter;
    the map must probe the actual ``<name>.md`` file.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root')
    (tmp_path / 'my.notes.md').write_text(
        '---\nname: notes\ndesc: d\n---\n\n# Notes\n\nalpha beta gamma\n',
        encoding='utf-8',
    )
    wiki.update()
    # word count covers the body incl. the H1 (5), not 0 from a mis-resolved path
    assert re.search(r'my\.notes \(5\)', wiki.map())
    # classified as markdown: shown with --markdown, hidden without
    assert 'my.notes' in wiki.map(markdown=True)
    assert 'my.notes' not in wiki.map(markdown=False)


def test_map_presentation_configurable(tmp_path: pathlib.Path) -> None:
    """settings.json ``map.*`` knobs set the indent, ellipsis, and desc limit."""
    config = tmp_path / '.wiki'
    config.mkdir()
    (config / 'settings.json').write_text(
        json.dumps({'map': {'indent': '. ', 'ellipsis': '###', 'desc_limit': 15}}),
        encoding='utf-8',
    )
    # a page with a long desc so the desc limit truncates it
    _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        '---\nname: core/design\ndesc: A long design note about the subsystem.\n---'
        '\n\n# core/design\n\nBody.\n',
        encoding='utf-8',
    )
    Wiki(tmp_path).update()
    out = Wiki(tmp_path).map()
    # map.indent: the nested page entry uses the custom indent unit
    assert any(line.startswith('. ') for line in out.splitlines())
    # map.desc_limit: the settings value bounds each desc;
    # map.ellipsis: a truncated desc ends with the custom marker
    assert 'A long design note about the subsystem.' not in out
    assert '###' in out
    # an explicit limit beats the settings value, and -1 disables truncation
    wide = Wiki(tmp_path).map(desc_limit=100)
    assert 'A long design note about the subsystem.' in wide
    unlimited = Wiki(tmp_path).map(desc_limit=-1)
    assert 'A long design note about the subsystem.' in unlimited


@pytest.mark.parametrize(
    ('content', 'match'),
    [
        ('{"map": [1]}', r'map block must be a JSON object'),
        ('{"map": {"desc_limit": "10"}}', r'desc_limit must be an int'),
        ('{"map": {"indent": 2}}', r'indent must be a string'),
        ('{"map": {"ellipsis": 5}}', r'ellipsis must be a string'),
    ],
    ids=[
        'non-object-map',
        'non-int-desc-limit',
        'non-string-indent',
        'non-string-ellipsis',
    ],
)
def test_map_rejects_malformed_settings(
    tmp_path: pathlib.Path,
    content: str,
    match: str,
) -> None:
    """Malformed ``map.*`` settings fail loudly, naming the file and key.

    ``settings.json`` is user-editable input: a wrong-typed presentation
    knob raises ``ValueError`` naming the file rather than leaking a raw
    exception from deep inside the map render.
    """
    # build a valid wiki, then corrupt its settings.json
    _make_wiki(tmp_path, folders={'core': ['design']})
    settings = tmp_path / '.wiki' / 'settings.json'
    settings.write_text(content, encoding='utf-8')

    # a fresh instance fails loudly, naming the settings file
    with pytest.raises(ValueError, match=match) as excinfo:
        Wiki(tmp_path).map()
    assert 'settings.json' in str(excinfo.value)


def test_map_names_undecodable_index(tmp_path: pathlib.Path) -> None:
    """An undecodable ``_index.md`` fails map with an error naming the file.

    A bare decode error carries only a byte offset -- unactionable on a
    tree of thousands of files -- so the render walk and the category
    prune must both name the offending index.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    index = tmp_path / 'core' / '_index.md'
    index.write_bytes(b'\xff\xfe not utf-8')

    # both the render walk and the category prune name the file
    with pytest.raises(UnicodeDecodeError, match=r'core/_index\.md'):
        wiki.map(words=False)
    with pytest.raises(UnicodeDecodeError, match=r'core/_index\.md'):
        wiki.map(category=['x'], words=False)


def test_map_descs_unbounded_by_default(tmp_path: pathlib.Path) -> None:
    """Descs reproduce whole unless a limit opts into truncation.

    Every map row naming a page reproduces its desc; with no
    ``map.desc_limit`` and no ``--desc-limit``, the dump stays faithful,
    and bounding the output is an explicit choice.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    long_desc = ('All about the widget design and its many moving parts. ' * 4).strip()
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        f'---\nname: design\ndesc: {long_desc}\n---\n\n# design\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()

    # the default map reproduces the full desc, no cut marker
    unbounded = Wiki(tmp_path).map()
    assert long_desc in unbounded
    assert '...' not in unbounded
    # an explicit limit bounds each desc, marking the cut
    bounded = Wiki(tmp_path).map(desc_limit=200)
    assert long_desc not in bounded
    assert '...' in bounded


def test_markerless_index_warns_in_map_and_flags_in_lint(
    tmp_path: pathlib.Path,
) -> None:
    """A root index that lost its ``***`` is named by map (warn) and lint.

    Without the delimiter the demoted link rows await ``wiki update``'s
    reclaim; until it runs, map must warn rather than read the populated wiki
    as empty, and lint must name the missing marker so the cause is obvious.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    # drop the root *** delimiter while pages remain on disk
    root_index = tmp_path / '_index.md'
    text = root_index.read_text(encoding='utf-8')
    text = '\n'.join(line for line in text.split('\n') if line.rstrip() != '***')
    root_index.write_text(text, encoding='utf-8')

    # map warns about the missing delimiter instead of returning an empty tree
    notices = _capture_notices(wiki)
    wiki.map()
    err = '\n'.join(event.description for event in notices)
    assert 'missing its *** delimiter' in err

    # lint names the missing marker specifically
    assert any('Index missing *** delimiter' in issue for issue in wiki.lint())


def test_map_survives_binary_attachment(tmp_path: pathlib.Path) -> None:
    """One binary attachment neither crashes map nor reads anonymously.

    Non-markdown files are first-class wiki entries; a word-count pass that
    read every entry as UTF-8 would let a single indexed image fail ``map``
    wiki-wide with an error naming no path.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    binary = tmp_path / 'core' / 'diagram.png'
    binary.write_bytes(b'\x89PNG\r\n\x1a\n\xff\xfe\x00\x01')
    wiki.update()

    # the whole tree still maps, with the attachment listed
    output = wiki.map()
    assert 'diagram.png' in output
    assert 'design' in output

    # reading the binary itself names the file, not a bare decode error
    with pytest.raises(UnicodeDecodeError, match=r'diagram\.png'):
        wiki.read('core/diagram.png')


# ------ category filters


@pytest.mark.parametrize(
    'category',
    ['to-do', 'v1.2', 'my cat'],
    ids=['dashed', 'dotted', 'spaced'],
)
def test_non_word_category_labels_filters_and_resolves(
    tmp_path: pathlib.Path,
    category: str,
) -> None:
    """A category with punctuation still labels, filters, and resolves.

    Were ``_parse_category`` to match word characters only, a dashed/dotted/
    spaced category would make the whole bracketed label read as the entry
    name, silently corrupting the map, its counts, and both filters.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root')
    _make_category_folder(tmp_path, 'db', category, 'The db section.')
    (tmp_path / 'db' / 'notes.md').write_text(
        '---\nname: notes\ndesc: Db pages.\n---\n\n# notes\n\nBody words here.\n',
        encoding='utf-8',
    )
    (tmp_path / 'alpha.md').write_text(
        f'---\nname: alpha\ndesc: A page.\ncategory: {category}\n---\n\n'
        '# alpha\n\nBody.\n',
        encoding='utf-8',
    )
    wiki.update()

    # the folder resolves (never unindexed) and its subtree stays visible
    output = wiki.map()
    assert f'[{category}] db/' in output
    assert '(unindexed)' not in output
    assert 'notes' in output
    # the page reads its real word count, not 0 from a mis-resolved path
    count = re.search(rf'\[{re.escape(category)}\] alpha \((\d+)\)', output)
    assert count is not None
    assert count.group(1) != '0'
    # the category filter matches, and the markdown filter is not inverted
    filtered = wiki.map(category=[category])
    assert 'alpha' in filtered
    assert 'db/' in filtered
    assert 'alpha' in wiki.map(markdown=True)


def test_map_category_shows_matches_beyond_depth(tmp_path: pathlib.Path) -> None:
    """A folder whose only category matches lie below ``--depth`` shows.

    The category prune drops a folder whose rendered children are empty,
    but a depth cutoff empties them for depth reasons, not content -- the
    subtree must be probed so ``--category X --depth 0`` never reports a
    populated wiki as empty.
    """
    wiki = _make_wiki(tmp_path)
    for folder_name, category in [('outer', 'keep'), ('plain', '')]:
        folder = tmp_path / folder_name
        folder.mkdir()
        frontmatter = '---\nname: inner\ndesc: Inner.\n'
        if category:
            frontmatter += f'category: {category}\n'
        (folder / 'inner.md').write_text(
            frontmatter + '---\n\n# inner\n\nBody.\n',
            encoding='utf-8',
        )
    wiki.update()

    # unlimited depth shows the folder and its matching page
    full = wiki.map(category=['keep'])
    assert 'outer/' in full
    assert 'inner' in full
    # a depth cutoff still shows the folder (its match lies beyond it) ...
    shallow = wiki.map(category=['keep'], depth=0)
    assert 'outer/' in shallow
    assert 'inner' not in shallow
    # ... while a folder with no matching descendants stays pruned
    assert 'plain/' not in shallow


# ------ broken links


def test_map_marks_copied_subtree_links_broken(tmp_path: pathlib.Path) -> None:
    """Map resolves entries by target, annotating preserved broken links.

    Copying a subtree keeps its root-relative links; update preserves
    them as broken beside the regenerated ones. Resolving entries by
    display label would render each broken link as its healthy same-named
    sibling, with no brokenness hint.
    """
    wiki = _make_wiki(tmp_path, folders={'src': ['doc']})
    shutil.copytree(tmp_path / 'src', tmp_path / 'dup')
    wiki.update()

    # the two healthy entries render with real counts; the preserved
    # broken link is annotated instead of impersonating its sibling
    output = wiki.map()
    assert len(re.findall(r'^\s*doc \(\d', output, re.M)) == 2
    assert len(re.findall(r'^\s*doc \(broken\)', output, re.M)) == 1
