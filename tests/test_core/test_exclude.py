"""Behavioral tests for ``exclude.patterns`` (``.wiki/settings.json``).

Policy validation, the gitignore-style matching semantics, the
walk-wide invisibility of excluded subtrees, the preserved-row /
``--prune`` contract naming the pattern as the cause, symlink
precedence, the nested-wiki lift, and the scope refusal. The lint,
map, search, and read surfaces are covered beside their precedent
tests in their own modules.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from wiki.core.wiki import Wiki

from ._helpers import _capture_notices, _make_wiki, _set_exclude_patterns

__all__ = [
    'test_exclude_policy_rejects_invalid_settings',
    'test_init_rejects_invalid_exclude_seed',
    'test_exclude_pattern_matching',
    'test_update_skips_excluded_subtree',
    'test_update_names_excluded_link_target',
    'test_update_symlink_precedence',
    'test_exclude_lifts_nested_wiki_refusal',
    'test_scope_inside_excluded_dir_refused',
]


# ------ policy validation


@pytest.mark.parametrize(
    argnames=('exclude', 'match'),
    argvalues=[
        ('vendor', r'exclude block must be a JSON object'),
        ({'patterns': 'vendor'}, r'exclude\.patterns must be a list'),
        ({'patterns': [5]}, r'entry must be a string'),
        ({'patterns': ['']}, r'empty or whitespace-only'),
        ({'patterns': ['   ']}, r'empty or whitespace-only'),
        ({'patterns': ['/']}, r'empty or whitespace-only'),
        ({'patterns': ['//']}, r'empty segment'),
        ({'patterns': ['a//b']}, r'empty segment'),
        ({'patterns': ['!vendor']}, r'negation'),
        ({'patterns': ['a\\b']}, r'separator'),
        ({'patterns': ['./vendor']}, r'segments are not allowed'),
        ({'patterns': ['a/../b']}, r'segments are not allowed'),
    ],
    ids=[
        'non-object-block',
        'non-list-patterns',
        'non-string-entry',
        'empty',
        'whitespace-only',
        'slash-alone',
        'double-slash-alone',
        'empty-segment',
        'negation',
        'backslash',
        'dot-segment',
        'dot-dot-segment',
    ],
)
def test_exclude_policy_rejects_invalid_settings(
    tmp_path: pathlib.Path,
    exclude: object,
    match: str,
) -> None:
    """A malformed ``exclude`` block fails loudly, naming the file and key.

    ``settings.json`` is user-editable input: a wrong-typed block or a
    pattern outside the supported grammar (negation and escapes are
    reserved, ``.``/``..`` and empty segments never name a wiki path)
    raises ``ValueError`` through any command that walks the tree,
    rather than silently indexing what the author meant to exclude.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    settings = tmp_path / '.wiki' / 'settings.json'
    settings.write_text(json.dumps({'exclude': exclude}), encoding='utf-8')

    # a fresh instance fails loudly, naming the settings file
    with pytest.raises(ValueError, match=match) as excinfo:
        Wiki(tmp_path).lint()
    assert 'exclude' in str(excinfo.value)
    assert 'settings.json' in str(excinfo.value)


def test_init_rejects_invalid_exclude_seed(tmp_path: pathlib.Path) -> None:
    """A rejected ``exclude`` seed aborts ``init`` before writing anything.

    The exclude policy is read lazily (first inside the walk), so init
    touches it up front: a bad seed must fail before the settings file,
    the root index, or any sweep write lands.
    """
    root = tmp_path / 'wiki'
    with pytest.raises(ValueError, match=r'exclude\.patterns'):
        Wiki(root).init(settings={'exclude': {'patterns': ['!vendor']}})
    assert not root.exists()


# ------ matching semantics


