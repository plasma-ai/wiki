"""Shared helpers for ``wiki`` CLI commands."""

from __future__ import annotations

import functools
import importlib.util
import pathlib
import subprocess
from collections.abc import Callable
from typing import Any, Optional

import typer

from wiki.core.wiki import DEFAULT_WIKI_NAME, WIKI_CONFIG, WIKI_INDEX, Wiki

__all__ = [
    'command',
    'parse_slice',
    'load_wiki_class',
    'resolve_wiki',
    'resolve_wiki_root',
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
    msg = f'Invalid slice format: {value!r} (expected n:m, n:, or :m).'
    if ':' not in value:
        raise typer.BadParameter(msg)
    left, right = value.split(':', 1)
    try:
        start = int(left) if left else None
        stop = int(right) if right else None
    except ValueError as e:
        raise typer.BadParameter(msg) from e
    return start, stop


def load_wiki_class(
    root: pathlib.Path,
    default: type[Wiki] = Wiki,
) -> type[Wiki]:
    """Load the Wiki subclass named by ``_config/wiki.py``'s sole ``__all__`` entry."""
    config_path = root / WIKI_CONFIG / 'wiki.py'
    if not config_path.exists():
        return default
    # NOTE: this executes arbitrary code from the wiki's _config/wiki.py,
    #   so it is safe only for first-party wikis -- opening an untrusted
    #   wiki would need an opt-in or an allowlist of trusted roots
    spec = importlib.util.spec_from_file_location('_wiki', config_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # the module's sole __all__ entry names the Wiki subclass to use
    names = getattr(module, '__all__', None)
    valid = isinstance(names, (list, tuple)) and len(names) == 1
    if not (valid and isinstance(names[0], str)):
        raise AttributeError('wiki.py __all__ must name exactly one Wiki subclass.')
    result = getattr(module, names[0], None)
    if isinstance(result, type) and issubclass(result, Wiki):
        return result
    raise TypeError(f'{names[0]} is not a Wiki subclass.')


def resolve_wiki(path: Optional[str]) -> Wiki:
    """Resolve a ``Wiki`` instance from a path or cwd."""
    wiki_root = resolve_wiki_root(path)
    if wiki_root.is_dir() and (wiki_root / WIKI_INDEX).is_file():
        cls = load_wiki_class(wiki_root)
        return cls(wiki_root)
    raise NotADirectoryError(f'No wiki at: {wiki_root} (missing {WIKI_INDEX})')


def resolve_wiki_root(path: Optional[str] = None) -> pathlib.Path:
    """Resolve wiki root directory."""
    # explicit path
    if path:
        result = pathlib.Path(path)
        if not result.is_absolute():
            result = pathlib.Path.cwd() / result
        return result.resolve()
    # walk up from cwd to find wiki root
    cwd = pathlib.Path.cwd().resolve()
    if (cwd / WIKI_INDEX).is_file():
        result = cwd
        while (result.parent / WIKI_INDEX).is_file():
            result = result.parent
        return result
    # check for wiki/ in cwd
    wiki_dir = cwd / DEFAULT_WIKI_NAME
    if (wiki_dir / WIKI_INDEX).is_file():
        return wiki_dir
    raise FileNotFoundError(
        f'Could not locate {WIKI_INDEX} or'
        f' {DEFAULT_WIKI_NAME}/{WIKI_INDEX} from the'
        f' current directory.'
    )


def configure_git_merge_driver(path: pathlib.Path) -> None:
    """Wire git's ``_index.md`` merge driver for the repo holding the wiki.

    Sets the ``merge.wiki-index`` config and writes the ``**/_index.md`` glob to
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
    # resolve bundled merge driver script
    package = pathlib.Path(__file__).parent.parent
    script = package / '_config' / 'git' / 'merge_index.sh'
    # name the merge driver
    cmd = [
        'config',
        'merge.wiki-index.name',
        'wiki index merge (auto-resolve generated sections)',
    ]
    _git(cmd, cwd=repo)
    # point the merge driver to wiki script
    cmd = [
        'config',
        'merge.wiki-index.driver',
        f"bash '{script}' %A %O %B",
    ]
    _git(cmd, cwd=repo)
    # map _index.md files to the driver
    gitattributes = repo / '.gitattributes'
    current = ''
    if gitattributes.exists():
        current = gitattributes.read_text(encoding='utf-8')
    if 'merge=wiki-index' in current:
        return
    # don't entangle with the user's pending work: if .gitattributes already has
    # uncommitted changes, leave it untouched (the merge.wiki-index config above
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
    gitattributes.write_text(
        f'{current}{prefix}# Wiki index merge driver\n**/_index.md merge=wiki-index\n',
        encoding='utf-8',
    )


# ------ helper functions


def _git(
    cmd: list[str],
    *,
    cwd: Optional[pathlib.Path] = None,
    check: bool = True,
) -> Optional[str]:
    """Run a git command and return stripped stdout."""
    full_cmd = ['git']
    if cwd:
        full_cmd.extend(['-C', f'{cwd}'])
    full_cmd.extend(cmd)
    # a missing git binary is treated like a failed command, so callers that
    # pass check=False (e.g. the leading rev-parse) degrade to a clean no-op
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        if check:
            cmd_string = ' '.join(cmd)
            raise RuntimeError(f'git {cmd_string} failed: {e}') from e
        return None
    if result.returncode != 0:
        if check:
            cmd_string = ' '.join(cmd)
            error = result.stderr.strip()
            raise RuntimeError(f'git {cmd_string} failed: {error!r}')
        return None
    return result.stdout.strip()
