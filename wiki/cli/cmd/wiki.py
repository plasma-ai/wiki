"""Implements ``wiki`` commands."""

from __future__ import annotations

import importlib.metadata
import importlib.resources
import json
import pathlib
import shutil
from typing import Optional

import typer

from wiki.cli.utils import (
    command,
    configure_git_merge_driver,
    parse_slice,
    resolve_wiki,
    resolve_wiki_root,
)
from wiki.core.wiki import DEFAULT_WIKI_NAME, WIKI_INDEX, Wiki

__all__ = [
    'version',
    'install',
    'init',
    'config',
    'read',
    'search',
    'update',
    'lint',
    'map',
]

# manual step: Obsidian gates community plugins behind "Restricted Mode"
OBSIDIAN_SETUP_HINT = (
    'In Obsidian: Settings -> Community plugins -> turn off Restricted'
    ' Mode, then enable Front Matter Title if needed.'
)


def version(app: typer.Typer) -> typer.Typer:
    """Register the ``--version`` flag on the root callback."""

    def _version_callback(value: bool) -> None:
        """Print the installed ``plasma-wiki`` version and exit."""
        if value:
            typer.echo(importlib.metadata.version('plasma-wiki'))
            raise typer.Exit()

    # version flag
    version_help = 'Show the version and exit.'
    version = typer.Option(
        None,
        '--version',
        callback=_version_callback,
        is_eager=True,
        help=version_help,
    )

    @app.callback()
    def _main(version: Optional[bool] = version) -> None:
        """Wiki command-line interface."""

    return app


def install(app: typer.Typer) -> typer.Typer:
    """Register the ``install`` command."""
    # project flag
    project_help = 'Install config in cwd rather than home directory.'
    project = typer.Option(False, '--project', help=project_help)

    @command(app, 'install')
    def _install(
        project: bool = project,
    ) -> None:
        """Install the wiki skill for Claude Code and Codex.

        Copies the bundled skill into the Claude (.claude/skills) and Codex
        (.agents/skills) skill directories. Targets your home directory by
        default, or the current project with --project.
        """
        # resolve install directory
        if project:
            root = pathlib.Path.cwd()
        else:
            root = pathlib.Path.home()
        # resolve agent skill directories
        targets = [
            root / '.claude' / 'skills',
            root / '.agents' / 'skills',
        ]
        # copy each bundled skill into every target (replaces any prior copy)
        base = importlib.resources.files('wiki')
        skills = base.joinpath('skills')
        for skill in sorted(path for path in skills.iterdir() if path.is_dir()):
            for target in targets:
                dest = target / skill.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.is_symlink() or dest.is_file():
                    dest.unlink()
                elif dest.is_dir():
                    shutil.rmtree(dest)
                shutil.copytree(skill, dest)
                typer.echo(f'Installed {skill.name} -> {dest}')

    return app


def init(app: typer.Typer) -> typer.Typer:
    """Register the ``init`` command."""
    # wiki name argument
    name_help = (
        'Wiki name (must satisfy the configured naming policy,'
        ' lenient by default). Defaults to the wiki folder name.'
    )
    name = typer.Argument(None, help=name_help)
    # wiki root option
    path_help = 'Wiki root directory. Defaults to {cwd}/wiki/.'
    path = typer.Option(None, '--path', help=path_help)
    # initial settings.json option
    settings_help = (
        'Initial _config/settings.json contents, as a JSON object'
        ' (e.g. {"naming": {"validate": ["ascii", "identifier"]}}).'
    )
    settings = typer.Option(None, '--settings', help=settings_help)

    @command(app, 'init')
    def _init(
        name: Optional[str] = name,
        path: Optional[str] = path,
        settings: Optional[str] = settings,
    ) -> None:
        """Initialize a wiki with a root _index.md file.

        The wiki name defaults to the wiki folder name. By default the naming
        policy is lenient (only structural characters, a leading dot, and the
        reserved _index/_config stems are rejected); stricter rules are opt-in
        per wiki via naming.validate in _config/settings.json, which --settings
        seeds at creation. The wiki is created at {cwd}/wiki/ when no --path is
        given.
        """
        # resolve wiki root and display name
        if path:
            path = resolve_wiki_root(path)
            name = name or path.name
        else:
            path = pathlib.Path.cwd() / DEFAULT_WIKI_NAME
            name = name or pathlib.Path.cwd().name
        # parse the optional settings JSON
        if settings is not None:
            try:
                settings = json.loads(settings)
            except json.JSONDecodeError as e:
                raise typer.BadParameter(f'--settings must be valid JSON: {e}') from e
            if not isinstance(settings, dict):
                raise typer.BadParameter('--settings must be a JSON object')
        # don't silently re-run a full update on an existing wiki
        if (path / WIKI_INDEX).is_file():
            typer.echo(f'Wiki already initialized at: {path}')
            return
        # initialize wiki
        wiki = Wiki(path)
        wiki.init(name, settings=settings)
        # materialize Obsidian config (downloads community plugins)
        warnings = wiki.update_config()
        # configure git merge driver
        configure_git_merge_driver(path)
        typer.echo(f'Initialized wiki at: {path}')
        # surface soft warnings, else point at the one manual step
        if warnings:
            for warning in warnings:
                typer.echo(warning, err=True)
        else:
            typer.echo('')
            typer.echo(OBSIDIAN_SETUP_HINT)

    return app


