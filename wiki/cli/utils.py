"""Shared helpers for ``wiki`` CLI commands."""

from __future__ import annotations

import datetime as dt
import errno
import fcntl
import functools
import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import sys
import time
import typing
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Literal, Optional

import typer

import wiki.util
from wiki.constants import (
    DEFAULT_WIKI_NAME,
    WIKI_CONFIG_DIR,
    WIKI_DIR,
    WIKI_INDEX,
    WIKI_SETTINGS,
)
from wiki.core.event import Event
from wiki.core.wiki import Wiki, _encloses_wiki_error

__all__ = [
    'command',
    'parse_slice',
    'parse_settings',
    'is_trusted',
    'trust_root',
    'load_wiki_class',
    'resolve_wiki',
    'resolve_wiki_root',
    'enclosing_wiki_root',
    'refuse_nested_init',
    'configure_git_merge_driver',
]

# bounds the wait for the trust-store lock in trust_root; long enough to
# outlast a fleet's spawn-time contention, short enough that one wedged
# holder refuses in plain language instead of hanging every node forever
_LOCK_TIMEOUT = 10.0

# how often the bounded wait retries the non-blocking lock acquisition
_LOCK_POLL = 0.05


def command(
    app: typer.Typer,
    name: str,
    **kwargs: Any,
) -> Callable:
    """Register a CLI command on ``app`` with error wrapping.

    A command error -- an unresolvable wiki, a bad subtree entry, a
    refused hook -- prints ``Error: <message>`` on stderr and exits 2,
    beside typer's own usage errors, so exit 1 is left to mean exactly
    the command's own nonzero outcome (``lint``'s issues found,
    ``search``'s no match, ``update --check``'s pending changes) and a
    script gating on one can never read a failed run as the other.
    """

    def decorator(f: Callable, /) -> Callable:
        if private := name.startswith('_'):
            kwargs.setdefault('hidden', True)

        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return f(*args, **kwargs)
            except (typer.Exit, typer.Abort, typer.BadParameter):
                raise
            except BrokenPipeError:
                # a downstream reader closed the pipe (not an error):
                # point stdout at devnull so the interpreter's exit
                # flush stays quiet, and end the pipeline successfully
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, sys.stdout.fileno())
                raise SystemExit(0) from None
            except Exception as e:
                error = type(e).__name__ if private else 'Error'
                typer.echo(f'{error}: {e}', err=True)
                raise SystemExit(2) from None

        return app.command(name, **kwargs)(wrapper)

    return decorator


