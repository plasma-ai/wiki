"""Test the ``wiki.cli.utils`` module."""

from __future__ import annotations

import concurrent.futures as cf
import fcntl
import json
import os
import pathlib
import subprocess
import sys
import threading
from typing import Optional

import pytest
import typer
from typer.testing import CliRunner

from wiki.cli import cmd, utils
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

from .conftest import GIT, _env, _git

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
    'test_trust_root_skips_rewrite_when_already_trusted',
    'test_trust_root_concurrent_writes_keep_every_entry',
    'test_trust_root_tightens_store_permissions',
    'test_trust_root_refuses_symlinked_store',
    'test_trust_root_refuses_hard_linked_store',
    'test_trust_reads_refuse_a_tampered_store',
    'test_trust_store_refuses_unsafe_modes',
    'test_trust_refuses_a_symlinked_config_home',
    'test_trust_root_tightens_and_guards_the_lock',
    'test_trust_root_bounds_the_wait_for_a_held_lock',
    'test_config_home_inside_a_wiki_is_refused',
    'test_trust_store_refuses_non_regular_files',
    'test_trust_root_refuses_a_corrupt_store',
    'test_trust_root_writes_over_a_blank_store',
    'test_is_trusted_ignores_malformed_store',
    'test_reused_command_honors_resolve_override',
    'test_resolve_wiki_default_class',
    'test_interrupt_reports_and_exits_130',
    'test_merge_driver_wiring_ignores_ambient_git_dir',
    'test_configure_git_merge_driver',
    'test_merge_driver_skips_dirty_gitattributes',
    'test_merge_driver_tolerates_undecodable_git_output',
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
    tree = tmp_path / 'tree'
    nested = tree / 'a' / 'b'
    nested.mkdir(parents=True)
    (tree / '_index.md').write_text('root\n', encoding='utf-8')
    (tree / 'a' / '_index.md').write_text('mid\n', encoding='utf-8')
    (nested / '_index.md').write_text('leaf\n', encoding='utf-8')
    monkeypatch.chdir(nested)
    result = resolve_wiki_root(None)
    assert result == tree

    # a raw (unindexed) folder inside the tree climbs from its nearest
    # indexed ancestor, like an explicit `--path .` does -- never to a
    # different wiki via the wiki/ fallback
    raw = nested / 'raw' / 'deep'
    raw.mkdir(parents=True)
    monkeypatch.chdir(raw)
    result = resolve_wiki_root(None)
    assert result == tree

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
    with pytest.raises(TypeError):
        load_wiki_class(tmp_path)

    # __all__ must have exactly one entry
    (config_dir / 'wiki.py').write_text(
        "__all__ = ['A', 'B']\nA = 1\nB = 2\n",
        encoding='utf-8',
    )
    with pytest.raises(TypeError):
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


