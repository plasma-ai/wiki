"""Tests for CLI path resolution and plugin loading."""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from wiki.cli.utils import (
    configure_git_merge_driver,
    load_wiki_class,
    resolve_wiki_root,
)
from wiki.core.wiki import Wiki

__all__ = [
    'test_resolve_wiki_root',
    'test_load_wiki_class',
    'test_configure_git_merge_driver',
    'test_merge_driver_skips_dirty_gitattributes',
]


# ------ resolve_wiki_root


def test_resolve_wiki_root(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolves the wiki root from an explicit path, a walk-up, or a wiki/ subdir."""
    # explicit path returned as-is
    result = resolve_wiki_root(str(tmp_path))
    assert result == tmp_path

    # walk up from cwd to find topmost _index.md
    nested = tmp_path / 'a' / 'b'
    nested.mkdir(parents=True)
    (tmp_path / '_index.md').write_text('root\n', encoding='utf-8')
    (tmp_path / 'a' / '_index.md').write_text('mid\n', encoding='utf-8')
    (nested / '_index.md').write_text('leaf\n', encoding='utf-8')
    monkeypatch.chdir(nested)
    result = resolve_wiki_root(None)
    assert result == tmp_path

    # wiki/ subdirectory with _index.md
    clean = tmp_path / 'clean_project'
    clean.mkdir()
    wiki_dir = clean / 'wiki'
    wiki_dir.mkdir()
    (wiki_dir / '_index.md').write_text('wiki root\n', encoding='utf-8')
    monkeypatch.chdir(clean)
    result = resolve_wiki_root(None)
    assert result == wiki_dir


# ------ load_wiki_class


def test_load_wiki_class(tmp_path: pathlib.Path) -> None:
    """Loads the default ``Wiki`` or the subclass named by the sole ``__all__`` entry."""
    # no config file -- returns default Wiki
    cls = load_wiki_class(tmp_path)
    assert cls is Wiki

    # custom subclass named by the sole __all__ entry
    config_dir = tmp_path / '_config'
    config_dir.mkdir()
    (config_dir / 'wiki.py').write_text(
        'from wiki.core.wiki import Wiki\n\n'
        'class MyWiki(Wiki):\n'
        '    pass\n\n'
        "__all__ = ['MyWiki']\n",
        encoding='utf-8',
    )
    cls = load_wiki_class(tmp_path)
    assert cls is not Wiki
    assert issubclass(cls, Wiki)

    # missing __all__
    (config_dir / 'wiki.py').write_text('x = 1\n', encoding='utf-8')
    with pytest.raises(AttributeError):
        load_wiki_class(tmp_path)

    # __all__ must have exactly one entry
    (config_dir / 'wiki.py').write_text(
        "__all__ = ['A', 'B']\nA = 1\nB = 2\n",
        encoding='utf-8',
    )
    with pytest.raises(AttributeError):
        load_wiki_class(tmp_path)

    # the named object is not a Wiki subclass
    (config_dir / 'wiki.py').write_text(
        "class NotWiki:\n    pass\n\n__all__ = ['NotWiki']\n",
        encoding='utf-8',
    )
    with pytest.raises(TypeError):
        load_wiki_class(tmp_path)


# ------ configure_git_merge_driver


def test_configure_git_merge_driver(tmp_path: pathlib.Path) -> None:
    """Wiring sets the git driver and writes the glob without ever committing.

    A no-op outside a git repo; idempotent; and -- per the org's
    never-auto-commit rule -- it writes ``.gitattributes`` to the working tree
    only, never staging or committing it (Issue #1).
    """

    def git(*args: str) -> str:
        result = subprocess.run(
            ['git', '-C', f'{tmp_path}', *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    # no-op outside a git repo
    configure_git_merge_driver(tmp_path)
    assert not (tmp_path / '.gitattributes').exists()

    # a real repo with a wiki subdir
    git('init', '-b', 'main')
    git('config', 'user.email', 'test@test.com')
    git('config', 'user.name', 'Test')
    (tmp_path / 'README.md').write_text('# r\n', encoding='utf-8')
    git('add', 'README.md')
    git('commit', '-m', 'init')
    wiki_dir = tmp_path / 'wiki'
    wiki_dir.mkdir()
    head_before = git('rev-parse', 'HEAD')

    configure_git_merge_driver(wiki_dir)

    # driver points at the bundled script and the glob is written to the worktree
    assert 'merge_index.sh' in git('config', 'merge.wiki-index.driver')
    attributes = (tmp_path / '.gitattributes').read_text(encoding='utf-8')
    assert '**/_index.md merge=wiki-index' in attributes
    # nothing is committed (no new HEAD) and nothing is staged (the rule)
    assert git('rev-parse', 'HEAD') == head_before
    assert '.gitattributes' not in git('diff', '--cached', '--name-only')
    assert '.gitattributes' in git('status', '--porcelain')

    # idempotent -- a second call does not duplicate the mapping
    configure_git_merge_driver(wiki_dir)
    final = (tmp_path / '.gitattributes').read_text(encoding='utf-8')
    assert final.count('**/_index.md merge=wiki-index') == 1


def test_merge_driver_skips_dirty_gitattributes(tmp_path: pathlib.Path) -> None:
    """The wiring leaves ``.gitattributes`` untouched while it has pending edits.

    It defers the attribute-map write until ``.gitattributes`` is clean (the
    ``merge.wiki-index`` config still applies), so it never entangles with the
    user's uncommitted work; once clean, a re-run writes the map (it converges).
    """

    def git(*args: str) -> str:
        result = subprocess.run(
            ['git', '-C', f'{tmp_path}', *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    # a repo whose tracked .gitattributes has an uncommitted edit
    git('init', '-b', 'main')
    git('config', 'user.email', 'test@test.com')
    git('config', 'user.name', 'Test')
    attributes = tmp_path / '.gitattributes'
    attributes.write_text('*.txt text\n', encoding='utf-8')
    git('add', '.gitattributes')
    git('commit', '-m', 'init')
    attributes.write_text('*.txt text\n*.md text\n', encoding='utf-8')
    wiki_dir = tmp_path / 'wiki'
    wiki_dir.mkdir()

    # dirty .gitattributes: the map is not written, but the config is still set
    configure_git_merge_driver(wiki_dir)
    assert 'merge=wiki-index' not in attributes.read_text(encoding='utf-8')
    assert 'merge_index.sh' in git('config', 'merge.wiki-index.driver')

    # once .gitattributes is clean, a re-run writes the map (it converges)
    git('add', '.gitattributes')
    git('commit', '-m', 'edit')
    configure_git_merge_driver(wiki_dir)
    assert '**/_index.md merge=wiki-index' in attributes.read_text(encoding='utf-8')
