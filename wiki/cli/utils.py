"""Shared helpers for ``wiki`` CLI commands."""

from __future__ import annotations

import datetime as dt
import functools
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
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


def command(
    app: typer.Typer,
    name: str,
    **kwargs: Any,
) -> Callable:
    """Register a CLI command on ``app`` with error wrapping."""

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
                raise SystemExit(1) from None

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
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    settings = _read_global_settings()
    trusted = settings.get('trusted')
    if not isinstance(trusted, dict):
        trusted = {}
    trusted[str(resolved)] = dt.datetime.now(dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    settings['trusted'] = trusted
    path = _settings_path()
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
) -> Wiki:
    """Resolve a ``Wiki`` instance from a path or cwd.

    A resolved root is valid when it is declared (``.wiki/settings.json``)
    or at least indexed (``_index.md``), not inside an enclosing wiki
    (declared, or implied by a parent ``_index.md`` chain), and -- when
    undeclared -- not enclosing a declared root of its own; corroboration
    diagnostics ride the resolution -- an undeclared tree (at its topmost
    index), a declared root missing its index, and an index chain
    extending above the declared root are each named on stderr rather
    than failing. ``fallbacks`` nominate embedder roots (see
    :func:`resolve_wiki_root`); ``default`` is the ``Wiki`` class when no
    ``.wiki/wiki.py`` hook names one.
    """
    wiki_root = resolve_wiki_root(path, fallbacks=fallbacks)
    # never treat a path inside an existing wiki as a wiki root: the command
    # would grow a second root index and rewrite name: paths relative to the
    # wrong root -- scoped work goes through the entry argument instead
    enclosing = enclosing_wiki_root(wiki_root)
    if enclosing is not None:
        raise _inside_wiki_error(enclosing)
    # the root is declared by its settings marker; a bare index tree is
    # tolerated with a notice, and anything less is not a wiki
    declared = (wiki_root / WIKI_SETTINGS).is_file()
    has_index = (wiki_root / WIKI_INDEX).is_file()
    if not (declared or has_index):
        raise _no_wiki_error(wiki_root)
    # an undeclared enclosing wiki leaves no marker for the guard above: a
    # parent index means the path sits inside an index chain, so refuse it
    # the same way, naming the chain's topmost index as the enclosing root
    if not declared and (wiki_root.parent / WIKI_INDEX).is_file():
        enclosing = wiki_root.parent
        while (enclosing.parent / WIKI_INDEX).is_file():
            enclosing = enclosing.parent
        raise _inside_wiki_error(enclosing)
    # never treat a path enclosing a declared wiki as an undeclared root:
    # the command would absorb the nested wiki, rewriting its name: paths
    # relative to the wrong root and planting a second settings marker
    if not declared:
        nested = _nested_wiki_root(wiki_root)
        if nested is not None:
            raise _encloses_wiki_error(nested)
    # corroboration diagnostics: name what resolution tolerated
    if not declared:
        typer.echo(
            f'{wiki_root}: {WIKI_SETTINGS} missing; `wiki update` will restore it',
            err=True,
        )
    elif not has_index:
        typer.echo(
            f'{wiki_root}: wiki root is missing its {WIKI_INDEX};'
            f' restore it from git or run `wiki update` to rebuild it',
            err=True,
        )
    if declared and (wiki_root.parent / WIKI_INDEX).is_file():
        typer.echo(
            f'{wiki_root.parent / WIKI_INDEX} extends above the wiki root at'
            f' {wiki_root} (a foreign or damaged outer index; the root is'
            f' declared by {WIKI_SETTINGS})',
            err=True,
        )
    cls = load_wiki_class(wiki_root, default=default)
    return cls(wiki_root)


def resolve_wiki_root(
    path: Optional[str] = None,
    *,
    fallbacks: Sequence[Callable[[], Optional[pathlib.Path]]] = (),
) -> pathlib.Path:
    """Resolve wiki root directory.

    An explicit ``path`` resolves as given. Otherwise the root is the
    ancestor (cwd included) declaring itself with ``.wiki/settings.json``;
    an undeclared index tree falls back to the topmost ``_index.md``
    chain, then to each ``fallbacks`` nomination in order, and a bare
    project falls back to ``{cwd}/wiki/``. A nomination wins only when
    declared or at least indexed -- an invalid one declines to the next.

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
    # undeclared tree: walk up from cwd to the topmost _index.md
    if (cwd / WIKI_INDEX).is_file():
        result = cwd
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

    Raises:
        ValueError: If ``path`` sits inside an enclosing wiki.

    """
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


def _read_global_settings() -> dict:
    """Load the user-global settings, tolerating absence or corruption."""
    try:
        result = json.loads(_settings_path().read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return result if isinstance(result, dict) else {}


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

    The user-global config home is exempt: its ``settings.json`` is the
    trust store, not a root marker, so the default ``~/.wiki`` never
    declares the home directory itself a wiki root.
    """
    # compare resolved on both sides: candidates arrive resolved, so an
    # unresolved config home under a symlinked $HOME would never match
    # and the trust store would declare the home directory a wiki root
    if (path / WIKI_DIR).resolve() == _config_home().resolve():
        return False
    return (path / WIKI_SETTINGS).is_file()


def _inside_wiki_error(enclosing: pathlib.Path) -> ValueError:
    """Build the inside-an-enclosing-wiki error, naming the enclosing root."""
    return ValueError(
        f'Path is inside the wiki at: {enclosing};'
        f' use the <entry> argument for scoped work.'
    )


def _no_wiki_error(root: pathlib.Path) -> NotADirectoryError:
    """Build the no-wiki-at-root error, naming the missing markers."""
    return NotADirectoryError(
        f'No wiki at: {root} (missing {WIKI_SETTINGS} and {WIKI_INDEX}).'
    )


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
    # a missing git binary is treated like a failed command, so callers that
    # pass check=False (e.g. the leading rev-parse) degrade to a clean no-op;
    # output is captured as bytes and fsdecoded -- text mode would decode with
    # the locale codec and raise on an undecodable repo path
    try:
        result = subprocess.run(full_cmd, capture_output=True)
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