def test_trust_root_skips_rewrite_when_already_trusted(
    tmp_path: pathlib.Path,
) -> None:
    """Re-trusting an already-trusted root does not rewrite its record.

    Trust is idempotent: the second call skips the write, so a fleet that
    trusts a root per node at spawn incurs no churn. A deliberately
    backdated timestamp seeded into the store proves it -- any rewrite
    would refresh it to now, so its survival is what a same-second
    re-run's byte-identical output cannot show.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    trust_root(root)
    store = pathlib.Path(os.environ['WIKI_CONFIG_DIR']) / 'settings.json'
    # backdate the recorded timestamp; a rewrite would bump it to now
    data = json.loads(store.read_text(encoding='utf-8'))
    data['trusted'][str(root.resolve())] = '2000-01-01T00:00:00Z'
    store.write_text(
        json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8'
    )
    recorded = trust_root(root)
    assert recorded == root.resolve()
    reread = json.loads(store.read_text(encoding='utf-8'))['trusted']
    assert reread[str(root.resolve())] == '2000-01-01T00:00:00Z'
    assert is_trusted(root)


def test_trust_root_concurrent_writes_keep_every_entry(
    tmp_path: pathlib.Path,
) -> None:
    """Parallel ``trust_root`` calls never lose each other's entries.

    The store is a read-modify-write of one JSON file, so without mutual
    exclusion concurrent trusters clobber each other; a fleet trusts one
    root per node at spawn, so the file lock is what makes that safe. The
    workers cross a barrier before writing to force the writes to contend,
    then the store must still hold every distinct root.
    """
    workers = 24
    roots = []
    for i in range(workers):
        root = tmp_path / f'w{i}'
        root.mkdir()
        roots.append(root)
    barrier = threading.Barrier(workers)

    def trust(root: pathlib.Path) -> None:
        # release all workers into the critical section together
        barrier.wait()
        trust_root(root)

    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(trust, roots))

    store = pathlib.Path(os.environ['WIKI_CONFIG_DIR']) / 'settings.json'
    recorded = json.loads(store.read_text(encoding='utf-8'))['trusted']
    assert set(recorded) == {str(root.resolve()) for root in roots}


def test_trust_root_tightens_store_permissions(tmp_path: pathlib.Path) -> None:
    """The trust store and its home are owner-only, and stay that way.

    The store decides which wikis may run code, so a group- or
    world-readable one leaks the list and a writable one is an outright
    authorization bypass. Modes loosened out-of-band (a backup restore, a
    stray chmod, a permissive umask) are repaired on the next call --
    including the idempotent path, where an already-trusted root returns
    before the write, so the repair cannot ride along with it.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    trust_root(root)
    home = pathlib.Path(os.environ['WIKI_CONFIG_DIR'])
    store = home / 'settings.json'
    assert store.stat().st_mode & 0o777 == 0o600
    assert home.stat().st_mode & 0o777 == 0o700
    # loosen both, then re-trust the same root: the early return skips the
    # rewrite, so only the self-heal can restore these modes
    os.chmod(store, 0o644)
    os.chmod(home, 0o755)  # noqa: S103
    trust_root(root)
    assert store.stat().st_mode & 0o777 == 0o600
    assert home.stat().st_mode & 0o777 == 0o700


def test_trust_root_refuses_symlinked_store(tmp_path: pathlib.Path) -> None:
    """A store symlinked out of the config home is refused, target untouched.

    The self-heal chmod and the rewrite behind it address the store by
    path, so a pre-planted symlink could retarget them onto an arbitrary
    file. The ``O_NOFOLLOW`` open refuses it up front -- on the rewrite
    (not-yet-trusted) path and the early-return (store claims trusted)
    path alike -- and the symlink's target keeps its bytes and mode.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    victim = tmp_path / 'victim'
    victim.write_text('victim bytes\n', encoding='utf-8')
    os.chmod(victim, 0o644)
    home = pathlib.Path(os.environ['WIKI_CONFIG_DIR'])
    home.mkdir(parents=True, exist_ok=True)
    store = home / 'settings.json'
    store.symlink_to(victim)
    # rewrite path: the root is not yet trusted
    with pytest.raises(PermissionError, match='symlinked trust store'):
        trust_root(root)
    assert victim.read_text(encoding='utf-8') == 'victim bytes\n'
    assert victim.stat().st_mode & 0o777 == 0o644
    assert store.is_symlink()
    # early-return path: the symlink's target claims the root is trusted
    victim.write_text(
        json.dumps({'trusted': {str(root.resolve()): '2000-01-01T00:00:00Z'}}),
        encoding='utf-8',
    )
    with pytest.raises(PermissionError, match='symlinked trust store'):
        trust_root(root)
    assert victim.stat().st_mode & 0o777 == 0o644
    assert store.is_symlink()


def test_trust_root_refuses_hard_linked_store(tmp_path: pathlib.Path) -> None:
    """A store hard-linked to a file outside the config home is refused.

    A second link is the symlink attack without the symlink: the store
    is the attacker's inode, so the self-heal would re-mode their file
    and every trusted root written afterwards would be editable through
    a name the ``0700`` home does not cover. Both paths refuse -- the
    rewrite, and the early return an already-trusted root takes.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    victim = tmp_path / 'victim'
    victim.write_text(
        json.dumps({'trusted': {str(root.resolve()): '2000-01-01T00:00:00Z'}}),
        encoding='utf-8',
    )
    os.chmod(victim, 0o644)
    home = pathlib.Path(os.environ['WIKI_CONFIG_DIR'])
    home.mkdir(parents=True, exist_ok=True)
    store = home / 'settings.json'
    os.link(victim, store)
    # the early-return path: the aliased store claims the root is trusted
    with pytest.raises(PermissionError, match='hard-linked trust store'):
        trust_root(root)
    assert victim.stat().st_mode & 0o777 == 0o644
    # the rewrite path: nothing claims the root is trusted
    victim.write_text('{}\n', encoding='utf-8')
    with pytest.raises(PermissionError, match='hard-linked trust store'):
        trust_root(root)
    assert victim.read_text(encoding='utf-8') == '{}\n'
    assert victim.stat().st_mode & 0o777 == 0o644