def parse_slice(value: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """Parse a slice string into ``(start, stop)``."""
    if value is None:
        return None, None
    message = f'Invalid slice format: {value!r} (expected n:m, n:, or :m).'
    if ':' not in value:
        raise typer.BadParameter(message)
    left, right = value.split(':', 1)
    try:
        start = int(left) if left else None
        stop = int(right) if right else None
    except ValueError as e:
        raise typer.BadParameter(message) from e
    return start, stop


def parse_settings(value: Optional[str]) -> Optional[dict]:
    """Parse a ``--settings`` JSON object string."""
    if value is None:
        return None
    try:
        result = json.loads(value)
    except json.JSONDecodeError as e:
        raise typer.BadParameter(f'--settings must be valid JSON ({e}).') from e
    if not isinstance(result, dict):
        raise typer.BadParameter('--settings must be a JSON object.')
    return result


def is_trusted(root: pathlib.Path, /) -> bool:
    """Return whether ``root`` is on the user's trusted-wiki list."""
    # a hand-edited non-dict 'trusted' value reads as an empty store -- a
    # string would turn `in` into substring matching and over-trust prefixes
    trusted = _read_global_settings().get('trusted')
    return isinstance(trusted, dict) and str(root.expanduser().resolve()) in trusted


def trust_root(root: pathlib.Path) -> pathlib.Path:
    """Record ``root`` as trusted in the user-global settings; return the key.

    The store is ``~/.wiki/settings.json`` (``0600`` under a ``0700``
    home), a ``{trusted: {resolved_path: timestamp}}`` map keyed by the
    resolved root. Absolute paths are correct here -- this is a
    machine-local store, not repo-committed data.
    """
    resolved = root.expanduser().resolve()
    home = _config_home()
    # create only what is missing: mkdir(exist_ok=True) reports a symlinked
    # home pointing at a non-directory (a dotfiles target not yet
    # materialized, a link into an unmounted volume) as a bare
    # FileExistsError, which names neither WIKI_CONFIG_DIR nor the fix --
    # leave any existing name for the guarded open below to judge
    if not home.is_symlink():
        home.mkdir(parents=True, exist_ok=True)
    # tighten the home through its guarded descriptor, like the store file:
    # a plain chmod follows a pre-planted symlink and re-modes a directory
    # outside the store's custody -- with the store then written inside it
    home_fd = _open_config_home()
    try:
        os.fchmod(home_fd, 0o700)
    finally:
        os.close(home_fd)
    path = _settings_path()
    # re-tighten the store on every call, so perms loosened out-of-band (a
    # backup restore, a stray chmod, a loose umask) are repaired even when
    # the idempotent early return below skips the rewrite; the shared
    # guarded open (see _open_store) refuses a tampered store, so the
    # repair -- or the rewrite behind it -- can never retarget a file
    # outside the store
    store_fd = _open_store(path)
    if store_fd is not None:
        try:
            os.fchmod(store_fd, 0o600)
        finally:
            os.close(store_fd)
    # an already-trusted root skips the rewrite -- re-trusting is idempotent,
    # and not touching the store keeps fleet-wide spawn-time trust calls cheap
    if is_trusted(resolved):
        return resolved
    # the read-modify-write below loses concurrent entries without mutual
    # exclusion; the lock is a separate file because write_atomic replaces
    # settings.json by rename, so a lock on the settings inode would not survive
    # the write
    lock_path = home / '.settings.lock'
    lock_fd = _open_lock(lock_path)
    with os.fdopen(lock_fd, 'rb') as lock:
        # a plain blocking LOCK_EX wedges every fleet-wide spawn-time trust
        # call behind one stopped holder, silently and forever: poll instead,
        # and refuse naming the lock once the wait budget is spent
        deadline = time.monotonic() + _LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f'Timed out waiting for the trust-store lock:'
                        f' {lock_path} (another `wiki trust` holds it;'
                        f' remove the lock if none does).'
                    ) from None
                time.sleep(_LOCK_POLL)
        # O_CREAT applies the mode at creation only (umask-masked): re-tighten
        # the surviving lock inode on every call, like the store and the home
        os.fchmod(lock_fd, 0o600)
        # strict: rewriting over a corrupt store would fold it into an empty
        # one and silently drop every trusted root -- refuse instead
        settings = _read_global_settings(strict=True)
        # strict admits a dict or no 'trusted' key at all, never another shape
        trusted = settings.get('trusted', {})
        trusted[str(resolved)] = dt.datetime.now(dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
        settings['trusted'] = trusted
        content = json.dumps(settings, indent=2, sort_keys=True)
        wiki.util.fs.write_atomic(path, content + '\n')
        os.chmod(path, 0o600)
    return resolved


def load_wiki_class(
    root: pathlib.Path,
    default: type[Wiki] = Wiki,
) -> type[Wiki]:
    """Load the Wiki subclass named by ``.wiki/wiki.py``'s sole ``__all__`` entry.

    A ``.wiki/wiki.py`` hook runs arbitrary code with the user's
    privileges, so it executes only for a root the user has trusted via
    ``wiki trust``. An untrusted hook is refused (never silently ignored:
    a custom subclass changes indexing/formatting, so falling back to the
    base class could generate a wrong wiki). A hookless wiki needs no
    trust and always loads the default class.
    """
    config_path = root / WIKI_DIR / 'wiki.py'
    if not config_path.exists():
        return default
    if not is_trusted(root):
        raise PermissionError(
            f'Refusing to run untrusted wiki hook: {config_path}\n'
            f'This wiki defines a {WIKI_DIR}/wiki.py that runs code with your'
            f' privileges.\nIf you trust this wiki, run `wiki trust`.'
        )
    spec = importlib.util.spec_from_file_location('_wiki', config_path)
    module = importlib.util.module_from_spec(spec)
    # a wiki that declares a subclass this environment cannot load must
    # fail naming the hook file, not with a bare import error
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise RuntimeError(f'Failed to load {config_path}: {e}') from e
    # the module's sole __all__ entry names the Wiki subclass to use
    names = getattr(module, '__all__', None)
    valid = isinstance(names, (list, tuple)) and len(names) == 1
    if not (valid and isinstance(names[0], str)):
        raise TypeError('wiki.py __all__ must name exactly one Wiki subclass.')
    result = getattr(module, names[0], None)
    if isinstance(result, type) and issubclass(result, Wiki):
        return result
    raise TypeError(f'{names[0]!r} is not a Wiki subclass.')


def resolve_wiki(
    path: Optional[str],
    *,
    fallbacks: Sequence[Callable[[], Optional[pathlib.Path]]] = (),
    default: type[Wiki] = Wiki,
    inside: Literal['resolve', 'refuse'] = 'resolve',
) -> Wiki:
    """Resolve a ``Wiki`` instance from a path or cwd.

    A resolved root is valid when it is declared (``.wiki/settings.json``)
    or at least indexed (``_index.md``) and -- when undeclared -- not
    enclosing a declared root of its own; a path inside an existing wiki
    (declared, or implied by a parent ``_index.md`` chain) resolves
    upward to the enclosing root with a notice, so the habitual
    root-relative ``--path`` works from inside the wiki too --
    ``inside='refuse'`` raises there instead, for a command whose entry
    argument is a write target a rebased root would silently relocate.
    A path that is itself a declared root names its own wiki (a
    vendored guest inside an excluding host), never the enclosing one.
    Corroboration diagnostics ride the resolution -- an undeclared tree
    (at its topmost index), a declared root missing its index, and an
    index chain extending above the declared root are each named on
    stderr rather than failing. ``fallbacks`` nominate embedder roots
    (see :func:`resolve_wiki_root`); ``default`` is the ``Wiki`` class
    when no ``.wiki/wiki.py`` hook names one.
    """
    wiki_root = resolve_wiki_root(path, fallbacks=fallbacks)
    # diagnostics stream to stderr as they fire AND ride the instance as
    # typed notes (resolver_notices), so a machine consumer reads them
    # from lint --json instead of scraping the prose
    diagnostics: list[str] = []

    def _say(text: str) -> None:
        """Record a diagnostic and stream it to stderr."""
        diagnostics.append(text)
        typer.echo(text, err=True)

    # a path inside an existing wiki is never itself a root: the command
    # would grow a second root index and rewrite name: paths relative to the
    # wrong root -- resolve upward to the enclosing root instead (scoped
    # work still goes through the entry argument); a path that is itself a
    # declared root names its own wiki (a vendored guest inside an excluding
    # host), never the enclosing one
    enclosing = None if _is_wiki_root(wiki_root) else enclosing_wiki_root(wiki_root)
    if enclosing is not None:
        if inside == 'refuse':
            raise _inside_wiki_error(enclosing)
        _say(f'{wiki_root}: inside the wiki at {enclosing}; using that root')
        wiki_root = enclosing
    declared = (wiki_root / WIKI_SETTINGS).is_file()
    # an undeclared enclosing wiki leaves no marker for the resolution
    # above: an ancestor index means the path sits inside an index chain,
    # so resolve upward the same way, to the chain's topmost index; an
    # indexed path climbs only a contiguous parent chain (a standalone
    # wiki under a stray outer index is its own topmost chain); a raw
    # path belongs to the nearest indexed ancestor's chain at any depth,
    # where update would adopt it -- the same climb cwd resolution runs,
    # so bare invocation and an explicit path agree
    if not declared:
        if (wiki_root / WIKI_INDEX).is_file():
            chain = wiki_root.parent
            if not (chain / WIKI_INDEX).is_file():
                chain = None
        else:
            chain = next(
                (
                    ancestor
                    for ancestor in wiki_root.parents
                    if (ancestor / WIKI_INDEX).is_file()
                ),
                None,
            )
        if chain is not None:
            enclosing = chain
            while (enclosing.parent / WIKI_INDEX).is_file():
                enclosing = enclosing.parent
            if inside == 'refuse':
                raise _inside_wiki_error(enclosing)
            _say(f'{wiki_root}: inside the wiki at {enclosing}; using that root')
            wiki_root = enclosing
    # the root is declared by its settings marker; a bare index tree is
    # tolerated with a notice, and anything less is not a wiki
    has_index = (wiki_root / WIKI_INDEX).is_file()
    if not (declared or has_index):
        raise _no_wiki_error(wiki_root)
    # never treat a path enclosing a declared wiki as an undeclared root:
    # the command would absorb the nested wiki, rewriting its name: paths
    # relative to the wrong root and planting a second settings marker
    if not declared:
        nested = _nested_wiki_root(wiki_root)
        if nested is not None:
            raise _encloses_wiki_error(nested)
        # an undeclared wiki below an unindexed folder is islanded from
        # this tree -- its own root, not a subtree of one -- and only a
        # guessed root would sweep it up, so refuse rather than absorb
        island = _island_wiki_root(wiki_root)
        if island is not None:
            raise _islands_wiki_error(island)
    # corroboration diagnostics: name what resolution tolerated
    if not declared:
        _say(f'{wiki_root}: {WIKI_SETTINGS} missing; `wiki update` will restore it')
    elif not has_index:
        _say(
            f'{wiki_root}: wiki root is missing its {WIKI_INDEX};'
            f' restore it from git or run `wiki update` to rebuild it'
        )
    if declared and (wiki_root.parent / WIKI_INDEX).is_file():
        _say(
            f'{wiki_root.parent / WIKI_INDEX} extends above the wiki root at'
            f' {wiki_root} (a foreign or damaged outer index; the root is'
            f' declared by {WIKI_SETTINGS})'
        )
    cls = load_wiki_class(wiki_root, default=default)
    result = cls(wiki_root)
    result.resolver_notices = [ResolverNoticeEvent(text=text) for text in diagnostics]
    return result


def resolve_wiki_root(
    path: Optional[str] = None,
    *,
    fallbacks: Sequence[Callable[[], Optional[pathlib.Path]]] = (),
) -> pathlib.Path:
    """Resolve wiki root directory.

    An explicit ``path`` resolves as given. Otherwise the root is the
    ancestor (cwd included) declaring itself with ``.wiki/settings.json``;
    an undeclared index tree falls back to the topmost ``_index.md``
    chain above the nearest indexed ancestor (cwd included -- a raw
    folder of an undeclared wiki resolves like an explicit ``--path .``
    does, unless it holds a wiki of its own, which the climb must not
    hand to an outer tree), then to each ``fallbacks`` nomination in
    order, and a bare project falls back to ``{cwd}/wiki/``. A
    nomination wins only when declared or at least indexed -- an invalid
    one declines to the next.

    Raises:
        ValueError: If the cwd's ancestor chain declares two wiki roots.
        FileNotFoundError: If no wiki can be located from the cwd.

    """
    # explicit path
    if path:
        result = pathlib.Path(path).expanduser()
        if not result.is_absolute():
            result = pathlib.Path.cwd() / result
        return result.resolve()
    # the declared root wins: walk the ancestor chain for the settings
    # marker (past the first hit, so a nested shadow refuses loudly)
    cwd = pathlib.Path.cwd().resolve()
    roots = _wiki_roots((cwd, *cwd.parents))
    if roots:
        return roots[0]
    # undeclared tree: climb from the nearest indexed ancestor (cwd
    # included) to the topmost _index.md -- a raw (unindexed) folder of
    # an undeclared wiki resolves like an explicit `--path .` does,
    # instead of falling through to a different wiki entirely; a raw cwd
    # holding a wiki of its own is the one case the climb must decline:
    # it is a project directory, not a folder of the outer tree, and
    # climbing would hand its standalone wiki to that tree to absorb
    if not ((cwd / WIKI_INDEX).is_file() or _indexed_dir_below(cwd)):
        climb_from = next(
            (ancestor for ancestor in cwd.parents if (ancestor / WIKI_INDEX).is_file()),
            None,
        )
    elif (cwd / WIKI_INDEX).is_file():
        climb_from = cwd
    else:
        climb_from = None
    if climb_from is not None:
        result = climb_from
        while (result.parent / WIKI_INDEX).is_file():
            result = result.parent
        return result
    # embedder-nominated roots: a nomination wins only when declared or at
    # least indexed (the same rule as the {cwd}/wiki fallback below), so a
    # stale nominator declines instead of masking a valid fallback
    for fallback in fallbacks:
        candidate = fallback()
        if candidate is None:
            continue
        if _is_wiki_root(candidate) or (candidate / WIKI_INDEX).is_file():
            return candidate
    # check for wiki/ in cwd (declared or at least indexed, matching the
    # validity rule in resolve_wiki, so a damaged declared wiki stays
    # reachable from the project root)
    wiki_dir = cwd / DEFAULT_WIKI_NAME
    if _is_wiki_root(wiki_dir) or (wiki_dir / WIKI_INDEX).is_file():
        return wiki_dir
    raise FileNotFoundError(
        f'Could not locate {WIKI_SETTINGS}, {WIKI_INDEX}, or'
        f' {DEFAULT_WIKI_NAME}/{WIKI_INDEX} from the'
        f' current directory.'
    )


def enclosing_wiki_root(path: pathlib.Path) -> Optional[pathlib.Path]:
    """Return the wiki root strictly above ``path``, if any.

    A directory is a wiki root when it holds ``.wiki/settings.json``;
    ``path`` itself is not checked -- being a wiki root is fine, being
    inside one is not.

    Raises:
        ValueError: If the ancestor chain declares two wiki roots.

    """
    roots = _wiki_roots(path.parents)
    if roots:
        return roots[0]
    return None


def refuse_nested_init(path: pathlib.Path) -> None:
    """Refuse to scaffold a wiki at a path enclosed by an existing wiki.

    The home directory is refused outright: its ``.wiki/settings.json``
    is the user-global trust store, so a wiki there would write its
    policy into that same file, and :func:`_is_wiki_root` exempts the
    path anyway -- the wiki would never resolve as a declared root.

    Raises:
        ValueError: If ``path`` is the home directory, or sits inside an
            enclosing wiki.

    """
    # refuse home directory
    if path.resolve() == pathlib.Path.home().resolve():
        raise ValueError(
            f'Cannot initialize a wiki at the home directory: {path}'
            f' ({WIKI_SETTINGS} there is the user-global trust store).'
        )
    # nested wikis have no boundary -- the outer update would rewrite the
    # inner index and absorb its pages -- so refuse to scaffold one
    enclosing = enclosing_wiki_root(path)
    # an undeclared index tree is a wiki too (resolve_wiki_root's
    # fallback), so a bare ancestor _index.md chain encloses just the same
    # -- unless path is itself a declared root (a foreign or damaged
    # outer index is tolerated, matching resolve_wiki)
    if (enclosing is None) and not (path / WIKI_SETTINGS).is_file():
        for ancestor in path.parents:
            if (ancestor / WIKI_INDEX).is_file():
                enclosing = ancestor
                while (enclosing.parent / WIKI_INDEX).is_file():
                    enclosing = enclosing.parent
                break
    if enclosing is not None:
        raise ValueError(
            f'Cannot initialize inside the wiki at: {enclosing}'
            f' (nested wikis are not supported).'
        )


def configure_git_merge_driver(path: pathlib.Path) -> None:
    """Wire git's wiki merge driver for the repo holding the wiki.

    Sets the ``merge.wiki`` config and writes the ``**/_index.md`` glob to
    ``.gitattributes`` (working tree only -- the user commits it). A no-op
    outside a git repo. The ``.gitattributes`` write is skipped while it has
    uncommitted changes (the config still applies; it writes on the next clean
    run), so the command never disturbs the user's pending work.

    Args:
        path: A path inside the wiki (used to find the enclosing repo).

    """
    # resolve enclosing git repo (no-op outside one)
    cmd = ['rev-parse', '--show-toplevel']
    toplevel = _git(cmd, cwd=path, check=False)
    if toplevel is None:
        return
    repo = pathlib.Path(toplevel)
    # name the merge driver
    cmd = [
        'config',
        'merge.wiki.name',
        'wiki merge (auto-resolve generated sections)',
    ]
    _git(cmd, cwd=repo)
    # point the merge driver at the stable CLI entry point -- an absolute
    # path into the installing venv silently breaks on a rebuild/move
    cmd = [
        'config',
        'merge.wiki.driver',
        'wiki _merge %O %A %B %L %P',
    ]
    _git(cmd, cwd=repo)
    # map _index.md files to the driver
    gitattributes = repo / '.gitattributes'
    current = ''
    if gitattributes.exists():
        current = gitattributes.read_text(encoding='utf-8')
    lines = current.split('\n')
    if '**/_index.md merge=wiki' in lines:
        return
    # don't entangle with the user's pending work: if .gitattributes already has
    # uncommitted changes, leave it untouched (the merge.wiki config above
    # still applies; the attribute map is written on the next clean run)
    cmd = ['status', '--porcelain', '--', '.gitattributes']
    if _git(cmd, cwd=repo, check=False):
        return
    if not current:
        prefix = ''
    elif current.endswith('\n'):
        prefix = '\n'
    else:
        prefix = '\n\n'
    # write the attribute map into the working tree only; the user stages and
    # commits .gitattributes themselves (this command never touches the index)
    wiki.util.fs.write_atomic(
        gitattributes,
        f'{current}{prefix}# Wiki index merge driver\n**/_index.md merge=wiki\n',
    )


# ------ helper functions


def _config_home() -> pathlib.Path:
    """Return the user-global config home (``~/.wiki``, ``$WIKI_CONFIG_DIR`` wins).

    A dedicated home dotdir mirrors the neighboring agent tools
    (``~/.claude``, ``~/.codex``) and the per-project ``.wiki/`` marker.
    The trust list lives here, outside any wiki -- an in-wiki marker
    would let a cloned/untrusted wiki vouch for itself.
    """
    override = os.environ.get(WIKI_CONFIG_DIR)
    if override:
        return pathlib.Path(override).expanduser()
    return pathlib.Path.home() / WIKI_DIR


def _settings_path() -> pathlib.Path:
    """Return the user-global settings file (``~/.wiki/settings.json``).

    The basename matches the per-project marker, so the global file is the
    plain counterpart of a project's ``.wiki/settings.json``;
    :func:`_is_wiki_root` exempts the config home from root detection so
    the shared name never declares ``$HOME`` a wiki root.
    """
    return _config_home() / pathlib.Path(WIKI_SETTINGS).name


def _open_config_home() -> int:
    """Open the config home as a directory, through its symlink guard.

    The one open the read path and the write path share, so both agree
    about what counts as the home: ``O_NOFOLLOW`` refuses a pre-planted
    symlink, which is the store attack one level up -- the home
    tightening would chmod its target, and ``O_NOFOLLOW`` on the store
    itself covers only the final component, so a redirected home would
    otherwise decide trust from a ``settings.json`` outside the store.
    The caller owns the returned descriptor.

    A home inside a wiki is refused outright: the store decides which
    wikis may run code, so a wiki holding it can vouch for itself --
    clone the repository, point the variable inside it, and its
    committed ``trusted`` map runs its own hook. Pointed at a wiki's own
    ``.wiki/``, the store and the wiki's declared-root marker are one
    file, which also merges the machine-local trust map into the
    repository's committed settings.

    Raises:
        FileNotFoundError: If the config home does not exist.
        PermissionError: If the config home is not a real directory, or
            sits inside a wiki.

    """
    home = _config_home()
    enclosing = _wiki_holding(home)
    if enclosing is not None:
        raise PermissionError(
            f'Refusing config home inside the wiki at: {enclosing};'
            f' the trust store must live outside every wiki (point'
            f' {WIKI_CONFIG_DIR} elsewhere), or a wiki can vouch for itself.'
        )
    try:
        return os.open(home, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            raise PermissionError(
                f'Refusing symlinked config home: {home}'
                f' (point {WIKI_CONFIG_DIR} at the real directory).'
            ) from e
        raise


def _wiki_holding(home: pathlib.Path) -> Optional[pathlib.Path]:
    """Return the wiki enclosing config home ``home``, if any.

    Indexed content above the home is the signal: a wiki root carries an
    ``_index.md``, so an ancestor holding one means the store lives
    inside a wiki's tree. A declared-but-index-less root (a damaged or
    half-initialized tree) counts too -- except through the home's own
    marker, since the default ``~/.wiki/settings.json`` is the store
    itself, not a declaration that the home directory is a wiki.
    """
    resolved = home.expanduser().resolve()
    for ancestor in resolved.parents:
        if (ancestor / WIKI_INDEX).is_file():
            return ancestor
        if (ancestor / WIKI_DIR).resolve() == resolved:
            continue
        if (ancestor / WIKI_SETTINGS).is_file():
            return ancestor
    return None


def _open_store(path: pathlib.Path) -> Optional[int]:
    """Open the trust store through its tamper guards; ``None`` when absent.

    The one open the read path and the permission self-heal share, so
    both agree about what counts as the store: it is opened relative to
    the guarded config home (see :func:`_open_config_home`), then
    ``O_NOFOLLOW`` refuses a pre-planted symlink (a shared
    ``WIKI_CONFIG_DIR`` can never redirect a trust decision onto a file
    outside the store), the ``st_nlink`` probe a second name for the
    store's inode (a name the ``0700`` home does not cover), the
    ``S_ISREG`` probe any non-regular file -- opened ``O_NONBLOCK``, so a
    planted FIFO is refused outright instead of blocking every
    invocation on a writer that never comes -- and the mode probe a
    store any other local user may write, whose contents decide which
    wikis run code and so cannot be repaired by re-tightening. Each
    refusal names the path in plain language, ``EACCES`` included.
    """
    try:
        home_fd = _open_config_home()
    except FileNotFoundError:
        return None
    try:
        fd = os.open(
            path=path.name,
            flags=os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=home_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise PermissionError(f'Refusing symlinked trust store: {path}') from e
        if e.errno == errno.EACCES:
            raise PermissionError(
                f'Cannot read the trust store: {path} (check its permissions).'
            ) from e
        raise
    finally:
        os.close(home_fd)
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode):
            raise PermissionError(
                f'Trust store is not a regular file: {path};'
                f' remove it and re-run `wiki trust`.'
            )
        if status.st_nlink > 1:
            raise PermissionError(f'Refusing hard-linked trust store: {path}')
        if status.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise PermissionError(
                f'Refusing group- or world-writable trust store: {path}'
                f' (mode {stat.S_IMODE(status.st_mode):04o});'
                f' remove it and re-run `wiki trust`.'
            )
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_lock(path: pathlib.Path) -> int:
    """Open the trust-store lock through the store's tamper guards.

    The lock is a real file in the config home, so it gets the store's
    custody: ``O_NOFOLLOW`` refuses a pre-planted symlink, the
    ``st_nlink`` probe a second name for the inode -- which the per-call
    ``fchmod`` would otherwise re-mode outside the store -- and the
    ``S_ISREG`` probe any non-regular file, opened ``O_NONBLOCK`` so a
    planted FIFO is refused rather than taken for a lock ``flock`` cannot
    hold. The mode is not probed: unlike the store the lock carries no
    content, so re-tightening it is the whole repair. Read-only is
    enough -- ``flock`` and ``fchmod`` need no write access, and a
    writable descriptor on a foreign inode is one more thing to lose.
    """
    unusable = (
        f'Trust-store lock is not a regular file: {path};'
        f' remove it and re-run `wiki trust`.'
    )
    try:
        fd = os.open(
            path=path,
            flags=os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            mode=0o600,
        )
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise PermissionError(f'Refusing symlinked trust-store lock: {path}') from e
        # a directory refuses the O_CREAT open outright on Linux, and opens
        # for the S_ISREG probe below on macOS: name it the same way on both
        if e.errno == errno.EISDIR:
            raise PermissionError(unusable) from e
        raise
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode):
            raise PermissionError(unusable)
        if status.st_nlink > 1:
            raise PermissionError(f'Refusing hard-linked trust-store lock: {path}')
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_global_settings(*, strict: bool = False) -> dict:
    """Load the user-global settings, through the store's tamper guards.

    An absent store reads as empty, and corruption -- unparseable JSON,
    undecodable bytes, or a shape a rewrite would destroy -- tolerantly
    reads as empty too -- fail-safe, nothing is trusted -- unless
    ``strict`` is set, which raises on corruption instead: a caller about
    to rewrite the store (``trust_root``) must never fold a corrupt store
    into an empty one and silently drop every trusted root. A blank store
    is the exception: it holds no roots, so a rewrite drops nothing and
    strict reads it as absent rather than wedging every trust call behind
    a manual repair. A tampered store (a symlinked, hard-linked,
    non-regular, or other-writable file) always raises: the write path
    refuses it, and a trust decision must never be read through what a
    trust write would refuse.
    """
    path = _settings_path()
    fd = _open_store(path)
    if fd is None:
        return {}
    stakes = (
        ' repair or remove it before re-trusting'
        ' (a rewrite would drop every trusted root).'
    )
    with os.fdopen(fd, 'r', encoding='utf-8') as handle:
        try:
            content = handle.read()
            # an empty (or whitespace-only) store is a zeroed file, not a
            # loss waiting to happen: a truncated restore or a bootstrap
            # `touch` must not block every trust call until a human rm's it
            if not content.strip():
                return {}
            result = json.loads(content)
        # undecodable bytes are corruption too, and escape json's own error
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            if strict:
                raise ValueError(
                    f'Trust store is corrupt: {path} ({e});{stakes}'
                ) from e
            return {}
    if not isinstance(result, dict):
        if strict:
            raise ValueError(
                f'Trust store is corrupt: {path} (top level is not an object);{stakes}'
            )
        return {}
    # a wrong-shaped 'trusted' is the one the rewrite would silently discard,
    # so strict refuses it exactly as it refuses a wrong-shaped top level
    if strict and not isinstance(result.get('trusted', {}), dict):
        raise ValueError(
            f'Trust store is corrupt: {path} (trusted is not an object);{stakes}'
        )
    return result


def _wiki_roots(chain: Iterable[pathlib.Path]) -> list[pathlib.Path]:
    """Collect the declared wiki roots along ``chain``, nearest first.

    The walk continues past the first marker so a nested root shadowing a
    real one is detected: two markers on one chain make every command ambiguous.

    Raises:
        ValueError: If ``chain`` declares more than one wiki root.

    """
    result = [ancestor for ancestor in chain if _is_wiki_root(ancestor)]
    if len(result) > 1:
        raise ValueError(
            f'Ambiguous wiki root: {result[0]} is nested inside the wiki at'
            f' {result[-1]} (two {WIKI_SETTINGS} markers on one path).'
        )
    return result


def _is_wiki_root(path: pathlib.Path) -> bool:
    """Return ``True`` if ``path`` holds the declared-root settings marker.

    Two directories are exempt, both because a trust store there is
    indistinguishable from a root marker. The home directory is never a
    wiki root: ``~/.wiki/settings.json`` is the default store, so a
    ``$HOME`` that read as a root would enclose every wiki beneath it and
    refuse every command. The active config home is exempt wherever
    ``WIKI_CONFIG_DIR`` points it -- and the home exemption stands on its
    own, since an override leaves the default store in place.
    """
    # compare resolved on both sides: candidates arrive resolved, so an
    # unresolved home or config home under a symlink would never match
    # and the trust store would declare its parent a wiki root
    if path.resolve() == pathlib.Path.home().resolve():
        return False
    if (path / WIKI_DIR).resolve() == _config_home().resolve():
        return False
    return (path / WIKI_SETTINGS).is_file()


class ResolverNoticeEvent(Event):
    """Emitted for a diagnostic wiki resolution tolerated or adjusted.

    The resolver's counterpart of the engine's notice events: upward
    resolution into an enclosing root, a missing settings marker or root
    index, and an index chain extending above a declared root each
    record one, so a machine consumer reads them typed instead of
    scraping the stderr prose.
    """

    text: str

    @property
    def description(self: ResolverNoticeEvent) -> str:
        """Return the recorded diagnostic line."""
        return self.text


def _inside_wiki_error(enclosing: pathlib.Path) -> ValueError:
    """Build the inside-an-enclosing-wiki error, naming the enclosing root."""
    return ValueError(
        f'Path is inside the wiki at: {enclosing};'
        f' pass --path {enclosing} and the root-relative name.'
    )


def _islands_wiki_error(island: pathlib.Path) -> ValueError:
    """Build the encloses-an-islanded-wiki error, naming the island."""
    return ValueError(
        f'Path encloses the wiki at: {island}, islanded from this tree by an'
        f' unindexed folder; run the command with --path {island}, or index'
        f' the folder between them to make it part of this wiki.'
    )


def _no_wiki_error(root: pathlib.Path) -> NotADirectoryError:
    """Build the no-wiki-at-root error, naming the missing markers."""
    return NotADirectoryError(
        f'No wiki at: {root} (missing {WIKI_SETTINGS} and {WIKI_INDEX}).'
    )


def _indexed_dir_below(path: pathlib.Path) -> Optional[pathlib.Path]:
    """Return the first indexed directory strictly below ``path``, if any.

    A raw folder holding one is a project directory rather than a folder
    of an outer tree, so cwd resolution declines to climb past it (see
    :func:`resolve_wiki_root`). Dot directories are pruned and symlinked
    directories are never followed, matching the walk.
    """
    for dirpath, dirnames, _ in os.walk(path):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        for dirname in dirnames:
            result = pathlib.Path(dirpath) / dirname
            if (result / WIKI_INDEX).is_file():
                return result
    return None


def _island_wiki_root(root: pathlib.Path) -> Optional[pathlib.Path]:
    """Return the first wiki islanded below ``root`` by an index gap.

    ``root``'s own tree is the contiguously indexed one: every folder of
    a healthy wiki carries an ``_index.md``, so an indexed directory
    reached only through an unindexed one is a separate wiki that
    happens to live inside this path -- a vendored or nested checkout.
    Sweeping it would rewrite its ``name:`` paths against the wrong
    root, so a resolution that only guessed this root refuses instead. A
    brand-new raw folder with nothing indexed below it opens no island,
    which is the ordinary pre-update state.
    """
    stack = [(root, True)]
    while stack:
        folder, attached = stack.pop()
        for child in sorted(folder.iterdir()):
            if child.name.startswith('.') or child.is_symlink():
                continue
            if not child.is_dir():
                continue
            indexed = (child / WIKI_INDEX).is_file()
            # an indexed directory under a gap is another wiki's root
            if indexed and not attached:
                return child
            stack.append((child, attached and indexed))
    return None


def _nested_wiki_root(path: pathlib.Path) -> Optional[pathlib.Path]:
    """Return the first declared wiki root strictly below ``path``, if any."""
    for dirpath, dirnames, _ in os.walk(path):
        # prune dot-dirs; each surviving child is probed for its own
        # settings marker directly, so .wiki itself needs no descent
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for dirname in dirnames:
            result = pathlib.Path(dirpath) / dirname
            if _is_wiki_root(result):
                return result
    return None


@typing.overload
def _git(
    cmd: list[str],
    *,
    cwd: Optional[pathlib.Path] = None,
    check: Literal[True] = True,
) -> str: ...


@typing.overload
def _git(
    cmd: list[str],
    *,
    cwd: Optional[pathlib.Path] = None,
    check: Literal[False],
) -> Optional[str]: ...


def _git(
    cmd: list[str],
    *,
    cwd: Optional[pathlib.Path] = None,
    check: bool = True,
) -> Optional[str]:
    """Run a git command and return stripped stdout.

    Args:
        cmd: Git subcommand and arguments (without ``git`` prefix).
        cwd: Working directory for the command.
        check: Raise ``RuntimeError`` on non-zero exit.

    Returns:
        Stripped stdout string, or ``None`` on non-zero
        exit when ``check`` is ``False``.

    """
    full_cmd = ['git']
    if cwd:
        full_cmd.extend(['-C', f'{cwd}'])
    full_cmd.extend(cmd)
    # the repository is the one enclosing the given cwd, so the command never
    # inherits git's repo-discovery environment (mirroring the gitignore
    # fence): a git hook exports GIT_DIR (relative, resolving against this
    # cwd) and a caller may export one pointing at another repo -- either
    # would wire the merge-driver config into a foreign repository and drop
    # .gitattributes beside the wrong toplevel
    env = {
        name: value for name, value in os.environ.items() if not name.startswith('GIT_')
    }
    # a missing git binary is treated like a failed command, so callers that
    # pass check=False (e.g. the leading rev-parse) degrade to a clean no-op;
    # output is captured as bytes and fsdecoded -- text mode would decode with
    # the locale codec and raise on an undecodable repo path
    try:
        result = subprocess.run(full_cmd, capture_output=True, env=env)
    except FileNotFoundError as e:
        if check:
            cmd_string = ' '.join(cmd)
            raise RuntimeError(f'git {cmd_string} failed: {e}') from e
        return None
    if result.returncode != 0:
        if check:
            cmd_string = ' '.join(cmd)
            error = os.fsdecode(result.stderr).strip()
            raise RuntimeError(
                f'git {cmd_string} failed (exit {result.returncode}): {error!r}'
            )
        return None
    return os.fsdecode(result.stdout).strip()
