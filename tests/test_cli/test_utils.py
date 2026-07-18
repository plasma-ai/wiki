"""Test the ``wiki.cli.utils`` module."""

from __future__ import annotations

import pathlib
from typing import Optional

import pytest
import typer
from typer.testing import CliRunner

from wiki.cli import cmd
from wiki.cli.utils import (
    configure_git_merge_driver,
    enclosing_wiki_root,
    is_trusted,
    load_wiki_class,
    resolve_wiki,
    resolve_wiki_root,
    trust_root,
)
from wiki.core.wiki import Wiki

from .conftest import GIT, _git

__all__ = [
    'test_resolve_wiki_root',
    'test_resolve_wiki_root_prefers_declared_marker',
    'test_resolve_wiki_root_falls_back_to_declared_subdir',
    'test_resolve_wiki_root_fallback_nominations',
    'test_resolver_refuses_ambiguous_root',
    'test_resolve_wiki_corroboration_notices',
    'test_load_wiki_class',
    'test_load_wiki_class_refuses_untrusted_hook',
    'test_trust_root_records_resolved_root',
    'test_reused_command_honors_resolve_override',
    'test_resolve_wiki_default_class',
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


def test_resolve_wiki_root_prefers_declared_marker(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The declared ``.wiki/settings.json`` marker wins over the index chain.

    A foreign ``_index.md`` above the declared root (a Hugo site, a
    damaged outer tree) must not re-root the wiki: the marker wins, and
    the index chain is only a fallback for undeclared trees.
    """
    # a declared root nested under a foreign index chain
    (tmp_path / '_index.md').write_text('foreign\n', encoding='utf-8')
    root = tmp_path / 'docs'
    nested = root / 'a'
    nested.mkdir(parents=True)
    _declare_root(root)
    (nested / '_index.md').write_text('leaf\n', encoding='utf-8')
    monkeypatch.chdir(nested)
    assert resolve_wiki_root(None) == root


def test_resolve_wiki_root_falls_back_to_declared_subdir(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ``{cwd}/wiki`` fallback honors the declared marker, not just the index.

    A declared wiki that lost its ``_index.md`` must stay reachable from
    the project root, so ``wiki update`` there can name the damage and
    rebuild the index instead of failing to locate the wiki at all.
    """
    project = tmp_path / 'project'
    wiki_dir = project / 'wiki'
    wiki_dir.mkdir(parents=True)
    _declare_root(wiki_dir, index=False)
    monkeypatch.chdir(project)
    assert resolve_wiki_root(None) == wiki_dir
    # full resolution rides along, naming the missing-index damage
    resolve_wiki(None)
    assert 'missing its _index.md' in capsys.readouterr().err


def test_resolve_wiki_root_fallback_nominations(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fallback nominations win only when valid, and ride the guard pipeline.

    An embedder fallback (e.g. plasma's project-anchored wiki dir) only
    nominates a candidate: a declared-or-indexed nomination resolves, an
    invalid one declines to the ``{cwd}/wiki`` fallback rather than
    masking it, and a winning nomination still flows through
    ``resolve_wiki``'s diagnostics.
    """
    # a declared wiki away from cwd, nominated by a fallback
    project = tmp_path / 'project'
    project.mkdir()
    elsewhere = tmp_path / 'elsewhere' / 'wiki'
    elsewhere.mkdir(parents=True)
    _declare_root(elsewhere)
    monkeypatch.chdir(project)
    assert resolve_wiki_root(None, fallbacks=(lambda: elsewhere,)) == elsewhere

    # an invalid nomination declines to the {cwd}/wiki fallback
    wiki_dir = project / 'wiki'
    wiki_dir.mkdir()
    (wiki_dir / '_index.md').write_text('wiki root\n', encoding='utf-8')
    # a valid nomination outranks the {cwd}/wiki fallback
    assert resolve_wiki_root(None, fallbacks=(lambda: elsewhere,)) == elsewhere
    missing = tmp_path / 'missing' / 'wiki'
    assert resolve_wiki_root(None, fallbacks=(lambda: missing,)) == wiki_dir
    # a declining fallback may also nominate nothing at all
    assert resolve_wiki_root(None, fallbacks=(lambda: None,)) == wiki_dir

    # a winning undeclared nomination still rides the guard pipeline
    indexed = tmp_path / 'indexed'
    indexed.mkdir()
    (indexed / '_index.md').write_text('undeclared\n', encoding='utf-8')
    (wiki_dir / '_index.md').unlink()
    wiki_dir.rmdir()
    resolve_wiki(None, fallbacks=(lambda: indexed,))
    assert 'settings.json missing' in capsys.readouterr().err


def test_resolver_refuses_ambiguous_root(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two markers on one ancestor chain refuse loudly, naming both.

    A nested ``.wiki/settings.json`` below a real root (a copied wiki, a
    stray marker) makes every command ambiguous.
    """
    outer = tmp_path / 'outer'
    inner = outer / 'inner'
    deep = inner / 'deep'
    deep.mkdir(parents=True)
    _declare_root(outer)
    _declare_root(inner)
    # the bare-cwd walk refuses
    monkeypatch.chdir(deep)
    with pytest.raises(ValueError, match='Ambiguous wiki root') as excinfo:
        resolve_wiki_root(None)
    assert str(outer) in str(excinfo.value)
    assert str(inner) in str(excinfo.value)
    # the enclosing-root probe (init nesting, --path guards) refuses too
    with pytest.raises(ValueError, match='Ambiguous wiki root'):
        enclosing_wiki_root(deep)


def test_resolve_wiki_corroboration_notices(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Resolution names missing markers, missing indexes, and outer indexes."""
    # an undeclared index tree is tolerated with the restore notice
    undeclared = tmp_path / 'undeclared'
    undeclared.mkdir()
    (undeclared / '_index.md').write_text('x\n', encoding='utf-8')
    resolve_wiki(str(undeclared))
    err = capsys.readouterr().err
    assert '.wiki/settings.json missing' in err
    assert 'wiki update' in err
    # a declared root missing its index is named at resolution time
    damaged = tmp_path / 'damaged'
    damaged.mkdir()
    _declare_root(damaged, index=False)
    resolve_wiki(str(damaged))
    assert 'missing its _index.md' in capsys.readouterr().err
    # an index chain extending above the declared root is a named warning
    outer = tmp_path / 'site'
    root = outer / 'docs'
    root.mkdir(parents=True)
    _declare_root(root)
    (outer / '_index.md').write_text('foreign\n', encoding='utf-8')
    resolve_wiki(str(root))
    assert 'above the wiki root' in capsys.readouterr().err
    # a directory with neither marker nor index is not a wiki at all
    empty = tmp_path / 'empty'
    empty.mkdir()
    with pytest.raises(NotADirectoryError):
        resolve_wiki(str(empty))


# ------ load_wiki_class


def test_load_wiki_class(tmp_path: pathlib.Path) -> None:
    """Loads the default ``Wiki`` or the subclass named by the sole ``__all__`` entry."""
    # no config file -- returns default Wiki (a hookless wiki needs no trust)
    cls = load_wiki_class(tmp_path)
    assert cls is Wiki

    # the hook cases below run code, so the root must be trusted first
    trust_root(tmp_path)

    # custom subclass named by the sole __all__ entry
    config_dir = tmp_path / '.wiki'
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

    # a hook this environment cannot load fails naming the hook file, so
    # a wiki declaring an uninstalled subclass is diagnosable, not cryptic
    (config_dir / 'wiki.py').write_text(
        'import _no_such_embedder_module\n',
        encoding='utf-8',
    )
    with pytest.raises(RuntimeError, match=r'\.wiki/wiki\.py'):
        load_wiki_class(tmp_path)


def test_load_wiki_class_refuses_untrusted_hook(tmp_path: pathlib.Path) -> None:
    """A ``.wiki/wiki.py`` on an untrusted root is refused, not executed.

    The refusal names the hook and points at ``wiki trust``; once the root
    is trusted the same hook loads. A hookless root never needs trust.
    """
    config_dir = tmp_path / '.wiki'
    config_dir.mkdir()
    # a hook whose top-level code drops a sentinel beside the wiki root
    # (an absolute path off __file__, so running it is observable without
    # polluting the caller's cwd)
    sentinel = tmp_path / 'ran'
    (config_dir / 'wiki.py').write_text(
        'import pathlib\n'
        'from wiki.core.wiki import Wiki\n\n'
        "(pathlib.Path(__file__).resolve().parent.parent / 'ran').touch()\n\n"
        'class MyWiki(Wiki):\n    pass\n\n'
        "__all__ = ['MyWiki']\n",
        encoding='utf-8',
    )
    assert not is_trusted(tmp_path)
    with pytest.raises(PermissionError, match=r'(?s)untrusted wiki hook.*wiki trust'):
        load_wiki_class(tmp_path)
    # the hook never ran, so its side effect never happened
    assert not sentinel.exists()

    # trusting the root lets the same hook load and run
    trust_root(tmp_path)
    cls = load_wiki_class(tmp_path)
    assert cls is not Wiki
    assert issubclass(cls, Wiki)
    assert sentinel.exists()


def test_trust_root_records_resolved_root(tmp_path: pathlib.Path) -> None:
    """``trust_root`` records the resolved root and reports it as trusted.

    Trust keys on the resolved path, so a symlink alias to the same tree
    reads back as trusted; the store never depends on how it was reached.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    assert not is_trusted(root)
    recorded = trust_root(root)
    assert recorded == root.resolve()
    assert is_trusted(root)
    # an alias resolving to the same root is covered by the one record
    alias = tmp_path / 'alias'
    alias.symlink_to(root)
    assert is_trusted(alias)


# ------ command registration seam


def test_reused_command_honors_resolve_override(tmp_path: pathlib.Path) -> None:
    """A reused command resolves its wiki through the injected ``resolve``.

    Embedders rebuild their sub-apps from ``wiki.cli.cmd`` registration
    functions, injecting resolution (root fallbacks, subclass defaults)
    through the ``resolve`` keyword instead of forking command bodies.
    """
    # a real wiki the stub resolver pins, regardless of cwd
    root = tmp_path / 'docs'
    root.mkdir()
    Wiki(root).init('demo')
    page = root / 'notes.md'
    page.write_text(
        '---\nname: notes\ndesc: Notes.\n---\n\n# notes\n', encoding='utf-8'
    )
    calls = []

    def resolve(path: Optional[str]) -> Wiki:
        calls.append(path)
        return Wiki(root)

    # the registered command reads through the injected resolver
    app = typer.Typer()
    cmd.read(app, resolve=resolve)
    result = CliRunner().invoke(app, ['notes'])
    assert result.exit_code == 0
    assert result.output == page.read_text(encoding='utf-8')
    assert calls == [None]


def test_resolve_wiki_default_class(tmp_path: pathlib.Path) -> None:
    """``default`` picks the class when no ``.wiki/wiki.py`` hook names one.

    An embedder CLI passes its own ``Wiki`` subclass, so its wikis get
    embedder semantics without a hook file; a hook still wins when
    present.
    """

    class EmbedderWiki(Wiki):
        pass

    root = tmp_path / 'docs'
    root.mkdir()
    _declare_root(root)
    # the embedder default is instantiated for a hookless wiki
    wiki = resolve_wiki(str(root), default=EmbedderWiki)
    assert type(wiki) is EmbedderWiki
    # the bare default remains the base class
    assert type(resolve_wiki(str(root))) is Wiki
    # a hook still overrides any default (once the root is trusted to run it)
    trust_root(root)
    (root / '.wiki' / 'wiki.py').write_text(
        'from wiki.core.wiki import Wiki\n\n'
        'class HookWiki(Wiki):\n'
        '    pass\n\n'
        "__all__ = ['HookWiki']\n",
        encoding='utf-8',
    )
    assert type(resolve_wiki(str(root), default=EmbedderWiki)).__name__ == 'HookWiki'


# ------ configure_git_merge_driver


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_configure_git_merge_driver(tmp_path: pathlib.Path) -> None:
    """Wiring sets the git driver and writes the glob without ever committing.

    A no-op outside a git repo; idempotent; and -- per the org's
    never-auto-commit rule -- it writes ``.gitattributes`` to the working tree
    only, never staging or committing it. The driver is registered as the
    stable ``wiki _merge`` command -- an absolute path into the installing
    venv silently breaks on a rebuild/move.
    """
    # no-op outside a git repo
    configure_git_merge_driver(tmp_path)
    assert not (tmp_path / '.gitattributes').exists()

    # a real repo with a wiki subdir
    _git(tmp_path, 'init', '-b', 'main')
    _git(tmp_path, 'config', 'user.email', 'test@test.com')
    _git(tmp_path, 'config', 'user.name', 'Test')
    (tmp_path / 'README.md').write_text('# r\n', encoding='utf-8')
    _git(tmp_path, 'add', 'README.md')
    _git(tmp_path, 'commit', '-m', 'init')
    wiki_dir = tmp_path / 'wiki'
    wiki_dir.mkdir()
    head_before = _git(tmp_path, 'rev-parse', 'HEAD').stdout

    configure_git_merge_driver(wiki_dir)

    # driver is the stable CLI command and the glob is written to the worktree
    assert _git(tmp_path, 'config', 'merge.wiki.driver').stdout.strip() == (
        'wiki _merge %O %A %B %L %P'
    )
    attributes = (tmp_path / '.gitattributes').read_text(encoding='utf-8')
    assert '**/_index.md merge=wiki' in attributes.splitlines()
    # nothing is committed (no new HEAD) and nothing is staged (the rule)
    assert _git(tmp_path, 'rev-parse', 'HEAD').stdout == head_before
    assert (
        '.gitattributes' not in _git(tmp_path, 'diff', '--cached', '--name-only').stdout
    )
    assert '.gitattributes' in _git(tmp_path, 'status', '--porcelain').stdout

    # idempotent -- a second call does not duplicate the mapping
    configure_git_merge_driver(wiki_dir)
    final = (tmp_path / '.gitattributes').read_text(encoding='utf-8')
    assert final.splitlines().count('**/_index.md merge=wiki') == 1


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_driver_skips_dirty_gitattributes(tmp_path: pathlib.Path) -> None:
    """The wiring leaves ``.gitattributes`` untouched while it has pending edits.

    It defers the attribute-map write until ``.gitattributes`` is clean (the
    ``merge.wiki`` config still applies), so it never entangles with the
    user's uncommitted work; once clean, a re-run writes the map (it converges).
    """
    # a repo whose tracked .gitattributes has an uncommitted edit
    _git(tmp_path, 'init', '-b', 'main')
    _git(tmp_path, 'config', 'user.email', 'test@test.com')
    _git(tmp_path, 'config', 'user.name', 'Test')
    attributes = tmp_path / '.gitattributes'
    attributes.write_text('*.txt text\n', encoding='utf-8')
    _git(tmp_path, 'add', '.gitattributes')
    _git(tmp_path, 'commit', '-m', 'init')
    attributes.write_text('*.txt text\n*.md text\n', encoding='utf-8')
    wiki_dir = tmp_path / 'wiki'
    wiki_dir.mkdir()

    # dirty .gitattributes: the map is not written, but the config is still set
    configure_git_merge_driver(wiki_dir)
    assert 'merge=wiki' not in attributes.read_text(encoding='utf-8')
    assert '_merge' in _git(tmp_path, 'config', 'merge.wiki.driver').stdout

    # once .gitattributes is clean, a re-run writes the map (it converges)
    _git(tmp_path, 'add', '.gitattributes')
    _git(tmp_path, 'commit', '-m', 'edit')
    configure_git_merge_driver(wiki_dir)
    assert '**/_index.md merge=wiki' in attributes.read_text(encoding='utf-8')


# ------ helpers


def _declare_root(root: pathlib.Path, *, index: bool = True) -> None:
    """Declare ``root`` as a wiki root (settings marker plus an index)."""
    (root / '.wiki').mkdir(parents=True, exist_ok=True)
    (root / '.wiki' / 'settings.json').write_text('{}\n', encoding='utf-8')
    if index:
        (root / '_index.md').write_text('root\n', encoding='utf-8')