def test_trust_reads_refuse_a_tampered_store(tmp_path: pathlib.Path) -> None:
    """The read path refuses the tampered stores the write path refuses.

    A trust decision must never be read through what a trust write would
    refuse: a store symlinked (or hard-linked) to a file outside the
    config home would let that file confer hook-execution trust, so
    ``is_trusted`` -- and the hook gate behind it -- raises the same
    plain refusal instead of following the link.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    # a file outside the config home claiming the root is trusted
    outside = tmp_path / 'outside'
    outside.write_text(
        json.dumps({'trusted': {str(root.resolve()): '2000-01-01T00:00:00Z'}}),
        encoding='utf-8',
    )
    home = pathlib.Path(os.environ['WIKI_CONFIG_DIR'])
    home.mkdir(parents=True, exist_ok=True)
    store = home / 'settings.json'
    store.symlink_to(outside)
    with pytest.raises(PermissionError, match='symlinked trust store'):
        is_trusted(root)
    # the hook gate reads through the same guard
    (root / '.wiki').mkdir()
    (root / '.wiki' / 'wiki.py').write_text('__all__ = []\n', encoding='utf-8')
    with pytest.raises(PermissionError, match='symlinked trust store'):
        load_wiki_class(root)
    # a hard link is the same attack without the symlink
    store.unlink()
    os.link(outside, store)
    with pytest.raises(PermissionError, match='hard-linked trust store'):
        is_trusted(root)


def test_trust_store_refuses_unsafe_modes(tmp_path: pathlib.Path) -> None:
    """A store other users may write is refused; an unreadable one says so.

    A group- or world-writable store is the hard-link attack without the
    hard link -- any local user edits the list that decides which wikis
    run code -- and re-tightening cannot undo an entry already planted,
    so both paths refuse instead of self-healing. Loosened *read* bits
    still self-heal (nothing was forgeable), which is what keeps the
    repair worth having. An unreadable store fails closed either way,
    but names the path and the fix rather than leaking ``EACCES``.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    home = pathlib.Path(os.environ['WIKI_CONFIG_DIR'])
    home.mkdir(parents=True, exist_ok=True)
    store = home / 'settings.json'
    planted = json.dumps({'trusted': {str(root.resolve()): '2000-01-01T00:00:00Z'}})
    store.write_text(planted, encoding='utf-8')
    for mode in (0o666, 0o660, 0o606):
        os.chmod(store, mode)
        with pytest.raises(PermissionError, match='world-writable trust store'):
            is_trusted(root)
        with pytest.raises(PermissionError, match='world-writable trust store'):
            trust_root(root)
        # the refusal is the whole response: the mode is not repaired under it
        assert store.stat().st_mode & 0o777 == mode
    # an unreadable store fails closed in plain language, read and write
    os.chmod(store, 0o000)
    for consult in (is_trusted, trust_root):
        with pytest.raises(PermissionError, match='Cannot read the trust store'):
            consult(root)


