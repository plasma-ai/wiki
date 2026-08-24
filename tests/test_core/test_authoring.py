"""End-to-end authoring workflow for the ``Wiki`` class.

A single flagship test walks the real authoring path an agent follows --
``init`` a wiki, author a titled page inside a subfolder, ``update`` to
generate links and frontmatter, ``lint`` the authored subtree, ``read``
a word slice, and ``match`` a frontmatter field -- exercising the core
operations together rather than in isolation. The ``new`` generator's
authored-inputs contract lives beside it.

The ``Wiki`` class is exercised directly (not via subprocess) since the
authoring path lives in core.
"""

from __future__ import annotations

import pathlib

import pytest

from wiki.core.wiki import Wiki

from ._helpers import _make_wiki, _set_exclude_patterns

__all__ = [
    'test_authoring_workflow_init_update_lint_read_match',
    'test_fresh_wiki_lints_clean',
    'test_update_path_joins_title',
    'test_new_generates_a_converged_index',
    'test_new_writes_multi_line_desc_as_block_scalar',
    'test_new_refuses_unauthored_inputs',
    'test_new_refuses_unreachable_targets',
    'test_new_preflights_sweep_refusals_before_writing',
]


# ------ flagship authoring workflow


def test_authoring_workflow_init_update_lint_read_match(
    tmp_path: pathlib.Path,
) -> None:
    """Init, author, update, lint, read --words, and match round-trip.

    Drives the whole authoring path an agent uses and checks the
    observable result of each stage: update reports the authored files,
    the authored subtree lints clean, a word slice preserves frontmatter
    while slicing the body, and a field match finds the authored desc.
    """
    root = tmp_path / 'wiki'
    wiki = Wiki(root)

    # init scaffolds the root index
    wiki.init(name='KnowledgeBase')
    assert (root / '_index.md').is_file()

    # author a titled page in a subfolder, then update the tree
    page = _author_page(
        root=root,
        folder='guides',
        stem='Onboarding',
        desc='How a new teammate gets started.',
        body='Welcome aboard. First clone the repo, then run bootstrap.',
    )
    updated = wiki.update()
    assert str(page.relative_to(root)) in updated

    # the authored subtree lints clean (desc and content are present)
    assert wiki.lint(name='guides') == []

    # read --words keeps frontmatter and slices the body by word index
    sliced = wiki.read('guides/Onboarding', start=0, stop=3, on='words')
    assert 'name:' in sliced
    assert 'Welcome' in sliced
    assert 'bootstrap' not in sliced

    # match a frontmatter field finds the authored description
    matches = wiki.match('teammate', field='desc')
    assert [relpath for relpath, _lineno, _line in matches] == ['guides/Onboarding.md']


# ------ fresh-wiki lint and update name behavior


def test_fresh_wiki_lints_clean(tmp_path: pathlib.Path) -> None:
    """A freshly initialized wiki produces no lint issues.

    ``init`` seeds the root with a placeholder ``desc: ...`` and no content;
    these are soft "not yet authored" states, so ``lint`` notes them on
    stderr rather than reporting them as issues.
    """
    root = tmp_path / 'wiki'
    wiki = Wiki(root)
    wiki.init(name='KnowledgeBase')
    assert wiki.lint() == []


def test_update_path_joins_title(tmp_path: pathlib.Path) -> None:
    """Update sets ``name``/H1 to the path-joined name by design.

    ``update`` rewrites the page ``name`` and H1 to the path-joined name
    (``guides/Onboarding``) so names stay consistent with the tree; a
    hand-edited heading on a title-less page is overwritten, and an
    authored ``title:`` frontmatter field is the sanctioned way to keep
    a display heading.
    """
    root = tmp_path / 'wiki'
    wiki = Wiki(root)
    wiki.init(name='KnowledgeBase')
    page = _author_page(
        root=root,
        folder='guides',
        stem='Onboarding',
        desc='How a new teammate gets started.',
        body='Welcome aboard. First clone the repo, then run bootstrap.',
    )
    wiki.update()
    text = page.read_text(encoding='utf-8')
    assert 'name: guides/Onboarding\n' in text
    assert '# guides/Onboarding\n' in text

    # an authored title: wins the H1 while name stays path-joined
    page.write_text(
        text.replace(
            'name: guides/Onboarding\n',
            'name: guides/Onboarding\ntitle: Onboarding Guide\n',
        ),
        encoding='utf-8',
    )
    wiki.update()
    titled = page.read_text(encoding='utf-8')
    assert 'name: guides/Onboarding\n' in titled
    assert '# Onboarding Guide\n' in titled


# ------ the new generator


