"""Implements ``wiki`` commands."""

from __future__ import annotations

import importlib.resources
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Any, Optional

import typer

import wiki
from wiki.cli.utils import (
    _no_wiki_error,
    command,
    configure_git_merge_driver,
    parse_settings,
    parse_slice,
    refuse_nested_init,
    resolve_wiki,
    resolve_wiki_root,
    trust_root,
)
from wiki.constants import DEFAULT_WIKI_NAME, WIKI_DIR, WIKI_INDEX, WIKI_SETTINGS
from wiki.core.event import Event
from wiki.core.wiki import (
    DescOverwriteEvent,
    FrontmatterMalformedEvent,
    IndexCreateEvent,
    IndexTruncatedEvent,
    LinkAddEvent,
    LinkBreakEvent,
    LinkPruneEvent,
    NameSkipEvent,
    PageAdoptEvent,
    Wiki,
    WriteSkipEvent,
)

__all__ = [
    'version',
    'install',
    'init',
    'config',
    'trust',
    'read',
    'search',
    'update',
    'lint',
    'map',
    'merge',
]

# manual step: Obsidian gates community plugins behind "Restricted Mode"
_OBSIDIAN_SETUP_HINT = (
    'In Obsidian: Settings -> Community plugins -> turn off Restricted'
    ' Mode, then enable Front Matter Title if needed.'
)

# condensed-mode narration categories, as (event class, one, many, check_one,
# check_many): a notice of the class collapses into the category's count
# line, worded for apply and --check runs
_UPDATE_CATEGORIES = [
    (
        IndexCreateEvent,
        'Created 1 new index (fill in its desc)',
        'Created {n} new indexes (fill in their descs)',
        'Would create 1 new index',
        'Would create {n} new indexes',
    ),
    (
        PageAdoptEvent,
        'Adopted 1 bare page (frontmatter added)',
        'Adopted {n} bare pages (frontmatter added)',
        'Would adopt 1 bare page',
        'Would adopt {n} bare pages',
    ),
    (
        LinkAddEvent,
        'Added 1 new link',
        'Added {n} new links',
        'Would add 1 new link',
        'Would add {n} new links',
    ),
    (
        LinkBreakEvent,
        '1 broken link (run `wiki lint` to list it)',
        '{n} broken links (run `wiki lint` to list them)',
        '1 broken link (run `wiki lint` to list it)',
        '{n} broken links (run `wiki lint` to list them)',
    ),
    (
        LinkPruneEvent,
        'Pruned 1 broken link',
        'Pruned {n} broken links',
        'Would prune 1 broken link',
        'Would prune {n} broken links',
    ),
    (
        DescOverwriteEvent,
        'Overwrote 1 link desc (page frontmatter descs win)',
        'Overwrote {n} link descs (page frontmatter descs win)',
        'Would overwrite 1 link desc (page frontmatter descs win)',
        'Would overwrite {n} link descs (page frontmatter descs win)',
    ),
    (
        NameSkipEvent,
        'Skipped 1 invalid name',
        'Skipped {n} invalid names',
        'Skipped 1 invalid name',
        'Skipped {n} invalid names',
    ),
    (
        WriteSkipEvent,
        'Skipped 1 concurrently-edited file (re-run `wiki update`)',
        'Skipped {n} concurrently-edited files (re-run `wiki update`)',
        'Skipped 1 concurrently-edited file (re-run `wiki update`)',
        'Skipped {n} concurrently-edited files (re-run `wiki update`)',
    ),
    (
        FrontmatterMalformedEvent,
        '1 page with malformed frontmatter (no closing ---)',
        '{n} pages with malformed frontmatter (no closing ---)',
        '1 page with malformed frontmatter (no closing ---)',
        '{n} pages with malformed frontmatter (no closing ---)',
    ),
    (
        IndexTruncatedEvent,
        '1 empty or truncated index (restore from git or delete to rebuild)',
        '{n} empty or truncated indexes (restore from git or delete to rebuild)',
        '1 empty or truncated index (restore from git or delete to rebuild)',
        '{n} empty or truncated indexes (restore from git or delete to rebuild)',
    ),
]