@pytest.mark.parametrize(
    argnames=('patterns', 'tree', 'indexed', 'excluded'),
    argvalues=[
        # a bare name floats: it matches its single segment at any depth
        (
            ['*.tmp'],
            ['a.tmp', 'core/b.tmp', 'core/keep.md'],
            ['core/', 'core/keep'],
            ['a.tmp', 'core/b.tmp'],
        ),
        # a leading '/' anchors the pattern at the wiki root
        (
            ['/scratch.md'],
            ['scratch.md', 'core/scratch.md'],
            ['core/scratch'],
            ['scratch'],
        ),
        # a pattern containing '/' is anchored without a leading '/'
        (
            ['core/notes'],
            ['core/notes/p.md', 'other/notes/p.md'],
            ['other/notes/'],
            ['core/notes/'],
        ),
        # a leading '**/' matches the trailing segments at any depth
        (
            ['**/build'],
            ['build/x.md', 'a/build/y.md', 'a/builder/z.md'],
            ['a/', 'a/builder/'],
            ['build/', 'a/build/'],
        ),
        # a middle '/**/' spans any nesting, including none
        (
            ['a/**/b'],
            ['a/x/b/p.md', 'a/b/q.md', 'c/b/r.md'],
            ['a/', 'a/x/', 'c/b/'],
            ['a/x/b/', 'a/b/'],
        ),
        # a trailing '/**' empties the folder but leaves it indexed
        (
            ['vendor/**'],
            ['vendor/lib.md', 'vendor/pkg/mod.md', 'keep.md'],
            ['vendor/', 'keep'],
            ['vendor/lib', 'vendor/pkg/'],
        ),
        # '?' matches exactly one non-separator character
        (
            ['temp?'],
            ['temp1/a.md', 'temp22/b.md'],
            ['temp22/'],
            ['temp1/'],
        ),
        # '[seq]' classes are fnmatch-style
        (
            ['drafts/[ab]*'],
            ['drafts/alpha.md', 'drafts/beta.md', 'drafts/gamma.md'],
            ['drafts/gamma'],
            ['drafts/alpha', 'drafts/beta'],
        ),
        # '[!seq]' negates the class
        (
            ['logs/[!a]*'],
            ['logs/apple.md', 'logs/beta.md'],
            ['logs/apple'],
            ['logs/beta'],
        ),
        # an embedded '**' collapses to '*' ('**' only spans whole segments)
        (
            ['v**r'],
            ['vendor/x.md', 'var/y.md', 'vex.md'],
            ['vex'],
            ['vendor/', 'var/'],
        ),
        # one trailing '/' is stripped (a pasted gitignore line just works)
        (
            ['vendor/'],
            ['vendor/lib.md', 'keep.md'],
            ['keep'],
            ['vendor/', 'vendor/lib'],
        ),
        # matching is case-sensitive on the on-disk byte form
        (
            ['Vendor'],
            ['vendor/lib.md'],
            ['vendor/', 'vendor/lib'],
            [],
        ),
        # excluding a directory excludes its whole subtree
        (
            ['a'],
            ['a/b/c/deep.md'],
            [],
            ['a/', 'a/b/', 'a/b/c/', 'a/b/c/deep'],
        ),
    ],
    ids=[
        'floating',
        'anchored-root',
        'anchored-nested',
        'leading-doublestar',
        'middle-doublestar',
        'trailing-doublestar',
        'question-mark',
        'class',
        'negated-class',
        'embedded-doublestar',
        'trailing-slash',
        'case-sensitive',
        'ancestor-subtree',
    ],
)
def test_exclude_pattern_matching(
    tmp_path: pathlib.Path,
    patterns: list[str],
    tree: list[str],
    indexed: list[str],
    excluded: list[str],
) -> None:
    """Each glob form excludes exactly its documented shape.

    Semantics are asserted through observable behavior: after an
    ``update`` sweep, an indexed folder carries an ``_index.md`` and an
    indexed page a ``[[target|...]]`` row somewhere in the tree, while
    an excluded entry has neither. Entries ending in ``/`` name
    folders, the rest name link targets. The translator's regex corners
    are additionally pinned by the ``wiki.util.glob`` doctests.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root', settings={'exclude': {'patterns': patterns}})
    for entry in tree:
        page = tmp_path / entry
        page.parent.mkdir(parents=True, exist_ok=True)
        if entry.endswith('.md'):
            stem = pathlib.PurePosixPath(entry).stem
            page.write_text(
                f'---\nname: {stem}\ndesc: A page.\n---\n\n# {stem}\n\nBody.\n',
                encoding='utf-8',
            )
        else:
            page.write_text('raw\n', encoding='utf-8')
    wiki.update()

    # collect every generated index row across the tree
    rows = '\n'.join(
        index.read_text(encoding='utf-8') for index in tmp_path.rglob('_index.md')
    )
    for entry in indexed:
        if entry.endswith('/'):
            assert (tmp_path / entry / '_index.md').is_file(), entry
        else:
            assert f'[[{entry}|' in rows, entry
    for entry in excluded:
        if entry.endswith('/'):
            assert not (tmp_path / entry / '_index.md').exists(), entry
        else:
            assert f'[[{entry}|' not in rows, entry


# ------ update, prune, and scope behavior


def test_update_skips_excluded_subtree(tmp_path: pathlib.Path) -> None:
    """An excluded subtree is never scaffolded, adopted, or rewritten.

    The subtree is invisible to the walk: no ``_index.md`` lands inside
    it, a bare page there is never adopted, and its bytes stay
    untouched, while the sibling subtree keeps indexing normally.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    # author an unindexed subtree, then exclude it
    vendor = tmp_path / 'vendor'
    (vendor / 'pkg').mkdir(parents=True)
    bare = vendor / 'pkg' / 'bare.md'
    bare.write_text('# bare\n\nNo frontmatter.\n', encoding='utf-8')
    _set_exclude_patterns(tmp_path, ['vendor'])

    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)
    wiki.update()
    # nothing scaffolded or adopted inside; the bytes are untouched
    assert not (vendor / '_index.md').exists()
    assert not (vendor / 'pkg' / '_index.md').exists()
    assert bare.read_text(encoding='utf-8') == '# bare\n\nNo frontmatter.\n'
    assert not any('Adopted' in event.description for event in notices)
    # the sibling stays indexed, and no row names the excluded subtree
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    assert '[[core/_index|core/]]' in root_index
    assert 'vendor' not in root_index