def test_new_generates_a_converged_index(tmp_path: pathlib.Path) -> None:
    """``new`` writes a lint-complete index and wires the parent row.

    The adoption shape: a folder of raw evidence files gains its index
    with an authored desc and content, the folder's own rows and the
    parent's new row (desc propagated) land in the same pass, and the
    tree is converged -- no follow-up update, no placeholder for lint
    to nag about.
    """
    wiki = _make_wiki(tmp_path, folders={'evidence': ['report']})
    # a folder of raw keeper legs, adopted with authored inputs
    verify = tmp_path / 'evidence' / 'verify'
    verify.mkdir()
    (verify / 'main.py').write_text('print(0)\n', encoding='utf-8')
    created = wiki.new(
        'evidence/verify',
        desc='The verify record: keeper legs and the grading quote.',
        content='Adopted at grading; re-run `main.py` to reproduce.',
    )
    assert created == 'evidence/verify/_index.md'

    # the index carries the authored desc (YAML-quoted for its ': ') and
    # content plus its own rows
    text = (verify / '_index.md').read_text(encoding='utf-8')
    assert "desc: 'The verify record: keeper legs and the grading quote.'" in text
    assert 'Adopted at grading' in text
    assert '[[evidence/verify/main.py|main.py]]' in text
    # the parent row landed with the desc propagated
    parent = (tmp_path / 'evidence' / '_index.md').read_text(encoding='utf-8')
    assert '[[evidence/verify/_index|verify/]]: The verify record:' in parent
    # the tree is converged and clean: no issues, no notes for the new index
    assert wiki.update() == []
    fresh = Wiki(tmp_path)
    assert fresh.lint() == []

    # a second generation over the same index is refused, bytes untouched
    with pytest.raises(ValueError, match='Index already exists'):
        wiki.new('evidence/verify', desc='Another.', content='More.')
    assert (verify / '_index.md').read_text(encoding='utf-8') == text


def test_new_writes_multi_line_desc_as_block_scalar(
    tmp_path: pathlib.Path,
) -> None:
    """A multi-line desc lands as a block scalar and rides the parent row.

    The authored desc's line breaks are content: the index frontmatter
    carries them as a literal block scalar, the parent row carries the
    continuation line, and the tree still converges with a clean lint
    -- no malformed YAML for a later sweep to trip on.
    """
    wiki = _make_wiki(tmp_path, folders={'evidence': ['report']})
    wiki.new(
        'evidence/verify',
        desc='The verify record.\nKeeper legs and the grading quote.',
        content='Adopted at grading.',
    )
    # the index carries the desc as an indented literal block scalar
    verify = tmp_path / 'evidence' / 'verify'
    text = (verify / '_index.md').read_text(encoding='utf-8')
    assert (
        'desc: |\n  The verify record.\n  Keeper legs and the grading quote.'
    ) in text
    # the parent row carries the continuation line, unindented
    parent = (tmp_path / 'evidence' / '_index.md').read_text(encoding='utf-8')
    assert (
        '[[evidence/verify/_index|verify/]]: The verify record.\n'
        'Keeper legs and the grading quote.'
    ) in parent
    # the tree is converged and clean
    assert wiki.update() == []
    fresh = Wiki(tmp_path)
    assert fresh.lint() == []


@pytest.mark.parametrize(
    argnames=('desc', 'content', 'match'),
    argvalues=[
        ('', 'Real content.', 'desc is required'),
        ('   ', 'Real content.', 'desc is required'),
        ('...', 'Real content.', 'desc is required'),
        ('A real desc.', '', 'Content is required'),
        ('A real desc.', '  \n ', 'Content is required'),
        ('A real desc.', '...', 'Content is required'),
    ],
    ids=[
        'empty-desc',
        'blank-desc',
        'placeholder-desc',
        'empty-content',
        'blank-content',
        'placeholder-content',
    ],
)
def test_new_refuses_unauthored_inputs(
    tmp_path: pathlib.Path,
    desc: str,
    content: str,
    match: str,
) -> None:
    """The generator refuses to emit without authored desc and content.

    Descriptions and content are judgment, never auto-stubbed: a blank
    or placeholder input is a loud refusal before any write, so a
    mechanical adoption can never ship the hidden hand-fill state.
    """
    wiki = _make_wiki(tmp_path, folders={'evidence': ['report']})
    with pytest.raises(ValueError, match=match):
        wiki.new('evidence/verify', desc=desc, content=content)
    assert not (tmp_path / 'evidence' / 'verify').exists()