def version(app: typer.Typer) -> typer.Typer:
    """Register the ``--version`` flag on the root callback."""

    def _version_callback(value: bool) -> None:
        """Print the running ``wiki`` package's version and exit."""
        if value:
            typer.echo(wiki.__version__)
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
    project_help = 'Install the skill in cwd rather than home directory.'
    project = typer.Option(False, '--project', help=project_help)
    # link flag
    link_help = (
        'Symlink the bundled skill instead of copying (requires the package'
        ' files on disk, e.g. an editable install), so source edits apply'
        ' without re-installing.'
    )
    link = typer.Option(False, '--link', help=link_help)

    @command(app, 'install')
    def _install(
        project: bool = project,
        link: bool = link,
    ) -> None:
        """Install the wiki skill for Claude Code and Codex.

        Copies the bundled skill into the Claude (.claude/skills) and Codex
        (.agents/skills) skill directories. Targets your home directory by
        default, or the current project with --project. --link symlinks the
        skill instead of copying -- the editable-install dev setup, where
        source edits apply without re-installing.
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
        # collect skills
        skills_dir = importlib.resources.files('wiki').joinpath('skills')
        skills = [path for path in skills_dir.iterdir() if path.is_dir()]
        # a symlink needs a real directory to point at; only an on-disk
        # package (an editable install, not a zipped one) provides it
        if link and not isinstance(skills_dir, pathlib.Path):
            raise RuntimeError(
                '--link requires the bundled skill to be a real directory'
                ' (an editable install); a zipped install cannot install'
                ' the skill from the CLI.'
            )
        # copy or link each skill into every target (replaces any prior install)
        for skill in sorted(skills, key=lambda path: path.name):
            for target in targets:
                dest = target / skill.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.is_symlink() or dest.is_file():
                    dest.unlink()
                elif dest.is_dir():
                    shutil.rmtree(dest)
                if link:
                    dest.symlink_to(skill)
                    typer.echo(f'Linked {skill.name} -> {dest}.')
                else:
                    shutil.copytree(skill, dest)
                    typer.echo(f'Installed {skill.name} -> {dest}.')

    return app


def init(app: typer.Typer) -> typer.Typer:
    """Register the ``init`` command."""
    # wiki name argument
    name_help = (
        'Wiki name (must satisfy the configured naming policy,'
        ' lenient by default). Defaults to the project (cwd) name,'
        ' or the --path folder name.'
    )
    name = typer.Argument(None, help=name_help)
    # wiki root option
    path_help = 'Wiki root directory. Defaults to {cwd}/wiki/.'
    path = typer.Option(None, '--path', help=path_help)
    # initial settings.json option
    settings_help = (
        'Initial .wiki/settings.json contents, as a JSON object'
        ' (e.g. {"naming": {"validate": ["ascii", "identifier"]}}).'
    )
    settings = typer.Option(None, '--settings', help=settings_help)
    # quiet flag
    quiet_help = 'Suppress the Obsidian hint and other non-error output.'
    quiet = typer.Option(False, '--quiet', help=quiet_help)

    @command(app, 'init')
    def _init(
        name: Optional[str] = name,
        path: Optional[str] = path,
        settings: Optional[str] = settings,
        quiet: bool = quiet,
    ) -> None:
        """Initialize a wiki with a root _index.md file.

        The wiki name defaults to the project (cwd) name, or the --path folder
        name. By default the naming policy is lenient (only structural
        characters, a leading dot, and the reserved _index stem are rejected);
        stricter rules are opt-in per wiki via naming.validate in
        .wiki/settings.json, which --settings seeds at creation. The wiki is
        created at {cwd}/wiki/ when no --path is given.
        """
        # resolve wiki root and display name
        if path:
            path = resolve_wiki_root(path)
            name = name or path.name
        else:
            path = pathlib.Path.cwd() / DEFAULT_WIKI_NAME
            name = name or pathlib.Path.cwd().name
        # parse the optional settings JSON
        settings = parse_settings(settings)
        # refuse to scaffold inside an enclosing wiki
        refuse_nested_init(path)
        # don't silently re-run a full update on an existing wiki
        if (path / WIKI_INDEX).is_file():
            if not quiet:
                typer.echo(f'Wiki already initialized at: {path}')
            return
        # initialize wiki, streaming its notices to stderr
        wiki = Wiki(path)
        wiki.on_notice = _echo_notice
        wiki.init(name, settings=settings)
        # materialize Obsidian config (downloads community plugins)
        warnings = wiki.update_config()
        # configure git merge driver
        configure_git_merge_driver(path)
        if not quiet:
            typer.echo(f'Initialized wiki at: {path}')
        # surface soft warnings, else point at the one manual step
        if warnings:
            for warning in warnings:
                typer.echo(warning, err=True)
        # the manual Obsidian step is human-only guidance: TTY only, on
        # stderr, so piped stdout stays parseable
        elif not quiet and sys.stderr.isatty():
            typer.echo('', err=True)
            typer.echo(_OBSIDIAN_SETUP_HINT, err=True)

    return app


def config(
    app: typer.Typer,
    *,
    resolve: Callable[[Optional[str]], Wiki] = resolve_wiki,
) -> typer.Typer:
    """Register the ``config`` command."""
    # wiki root option
    path_help = (
        'Wiki root directory. Defaults to the enclosing wiki root (the'
        ' ancestor declaring .wiki/settings.json, else the outermost'
        ' _index.md chain), else {cwd}/wiki/.'
    )
    path = typer.Option(None, '--path', help=path_help)

    @command(app, 'config')
    def _config(
        path: Optional[str] = path,
    ) -> None:
        """Install or refresh the Obsidian integration config.

        Copies .wiki/obsidian/ into .obsidian/ (downloading pinned plugin
        code), restores a missing .wiki/settings.json ({}), registers the
        git merge driver in the repo's local config, and writes the
        **/_index.md glob to .gitattributes when that file has no pending
        edits (you commit it yourself). Run once per clone. Exits 0 even
        when a plugin download fails -- download failures are stderr
        warnings (re-run online to finish setup), never the exit code.
        """
        # merge Obsidian config, streaming notices (the restored-marker
        # line the help text documents) to stderr
        wiki = resolve(path)
        wiki.on_notice = _echo_notice
        warnings = wiki.update_config()
        if warnings:
            for warning in warnings:
                typer.echo(warning, err=True)
        else:
            typer.echo('Updated Obsidian config.')
            # the manual Obsidian step is human-only guidance: TTY only,
            # on stderr, so piped stdout stays parseable
            if sys.stderr.isatty():
                typer.echo('', err=True)
                typer.echo(_OBSIDIAN_SETUP_HINT, err=True)
        # (re)configure git merge driver
        configure_git_merge_driver(wiki._root)

    return app


def trust(app: typer.Typer) -> typer.Typer:
    """Register the ``trust`` command."""
    # wiki root option
    path_help = (
        'Wiki root directory. Defaults to the enclosing wiki root (the'
        ' ancestor declaring .wiki/settings.json, else the outermost'
        ' _index.md chain), else {cwd}/wiki/.'
    )
    path = typer.Option(None, '--path', help=path_help)

    @command(app, 'trust')
    def _trust(
        path: Optional[str] = path,
    ) -> None:
        """Mark a wiki as trusted to run its .wiki/wiki.py hook.

        A .wiki/wiki.py runs code with your privileges, so wiki refuses to
        load one from an untrusted root (any command that resolves the
        wiki). Run this from inside a wiki you trust to record its root in
        ~/.wiki/settings.json; a hookless wiki records trust too (harmless,
        future-proofing a hook added later). Only trust a wiki whose
        contents you have vetted.
        """
        # resolve the root without loading the hook (resolve_wiki_root
        # never execs .wiki/wiki.py), then record it as trusted
        root = resolve_wiki_root(path)
        # trust pre-authorizes arbitrary code, so only a real wiki (declared
        # or at least indexed) may be recorded -- never a typo'd path
        if not ((root / WIKI_SETTINGS).is_file() or (root / WIKI_INDEX).is_file()):
            raise _no_wiki_error(root)
        resolved = trust_root(root)
        hook = resolved / WIKI_DIR / 'wiki.py'
        if hook.is_file():
            typer.echo(f'Trusted wiki: {resolved}')
        else:
            typer.echo(f'Trusted wiki: {resolved} (no {WIKI_DIR}/wiki.py hook present)')

    return app


def read(
    app: typer.Typer,
    *,
    resolve: Callable[[Optional[str]], Wiki] = resolve_wiki,
) -> typer.Typer:
    """Register the ``read`` command."""
    # file name argument
    name_help = 'File or directory path to read (relative to wiki root).'
    name = typer.Argument(..., help=name_help)
    # wiki root option
    path_help = (
        'Wiki root directory. Defaults to the enclosing wiki root (the'
        ' ancestor declaring .wiki/settings.json, else the outermost'
        ' _index.md chain), else {cwd}/wiki/.'
    )
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
        """Return content for a named wiki entry.

        Prints the content verbatim with no appended newline, so
        redirected output round-trips byte-for-byte for LF files (reads
        normalize CRLF); a slice keeps the frontmatter and appends a
        trailing newline.
        """
        # validate arguments: the slice units are pairwise exclusive
        if lines and words:
            raise typer.BadParameter('--lines and --words are mutually exclusive.')
        if lines and chars:
            raise typer.BadParameter('--lines and --chars are mutually exclusive.')
        if words and chars:
            raise typer.BadParameter('--words and --chars are mutually exclusive.')
        # resolve the one given slice unit, if any
        ranges = {'lines': lines, 'words': words, 'chars': chars}
        given = {on: spec for on, spec in ranges.items() if spec}
        wiki = resolve(path)
        if given:
            on, spec = next(iter(given.items()))
            start, stop = parse_slice(spec)
            content = wiki.read(name=name, start=start, stop=stop, on=on)
        else:
            content = wiki.read(name=name)
        # emit the content verbatim -- an appended newline would break the
        # byte-for-byte round-trip of redirected output
        typer.echo(content, nl=False)

    return app


def search(
    app: typer.Typer,
    *,
    resolve: Callable[[Optional[str]], Wiki] = resolve_wiki,
) -> typer.Typer:
    """Register the ``search`` command."""
    # search pattern argument
    pattern_help = 'Regex pattern to search for.'
    pattern = typer.Argument(..., help=pattern_help)
    # folder name argument
    name_help = 'Restrict scope to named subtree (relative path).'
    name = typer.Argument(None, help=name_help)
    # wiki root option
    path_help = (
        'Wiki root directory. Defaults to the enclosing wiki root (the'
        ' ancestor declaring .wiki/settings.json, else the outermost'
        ' _index.md chain), else {cwd}/wiki/.'
    )
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
    lines = typer.Option(False, '--lines', help=lines_help)
    # lineno flag
    lineno_help = 'Show file paths with line numbers (no content).'
    lineno = typer.Option(False, '--lineno', help=lineno_help)

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
        """Search wiki content for a regex pattern.

        Follows the grep convention: a match exits 0, no match prints a
        notice on stderr and exits 1, and an error (invalid regex, no
        resolvable wiki) exits 2, so scripts should branch on the exit
        code rather than parse the output.
        """
        if lines and lineno:
            raise typer.BadParameter('--lines and --lineno are mutually exclusive.')
        # every failure here -- a bad pattern, an unresolvable wiki or
        # subtree, a refused or broken hook -- is the triple's error leg,
        # so the catch is total: a per-type list would leak new failure
        # modes to the wrapper's exit 1, aliasing them with a no-match
        try:
            wiki = resolve(path)
            matches = wiki.search(
                pattern,
                name=name,
                field=field,
                ignore_case=ignore_case,
                all_files=all_files,
            )
        except Exception as e:
            # grep triple: runtime errors exit 2 (the wrapper's exception
            # path exits 1), so the body renders in the wrapper's grammar
            typer.echo(f'Error: {e}', err=True)
            raise typer.Exit(code=2) from e
        # grep convention: no-match exits 1 with the notice on stderr, so
        # scripts can distinguish no-match from match by exit code alone
        if not matches:
            typer.echo('No matches found.', err=True)
            raise SystemExit(1)
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


def update(
    app: typer.Typer,
    *,
    resolve: Callable[[Optional[str]], Wiki] = resolve_wiki,
) -> typer.Typer:
    """Register the ``update`` command."""
    # folder name argument
    name_help = 'Restrict scope to named subtree (relative path).'
    name = typer.Argument(None, help=name_help)
    # wiki root option
    path_help = (
        'Wiki root directory. Defaults to the enclosing wiki root (the'
        ' ancestor declaring .wiki/settings.json, else the outermost'
        ' _index.md chain), else {cwd}/wiki/.'
    )
    path = typer.Option(None, '--path', help=path_help)
    # prune flag
    prune_help = 'Remove broken links instead of preserving them.'
    prune = typer.Option(False, '--prune', help=prune_help)
    # check flag
    check_help = 'Report files that would change without writing them.'
    check = typer.Option(False, '--check', help=check_help)
    # full flag
    full_help = 'Print every narration line instead of per-category counts.'
    full = typer.Option(False, '--full', help=full_help)
    # count flag
    count_help = 'Print one count line per narration category (the default).'
    count = typer.Option(False, '--count', help=count_help)

    @command(app, 'update')
    def _update(
        name: Optional[str] = name,
        path: Optional[str] = path,
        prune: bool = prune,
        check: bool = check,
        full: bool = full,
        count: bool = count,
    ) -> None:
        """Update wiki files.

        Rewrites whatever drifted from the generated form: index links,
        frontmatter fields, and CRLF line endings. Restores a missing
        .wiki/settings.json ({}) and preserves broken links (--prune
        removes them). Narrations condense to one count line per category
        by default; --full prints every line. Exits 0 after a successful
        run; with --check, writes nothing and exits 1 when changes are
        pending.
        """
        if full and count:
            raise typer.BadParameter('--full and --count are mutually exclusive.')
        wiki = resolve(path)
        # update narrations are a side report (the diff is the record), so
        # they default to condensed; --full restores the per-line narration
        notices: list[Event] = []

        def _capture(event: Event, **kwargs: Any) -> Event:
            """Capture a notice; stream it in --full mode (order preserved)."""
            notices.append(event)
            if full:
                typer.echo(event.description, err=True)
            return event

        wiki.on_notice = _capture
        # flush the captured notices even when update raises: one-time lines
        # (a restored marker) describe mutations that already happened and
        # must never be swallowed by the error path
        try:
            updated = wiki.update(name=name, prune=prune, check=check)
        finally:
            if not full:
                for line in _condense(notices, check):
                    typer.echo(line, err=True)
        file_count = len(updated)
        s = 's' if file_count != 1 else ''
        # dry run: report would-change files and exit nonzero if any
        if check:
            if updated:
                for relpath in updated:
                    typer.echo(f'Would update: {relpath}')
                typer.echo(
                    f'\n{file_count} file{s} would change'
                    f' (run without --check to apply).'
                )
                raise SystemExit(1)
            typer.echo('Nothing to update.')
            return
        if updated:
            typer.echo(f'Updated {file_count} file{s}.')
        else:
            typer.echo('Nothing to update.')

    return app


def lint(
    app: typer.Typer,
    *,
    resolve: Callable[[Optional[str]], Wiki] = resolve_wiki,
) -> typer.Typer:
    """Register the ``lint`` command."""
    # folder name argument
    name_help = 'Restrict scope to named subtree (relative path).'
    name = typer.Argument(None, help=name_help)
    # wiki root option
    path_help = (
        'Wiki root directory. Defaults to the enclosing wiki root (the'
        ' ancestor declaring .wiki/settings.json, else the outermost'
        ' _index.md chain), else {cwd}/wiki/.'
    )
    path = typer.Option(None, '--path', help=path_help)
    # full flag
    full_help = 'Print every issue and note line (the default).'
    full = typer.Option(False, '--full', help=full_help)
    # count flag
    count_help = 'Print only the closing issue/note summary.'
    count = typer.Option(False, '--count', help=count_help)

    @command(app, 'lint')
    def _lint(
        name: Optional[str] = name,
        path: Optional[str] = path,
        full: bool = full,
        count: bool = count,
    ) -> None:
        """Check wiki health.

        Exits 1 when issues are found and 0 when the wiki is clean; notes
        (stderr) never affect the exit code. Issues are lint's product, so
        every line prints by default; --count condenses the run to the
        closing summary. The prose output is for humans -- scripts should
        branch on the exit code rather than parse it.
        """
        if full and count:
            raise typer.BadParameter('--full and --count are mutually exclusive.')
        wiki = resolve(path)
        # count the soft notes lint sends to stderr, so the closing summary
        # reflects them instead of contradicting the notes still on screen
        notices: list[Event] = []

        def _capture(event: Event, **kwargs: Any) -> Event:
            """Capture a notice; stream it unless --count condenses the run."""
            notices.append(event)
            if not count:
                typer.echo(event.description, err=True)
            return event

        wiki.on_notice = _capture
        issues = wiki.lint(name=name)
        note_count = len(notices)
        note_s = 's' if note_count != 1 else ''
        if issues:
            # issues are the product: every line prints unless condensed
            if not count:
                for issue in issues:
                    typer.echo(issue)
            issue_count = len(issues)
            s = 's' if issue_count != 1 else ''
            if note_count:
                summary = f'{issue_count} issue{s}, {note_count} note{note_s}.'
            else:
                summary = f'{issue_count} issue{s} found.'
            if count:
                typer.echo(summary)
            else:
                typer.echo(f'\n{summary}')
            raise SystemExit(1)
        elif note_count:
            typer.echo(f'No issues found ({note_count} note{note_s}).')
        else:
            typer.echo('No issues found.')

    return app


def map(
    app: typer.Typer,
    *,
    resolve: Callable[[Optional[str]], Wiki] = resolve_wiki,
) -> typer.Typer:
    """Register the ``map`` command."""
    # folder name argument
    name_help = 'Restrict scope to named subtree (relative path).'
    name = typer.Argument(None, help=name_help)
    # wiki root option
    path_help = (
        'Wiki root directory. Defaults to the enclosing wiki root (the'
        ' ancestor declaring .wiki/settings.json, else the outermost'
        ' _index.md chain), else {cwd}/wiki/.'
    )
    path = typer.Option(None, '--path', help=path_help)
    # maximum depth option
    depth_help = 'Maximum tree depth (0 means top-level only).'
    depth = typer.Option(None, '--depth', help=depth_help)
    # description flag
    desc_help = 'Show descriptions from parent index.'
    desc = typer.Option(True, '--desc/--no-desc', help=desc_help)
    # description character limit option
    desc_limit_help = (
        'Max characters per description (defaults to the map.desc_limit'
        ' setting, else untruncated); -1 disables truncation.'
    )
    desc_limit = typer.Option(None, '--desc-limit', help=desc_limit_help)
    # category filter option
    category_help = 'Comma-separated categories (empty string for uncategorized only).'
    category = typer.Option(None, '--category', help=category_help)
    # markdown flag
    markdown_help = 'Show only markdown or non-markdown entries (folders always shown).'
    markdown = typer.Option(None, '--markdown/--no-markdown', help=markdown_help)
    # words flag
    words_help = (
        'Show word counts -- "(page)" for a page, "(page/tree)" for a folder'
        ' (its index and its whole subtree), k/m-abbreviated past a thousand;'
        ' cached under .wiki/cache/.'
    )
    words = typer.Option(True, '--words/--no-words', help=words_help)
    # stat flag
    stat_help = (
        'Print a one-line size summary of the rendered tree (lines, chars,'
        ' words) instead of the tree itself.'
    )
    stat = typer.Option(False, '--stat', help=stat_help)

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
        stat: bool = stat,
    ) -> None:
        """Print a compact tree overview of the wiki.

        --stat sizes the dump instead of printing it -- the cheap probe
        before dumping a large wiki.
        """
        # validate numeric bounds
        if (depth is not None) and (depth < 0):
            raise typer.BadParameter('--depth must be >= 0.')
        if (desc_limit is not None) and (desc_limit < -1):
            raise typer.BadParameter('--desc-limit must be >= -1.')
        # parse category filter
        category_filter = None
        if category is not None:
            category_filter = [c for c in category.split(',') if c]
        # stream map's notices (the markerless-index warning) to stderr
        wiki = resolve(path)
        wiki.on_notice = _echo_notice
        result = wiki.map(
            name=name,
            depth=depth,
            desc=desc,
            desc_limit=desc_limit,
            category=category_filter,
            markdown=markdown,
            words=words,
        )
        # --stat: report the size of the tree the same flags would print
        if stat:
            line_count = len(result.splitlines())
            char_count = len(result)
            word_count = len(result.split())
            line_s = 's' if line_count != 1 else ''
            char_s = 's' if char_count != 1 else ''
            word_s = 's' if word_count != 1 else ''
            summary = (
                f'{line_count} line{line_s}, {char_count} char{char_s},'
                f' {word_count} word{word_s}'
            )
            typer.echo(summary)
            return
        if result:
            typer.echo(result)
            return
        # an empty map of a markerless index is not an empty wiki: map already
        # warned about the missing *** marker, so don't also claim emptiness
        folder = wiki._resolve_folder(name) if name else wiki._root
        if not wiki._index_missing_marker(folder):
            typer.echo('Wiki is empty.')

    return app


def merge(app: typer.Typer) -> typer.Typer:
    """Register the ``_merge`` command."""
    # base argument
    base_help = 'Common ancestor version (%O).'
    base = typer.Argument(..., help=base_help)
    # ours argument
    ours_help = 'Current branch version (%A); receives the merged result.'
    ours = typer.Argument(..., help=ours_help)
    # theirs argument
    theirs_help = 'Other branch version (%B).'
    theirs = typer.Argument(..., help=theirs_help)
    # marker size argument
    marker_size_help = 'Conflict-marker size (%L).'
    marker_size = typer.Argument(..., help=marker_size_help)
    # pathname argument
    pathname_help = 'Repo-relative pathname of the merging file (%P).'
    pathname = typer.Argument(..., help=pathname_help)

    # a %P pathname may lead with a dash; never parse it as an option
    @command(app, '_merge', context_settings={'ignore_unknown_options': True})
    def _merge(
        base: str = base,
        ours: str = ours,
        theirs: str = theirs,
        marker_size: str = marker_size,
        pathname: str = pathname,
    ) -> None:
        """Run the wiki merge driver (invoked by git).

        init/config register ``wiki _merge %O %A %B %L %P`` as the merge.wiki
        driver -- a stable entry point that survives the venv rebuilds and
        moves an absolute script path silently breaks on. The real pathname
        (%P) dispatches internally, so the registration string never changes:
        an ``_index.md`` below a declared wiki root gets the field-aware
        index merge, everything else git's default text merge.
        """
        # dispatch on the real pathname: an _index.md below a declared wiki
        # root -> field-aware index merge (git runs merge drivers at the
        # worktree toplevel, so the repo-relative %P resolves from the cwd)
        folder = pathlib.Path(pathname).parent
        in_wiki = any(
            (ancestor / WIKI_SETTINGS).is_file()
            for ancestor in (folder, *folder.parents)
        )
        if pathlib.PurePosixPath(pathname).name == WIKI_INDEX and in_wiki:
            package = pathlib.Path(__file__).parent.parent.parent
            script = package / '_assets' / 'git' / 'merge_index.sh'
            cmd = ['bash', f'{script}', ours, base, theirs, marker_size]
        # any other file class: git's default three-way text merge -- an
        # _index.md outside every declared wiki (a site generator's content
        # page) is not tool-owned, and the index merge would resolve its
        # pre-*** region to ours, silently dropping theirs' edits
        else:
            size = f'--marker-size={marker_size}'
            cmd = ['git', 'merge-file', size, ours, base, theirs]
        # pass the exit code through (nonzero tells git the merge left conflicts)
        result = subprocess.run(cmd)
        raise SystemExit(result.returncode)

    return app


# ------ helper functions


def _condense(notices: list[Event], check: bool) -> list[str]:
    """Collapse captured update events to one count line per category.

    Each known category (see ``_UPDATE_CATEGORIES``) aggregates into a
    single count line standing at its first occurrence, worded for an
    apply or ``--check`` run; events with no category row (one-time
    restore lines) pass through as their description verbatim in place.

    Args:
        notices: Captured update events, in emission order.
        check: Whether the run is a ``--check`` dry run.

    Returns:
        Condensed narration lines, in first-occurrence order.

    """
    # tally each category, remembering where it first appeared
    counts: dict[int, int] = {}
    first_seen: dict[int, int] = {}
    lines: list[tuple[int, str]] = []
    for position, event in enumerate(notices):
        for index, (event_class, *_rest) in enumerate(_UPDATE_CATEGORIES):
            if type(event) is event_class:
                counts[index] = counts.get(index, 0) + 1
                first_seen.setdefault(index, position)
                break
        else:
            lines.append((position, event.description))
    # render one count line per category at its first-seen position
    for index, position in first_seen.items():
        _event_class, one, many, check_one, check_many = _UPDATE_CATEGORIES[index]
        if check:
            one, many = check_one, check_many
        n = counts[index]
        line = one if n == 1 else many.format(n=n)
        lines.append((position, line))
    return [line for _position, line in sorted(lines)]


def _echo_notice(event: Event, **kwargs: Any) -> Event:
    """Stream a notice event to stderr.

    The ``on_notice`` sink for commands with no capture or condensed
    mode (init, config, map), so their notices print as they fire.

    Args:
        event: The notice event to stream.
        **kwargs: Extra hook arguments (unused).

    Returns:
        The event, unchanged.

    """
    typer.echo(event.description, err=True)
    return event
