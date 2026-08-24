"""Behavioral tests for the indexing exclusions.

``exclude.patterns`` (``.wiki/settings.json``): policy validation, the
gitignore-style matching semantics, the walk-wide invisibility of
excluded subtrees, the pruned-row notices naming the pattern as the
cause, symlink precedence, the nested-wiki lift, and the scope refusal.
The enclosing repo's gitignore fences: adoption/minting refusal for
fenced strays, pattern-pure matching, the ignored-root lift, and the
fence-named prune cause. The lint, map, search, and read surfaces are
covered beside their precedent tests in their own modules.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

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
    'test_gitignored_residue_is_never_adopted',
    'test_fenced_directory_is_invisible_to_every_check',
    'test_gitignore_fence_is_pattern_pure',
    'test_gitignore_fence_ignores_machine_local_excludes',
    'test_gitignore_fence_ignores_an_inherited_git_dir',
    'test_unavailable_fence_is_narrated_inside_a_repository',
    'test_personally_ignored_row_draws_a_note',
    'test_gitignore_fence_reaches_a_nested_wiki_root',
    'test_ignored_wiki_root_stays_unfenced',
    'test_new_refuses_a_fenced_target',
    'test_gitignored_link_target_names_the_cause',
]

# the gitignore-fence tests drive a real repository
_needs_git = pytest.mark.skipif(shutil.which('git') is None, reason='requires git')


def _git_repo(path: pathlib.Path, *ignores: str) -> None:
    """Initialize a git repository at ``path`` with ``ignores`` fence lines."""
    subprocess.run(
        ['git', 'init', '-q', str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    if ignores:
        (path / '.gitignore').write_text('\n'.join(ignores) + '\n', encoding='utf-8')


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
    cause beside the prune notice naming the removal.
    """
    _make_wiki(tmp_path, folders={'data': ['child', 'report']})
    # index the page first, then exclude it
    _set_exclude_patterns(tmp_path, ['data/report.md'])
    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)

    # the exclusion is named as the cause beside the removal
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Link targets an excluded path:' in err
    assert "exclude.patterns 'data/report.md'" in err
    assert 'Pruned link:' in err
    index = tmp_path / 'data' / '_index.md'
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
    subprocess.run(
        ['git', '-C', str(tmp_path), 'add', '-f', str(root), '.gitignore'],
        capture_output=True,
        text=True,
        check=True,
    )

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
    subprocess.run(
        ['git', '-C', str(tmp_path), 'add', '-f', str(junk)],
        capture_output=True,
        text=True,
        check=True,
    )

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
    subprocess.run(
        ['git', '-C', str(tmp_path), 'config', 'core.excludesFile', str(excludes)],
        capture_output=True,
        text=True,
        check=True,
    )
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
        event for event in notices if type(event).__name__ == 'UntrackablePathEvent'
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
    assert any(type(event).__name__ == 'UntrackablePathEvent' for event in notices)

    # a file the repository itself tracks draws nothing
    assert not any(getattr(event, 'path', None) == 'notes/keep.md' for event in notices)


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
def test_ignored_wiki_root_stays_unfenced(tmp_path: pathlib.Path) -> None:
    """A wiki that is itself gitignored keeps indexing normally.

    The fence keeps non-corpus out of a tracked wiki; a wiki
    deliberately fenced out of its repo (a scratch tree) has no corpus
    boundary to defend, and fencing it would empty it of itself.
    """
    _git_repo(tmp_path, 'notes/')
    root = tmp_path / 'notes'
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
