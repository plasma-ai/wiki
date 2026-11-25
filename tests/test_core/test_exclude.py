"""Behavioral tests for the indexing exclusions.

``exclude.patterns`` (``.wiki/settings.json``): policy validation, the
gitignore-style matching semantics, the walk-wide invisibility of
excluded subtrees, the pruned-row notices naming the pattern as the
cause, symlink precedence, the nested-wiki lift, and the scope refusal.
The enclosing repo's gitignore fences: adoption/minting refusal for
fenced strays, pattern-pure matching, the ignored-root lift, and the
fence-named prune cause. The lint, map, match, and read surfaces are
covered beside their precedent tests in their own modules.
"""

from __future__ import annotations

import json
import pathlib
import random
import re
from collections.abc import Iterator
from typing import Optional

import pytest

from wiki.core.wiki import Wiki
from wiki.util import git
from wiki.util.glob import translate

from ._helpers import (
    _capture_notices,
    _git,
    _git_repo,
    _make_wiki,
    _needs_git,
    _set_exclude_patterns,
)

__all__ = [
    'test_exclude_policy_rejects_invalid_settings',
    'test_init_rejects_invalid_exclude_seed',
    'test_exclude_pattern_matching',
    'test_exclusion_names_the_pattern_a_full_scan_names',
    'test_update_skips_excluded_subtree',
    'test_update_names_excluded_link_target',
    'test_update_symlink_precedence',
    'test_exclude_lifts_nested_wiki_refusal',
    'test_scope_inside_excluded_dir_refused',
    'test_gitignored_residue_is_never_adopted',
    'test_fenced_directory_is_invisible_to_every_check',
    'test_gitignore_fence_is_pattern_pure',
    'test_gitignore_fence_ignores_machine_local_excludes',
    'test_gitignore_fence_ignores_an_inherited_git_dir',
    'test_unavailable_fence_is_narrated_inside_a_repository',
    'test_personally_ignored_row_draws_a_note',
    'test_reincluded_row_draws_no_note',
    'test_untrackable_notes_spare_tracked_and_reincluded_paths',
    'test_untrackable_notes_stop_at_an_ignored_path_inside_a_submodule',
    'test_untrackable_notes_read_ignore_files_from_disk',
    'test_a_leading_colon_reads_as_a_name_in_every_git_probe',
    'test_gitignore_fence_reaches_a_nested_wiki_root',
    'test_gitignore_fence_honors_a_discovery_ceiling',
    'test_unavailable_fence_note_honors_a_discovery_ceiling',
    'test_ignored_wiki_root_stays_unfenced',
    'test_allowlist_fence_holds_at_the_repository_root',
    'test_new_refuses_a_fenced_target',
    'test_new_judges_the_index_it_creates',
    'test_gitignored_link_target_names_the_cause',
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


@pytest.mark.parametrize('source', ['corners', 'fuzz', 'class-fuzz'])
def test_exclusion_names_the_pattern_a_full_scan_names(
    tmp_path: pathlib.Path,
    source: str,
) -> None:
    """A path is excluded by the pattern a scan of the whole list names.

    The reference runs every pattern's translation, in list order,
    against the path and each of its ancestors and names the first
    that matches; the wiki names the same pattern, or none, for every
    path. ``corners`` runs the hand-written patterns as one list, as
    the list reversed, one at a time and as seeded sub-lists, over
    names nested under each other; the fuzz sources draw seeded
    patterns (free-form, then mostly classes) and paths built to match
    them. Verdicts are read from the matcher itself: the paths hold
    newlines, control characters, and names differing only in case or
    normalization, which no one tree on disk can hold. Every source
    must also name a wildcard-free pattern, name a pattern holding a
    wildcard, and leave a path unnamed, so one whose patterns or paths
    thin out fails instead of passing on nothing.
    """
    root = tmp_path.resolve()
    _make_wiki(root)
    rng = random.Random(_SEED)
    if source == 'corners':
        cases = _corner_cases(rng)
    else:
        cases = _fuzz_cases(rng, root, classes=(source == 'class-fuzz'))
    kinds = set()
    for patterns, paths in cases:
        _set_exclude_patterns(root, patterns)
        wiki = Wiki(root)
        policy = [(pattern, re.compile(translate(pattern))) for pattern in patterns]
        for relative in paths:
            named = wiki._excluded_by(root / relative)
            assert named == _scan(policy, relative), (patterns, relative)
            kinds.add(_kind(named))
    assert kinds == {'literal', 'wildcard', None}


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


@pytest.mark.parametrize(
    argnames=('pattern', 'index', 'link'),
    argvalues=[
        # the pattern names the page itself
        ('data/report.md', 'data/_index.md', '[[data/report|report]]'),
        # the pattern names the folder, covering the row into its index
        ('data', '_index.md', '[[data/_index|data/]]'),
    ],
    ids=['page', 'folder'],
)
def test_update_names_excluded_link_target(
    tmp_path: pathlib.Path,
    pattern: str,
    index: str,
    link: str,
) -> None:
    """An index link whose target became excluded names the pattern.

    Excluded paths are dropped from the walk, so the link is no longer
    backed by an indexed entry -- but its target is still on disk, and
    a generic broken-link warning would send the user hunting for a
    deleted file. Update names the exclusion (and its pattern) as the
    cause beside the prune notice naming the removal, whether the
    pattern names the target itself or a folder above it.
    """
    _make_wiki(tmp_path, folders={'data': ['child', 'report']})
    # index the entries first, then exclude the target
    _set_exclude_patterns(tmp_path, [pattern])
    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)

    # the exclusion is named as the cause beside the removal
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert f'Link targets an excluded path: {link}' in err
    assert f"exclude.patterns '{pattern}'" in err
    assert f'Pruned link: {link}' in err
    assert link not in (tmp_path / index).read_text(encoding='utf-8')


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
    """An excluded nested declared wiki does not refuse the sweep.

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


@pytest.mark.parametrize('operation', ['update', 'lint', 'map', 'match'])
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
        'match': lambda: wiki.match('x', name='vendor/pkg'),
    }

    with pytest.raises(ValueError, match='excluded directory') as excinfo:
        calls[operation]()
    assert "exclude.patterns 'vendor'" in str(excinfo.value)


# ------ gitignore fences


@_needs_git
def test_gitignored_residue_is_never_adopted(tmp_path: pathlib.Path) -> None:
    """A gitignore-fenced stray is invisible: no adoption, no rows, no red.

    The battery-residue trap: a driver writes stray outputs beside its
    evidence, lint reds on the bare files, and the approved repair --
    ``wiki update`` -- is what adopts them (frontmatter written into
    driver files, ``_index.md`` cards minted, parent rows added). With
    the repo's fences extended to indexing, the residue draws no issue
    and update writes none of it into the corpus; dropping the fence
    line re-admits it.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    _git_repo(tmp_path, 'out/', 'TABLES.md')
    # driver residue: a bare page and a stray output directory
    residue = '# t\n\nraw rows\n'
    (tmp_path / 'core' / 'TABLES.md').write_text(residue, encoding='utf-8')
    out = tmp_path / 'core' / 'out'
    out.mkdir()
    (out / 'dump.md').write_text('# dump\n\nraw\n', encoding='utf-8')

    # lint stays clean: the residue is not corpus (fresh instance -- the
    # fence, like every policy, is cached per instance)
    wiki = Wiki(tmp_path)
    assert wiki.lint() == []
    # update adopts nothing and mints nothing
    notices = _capture_notices(wiki)
    wiki.update()
    assert (tmp_path / 'core' / 'TABLES.md').read_text(encoding='utf-8') == residue
    assert not (out / '_index.md').exists()
    index = (tmp_path / 'core' / '_index.md').read_text(encoding='utf-8')
    assert 'TABLES' not in index
    assert '[[core/out' not in index
    assert not any('Adopted' in event.description for event in notices)

    # dropping the fence re-admits the residue on the next sweep
    (tmp_path / '.gitignore').unlink()
    issues = Wiki(tmp_path).lint()
    assert any('Bare page' in issue for issue in issues)