def test_trust_refuses_a_symlinked_config_home(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config home symlinked at a foreign directory is refused, read and write.

    The home tightening (``0700``) would otherwise chmod through the
    link -- re-moding a directory outside the store's custody, with the
    store then written inside it -- and the read path would decide trust
    from the ``settings.json`` inside the link's target, since
    ``O_NOFOLLOW`` on the store covers only the final component: a
    redirected home is the store attack one level up, so it would confer
    hook execution on a root the user never trusted. A link at a
    non-directory (a dotfiles target not yet materialized, a link into an
    unmounted volume) is the same fix, and gets the same message instead
    of a bare ``File exists``. Every refusal names the sanctioned
    relocation (``WIKI_CONFIG_DIR`` at the real directory), and the
    link's target keeps its mode and contents.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    victim = tmp_path / 'victim_home'
    victim.mkdir()
    os.chmod(victim, 0o755)  # noqa: S103
    # the target vouches for a root the user never trusted
    (victim / 'settings.json').write_text(
        json.dumps({'trusted': {str(root.resolve()): '2000-01-01T00:00:00Z'}}),
        encoding='utf-8',
    )
    linked = tmp_path / 'linked_home'
    linked.symlink_to(victim)
    monkeypatch.setenv('WIKI_CONFIG_DIR', str(linked))
    with pytest.raises(PermissionError, match='symlinked config home'):
        trust_root(root)
    with pytest.raises(PermissionError, match='symlinked config home'):
        is_trusted(root)
    # the hook gate reads through the same guard, so the hook never runs
    (root / '.wiki').mkdir()
    (root / '.wiki' / 'wiki.py').write_text('__all__ = []\n', encoding='utf-8')
    with pytest.raises(PermissionError, match='symlinked config home'):
        load_wiki_class(root)
    assert victim.stat().st_mode & 0o777 == 0o755
    assert [entry.name for entry in victim.iterdir()] == ['settings.json']
    # a link at a non-directory is named the same way, not `File exists`
    absent = tmp_path / 'never_materialized'
    victim_file = tmp_path / 'victim_file'
    victim_file.write_text('victim bytes\n', encoding='utf-8')
    for target in (absent, victim_file):
        link = tmp_path / f'home_at_{target.name}'
        link.symlink_to(target)
        monkeypatch.setenv('WIKI_CONFIG_DIR', str(link))
        with pytest.raises(PermissionError, match='symlinked config home'):
            trust_root(root)
    assert not absent.exists()
    assert victim_file.read_text(encoding='utf-8') == 'victim bytes\n'


def test_trust_root_tightens_and_guards_the_lock(tmp_path: pathlib.Path) -> None:
    """The lock sibling gets the store's custody: re-tightened and unaliased.

    ``O_CREAT`` applies its mode at creation only (umask-masked), so a
    lock loosened out-of-band would stay loose forever without the
    per-call re-tighten. Every shape the store refuses the lock refuses
    too, in the same plain language and never as a raw errno: the
    per-call ``fchmod`` addresses whatever inode the name reaches, so a
    symlinked or hard-linked lock re-modes a file outside the store, and
    a FIFO or a directory is no lock at all.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    home = pathlib.Path(os.environ['WIKI_CONFIG_DIR'])
    home.mkdir(parents=True, exist_ok=True)
    lock = home / '.settings.lock'
    # a loosened surviving lock is re-tightened by the next write
    lock.touch()
    os.chmod(lock, 0o666)  # noqa: S103
    trust_root(root)
    assert lock.stat().st_mode & 0o777 == 0o600
    # a lock aliasing a file outside the store is refused, its target untouched
    other = tmp_path / 'wiki2'
    other.mkdir()
    victim = tmp_path / 'victim_lock'
    victim.write_text('victim bytes\n', encoding='utf-8')
    os.chmod(victim, 0o644)
    for alias, refusal in (
        (lock.symlink_to, 'symlinked trust-store lock'),
        (lambda target: os.link(target, lock), 'hard-linked trust-store lock'),
    ):
        lock.unlink()
        alias(victim)
        with pytest.raises(PermissionError, match=refusal):
            trust_root(other)
        assert victim.read_text(encoding='utf-8') == 'victim bytes\n'
        assert victim.stat().st_mode & 0o777 == 0o644
    # a non-regular lock is named, not blocked on and not left to flock
    lock.unlink()
    os.mkfifo(lock)
    with pytest.raises(PermissionError, match='lock is not a regular file'):
        trust_root(other)
    lock.unlink()
    lock.mkdir()
    with pytest.raises(PermissionError, match='lock is not a regular file'):
        trust_root(other)


def test_trust_root_bounds_the_wait_for_a_held_lock(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock held by someone else times out loudly instead of hanging forever.

    A blocking ``LOCK_EX`` wedges every fleet-wide spawn-time trust call
    behind one stopped holder, with no output to diagnose it by. The wait
    is bounded and the refusal names the lock; once the holder lets go,
    the very same call succeeds, so the bound never costs a legitimate
    waiter its turn.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    home = pathlib.Path(os.environ['WIKI_CONFIG_DIR'])
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(utils, '_LOCK_TIMEOUT', 0.2)
    holder = os.open(home / '.settings.lock', os.O_RDONLY | os.O_CREAT, 0o600)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        with pytest.raises(TimeoutError, match='trust-store lock'):
            trust_root(root)
    finally:
        os.close(holder)
    assert not is_trusted(root)
    trust_root(root)
    assert is_trusted(root)


@pytest.mark.parametrize('placement', ['control-dir', 'subfolder'])
def test_config_home_inside_a_wiki_is_refused(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    placement: str,
) -> None:
    """A trust store inside a wiki is refused: no wiki vouches for itself.

    The store decides which wikis may run code, so a wiki that holds it
    can list itself as trusted -- clone the repository, point
    ``WIKI_CONFIG_DIR`` inside it, and its committed map runs its own
    hook. At the wiki's own ``.wiki/`` the store is additionally the
    same file as the declared-root marker, merging the machine-local
    trust map into the repository's committed settings. Both the read
    path (which the hook gate consults) and the write path refuse.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    (root / '_index.md').write_text('# wiki\n\n***\n', encoding='utf-8')
    if placement == 'control-dir':
        home = root / '.wiki'
    else:
        home = root / 'notes' / 'store'
    home.mkdir(parents=True)
    monkeypatch.setenv('WIKI_CONFIG_DIR', str(home))
    # a store the wiki itself carries, claiming the wiki is trusted
    (home / 'settings.json').write_text(
        json.dumps({'trusted': {str(root.resolve()): '2000-01-01T00:00:00Z'}}),
        encoding='utf-8',
    )

    # the read path refuses, so the self-conferred entry never grants trust
    with pytest.raises(PermissionError, match='config home inside the wiki'):
        is_trusted(root)
    (root / '.wiki').mkdir(exist_ok=True)
    (root / '.wiki' / 'wiki.py').write_text('__all__ = []\n', encoding='utf-8')
    with pytest.raises(PermissionError, match='config home inside the wiki'):
        load_wiki_class(root)
    # and the write path refuses to record anything there
    with pytest.raises(PermissionError, match='config home inside the wiki'):
        trust_root(root)


def test_trust_store_refuses_non_regular_files(tmp_path: pathlib.Path) -> None:
    """A non-regular file planted as the store is refused, never blocked on.

    A FIFO would block the open until a writer appears -- hanging every
    invocation that consults trust -- and a directory would fail deep in
    the rewrite with a cryptic error; both are refused up front, read
    and write alike, with a message naming the path and the fix.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    home = pathlib.Path(os.environ['WIKI_CONFIG_DIR'])
    home.mkdir(parents=True, exist_ok=True)
    store = home / 'settings.json'
    # a FIFO with no writer: the guarded open must not block
    os.mkfifo(store)
    with pytest.raises(PermissionError, match='not a regular file'):
        is_trusted(root)
    with pytest.raises(PermissionError, match='not a regular file'):
        trust_root(root)
    # a directory in the store's place is refused the same way
    store.unlink()
    store.mkdir()
    with pytest.raises(PermissionError, match='not a regular file'):
        trust_root(root)


def test_trust_root_refuses_a_corrupt_store(tmp_path: pathlib.Path) -> None:
    """Re-trusting over a corrupt store refuses instead of emptying it.

    A tolerant read folds corruption into an empty store -- right for a
    trust decision (nothing is trusted), catastrophic for the rewrite,
    which would silently drop every trusted root with a clean exit. That
    holds for every shape a rewrite cannot preserve: unparseable JSON, a
    top level that is not an object, a ``trusted`` that is not an object
    (the one key that matters), and bytes that are not even UTF-8. The
    refusal names the store and the stakes; the corrupt bytes survive
    for repair, and reads keep failing safe.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    home = pathlib.Path(os.environ['WIKI_CONFIG_DIR'])
    home.mkdir(parents=True, exist_ok=True)
    store = home / 'settings.json'
    corruptions = (
        b'{"trusted": {truncated',
        b'["not", "an", "object"]',
        b'{"trusted": ["/one", "/two"], "other": "keepme"}',
        b'{"trusted": {"\xff": "2000-01-01T00:00:00Z"}}',
    )
    for corrupt in corruptions:
        store.write_bytes(corrupt)
        # the trust decision fails safe; the rewrite refuses loudly
        assert not is_trusted(root)
        with pytest.raises(ValueError, match='Trust store is corrupt'):
            trust_root(root)
        # the corrupt bytes survive for repair
        assert store.read_bytes() == corrupt


def test_trust_root_writes_over_a_blank_store(tmp_path: pathlib.Path) -> None:
    """A blank store is written, not refused as unrepairable corruption.

    An empty file holds no trusted roots, so the strict read's whole
    justification -- a rewrite would drop every trusted root -- is
    vacuous, while refusing it wedges every spawn-time trust call on the
    machine until a human removes the file. A store zeroed by an
    interrupted copy or a bootstrap ``touch`` reads as absent instead.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    home = pathlib.Path(os.environ['WIKI_CONFIG_DIR'])
    home.mkdir(parents=True, exist_ok=True)
    store = home / 'settings.json'
    for blank in ('', '\n  \n'):
        store.write_text(blank, encoding='utf-8')
        assert not is_trusted(root)
        assert trust_root(root) == root.resolve()
        assert is_trusted(root)


def test_is_trusted_ignores_malformed_store(tmp_path: pathlib.Path) -> None:
    """A hand-edited non-dict ``trusted`` value reads as an empty store.

    A string value would turn the membership check into substring
    matching, marking any prefix of the stored text as trusted.
    """
    store = pathlib.Path(os.environ['WIKI_CONFIG_DIR']) / 'settings.json'
    store.parent.mkdir(parents=True, exist_ok=True)
    corrupt = json.dumps({'trusted': str(tmp_path / 'wikis')})
    store.write_text(corrupt, encoding='utf-8')
    assert not is_trusted(tmp_path)


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
        '---\nname: notes\ndesc: Notes.\n---\n\n# notes\n',
        encoding='utf-8',
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
        """Wiki subclass standing in for an embedder CLI's default."""

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


# ------ command error wrapper

# a wrapped command interrupted mid-body: the self-delivered SIGINT is a
# foreground Ctrl-C in miniature
_INTERRUPTED = """
import os
import signal

import typer

from wiki.cli.utils import command

app = typer.Typer()


@command(app, 'quick')
def quick() -> None:
    os.kill(os.getpid(), signal.SIGINT)


app([])
"""


def test_interrupt_reports_and_exits_130() -> None:
    """A Ctrl-C prints one ``Interrupted.`` line and exits 130.

    The wrapper names the interrupt on stderr and re-raises, so typer's
    own KeyboardInterrupt handling supplies the exit code and no
    traceback reaches the operator.
    """
    result = subprocess.run(
        [sys.executable, '-c', _INTERRUPTED],
        capture_output=True,
        text=True,
        env=_env(),
        timeout=60,
    )
    assert result.returncode == 130, result.stderr
    assert result.stderr == 'Interrupted.\n'


# ------ configure_git_merge_driver


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_driver_wiring_ignores_ambient_git_dir(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An inherited ``GIT_DIR`` never retargets the driver wiring.

    A git hook exports ``GIT_DIR`` (relative, resolving against the
    probe's cwd) and a caller may export one naming another repository;
    either would land ``merge.wiki.driver`` in the foreign repo's config
    and drop ``.gitattributes`` beside the wrong toplevel -- inside the
    wiki itself, since ``GIT_DIR`` defaults the work tree to the cwd.
    Discovery pins to the repository enclosing the wiki.
    """
    # the repo that must receive the wiring, and a foreign one
    repo = tmp_path / 'repo'
    (repo / 'wiki').mkdir(parents=True)
    _git(repo, 'init', '-q', '-b', 'main')
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    _git(foreign, 'init', '-q', '-b', 'main')
    monkeypatch.setenv('GIT_DIR', str(foreign / '.git'))
    configure_git_merge_driver(repo / 'wiki')
    monkeypatch.delenv('GIT_DIR')
    # the enclosing repo got both halves of the wiring
    driver = _git(repo, 'config', '--get', 'merge.wiki.driver')
    assert driver.stdout.strip() == 'wiki _merge %O %A %B %L %P'
    attributes = (repo / '.gitattributes').read_text(encoding='utf-8')
    assert '**/_index.md merge=wiki' in attributes.splitlines()
    # the foreign repo got neither, and none landed inside the wiki
    foreign_driver = _git(foreign, 'config', '--get', 'merge.wiki.driver')
    assert foreign_driver.returncode != 0
    assert not (foreign / '.gitattributes').exists()
    assert not (repo / 'wiki' / '.gitattributes').exists()


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


def test_merge_driver_tolerates_undecodable_git_output(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Git output the locale codec cannot decode never crashes the wiring.

    Repo paths are raw bytes, so a toplevel undecodable in the active
    locale must round-trip through the driver configuration instead of
    raising mid-run. The stub git emits such a toplevel and reports a
    dirty ``.gitattributes``, so the run completes as a clean no-op.
    """
    stub_dir = tmp_path / 'bin'
    stub_dir.mkdir()
    stub = stub_dir / 'git'
    stub.write_bytes(
        b'#!/bin/sh\n'
        b'case "$*" in\n'
        b"*rev-parse*) printf '/caf\\351\\n' ;;\n"
        b"*status*) printf ' M .gitattributes\\n' ;;\n"
        b'esac\n'
    )
    stub.chmod(0o755)
    path = os.environ['PATH']
    monkeypatch.setenv('PATH', f'{stub_dir}{os.pathsep}{path}')
    configure_git_merge_driver(tmp_path)


# ------ helpers


def _declare_root(root: pathlib.Path, *, index: bool = True) -> None:
    """Declare ``root`` as a wiki root (settings marker plus an index)."""
    (root / '.wiki').mkdir(parents=True, exist_ok=True)
    (root / '.wiki' / 'settings.json').write_text('{}\n', encoding='utf-8')
    if index:
        (root / '_index.md').write_text('root\n', encoding='utf-8')