def config(app: typer.Typer) -> typer.Typer:
    """Register the ``config`` command."""
    # wiki root option
    path_help = 'Wiki root directory. Defaults to {cwd}/wiki/.'
    path = typer.Option(None, '--path', help=path_help)

    @command(app, 'config')
    def _config(
        path: Optional[str] = path,
    ) -> None:
        """Install or refresh the Obsidian integration config."""
        # merge Obsidian config
        wiki = resolve_wiki(path)
        warnings = wiki.update_config()
        if warnings:
            for warning in warnings:
                typer.echo(warning, err=True)
        else:
            typer.echo('Updated Obsidian config.')
            typer.echo('')
            typer.echo(OBSIDIAN_SETUP_HINT)
        # (re)configure git merge driver
        configure_git_merge_driver(wiki._root)

    return app


def read(app: typer.Typer) -> typer.Typer:
    """Register the ``read`` command."""
    # file name argument
    name_help = 'File or directory path to read (relative to wiki root).'
    name = typer.Argument(..., help=name_help)
    # wiki root option
    path_help = 'Wiki root directory. Defaults to {cwd}/wiki/.'
    path = typer.Option(None, '--path', help=path_help)
    # line slice option
    lines_help = 'Slice the body by line range (0-indexed half-open; e.g. n:m, n:, :m).'
    lines = typer.Option(None, '--lines', '-l', help=lines_help)
    # word slice option
    words_help = 'Slice the body by word range (0-indexed half-open; e.g. n:m, n:, :m).'
    words = typer.Option(None, '--words', '-w', help=words_help)
    # char slice option
    chars_help = (
        'Slice the body by character range (0-indexed half-open; e.g. n:m, n:, :m).'
    )
    chars = typer.Option(None, '--chars', '-c', help=chars_help)

    @command(app, 'read')
    def _read(
        name: str = name,
        path: Optional[str] = path,
        lines: Optional[str] = lines,
        words: Optional[str] = words,
        chars: Optional[str] = chars,
    ) -> None:
        """Return content for a named wiki entry."""
        # only one of --lines/--words/--chars may be given
        ranges = {'lines': lines, 'words': words, 'chars': chars}
        given = {on: spec for on, spec in ranges.items() if spec}
        if len(given) > 1:
            raise typer.BadParameter('Use only one of --lines/--words/--chars.')
        wiki = resolve_wiki(path)
        if given:
            on, spec = next(iter(given.items()))
            start, stop = parse_slice(spec)
            content = wiki.read(name, start=start, stop=stop, on=on)
        else:
            content = wiki.read(name)
        typer.echo(content)

    return app


def search(app: typer.Typer) -> typer.Typer:
    """Register the ``search`` command."""
    # search pattern argument
    pattern_help = 'Regex pattern to search for.'
    pattern = typer.Argument(..., help=pattern_help)
    # folder name argument
    name_help = 'Restrict scope to named subtree (relative path).'
    name = typer.Argument(None, help=name_help)
    # wiki root option
    path_help = 'Wiki root directory. Defaults to {cwd}/wiki/.'
    path = typer.Option(None, '--path', help=path_help)
    # search field option
    field_help = 'Comma-separated frontmatter fields to search.'
    field = typer.Option(None, '--field', '-f', help=field_help)
    # ignore case flag
    ignore_case_help = 'Case-insensitive matching.'
    ignore_case = typer.Option(False, '--ignore-case', '-i', help=ignore_case_help)
    # all files flag
    all_files_help = 'Include non-markdown files in the search.'
    all_files = typer.Option(False, '--all', '-a', help=all_files_help)
    # lines flag
    lines_help = 'Show matching lines with line numbers.'
    lines = typer.Option(False, '--lines', '-l', help=lines_help)
    # lineno flag
    lineno_help = 'Show file paths with line numbers (no content).'
    lineno = typer.Option(False, '--lineno', '-n', help=lineno_help)

    @command(app, 'search')
    def _search(
        pattern: str = pattern,
        name: Optional[str] = name,
        path: Optional[str] = path,
        field: Optional[str] = field,
        ignore_case: bool = ignore_case,
        all_files: bool = all_files,
        lines: bool = lines,
        lineno: bool = lineno,
    ) -> None:
        """Search wiki content for a regex pattern."""
        if lines and lineno:
            raise typer.BadParameter('--lines and --lineno are mutually exclusive.')
        wiki = resolve_wiki(path)
        matches = wiki.search(
            pattern,
            name=name,
            field=field,
            ignore_case=ignore_case,
            all_files=all_files,
        )
        if not matches:
            typer.echo('No matches found.')
        elif lines:
            for relpath, line_number, line_text in matches:
                typer.echo(f'{relpath}:{line_number}: {line_text}')
        elif lineno:
            for relpath, line_number, _line_text in matches:
                typer.echo(f'{relpath}:{line_number}')
        else:
            seen = set()
            for relpath, _line_number, _line_text in matches:
                if relpath not in seen:
                    seen.add(relpath)
                    typer.echo(relpath)

    return app