@_needs_git
def test_fenced_directory_is_invisible_to_every_check(
    tmp_path: pathlib.Path,
) -> None:
    """A fenced directory is invisible to the directory-level checks too.

    The fence hides files from adoption, and it must hide their folder
    just as completely: no ``Missing index`` for the fenced directory,
    no row minted into it from its parent, and no rewrite of an
    ``_index.md`` already tracked inside it -- the corpus shape where a
    glob (``**/evidence/*/output/``) fences directories whose contents
    were committed before the fence existed, so the match must stay
    pattern-pure rather than reading those tracked files as content.
    """
    _git_repo(tmp_path, '**/evidence/*/output/')
    root = tmp_path / 'math'
    fenced = root / 'claim' / 'evidence' / 'depth_free' / 'output'
    fenced.mkdir(parents=True)
    _make_wiki(
        root,
        folders={
            'claim': [],
            'claim/evidence': [],
            'claim/evidence/depth_free': [],
        },
    )
    # residue committed before the fence existed: tracked, and fenced
    stale = '---\nname: output\ndesc: Stale.\n---\n\n# output\n\n***\n\nBody.\n'
    (fenced / '_index.md').write_text(stale, encoding='utf-8')
    (fenced / 'base_table.log').write_text('rows\n', encoding='utf-8')
    _git(tmp_path, 'add', '-f', str(root), '.gitignore')

    # every check is silent about the fenced directory, and it stays put
    wiki = Wiki(root)
    assert wiki.lint() == []
    assert wiki.update() == []
    parent = (fenced.parent / '_index.md').read_text(encoding='utf-8')
    assert 'output' not in parent
    assert (fenced / '_index.md').read_text(encoding='utf-8') == stale


@_needs_git
def test_gitignore_fence_is_pattern_pure(tmp_path: pathlib.Path) -> None:
    """A force-tracked file matching the fence is fenced all the same.

    ``git check-ignore`` consults the index by default, so junk already
    swept into version control would read as unfenced exactly where the
    repair matters; the fence matches patterns alone (``--no-index``),
    so the fence and the tool agree about what is corpus.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    _git_repo(tmp_path, 'TABLES.md')
    junk = tmp_path / 'core' / 'TABLES.md'
    junk.write_text('# t\n\nrows\n', encoding='utf-8')
    _git(tmp_path, 'add', '-f', str(junk))

    wiki = Wiki(tmp_path)
    assert wiki.lint() == []
    wiki.update()
    index = (tmp_path / 'core' / '_index.md').read_text(encoding='utf-8')
    assert 'TABLES' not in index


@_needs_git
def test_gitignore_fence_ignores_machine_local_excludes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user-global ``core.excludesFile`` never feeds the fence.

    The fence must read the same on every clone -- the repository's own
    committed ``.gitignore`` files -- or one machine's personal patterns
    would fence corpus content there alone, and its auto-pruned rows
    would churn against every other machine's updates.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    _git_repo(tmp_path)
    # a personal global config fencing the corpus folder
    excludes = tmp_path.parent / 'personal_ignore'
    excludes.write_text('core/\n', encoding='utf-8')
    gitconfig = tmp_path.parent / 'personal_gitconfig'
    gitconfig.write_text(f'[core]\n\texcludesFile = {excludes}\n', encoding='utf-8')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(gitconfig))

    # the corpus folder stays indexed: no prune, no invisibility
    wiki = Wiki(tmp_path)
    assert wiki.lint() == []
    wiki.update()
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    assert '[[core/_index|core/]]' in root_index


@_needs_git
@pytest.mark.parametrize(
    argnames='inherited',
    argvalues=['foreign-repo', 'dangling'],
)
def test_gitignore_fence_ignores_an_inherited_git_dir(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    inherited: str,
) -> None:
    """An inherited ``GIT_DIR`` never redirects or disables the fence.

    The fence is the enclosing repository's own rules, whatever git
    environment the caller carries -- a hook runs its commands with
    ``GIT_DIR`` exported. Pointed at another repository it would fence
    corpus content by foreign rules (pruning live rows, churning lint);
    pointed anywhere git cannot discover a repository it would drop the
    fence and adopt fenced residue, writing frontmatter into files the
    repo keeps out of version control.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    _git_repo(tmp_path, 'TABLES.md')
    residue = '# t\n\nraw rows\n'
    (tmp_path / 'core' / 'TABLES.md').write_text(residue, encoding='utf-8')
    # a second repository whose rules fence the corpus folder instead
    foreign = tmp_path.parent / f'{tmp_path.name}_foreign'
    foreign.mkdir()
    _git_repo(foreign)
    (foreign / '.git' / 'info' / 'exclude').write_text('core/\n', encoding='utf-8')
    # the git environment the caller carries in: another repository's, or
    # one no repository lives at
    git_dir = {'foreign-repo': foreign / '.git', 'dangling': foreign / 'gone' / '.git'}
    monkeypatch.setenv('GIT_DIR', str(git_dir[inherited]))

    # the verdict is the one the enclosing repo's rules give: corpus
    # indexed, residue fenced (fresh instance -- the fence is cached)
    wiki = Wiki(tmp_path)
    assert wiki.lint() == []
    wiki.update()
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    assert '[[core/_index|core/]]' in root_index
    core_index = (tmp_path / 'core' / '_index.md').read_text(encoding='utf-8')
    assert 'TABLES' not in core_index
    assert (tmp_path / 'core' / 'TABLES.md').read_text(encoding='utf-8') == residue