def test_update_names_excluded_link_target(tmp_path: pathlib.Path) -> None:
    """An index link whose target became excluded names the pattern.

    Excluded paths are dropped from the walk, so the link is no longer
    backed by an indexed entry -- but its target is still on disk, and
    a generic broken-link warning would send the user hunting for a
    deleted file. Update names the exclusion (and its pattern) as the
    cause instead, preserving the row; prune removes it, naming the
    removal alongside the cause.
    """
    _make_wiki(tmp_path, folders={'data': ['child', 'report']})
    # index the page first, then exclude it
    _set_exclude_patterns(tmp_path, ['data/report.md'])
    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)

    # the exclusion is named as the cause, not a generic broken link
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Link targets an excluded path:' in err
    assert "exclude.patterns 'data/report.md'" in err
    assert 'Broken link:' not in err
    # the row is preserved by default
    index = tmp_path / 'data' / '_index.md'
    assert '[[data/report|report]]' in index.read_text(encoding='utf-8')
    # prune removes the row, naming both the removal and the cause
    notices.clear()
    wiki.update(prune=True)
    err = '\n'.join(event.description for event in notices)
    assert 'Pruned link:' in err
    assert 'Link targets an excluded path:' in err
    assert '[[data/report|report]]' not in index.read_text(encoding='utf-8')


def test_update_symlink_precedence(tmp_path: pathlib.Path) -> None:
    """A symlinked and excluded target reports the symlink cause alone.

    Symlinked files are excluded unconditionally and checked first, so
    a target that is both a symlink and pattern-excluded keeps today's
    symlink-skip report rather than gaining a second cause.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    _make_wiki(root, folders={'data': ['child']})
    # index a real file, then swap it for a symlink AND exclude its path
    page = root / 'data' / 'report.md'
    page.write_text(
        '---\nname: report\ndesc: A page.\n---\n\n# report\n\nText.\n',
        encoding='utf-8',
    )
    Wiki(root).update()
    secret = tmp_path / 'secret'
    secret.write_text('outside\n', encoding='utf-8')
    page.unlink()
    page.symlink_to(secret)
    _set_exclude_patterns(root, ['data/report.md'])
    wiki = Wiki(root)
    notices = _capture_notices(wiki)

    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Link targets a symlink:' in err
    assert 'Link targets an excluded path:' not in err


def test_exclude_lifts_nested_wiki_refusal(tmp_path: pathlib.Path) -> None:
    """An excluded nested declared wiki no longer refuses the sweep.

    A vendored wiki (a directory carrying its own
    ``.wiki/settings.json``) inside the tree refuses every sweep;
    excluding its directory drops it from the walk, so the host wiki
    updates cleanly around it and the guest stays untouched.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    # a nested declared wiki under vendor/
    nested = tmp_path / 'vendor' / 'guest'
    _make_wiki(nested, folders={'notes': ['page']})
    # without the pattern the sweep refuses, naming the nested root
    with pytest.raises(ValueError, match=r'encloses the wiki at: .*guest'):
        Wiki(tmp_path).update()
    # with the pattern the sweep proceeds, leaving the guest untouched
    _set_exclude_patterns(tmp_path, ['vendor'])
    before = (nested / '_index.md').read_text(encoding='utf-8')
    Wiki(tmp_path).update()
    assert (nested / '_index.md').read_text(encoding='utf-8') == before
    assert not (tmp_path / 'vendor' / '_index.md').exists()


@pytest.mark.parametrize('operation', ['update', 'lint', 'map', 'search'])
def test_scope_inside_excluded_dir_refused(
    tmp_path: pathlib.Path,
    operation: str,
) -> None:
    """A scope at or under an excluded directory is refused, naming the pattern.

    The walk skips excluded directories by construction, so scaffolding
    (or previewing) indexes inside one would leave junk no later walk
    can see or repair -- the same refusal dot directories get, extended
    with the pattern so the author knows which rule to lift.
    """
    _make_wiki(tmp_path, folders={'vendor/pkg': ['mod']})
    _set_exclude_patterns(tmp_path, ['vendor'])
    wiki = Wiki(tmp_path)
    calls = {
        'update': lambda: wiki.update('vendor/pkg'),
        'lint': lambda: wiki.lint('vendor/pkg'),
        'map': lambda: wiki.map('vendor/pkg'),
        'search': lambda: wiki.search('x', name='vendor/pkg'),
    }

    with pytest.raises(ValueError, match='excluded directory') as excinfo:
        calls[operation]()
    assert "exclude.patterns 'vendor'" in str(excinfo.value)