def update(app: typer.Typer) -> typer.Typer:
    """Register the ``update`` command."""
    # folder name argument
    name_help = 'Restrict scope to named subtree (relative path).'
    name = typer.Argument(None, help=name_help)
    # wiki root option
    path_help = 'Wiki root directory. Defaults to {cwd}/wiki/.'
    path = typer.Option(None, '--path', help=path_help)
    # prune flag
    prune_help = 'Remove broken links instead of preserving them.'
    prune = typer.Option(False, '--prune', help=prune_help)
    # check flag
    check_help = 'Report files that would change without writing them.'
    check = typer.Option(False, '--check', help=check_help)

    @command(app, 'update')
    def _update(
        name: Optional[str] = name,
        path: Optional[str] = path,
        prune: bool = prune,
        check: bool = check,
    ) -> None:
        """Update wiki files."""
        wiki = resolve_wiki(path)
        updated = wiki.update(name=name, prune=prune, check=check)
        count = len(updated)
        s = 's' if count != 1 else ''
        # dry run: report would-change files and exit nonzero if any
        if check:
            if updated:
                for relpath in updated:
                    typer.echo(f'Would update: {relpath}')
                typer.echo(
                    f'\n{count} file{s} would change (run without --check to apply).'
                )
                raise SystemExit(1)
            typer.echo('Nothing to update.')
            return
        if updated:
            typer.echo(f'Updated {count} file{s}.')
        else:
            typer.echo('Nothing to update.')

    return app


def lint(app: typer.Typer) -> typer.Typer:
    """Register the ``lint`` command."""
    # folder name argument
    name_help = 'Restrict scope to named subtree (relative path).'
    name = typer.Argument(None, help=name_help)
    # wiki root option
    path_help = 'Wiki root directory. Defaults to {cwd}/wiki/.'
    path = typer.Option(None, '--path', help=path_help)

    @command(app, 'lint')
    def _lint(
        name: Optional[str] = name,
        path: Optional[str] = path,
    ) -> None:
        """Check wiki health."""
        wiki = resolve_wiki(path)
        issues = wiki.lint(name=name)
        if issues:
            for issue in issues:
                typer.echo(issue)
            count = len(issues)
            s = 's' if count != 1 else ''
            typer.echo(f'\n{count} issue{s} found.')
            raise SystemExit(1)
        else:
            typer.echo('No issues found.')

    return app


def map(app: typer.Typer) -> typer.Typer:
    """Register the ``map`` command."""
    # folder name argument
    name_help = 'Restrict scope to named subtree (relative path).'
    name = typer.Argument(None, help=name_help)
    # wiki root option
    path_help = 'Wiki root directory. Defaults to {cwd}/wiki/.'
    path = typer.Option(None, '--path', help=path_help)
    # maximum depth option
    depth_help = 'Maximum tree depth (0 means top-level only).'
    depth = typer.Option(None, '--depth', help=depth_help)
    # description flag
    desc_help = 'Show descriptions from parent index.'
    desc = typer.Option(True, '--desc/--no-desc', help=desc_help)
    # description character limit option
    desc_limit_help = 'Max characters per description.'
    desc_limit = typer.Option(None, '--desc-limit', help=desc_limit_help)
    # category filter option
    category_help = 'Comma-separated categories (empty string for uncategorized only).'
    category = typer.Option(None, '--category', '-c', help=category_help)
    # markdown flag
    markdown_help = 'Show only markdown or non-markdown entries (folders always shown).'
    markdown = typer.Option(None, '--markdown/--no-markdown', help=markdown_help)
    # words flag
    words_help = 'Show word counts from frontmatter.'
    words = typer.Option(True, '--words/--no-words', help=words_help)

    @command(app, 'map')
    def _map(
        name: Optional[str] = name,
        path: Optional[str] = path,
        depth: Optional[int] = depth,
        desc: bool = desc,
        desc_limit: Optional[int] = desc_limit,
        category: Optional[str] = category,
        markdown: Optional[bool] = markdown,
        words: bool = words,
    ) -> None:
        """Print a compact tree overview of the wiki."""
        # validate numeric bounds
        if depth is not None and depth < 0:
            raise typer.BadParameter('--depth must be >= 0.')
        if desc_limit is not None and desc_limit < 0:
            raise typer.BadParameter('--desc-limit must be >= 0.')
        # parse category filter
        category_filter = None
        if category is not None:
            category_filter = [c for c in category.split(',') if c]
        wiki = resolve_wiki(path)
        result = wiki.map(
            name=name,
            depth=depth,
            desc=desc,
            desc_limit=desc_limit,
            category=category_filter,
            markdown=markdown,
            words=words,
        )
        if result:
            typer.echo(result)
            return
        # an empty map of a markerless index is not an empty wiki: map already
        # warned about the missing *** marker, so don't also claim emptiness
        folder = wiki._resolve_folder(name) if name else wiki._root
        if not wiki._index_missing_marker(folder):
            typer.echo('Wiki is empty.')

    return app