@_needs_git
@pytest.mark.parametrize(
    argnames='in_repo',
    argvalues=[True, False],
    ids=['in-repo', 'no-repo'],
)
def test_unavailable_fence_is_narrated_inside_a_repository(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    in_repo: bool,
) -> None:
    """A fence probe that fails inside a repository narrates the degrade.

    With git off PATH the sweep proceeds unfenced, adopting what the
    repository fences -- frontmatter written into untracked driver
    files, rows minted -- so the operator gets a line naming the
    unavailable fence rather than a silent mutation. Outside a
    repository there is no fence to lose, and the same degrade stays
    quiet.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    if in_repo:
        _git_repo(tmp_path, 'TABLES.md')
    (tmp_path / 'core' / 'TABLES.md').write_text('# t\n\nrows\n', encoding='utf-8')
    monkeypatch.setenv('PATH', '')

    # unfenced either way: the notice is what separates the two
    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)
    issues = wiki.lint()
    assert any('Bare page' in issue for issue in issues)
    unavailable = [n for n in notices if 'fence unavailable' in n.description]
    assert bool(unavailable) is in_repo


@_needs_git
def test_personally_ignored_row_draws_a_note(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row for a path only this machine ignores is named, not silent.

    The fence reads the repository's rules alone, so indexing never
    varies by machine -- but the author's ``core.excludesFile`` still
    decides what their ``git add`` accepts. Indexing a file only they
    ignore ships a row the file cannot follow, and every other clone
    reds on the broken link while the author's lint stays green. Both
    the sweep that mints the row and the lint that audits it name the
    divergence and its source; the row is still minted, since refusing
    would make indexing machine-dependent.
    """
    _make_wiki(tmp_path, folders={'notes': ['keep']})
    _git_repo(tmp_path)
    # a personal global ignore, in force for this repo only on this machine
    excludes = tmp_path.parent / 'personal_ignore'
    excludes.write_text('*.draft.md\n', encoding='utf-8')
    _git(tmp_path, 'config', 'core.excludesFile', str(excludes))
    (tmp_path / 'notes' / 'plan.draft.md').write_text(
        '---\nname: plan\ndesc: A draft.\n---\n\n# plan\n\nBody.\n',
        encoding='utf-8',
    )

    # the sweep mints the row (indexing stays machine-independent) and
    # names the divergence as it happens
    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)
    wiki.update()
    index = (tmp_path / 'notes' / '_index.md').read_text(encoding='utf-8')
    assert '[[notes/plan.draft|plan.draft]]' in index
    flagged = [
        event for event in notices if type(event).__name__ == 'PathUntrackableEvent'
    ]
    assert [event.path for event in flagged] == ['notes/plan.draft.md']
    assert flagged[0].pattern == '*.draft.md'
    assert flagged[0].source == str(excludes)
    assert 'every other clone reds' in flagged[0].description

    # lint repeats it as a soft note: the corpus is not red on this
    # machine, and gating on it would gate differently per machine
    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    assert any(type(event).__name__ == 'PathUntrackableEvent' for event in notices)

    # a file the repository itself tracks draws nothing
    assert not any(getattr(event, 'path', None) == 'notes/keep.md' for event in notices)


@_needs_git
def test_reincluded_row_draws_no_note(tmp_path: pathlib.Path) -> None:
    """A path a negated pattern re-includes draws no untrackable note.

    An allowlist ignore file in the wiki root -- ignore everything,
    re-include the page suffix -- keeps a corpus to the files it names.
    Git tracks every page the negation re-includes, so neither the
    sweep nor the lint may name one as a row the file cannot follow,
    while the files the allowlist leaves out stay fenced.
    """
    _git_repo(tmp_path)
    root = tmp_path / 'memory'
    _make_wiki(root, folders={'notes': ['keep']})
    (root / '.gitignore').write_text('*\n!*/\n!*.md\n', encoding='utf-8')
    (root / 'notes' / 'dump.tar').write_bytes(b'raw')

    # the sweep indexes the re-included page, fences the rest, and
    # names nothing
    wiki = Wiki(root)
    notices = _capture_notices(wiki)
    wiki.update()
    index = (root / 'notes' / '_index.md').read_text(encoding='utf-8')
    assert '[[notes/keep|keep]]' in index
    assert 'dump.tar' not in index
    assert not any(type(event).__name__ == 'PathUntrackableEvent' for event in notices)

    # lint audits the same rows and names nothing either
    wiki = Wiki(root)
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    assert not any(type(event).__name__ == 'PathUntrackableEvent' for event in notices)


