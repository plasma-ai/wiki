"""End-to-end authoring workflow for the ``Wiki`` class.

A single flagship test walks the real authoring path an agent follows --
``init`` a wiki, author a titled page inside a subfolder, ``update`` to
generate links and frontmatter, ``lint`` the authored subtree, ``read``
a word slice, and ``search`` a frontmatter field -- exercising the core
operations together rather than in isolation.

The ``Wiki`` class is exercised directly (not via subprocess) since the
authoring path lives in core.
"""

from __future__ import annotations

import pathlib

from wiki.core.wiki import Wiki

__all__ = [
    'test_authoring_workflow_init_update_lint_read_search',
    'test_fresh_wiki_lints_clean',
    'test_update_path_joins_title',
]


# ------ flagship authoring workflow


def test_authoring_workflow_init_update_lint_read_search(
    tmp_path: pathlib.Path,
) -> None:
    """Init, author, update, lint, read --words, and search round-trip.

    Drives the whole authoring path an agent uses and checks the
    observable result of each stage: update reports the authored files,
    the authored subtree lints clean, a word slice preserves frontmatter
    while slicing the body, and a field search finds the authored desc.
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

    # search a frontmatter field finds the authored description
    matches = wiki.search('teammate', field='desc')
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