def test_new_refuses_unreachable_targets(tmp_path: pathlib.Path) -> None:
    """The generator refuses targets indexing cannot reach or already owns.

    An index written outside the root, at the root, under a missing or
    unindexed parent, through a symlinked segment (which every walk
    excludes, and which may point outside the root), into an excluded
    subtree, or against the naming policy would be junk no later walk
    sees or repairs -- each is refused naming its cause, with nothing
    written.
    """
    wiki = _make_wiki(tmp_path, folders={'evidence': ['report']})
    cases = [
        ('../outside', 'outside wiki root'),
        ('.', 'root index'),
        ('missing/verify', 'Parent folder does not exist'),
        ('evidence/bad#name', 'Invalid folder name'),
        ('evidence', 'Index already exists'),
    ]
    for name, match in cases:
        with pytest.raises(ValueError, match=match):
            wiki.new(name, desc='A real desc.', content='Real content.')
    # an existing-but-unindexed parent is refused too: the wiring sweep
    # would mint its index as a placeholder, and the root chain would
    # never gain its row
    (tmp_path / 'rawparent').mkdir()
    with pytest.raises(ValueError, match='Parent folder is not indexed'):
        wiki.new('rawparent/child', desc='A real desc.', content='Real content.')
    assert not (tmp_path / 'rawparent' / '_index.md').exists()
    assert not (tmp_path / 'rawparent' / 'child').exists()
    # a symlinked segment is refused -- writing through it would land the
    # index outside the root, invisibly to every walk
    outside = tmp_path.parent / 'outside'
    outside.mkdir()
    (tmp_path / 'evil').symlink_to(outside)
    for name in ('evil', 'evil/sub'):
        with pytest.raises(ValueError, match='crosses a symlink'):
            wiki.new(name, desc='A real desc.', content='Real content.')
    assert list(outside.iterdir()) == []
    # an excluded target is refused naming the pattern
    _set_exclude_patterns(tmp_path, ['vendor'])
    with pytest.raises(ValueError, match=r"exclude\.patterns 'vendor'"):
        Wiki(tmp_path).new('vendor', desc='A real desc.', content='Real content.')
    assert not (tmp_path / 'vendor').exists()


def test_new_preflights_sweep_refusals_before_writing(
    tmp_path: pathlib.Path,
) -> None:
    """The wiring sweep's refusals fire before the index is written.

    A refusal after the write would strand a half-adoption -- the index
    on disk, the parent row never wired -- whose retry dead-ends on the
    never-overwrites guard. The sweep's refusals are pre-flighted and
    marker-shaped authored input is refused at the boundary, so a
    refused adoption leaves nothing on disk.
    """
    wiki = _make_wiki(tmp_path, folders={'evidence': ['report']})
    conflict = '\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n'
    # a conflicted sibling in the parent scope refuses with nothing created
    sibling = tmp_path / 'evidence' / 'report.md'
    clean = sibling.read_text(encoding='utf-8')
    sibling.write_text(clean + conflict, encoding='utf-8')
    with pytest.raises(ValueError, match='Merge conflict markers'):
        wiki.new('evidence/verify', desc='A real desc.', content='Real content.')
    assert not (tmp_path / 'evidence' / 'verify').exists()
    # marker-shaped authored content is refused at the boundary too
    sibling.write_text(clean, encoding='utf-8')
    with pytest.raises(ValueError, match='Merge conflict markers'):
        wiki.new('evidence/verify', desc='A real desc.', content=conflict.strip())
    assert not (tmp_path / 'evidence' / 'verify').exists()
    # a nested declared wiki in the parent scope refuses pre-write alike
    guest = tmp_path / 'evidence' / 'guest'
    (guest / '.wiki').mkdir(parents=True)
    (guest / '.wiki' / 'settings.json').write_text('{}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='encloses the wiki at'):
        wiki.new('evidence/verify', desc='A real desc.', content='Real content.')
    assert not (tmp_path / 'evidence' / 'verify').exists()


# ------ helpers


def _author_page(
    root: pathlib.Path,
    folder: str,
    stem: str,
    desc: str,
    body: str,
) -> pathlib.Path:
    """Author a lint-clean subfolder index plus a titled page, returning its path."""
    # create the subfolder index
    subfolder = root / folder
    subfolder.mkdir(parents=True, exist_ok=True)
    (subfolder / '_index.md').write_text(
        f'---\nname: {folder}\ndesc: The {folder} section.\n---\n\n'
        f'# {folder}\n\n***\n\nOverview of {folder}.\n',
        encoding='utf-8',
    )
    # author the titled page
    page = subfolder / f'{stem}.md'
    page.write_text(
        f'---\nname: {stem}\ndesc: {desc}\n---\n\n# {stem}\n\n{body}\n',
        encoding='utf-8',
    )
    return page