@_needs_git
def test_untrackable_notes_spare_tracked_and_reincluded_paths(
    tmp_path: pathlib.Path,
) -> None:
    """Each note is this machine's verdict on one indexed path.

    The check asks what this machine's ``git add`` would refuse, under
    its whole ignore stack: an untracked page a personal pattern
    ignores is named, as are the index and the pages of a folder one
    ignores. A file the repository already tracks is safe whatever
    ignores it, and a path a later negation re-includes -- the personal
    file's or a committed allowlist's -- is one git accepts, so neither
    is named. Git reads a probed path as a pathspec, so an ignored name
    holding glob characters is judged tracked when a tracked name
    matches it, and is named only when none does.
    """
    root = tmp_path / 'memory'
    _make_wiki(
        root,
        folders={
            'notes': ['keep', 'kept.draft'],
            'scratch': ['pinned'],
            'globs': ['qa', 'starry'],
            'allow': ['tracked'],
        },
    )
    _git_repo(tmp_path)
    # a personal ignore file, in force on this machine only
    excludes = tmp_path.parent / f'{tmp_path.name}_personal_ignore'
    excludes.write_text(
        '*.draft.md\n!final.draft.md\nscratch/\ndrafts/\nq*.md\nstar*.md\nlone*\n',
        encoding='utf-8',
    )
    _git(tmp_path, 'config', 'core.excludesFile', str(excludes))
    # a committed allowlist: ignore the folder's files, re-include its pages
    (root / 'allow' / '.gitignore').write_text('*\n!*.md\n', encoding='utf-8')
    # everything so far is tracked, the personally ignored files included
    _git(tmp_path, 'add', '-f', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')
    # the pages no commit holds
    (root / 'drafts').mkdir()
    untracked = [
        'notes/plan.draft.md',
        'notes/final.draft.md',
        'scratch/idea.md',
        'drafts/idea.md',
        'allow/fresh.md',
        'globs/q?.md',
        'globs/star*.md',
        'globs/lone?.md',
        'globs/lone[1].md',
    ]
    for relpath in untracked:
        (root / relpath).write_text('# page\n\nBody.\n', encoding='utf-8')

    wiki = Wiki(root)
    notices = _capture_notices(wiki)
    wiki.lint()
    flagged = [
        (event.path, event.pattern)
        for event in notices
        if type(event).__name__ == 'PathUntrackableEvent'
    ]
    assert flagged == [
        # an ignored folder: the index it would gain, and its page
        ('drafts/_index.md', 'drafts/'),
        ('drafts/idea.md', 'drafts/'),
        # glob characters that match no tracked name
        ('globs/lone?.md', 'lone*'),
        ('globs/lone[1].md', 'lone*'),
        # an untracked page beside a tracked one the same pattern ignores
        ('notes/plan.draft.md', '*.draft.md'),
        # an untracked page in a tracked folder the personal file ignores
        ('scratch/idea.md', 'scratch/'),
    ]


@_needs_git
@pytest.mark.parametrize(
    argnames=('ignored_inside', 'scoped', 'flagged'),
    argvalues=[
        (False, False, ['notes/plan.draft.md']),
        (False, True, ['notes/plan.draft.md']),
        (True, False, []),
        (True, True, ['notes/plan.draft.md']),
    ],
    ids=[
        'whole-wiki',
        'scope-outside',
        'ignored-inside-whole-wiki',
        'ignored-inside-scope-outside',
    ],
)
def test_untrackable_notes_stop_at_an_ignored_path_inside_a_submodule(
    tmp_path: pathlib.Path,
    ignored_inside: bool,
    scoped: bool,
    flagged: list[str],
) -> None:
    """A submodule silences the check only through an ignored path inside it.

    Git refuses an index-aware probe of a path inside a submodule, and
    a refused batch draws no note. Only the paths some pattern ignores
    are probed that way, so a wiki holding a submodule still names the
    ignored page outside it. An ignored page inside the submodule joins
    that batch: git refuses it, and no path of the walk is named, while
    a scope that leaves the submodule out is judged on its own paths.
    """
    _make_wiki(tmp_path, folders={'notes': ['keep']})
    _git_repo(tmp_path)
    # a personal ignore file, in force on this machine only
    excludes = tmp_path.parent / f'{tmp_path.name}_personal_ignore'
    excludes.write_text('*.draft.md\n', encoding='utf-8')
    _git(tmp_path, 'config', 'core.excludesFile', str(excludes))
    # a repository of its own below the wiki, committed as a submodule
    module = tmp_path / 'module'
    module.mkdir()
    _git_repo(module)
    (module / 'inner.md').write_text('# inner\n\nBody.\n', encoding='utf-8')
    _git(module, 'add', '-A')
    _git(module, 'commit', '-q', '-m', 'module')
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')
    # the pages no commit holds
    untracked = [tmp_path / 'notes' / 'plan.draft.md']
    if ignored_inside:
        untracked.append(module / 'inside.draft.md')
    for path in untracked:
        path.write_text('# page\n\nBody.\n', encoding='utf-8')

    # lint the whole wiki, or the one folder outside the submodule
    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)
    wiki.lint('notes' if scoped else None)
    paths = [
        event.path
        for event in notices
        if type(event).__name__ == 'PathUntrackableEvent'
    ]
    assert paths == flagged


@_needs_git
def test_untrackable_notes_read_ignore_files_from_disk(tmp_path: pathlib.Path) -> None:
    """An ignore file the index alone holds names no path.

    A sparse checkout can keep a tracked ``.gitignore`` out of the
    working tree. The fence reads ignore files from disk, so a page
    that file would ignore is indexed like any other, and the check
    narrows its paths by the same files: the page draws no note, while
    a page the personal file ignores is still named.
    """
    _make_wiki(tmp_path, folders={'notes': ['keep']})
    _git_repo(tmp_path)
    # a personal ignore file, in force on this machine only
    excludes = tmp_path.parent / f'{tmp_path.name}_personal_ignore'
    excludes.write_text('*.draft.md\n', encoding='utf-8')
    _git(tmp_path, 'config', 'core.excludesFile', str(excludes))
    # a committed folder ignore file the checkout leaves off disk
    ignore = tmp_path / 'notes' / '.gitignore'
    ignore.write_text('secret.md\n', encoding='utf-8')
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')
    _git(tmp_path, 'update-index', '--skip-worktree', 'notes/.gitignore')
    ignore.unlink()
    for stem in ('secret', 'plan.draft'):
        (tmp_path / 'notes' / f'{stem}.md').write_text(
            '# page\n\nBody.\n',
            encoding='utf-8',
        )

    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)
    wiki.lint()
    flagged = [
        event.path
        for event in notices
        if type(event).__name__ == 'PathUntrackableEvent'
    ]
    assert flagged == ['notes/plan.draft.md']


@_needs_git
def test_a_leading_colon_reads_as_a_name_in_every_git_probe(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A folder named ``:notes`` is judged as itself, never as ``notes``.

    Git reads a path opening on ``:`` as pathspec magic, so a bare
    ``:notes`` would answer for ``notes``. Every probe names its path
    literally: the walk indexes ``:notes`` though ``notes`` is ignored,
    the generator creates ``:fresh`` though ``fresh`` is, and a scoped
    update of ``:notes`` restamps the hand edit inside it.
    """
    old = '2020-01-01T00:00:00Z'
    monkeypatch.setattr(Wiki, '_utc_now', lambda self: old)
    _make_wiki(tmp_path, folders={':notes': ['page']})
    _git_repo(tmp_path, 'notes', 'fresh')

    # the walk indexes the folder its own name names
    Wiki(tmp_path).update()
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    assert '[[:notes/_index|:notes/]]' in root_index

    # the generator creates a folder whose bare name an ignore rule names
    created = Wiki(tmp_path).new(':fresh', desc='A real desc.', content='Real content.')
    assert created == ':fresh/_index.md'

    # a scoped update restamps a hand edit inside the folder
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')
    page = tmp_path / ':notes' / 'page.md'
    page.write_text(page.read_text(encoding='utf-8') + '\nEdited.\n', encoding='utf-8')
    now = '2031-01-01T00:00:00Z'
    monkeypatch.setattr(Wiki, '_utc_now', lambda self: now)
    assert Wiki(tmp_path).update(':notes') == [':notes/page.md']
    assert f'updated: {now}' in page.read_text(encoding='utf-8')


@_needs_git
def test_gitignore_fence_reaches_a_nested_wiki_root(tmp_path: pathlib.Path) -> None:
    """Repo-root fences apply inside a wiki nested below the repo root.

    The corpus shape: the repository root carries the fence lines and
    the wiki lives in a subdirectory, so the fence must resolve through
    the enclosing repo rather than the wiki root alone.
    """
    _git_repo(tmp_path, 'out/')
    root = tmp_path / 'math'
    _make_wiki(root, folders={'core': ['design']})
    out = root / 'core' / 'out'
    out.mkdir()
    (out / 'dump.md').write_text('# dump\n\nraw\n', encoding='utf-8')

    wiki = Wiki(root)
    assert wiki.lint() == []
    wiki.update()
    assert not (out / '_index.md').exists()
    index = (root / 'core' / '_index.md').read_text(encoding='utf-8')
    assert '[[core/out' not in index


@_needs_git
@pytest.mark.parametrize(
    argnames='ceiling',
    argvalues=[False, True],
    ids=['no-ceiling', 'ceiling'],
)
def test_gitignore_fence_honors_a_discovery_ceiling(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    ceiling: bool,
) -> None:
    """A caller's ``GIT_CEILING_DIRECTORIES`` bounds the fence's repo search.

    A ceiling only restricts git, so unlike ``GIT_DIR`` it reaches the
    wiki's git calls: a fixture wiki that a test fences off from the
    repository around it reads as outside any repository, and that
    repository's rules do not decide what the wiki indexes. Outside
    a repository there is no fence to lose, so no note says the fence
    is unavailable.
    """
    _git_repo(tmp_path, 'out/')
    root = tmp_path / 'math'
    _make_wiki(root, folders={'core': ['design']})
    out = root / 'core' / 'out'
    out.mkdir()
    (out / 'dump.md').write_text('# dump\n\nraw\n', encoding='utf-8')
    if ceiling:
        monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path))
    else:
        monkeypatch.delenv('GIT_CEILING_DIRECTORIES', raising=False)

    # the fenced folder is indexed exactly when the ceiling hides the repo
    # (fresh instance -- the fence is cached), and the hidden repo draws no
    # unavailable-fence note
    wiki = Wiki(root)
    notices = _capture_notices(wiki)
    wiki.update()
    index = (root / 'core' / '_index.md').read_text(encoding='utf-8')
    assert ('[[core/out' in index) is ceiling
    assert not [n for n in notices if 'fence unavailable' in n.description]


@_needs_git
@pytest.mark.parametrize(
    argnames=('ceiling', 'hidden'),
    argvalues=[
        # the repository's own folder hides it, however it is spelled
        ('{repo}', True),
        ('{repo}/', True),
        ('{link}', True),
        # an entry after an empty one is read as written, unresolved
        (':{link}', False),
        # a relative entry, the wiki root itself and a folder above the
        # repository hide nothing
        ('repo', False),
        ('{root}', False),
        ('{above}', False),
    ],
    ids=[
        'repo',
        'trailing-slash',
        'symlink',
        'unresolved',
        'relative',
        'root',
        'above',
    ],
)
def test_unavailable_fence_note_honors_a_discovery_ceiling(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    ceiling: str,
    hidden: bool,
) -> None:
    """The unavailable-fence note reads a ceiling as git's discovery does.

    A repository the caller's ``GIT_CEILING_DIRECTORIES`` hides from the
    wiki's git calls is no fence to lose, so a failed probe says nothing;
    a ceiling git does not honor hides nothing, and the note stands.
    """
    # a wiki inside a repository and a symlink to it; the ceiling is set
    # from beside the repository, where the relative entry would name it
    repo = tmp_path / 'repo'
    root = repo / 'math'
    _make_wiki(root, folders={'core': ['design']})
    _git_repo(repo)
    link = tmp_path / 'link'
    link.symlink_to(repo)
    spelled = ceiling.format(repo=repo, link=link, root=root, above=tmp_path)
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', spelled)
    monkeypatch.chdir(tmp_path)

    # the wiki's git calls find the repository exactly when it is not hidden
    found = git._git(['rev-parse', '--git-dir'], cwd=root, check=False)
    assert (found is None) is hidden

    # with git off PATH the failed probe is narrated where git finds the repo
    monkeypatch.setenv('PATH', '')
    wiki = Wiki(root)
    notices = _capture_notices(wiki)
    wiki.lint()
    unavailable = [n for n in notices if 'fence unavailable' in n.description]
    assert bool(unavailable) is not hidden


@_needs_git
@pytest.mark.parametrize(
    argnames=('ignore', 'folder'),
    argvalues=[('notes/', 'notes'), ('*', '')],
    ids=['ignored-folder', 'everything-ignored-at-repo-root'],
)
def test_ignored_wiki_root_stays_unfenced(
    tmp_path: pathlib.Path,
    ignore: str,
    folder: str,
) -> None:
    """A wiki that is itself gitignored keeps indexing normally.

    The fence keeps non-corpus out of a tracked wiki; a wiki
    deliberately fenced out of its repo (a scratch tree) has no corpus
    boundary to defend, and fencing it would empty it of itself. At a
    repository's top level, which git never ignores, a ``*`` line that
    ignores everything keeps the wiki out the same way.
    """
    _git_repo(tmp_path, ignore)
    root = tmp_path / folder
    wiki = _make_wiki(root, folders={'core': ['design']})
    assert wiki.lint() == []
    # a bare page inside is adopted as usual
    (root / 'core' / 'extra.md').write_text('# Extra\n\nBody.\n', encoding='utf-8')
    wiki = Wiki(root)
    wiki.update()
    index = (root / 'core' / '_index.md').read_text(encoding='utf-8')
    assert '[[core/extra|extra]]' in index
    # the generator stays exempt too: a fenced root never fences new work
    created = Wiki(root).new('plans', desc='A real desc.', content='Real content.')
    assert created == 'plans/_index.md'


@_needs_git
def test_allowlist_fence_holds_at_the_repository_root(tmp_path: pathlib.Path) -> None:
    """An allowlist ignore file fences a wiki at its repository's top level.

    At the top level git reads the root as ignored under any ``*`` line,
    whatever re-includes follow it, so the root alone cannot say
    whether the repository keeps the wiki out. An allowlist
    that re-includes the pages tracks the wiki, and its fence holds as
    in any subfolder: the walk leaves out what the allowlist leaves
    out, and the generator refuses a folder the allowlist ignores.
    """
    _make_wiki(tmp_path, folders={'notes': ['keep']})
    _git_repo(tmp_path, '*', '!*/', '!*.md', 'scratch/')
    (tmp_path / 'notes' / 'dump.tar').write_bytes(b'raw')

    # the walk indexes the re-included page and fences the rest
    Wiki(tmp_path).update()
    index = (tmp_path / 'notes' / '_index.md').read_text(encoding='utf-8')
    assert '[[notes/keep|keep]]' in index
    assert 'dump.tar' not in index
    assert Wiki(tmp_path).lint() == []

    # the generator refuses a folder the allowlist ignores
    with pytest.raises(ValueError, match='gitignored'):
        Wiki(tmp_path).new('scratch', desc='A real desc.', content='Real content.')
    assert not (tmp_path / 'scratch').exists()


@_needs_git
def test_new_refuses_a_fenced_target(tmp_path: pathlib.Path) -> None:
    """``wiki new`` refuses a gitignore-fenced target, existing or not.

    The cached fence set enumerates existing paths only, so the
    not-yet-existing folder takes a direct probe: without it the
    generator would mint an index the very next walk cannot see, plus a
    parent row for the following update to prune.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    _git_repo(tmp_path, 'scratch/')
    wiki = Wiki(tmp_path)
    with pytest.raises(ValueError, match='gitignored'):
        wiki.new('scratch', desc='A real desc.', content='Real content.')
    assert not (tmp_path / 'scratch').exists()
    root_index = (tmp_path / '_index.md').read_text(encoding='utf-8')
    assert 'scratch' not in root_index


@_needs_git
@pytest.mark.parametrize(
    argnames=('folder', 'ignores', 'target', 'refused'),
    argvalues=[
        ('memory', ('*', '!*/', '!*.md'), 'plans', False),
        ('', ('*', '!*/', '!*.md'), 'plans', False),
        ('', ('*', '!*/', '!*.md', 'scratch/'), 'scratch', True),
        ('', ('_index.md',), 'plans', True),
    ],
    ids=[
        'allowlist-in-subfolder',
        'allowlist-at-repo-root',
        'allowlist-ignored-folder',
        'ignored-indexes',
    ],
)
def test_new_judges_the_index_it_creates(
    tmp_path: pathlib.Path,
    folder: str,
    ignores: tuple[str, ...],
    target: str,
    refused: bool,
) -> None:
    """``wiki new`` refuses a folder exactly when git ignores its new index.

    The target does not exist yet, so git cannot apply a re-include that
    names folders only (``!*/``) to the bare folder path; the index path
    carries the folder as a leading directory, which git reads as one.
    An allowlist that re-includes folders and pages admits the new
    folder; a rule ignoring the folder, or the index files themselves,
    refuses it, since that index could never be committed.
    """
    _git_repo(tmp_path)
    root = tmp_path / folder
    _make_wiki(root, folders={'core': ['design']})
    (root / '.gitignore').write_text('\n'.join(ignores) + '\n', encoding='utf-8')

    wiki = Wiki(root)
    if refused:
        with pytest.raises(ValueError, match='gitignored'):
            wiki.new(target, desc='A real desc.', content='Real content.')
        assert not (root / target).exists()
    else:
        created = wiki.new(target, desc='A real desc.', content='Real content.')
        assert created == f'{target}/_index.md'


@_needs_git
def test_gitignored_link_target_names_the_cause(tmp_path: pathlib.Path) -> None:
    """A row whose target became gitignored prunes, naming the fence.

    The target is still on disk, so a generic broken-link report would
    send the user hunting for a deleted file; lint and the prune notice
    name the gitignore fence as the cause instead.
    """
    _make_wiki(tmp_path, folders={'data': ['child', 'report']})
    # index the page first, then fence it
    _git_repo(tmp_path, 'report.md')

    # lint names the cause pre-update, still as a hard issue
    wiki = Wiki(tmp_path)
    issues = wiki.lint()
    joined = '\n'.join(issues)
    assert 'targets a gitignored path; gitignored paths are not indexed' in joined
    assert 'Broken link' not in joined
    fenced = [issue for issue in issues if 'gitignored path' in issue]
    assert fenced
    assert all(issue.kind == 'gitignored_link_target' for issue in fenced)
    # update prunes the row, naming the fence as the cause beside the removal
    notices = _capture_notices(wiki)
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Link targets a gitignored path:' in err
    assert 'gitignored paths are not indexed' in err
    assert 'Pruned link:' in err
    index = tmp_path / 'data' / '_index.md'
    assert '[[data/report|report]]' not in index.read_text(encoding='utf-8')


# ------ helpers

# seeds the sub-lists, the random paths and the fuzz draws
_SEED = 7
# seeded sub-lists of the hand-written patterns, and the longest of them
_CORNER_SUBLISTS = 30
_CORNER_SUBLIST_SIZE = 12
# random paths joined from the hand-written names, and their deepest nesting
_CORNER_RANDOM_PATHS = 400
_CORNER_DEPTH = 5
# fuzz rounds, and the patterns drawn in each
_FUZZ_ROUNDS = 40
_FUZZ_ROUND_SIZE = 8
# hand-written patterns, each one the settings accept
_CORNER_PATTERNS = [
    # trailing slash, anchoring and floating
    'vendor/',
    'vendor',
    '/vendor',
    '/vendor/',
    'a/b/',
    'a/b',
    'foo.md',
    '*.md',
    'notes',
    'x',
    'b',
    'c',
    # '**' forms
    '**',
    '**/',
    '/**',
    '/**/',
    '**/x',
    'a/**',
    'a/**/',
    'a/**/b',
    '**/**/x',
    'a/**/**/b',
    'a/**/**',
    '**/a/**',
    'a**b',
    '***',
    'a/***/b',
    '**x',
    'x**',
    '/**/x',
    'a/**/b/**/c',
    '**/**',
    '**/*',
    '*/**',
    '*',
    '?',
    '/*',
    '*/*',
    # classes and '/'
    '[/]',
    'a[!/]b',
    '[a/b]',
    '[.-0]',
    'a[.-0]c',
    '[!.-0]',
    '[/-z]',
    # negated, reversed and odd ranges
    '[!a]',
    '[!]a]',
    '[!-]',
    '[!]',
    '[!z-a]',
    '[z-a]',
    '[z-ab]',
    '[b-a-c]',
    '[z-a!x]',
    '[z-a!]',
    '[z-a^x]',
    '[z-a[x]',
    '[a-c-e]',
    '[--z]',
    '[!--z]',
    '[]-a]',
    '[!]-a]',
    '[a-]',
    '[-a]',
    '[!-a]',
    '[a--b]',
    '[a&&b]',
    '[a~~b]',
    '[a||b]',
    '[^a]',
    '[[:alpha:]]',
    '[[a]',
    '[a[b]',
    '[!^]',
    '[]',
    '[]]',
    '[]x]',
    '[!]x]',
    '[!]]',
    '[ab]*',
    'file?.[ch]',
    '[é-ü]',
    '[!é]',
    '[😀-😂]',
    '[\x00-\x1f]',
    '[ -~]',
    '[!\n]',
    '[\n]',
    # an unclosed '['
    '[x',
    'a[',
    '[',
    '[!',
    'a[b/c]',
    '[a/',
    '[!a',
    # braces and regex metacharacters, all literal
    '{a,b}',
    'a{1,2}',
    '{}',
    'a.b',
    'a+b',
    '(x)',
    '$x',
    '^x',
    'a|b',
    '#x',
    'x y',
    'foo ',
    ' foo',
    '<!-- a comment -->',
    '.hidden',
    'a.',
    '-',
    '~',
    '&',
    '|',
    # non-ASCII names (composed and decomposed) and case
    'é.md',
    'e\u0301.md',
    '€*',
    '😀',
    '?.md',
    'Foo',
    'ß',
    'İ',
    # newlines
    'a\nb',
    '*\n*',
    'x\n',
    '**/a\nb/**',
    'a\nb/c',
    '[\n-\r]',
    # deep anchored literals, and patterns listed twice
    'a/b/c',
    '/a/b/c',
    'vendor',
    '*.md',
    'deep/er/path/x',
]
# hand-written path segments
_CORNER_NAMES = [
    'a',
    'b',
    'c',
    'x',
    'z',
    'vendor',
    'notes',
    'foo.md',
    'Foo',
    'foo',
    'foo ',
    ' foo',
    'a.b',
    'aXb',
    'axxb',
    'ab',
    'a+b',
    'aab',
    '(x)',
    '$x',
    '^x',
    'a|b',
    '#x',
    'x y',
    '{a,b}',
    'a{1,2}',
    '{}',
    'a1',
    '[x',
    'a[',
    '[',
    '[!',
    '[a',
    '[!a',
    '!',
    '^',
    ']',
    '-',
    '..x',
    '.hidden',
    'a.',
    '&',
    '~',
    '|',
    '\\',
    'a\\b',
    '\\d',
    'é.md',
    'e\u0301.md',
    'ë',
    'é',
    'e\u0301',
    '€',
    '€x',
    '😀',
    '😁',
    '😃',
    'ß',
    'İ',
    'i',
    'a\nb',
    '\n',
    'x\n',
    '\nx',
    'a\rb',
    '\x00',
    '\x1f',
    ' ',
    'q',
    'file1.c',
    'file1.h',
    'file12.c',
    'temp1',
    'build',
    'drafts',
    'cache',
    '<!-- a comment -->',
    'x.md',
    'notes.md',
    'deep',
    'er',
    'path',
    '0',
    ':',
    '[]',
    '[]]',
    'x]',
    '***',
    'a**b',
    '*',
]
# folders each hand-written name is nested under, and nests
_CORNER_PARENTS = ['a', 'x', 'vendor', 'a\nb', 'é', 'a/b', 'deep/er']
# characters of a free-form fuzzed pattern, the weightier ones repeated
_FUZZ_PATTERN_CHARS = 'ab/*?[]!-^&~|{},.\n é€😀#<>$()+:zA' + 'ab*[]/-!'
# characters of a fuzzed class body, the weightier ones repeated
_FUZZ_CLASS_CHARS = '!-]^az&~|[é/0.!-]' + '--!]za'
# characters of a fuzzed path segment, the weightier ones repeated
_FUZZ_NAME_CHARS = 'ab-z!^&]\n[ é€😀#.{},:A~|\\*?' + 'abab'
# characters a fuzzed path puts where a pattern holds a wildcard
_FUZZ_FILL_CHARS = ['', 'a', 'z', '!', '\n', 'é']
# characters a fuzzed path puts where a pattern holds a class
_FUZZ_MEMBER_CHARS = ['a', 'b', '-', '!', 'z', '^']


def _scan(policy: list[tuple[str, re.Pattern[str]]], relative: str) -> Optional[str]:
    """Return the first pattern, in list order, matching a path or an ancestor."""
    candidates = [relative]
    while '/' in candidates[-1]:
        candidates.append(candidates[-1].rpartition('/')[0])
    for pattern, compiled in policy:
        if any(compiled.match(candidate) for candidate in candidates):
            return pattern
    return None


def _kind(named: Optional[str]) -> Optional[str]:
    """Return the kind of pattern a verdict names, ``None`` when it names none."""
    if named is None:
        return None
    return 'wildcard' if any(char in named for char in '*?[') else 'literal'


def _walkable(paths: set[str]) -> list[str]:
    """Return the ``paths`` a walk could yield, sorted.

    A walked path has no empty segment, and ``pathlib`` drops a ``.``
    segment, so the wiki would judge a different path than the reference.
    """
    return sorted(path for path in paths if not ({'', '.'} & set(path.split('/'))))


def _corner_cases(rng: random.Random) -> Iterator[tuple[list[str], list[str]]]:
    """Yield the hand-written policies, each with the hand-written paths."""
    paths = set(_CORNER_NAMES)
    for name in _CORNER_NAMES:
        for parent in _CORNER_PARENTS:
            paths.add(f'{parent}/{name}')
            paths.add(f'{name}/{parent}')
    for _ in range(_CORNER_RANDOM_PATHS):
        depth = rng.randint(1, _CORNER_DEPTH)
        paths.add('/'.join(rng.choice(_CORNER_NAMES) for _ in range(depth)))
    walkable = _walkable(paths)
    # the whole list both ways round, so each pattern shadows and is shadowed
    yield _CORNER_PATTERNS, walkable
    yield _CORNER_PATTERNS[::-1], walkable
    # each pattern alone, so every pattern's own verdict is exercised
    for pattern in dict.fromkeys(_CORNER_PATTERNS):
        yield [pattern], walkable
    for _ in range(_CORNER_SUBLISTS):
        size = rng.randint(1, _CORNER_SUBLIST_SIZE)
        yield rng.sample(_CORNER_PATTERNS, size), walkable


def _fuzz_cases(
    rng: random.Random,
    root: pathlib.Path,
    classes: bool,
) -> Iterator[tuple[list[str], list[str]]]:
    """Yield fuzzed policies, each with paths built from its patterns.

    Each round draws patterns, keeps the ones the settings accept, and
    yields them as one list and one at a time. With ``classes``, four
    draws in five wrap a class body, and half the path segments are
    single characters a class is likely to hold.
    """
    for _ in range(_FUZZ_ROUNDS):
        drawn = [_fuzz_pattern(rng, classes) for _ in range(_FUZZ_ROUND_SIZE)]
        patterns = [pattern for pattern in drawn if _accepted(root, pattern)]
        paths = set()
        for pattern in patterns:
            path = _fuzz_match(rng, pattern)
            paths.update([path, f'a/{path}', f'{path}/b'])
        for _ in range(_FUZZ_ROUND_SIZE):
            depth = rng.randint(1, 4)
            paths.add('/'.join(_fuzz_name(rng, classes) for _ in range(depth)))
        walkable = _walkable(paths)
        yield patterns, walkable
        for pattern in patterns:
            yield [pattern], walkable


def _fuzz_pattern(rng: random.Random, classes: bool) -> str:
    """Draw one pattern: free-form, or wrapped around a class body."""
    if classes and (rng.random() < 0.8):
        body = ''.join(rng.choice(_FUZZ_CLASS_CHARS) for _ in range(rng.randint(0, 6)))
        head = rng.choice(['', 'a', '*'])
        close = rng.choice([']', ']', ']x', ''])
        tail = rng.choice(['', '*', '/b'])
        return f'{head}[{body}{close}{tail}'
    return ''.join(rng.choice(_FUZZ_PATTERN_CHARS) for _ in range(rng.randint(1, 10)))


def _fuzz_name(rng: random.Random, classes: bool) -> str:
    """Draw one path segment."""
    if classes and (rng.random() < 0.5):
        return rng.choice(_FUZZ_CLASS_CHARS + 'bcy`{1~+,é\n')
    return ''.join(rng.choice(_FUZZ_NAME_CHARS) for _ in range(rng.randint(1, 4)))


def _fuzz_match(rng: random.Random, pattern: str) -> str:
    """Return a path ``pattern`` is likely to match: its wildcards filled in."""
    path = pattern.strip('/').replace('**', rng.choice(['a', 'a/b', 'é']))
    path = re.sub(r'[*?]', lambda _: rng.choice(_FUZZ_FILL_CHARS), path)
    return re.sub(r'\[[^\]/]*\]', lambda _: rng.choice(_FUZZ_MEMBER_CHARS), path)


def _accepted(root: pathlib.Path, pattern: str) -> bool:
    """Return ``True`` if the settings accept ``pattern`` as a policy."""
    _set_exclude_patterns(root, [pattern])
    try:
        Wiki(root)._exclude_policy  # noqa: B018
    except ValueError:
        return False
    return True
