"""End-to-end tests for the ``wiki`` CLI command matrix.

Drives the real ``wiki`` console script as a subprocess against a throwaway
wiki built with two folders (``core``, ``guides``) and a handful of pages.
The suite covers every sub-command -- init, update (+ ``--prune``), lint,
map, search, read, and config -- exercising option behavior, exit codes,
and error reporting as observable output rather than internal state.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

from wiki.cli.utils import configure_git_merge_driver
from wiki.core.wiki import _OFFLINE_MODE

__all__ = [
    'test_init_creates_root_index',
    'test_init_guards_existing_wiki',
    'test_init_seeds_settings',
    'test_update_generates_child_links',
    'test_update_prune_removes_broken_link',
    'test_update_check_reports_changes_without_writing',
    'test_update_noop_reports_nothing_to_update',
    'test_lint_reports_issue_taxonomy_and_exits_nonzero',
    'test_map_respects_view_options',
    'test_map_filters_by_category',
    'test_map_empty_wiki_reports_empty',
    'test_search_output_modes',
    'test_search_field_and_ignore_case',
    'test_search_all_includes_non_markdown',
    'test_search_no_match_is_clean_success',
    'test_search_line_flags_are_mutually_exclusive',
    'test_search_invalid_regex_reports_error',
    'test_search_all_skips_undecodable_files',
    'test_read_slice_forms',
    'test_read_resolves_dotted_page_name',
    'test_read_errors',
    'test_config_applies_obsidian',
    'test_config_downloads_plugin',
    'test_lint_clean_after_update',
    'test_merge_driver_no_op_without_git',
    'test_init_writes_gitattributes_without_committing',
    'test_version_reports_installed_version',
]

GIT = shutil.which('git')
WIKI = shutil.which('wiki')
pytestmark = pytest.mark.skipif(
    WIKI is None,
    reason='wiki console script not on PATH',
)


@pytest.fixture(scope='module')
def wiki(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """A populated wiki rooted at ``{tmp}/wiki``, built via the real CLI.

    Layout (after ``init`` + authored files + ``update``)::

        core/   -- design.md, snippet.txt (non-markdown)
        guides/ -- setup.md

    Built once per module so link generation, frontmatter enrichment, and
    word counts are exercised exactly as a user would drive them.
    """
    base = tmp_path_factory.mktemp('wiki_cli')
    root = base / 'wiki'
    assert _wiki(base, 'init', 'Knowledge', '--path', str(root)).returncode == 0
    # author two folders with pages and one non-markdown file
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Real text.'))
    _write(
        root / 'core' / 'design.md',
        _page(
            'Design',
            'A design document about widgets.',
            'The widget subsystem handles rendering.\nWidgets matter.',
        ),
    )
    _write(root / 'core' / 'snippet.txt', 'widget appears in plain code here\n')
    _write(root / 'guides' / '_index.md', _index('Guides', 'How-to guides.', 'Text.'))
    _write(
        root / 'guides' / 'setup.md',
        _page(
            'Setup',
            'Setup instructions for the project.',
            'Run the installer to set up the environment.',
        ),
    )
    # generate links and word counts across the tree
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    return root


# ------ init


def test_init_creates_root_index(tmp_path: pathlib.Path) -> None:
    """A fresh init writes a root ``_index.md`` with the chosen display name."""
    root = tmp_path / 'wiki'
    result = _wiki(tmp_path, 'init', 'Handbook', '--path', str(root))
    assert result.returncode == 0
    assert 'Initialized wiki' in result.stdout
    index_text = (root / '_index.md').read_text(encoding='utf-8')
    assert 'name: Handbook' in index_text
    # init now also materializes the Obsidian config; offline (see _wiki)
    # the skipped plugin download surfaces as a warning rather than success
    assert (root / '.obsidian' / 'community-plugins.json').is_file()
    assert 'OFFLINE_MODE' in result.stderr


def test_init_guards_existing_wiki(tmp_path: pathlib.Path) -> None:
    """Re-running init on an existing wiki reports rather than re-creating."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    result = _wiki(tmp_path, 'init', '--path', str(root))
    assert result.returncode == 0
    assert 'already initialized' in result.stdout.lower()


def test_init_seeds_settings(tmp_path: pathlib.Path) -> None:
    """``init --settings`` seeds the given JSON into ``_config/settings.json``."""
    root = tmp_path / 'wiki'
    policy = '{"naming": {"validate": ["ascii", "identifier"]}}'
    result = _wiki(tmp_path, 'init', '--path', str(root), '--settings', policy)
    assert result.returncode == 0
    settings = root / '_config' / 'settings.json'
    data = json.loads(settings.read_text(encoding='utf-8'))
    assert data == {'naming': {'validate': ['ascii', 'identifier']}}


# ------ update (+ --prune)


def test_update_generates_child_links(wiki: pathlib.Path) -> None:
    """An update wires each folder index to its children and parent."""
    core_index = (wiki / 'core' / '_index.md').read_text(encoding='utf-8')
    root_index = (wiki / '_index.md').read_text(encoding='utf-8')
    # the root links down to the folders, the folder links to its page
    assert '[[core/_index|core/]]' in root_index
    assert '[[guides/_index|guides/]]' in root_index
    assert '[[core/design|design]]' in core_index


def test_update_prune_removes_broken_link(tmp_path: pathlib.Path) -> None:
    """A plain update preserves a stale link, but ``--prune`` removes it."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    _write(root / 'core' / 'keep.md', _page('Keep', 'Stays.', 'Body.'))
    _write(root / 'core' / 'gone.md', _page('Gone', 'Removed soon.', 'Body.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    # delete a page, leaving a dangling link in the folder index
    (root / 'core' / 'gone.md').unlink()
    index = root / 'core' / '_index.md'
    assert '[[core/gone|gone]]' in index.read_text(encoding='utf-8')
    # a plain update keeps the broken link
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    assert '[[core/gone|gone]]' in index.read_text(encoding='utf-8')
    # --prune drops it while keeping the live link
    assert _wiki(root, 'update', '--path', str(root), '--prune').returncode == 0
    after = index.read_text(encoding='utf-8')
    assert '[[core/gone|gone]]' not in after
    assert '[[core/keep|keep]]' in after


def test_update_check_reports_changes_without_writing(tmp_path: pathlib.Path) -> None:
    """``update --check`` lists would-change files, writes nothing, and exits 1."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    _write(root / 'core' / 'design.md', _page('Design', 'A design.', 'Body.'))
    # a dry run reports the files and exits non-zero
    result = _wiki(root, 'update', '--check', '--path', str(root))
    assert result.returncode == 1
    assert 'Would update: core/design.md' in result.stdout
    assert 'would change' in result.stdout
    # nothing was written, so a second check still reports changes
    assert _wiki(root, 'update', '--check', '--path', str(root)).returncode == 1
    # a real update makes a follow-up check clean
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    clean = _wiki(root, 'update', '--check', '--path', str(root))
    assert clean.returncode == 0
    assert 'Nothing to update.' in clean.stdout


def test_update_noop_reports_nothing_to_update(tmp_path: pathlib.Path) -> None:
    """A second update on an up-to-date tree writes nothing and says so."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    _write(root / 'core' / 'design.md', _page('Design', 'A design.', 'Body.'))
    # the first update brings the tree current and reports the files it changed
    first = _wiki(root, 'update', '--path', str(root))
    assert first.returncode == 0
    assert 'Updated' in first.stdout
    # a second update finds nothing to change and reports the no-op
    second = _wiki(root, 'update', '--path', str(root))
    assert second.returncode == 0
    assert 'Nothing to update.' in second.stdout


# ------ lint


def test_lint_reports_issue_taxonomy_and_exits_nonzero(
    tmp_path: pathlib.Path,
) -> None:
    """The lint command flags distinct problem kinds and exits non-zero."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a folder whose name is not a valid slug and has no index
    (root / 'Bad#Folder').mkdir()
    # a page with no frontmatter at all
    _write(root / 'core' / '_index.md', _index('core', 'Core.', 'Text.'))
    _write(root / 'core' / 'orphan.md', '# Orphan\n\nNo frontmatter at all.\n')
    # a page whose name/heading disagree with its path
    _write(root / 'core' / 'mismatch.md', _page('Wrong', 'A page.', 'Body.'))
    result = _wiki(root, 'lint', '--path', str(root))
    out = result.stdout
    assert result.returncode == 1
    # human-only problems are reported by message
    assert 'Bad#Folder/: Invalid folder name' in out
    assert 'Bad#Folder/: Missing index' in out
    # out-of-date files are shown as the diff update would apply
    assert 'core/orphan.md' in out
    assert '+name: core/orphan' in out
    assert 'core/mismatch.md' in out
    assert '+name: core/mismatch' in out
    assert '-# Wrong' in out
    assert 'issue' in out.lower()
    assert 'found' in out.lower()


# ------ map


@pytest.mark.parametrize(
    ('args', 'present', 'absent'),
    [
        # default view shows nested pages with word counts and descriptions
        ([], ['core/', 'design', 'Core concepts.'], []),
        # depth 0 keeps only top-level folders
        (['--depth', '0'], ['core/', 'guides/'], ['design']),
        # descriptions can be suppressed
        (['--no-desc'], ['core/'], ['Core concepts.']),
        # word counts can be suppressed (parentheses disappear)
        (['--no-words'], ['core/'], ['(']),
        # descriptions can be truncated to a character budget
        (['--desc-limit', '4'], ['...'], ['Core concepts.']),
    ],
)
def test_map_respects_view_options(
    wiki: pathlib.Path,
    args: list[str],
    present: list[str],
    absent: list[str],
) -> None:
    """The map view honors --depth, --desc, --no-words, and --desc-limit."""
    result = _wiki(wiki, 'map', '--path', str(wiki), *args)
    assert result.returncode == 0
    for token in present:
        assert token in result.stdout
    for token in absent:
        assert token not in result.stdout


def test_map_filters_by_category(tmp_path: pathlib.Path) -> None:
    """The map view narrows to a named category or uncategorized entries."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # one categorized folder, one left uncategorized
    _write(
        root / 'backend' / '_index.md',
        '---\nname: Backend\ndesc: Server side.\ncategory: services\n---\n'
        '\n# Backend\n\nText.\n\n***\n',
    )
    _write(root / 'misc' / '_index.md', _index('Misc', 'Other notes.', 'Text.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    # filtering to the category keeps only the matching subtree
    matched = _wiki(root, 'map', '--path', str(root), '--category', 'services')
    assert matched.returncode == 0
    assert 'backend/' in matched.stdout
    assert 'misc/' not in matched.stdout
    # an empty category string keeps only uncategorized entries
    uncategorized = _wiki(root, 'map', '--path', str(root), '--category', '')
    assert uncategorized.returncode == 0
    assert 'misc/' in uncategorized.stdout
    assert 'backend/' not in uncategorized.stdout


def test_map_empty_wiki_reports_empty(tmp_path: pathlib.Path) -> None:
    """A map of a wiki with no folders reports emptiness, not a crash."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    result = _wiki(root, 'map', '--path', str(root))
    assert result.returncode == 0
    assert 'empty' in result.stdout.lower()


# ------ search


def test_search_output_modes(wiki: pathlib.Path) -> None:
    """A search prints unique paths by default, and line detail on request."""
    # default mode lists each matching file once
    paths = _wiki(wiki, 'search', 'widget', '--path', str(wiki))
    assert paths.returncode == 0
    assert 'core/design.md' in paths.stdout
    assert ':' not in paths.stdout.replace('.md', '').replace('.txt', '')
    # --lines includes line numbers and the matching text
    lines = _wiki(wiki, 'search', 'widget', '--path', str(wiki), '--lines')
    assert lines.returncode == 0
    assert 'core/design.md:' in lines.stdout
    assert 'subsystem' in lines.stdout
    # --lineno includes line numbers but not the line text
    lineno = _wiki(wiki, 'search', 'widget', '--path', str(wiki), '--lineno')
    assert lineno.returncode == 0
    assert 'core/design.md:' in lineno.stdout
    assert 'subsystem' not in lineno.stdout


def test_search_field_and_ignore_case(wiki: pathlib.Path) -> None:
    """A search can target a frontmatter field and match case-insensitively."""
    # a body-content search for 'design' should not match the frontmatter desc
    field = _wiki(
        wiki,
        'search',
        'design',
        '--path',
        str(wiki),
        '--field',
        'desc',
        '--lines',
    )
    assert field.returncode == 0
    assert 'desc: A design document' in field.stdout
    # case-insensitive matching finds the lowercase body term from an upper query
    insensitive = _wiki(
        wiki,
        'search',
        'WIDGET',
        '--path',
        str(wiki),
        '--ignore-case',
    )
    assert insensitive.returncode == 0
    assert 'core/design.md' in insensitive.stdout
    # without the flag the uppercase query misses the lowercase body
    sensitive = _wiki(wiki, 'search', 'WIDGET', '--path', str(wiki))
    assert sensitive.returncode == 0
    assert 'No matches' in sensitive.stdout


def test_search_all_includes_non_markdown(wiki: pathlib.Path) -> None:
    """--all widens the search to non-markdown files in the tree."""
    without = _wiki(wiki, 'search', 'widget', '--path', str(wiki))
    with_all = _wiki(wiki, 'search', 'widget', '--path', str(wiki), '--all')
    assert without.returncode == 0
    assert with_all.returncode == 0
    assert 'snippet.txt' not in without.stdout
    assert 'snippet.txt' in with_all.stdout


def test_search_no_match_is_clean_success(wiki: pathlib.Path) -> None:
    """A pattern with no hits reports cleanly and still succeeds."""
    result = _wiki(wiki, 'search', 'zzz_no_such_token', '--path', str(wiki))
    assert result.returncode == 0
    assert 'No matches' in result.stdout


def test_search_line_flags_are_mutually_exclusive(wiki: pathlib.Path) -> None:
    """--lines and --lineno cannot be combined (usage error, exit 2)."""
    result = _wiki(
        wiki,
        'search',
        'widget',
        '--path',
        str(wiki),
        '--lines',
        '--lineno',
    )
    assert result.returncode == 2
    assert 'mutually exclusive' in (result.stdout + result.stderr).lower()


def test_search_invalid_regex_reports_error(wiki: pathlib.Path) -> None:
    """A malformed regex surfaces a clear error and a non-zero exit."""
    result = _wiki(wiki, 'search', '[', '--path', str(wiki))
    assert result.returncode != 0
    assert result.returncode != 2
    assert 'error' in (result.stdout + result.stderr).lower()


def test_search_all_skips_undecodable_files(wiki: pathlib.Path) -> None:
    """``search --all`` skips a non-UTF-8 file instead of crashing the whole run."""
    binary = wiki / 'diagram.png'
    binary.write_bytes(b'\x89PNG\r\n\x1a\n\xff\xfe\x00\x01')
    try:
        result = _wiki(wiki, 'search', 'widget', '--path', str(wiki), '--all')
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'snippet.txt' in result.stdout
    finally:
        binary.unlink()


# ------ read


@pytest.mark.parametrize(
    ('slice_arg', 'expected', 'unexpected'),
    [
        # n:m -- the H1 leads the body, so words 0:2 are the heading itself
        ('0:2', '# core/design', 'The widget'),
        # n: -- from an offset past the H1 to the end of the body prose
        ('2:', 'The widget subsystem', '# core/design'),
        # :m -- a prefix spanning the H1 and the first prose word
        (':3', '# core/design', 'subsystem'),
        # negative bounds count from the end -- trailing prose, no H1
        ('-2:', 'Widgets matter.', '# core/design'),
    ],
)
def test_read_slice_forms(
    wiki: pathlib.Path,
    slice_arg: str,
    expected: str,
    unexpected: str,
) -> None:
    """A read --words supports n:m, n:, and :m word windows.

    Only the frontmatter is special: the H1 is body content occupying the first
    two words, so it appears only when the window includes the start.
    """
    result = _wiki(
        wiki,
        'read',
        'core/design',
        '--path',
        str(wiki),
        '--words',
        slice_arg,
    )
    assert result.returncode == 0
    # frontmatter is always preserved as well-formed markdown
    assert 'name: core/design' in result.stdout
    assert expected in result.stdout
    assert unexpected not in result.stdout


def test_read_resolves_dotted_page_name(wiki: pathlib.Path) -> None:
    """A page whose name contains a dot reads by its bare name (not just <name>.md).

    ``with_suffix`` would mangle ``app.config`` -> ``app.md``; resolution appends
    ``.md`` instead so dotted names (``v1.2``, ``app.config``) round-trip.
    """
    page = wiki / 'app.config.md'
    page.write_text(
        '---\nname: app.config\ndesc: Config.\n---\n# app.config\n\nbody-marker.\n',
        encoding='utf-8',
    )
    try:
        result = _wiki(wiki, 'read', 'app.config', '--path', str(wiki))
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'body-marker.' in result.stdout
    finally:
        page.unlink()


@pytest.mark.parametrize(
    ('args', 'returncode', 'needle'),
    [
        # a slice without a colon is a usage error
        (['--words', 'abc'], 2, 'slice format'),
        # a slice with non-integer bounds is a usage error
        (['--words', 'a:b'], 2, 'slice format'),
        # a missing entry is a clean runtime error, not a traceback
        ([], 1, 'not found'),
    ],
)
def test_read_errors(
    wiki: pathlib.Path,
    args: list[str],
    returncode: int,
    needle: str,
) -> None:
    """A read rejects malformed slices and missing entries with clear errors."""
    name = 'core/design' if args else 'core/missing_entry'
    result = _wiki(wiki, 'read', name, '--path', str(wiki), *args)
    assert result.returncode == returncode
    assert needle in (result.stdout + result.stderr).lower()


# ------ config


def test_config_applies_obsidian(tmp_path: pathlib.Path) -> None:
    """Config enables the plugin and writes its settings into ``.obsidian/``.

    The plugin download is skipped here (see ``_wiki``) so the suite stays
    offline; the live fetch is covered by ``test_config_downloads_plugin``.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    result = _wiki(root, 'config', '--path', str(root))
    assert result.returncode == 0
    # the plugin is enabled and its curated settings are written
    plugin_id = 'obsidian-front-matter-title-plugin'
    cp_file = root / '.obsidian' / 'community-plugins.json'
    assert plugin_id in json.loads(cp_file.read_text(encoding='utf-8'))
    assert (root / '.obsidian' / 'plugins' / plugin_id / 'data.json').is_file()


@pytest.mark.online
def test_config_downloads_plugin(tmp_path: pathlib.Path) -> None:
    """With downloads allowed, config fetches the plugin code into the vault.

    Marked ``online`` and excluded by default (``-m 'not online'``); run
    with ``uv run pytest -m online`` when online.
    """
    root = tmp_path / 'wiki'
    init = _wiki(tmp_path, 'init', '--path', str(root), allow_download=True)
    assert init.returncode == 0
    result = _wiki(root, 'config', '--path', str(root), allow_download=True)
    assert result.returncode == 0
    # the downloaded plugin code lands in the vault
    plugin = root / '.obsidian' / 'plugins' / 'obsidian-front-matter-title-plugin'
    assert (plugin / 'main.js').is_file()
    assert (plugin / 'manifest.json').is_file()


# ------ lint after update


def test_lint_clean_after_update(wiki: pathlib.Path) -> None:
    """A wiki that has just been updated passes lint with exit 0."""
    result = _wiki(wiki, 'lint', '--path', str(wiki))
    assert result.returncode == 0


# ------ git merge driver


def test_merge_driver_no_op_without_git(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no ``git`` on PATH, wiring the merge driver is a clean no-op.

    The leading ``rev-parse`` is best-effort (check=False); a missing binary
    must degrade to a no-op like a failed command, not a hard crash that
    aborts a half-finished ``wiki init``/``config``.
    """
    monkeypatch.setenv('PATH', str(tmp_path / 'no-bin'))
    configure_git_merge_driver(tmp_path)


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_init_writes_gitattributes_without_committing(tmp_path: pathlib.Path) -> None:
    """``wiki init`` wires the merge driver but never commits ``.gitattributes``.

    Per the org's never-auto-commit rule (Issue #1), init writes the attribute
    map to the working tree only; it leaves HEAD and the index untouched.
    """

    def git(*args: str) -> str:
        return subprocess.run(
            [GIT, '-C', str(tmp_path), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    # a real repo with one commit so .gitattributes would be brand-new
    git('init', '-q', '-b', 'main')
    git('config', 'user.email', 't@t')
    git('config', 'user.name', 't')
    (tmp_path / 'README').write_text('x', encoding='utf-8')
    git('add', 'README')
    git('commit', '-q', '-m', 'init')
    head = git('rev-parse', 'HEAD')

    # init wires the driver: .gitattributes is written but neither staged nor committed
    assert _wiki(tmp_path, 'init', '--path', str(tmp_path / 'wiki')).returncode == 0
    assert 'merge=wiki-index' in (tmp_path / '.gitattributes').read_text(
        encoding='utf-8'
    )
    assert git('rev-parse', 'HEAD') == head
    assert '.gitattributes' not in git('diff', '--cached', '--name-only')


# ------ version


def test_version_reports_installed_version(tmp_path: pathlib.Path) -> None:
    """``wiki --version`` prints the installed version and exits 0."""
    result = _wiki(tmp_path, '--version')
    assert result.returncode == 0
    assert any(char.isdigit() for char in result.stdout)


# ------ helpers


def _wiki(
    cwd: pathlib.Path,
    *args: str,
    allow_download: bool = False,
) -> subprocess.CompletedProcess:
    """Run the ``wiki`` CLI in ``cwd`` and capture text output.

    Plugin downloads are skipped by default so the suite stays offline;
    pass ``allow_download=True`` to exercise the real network fetch.
    """
    env = dict(os.environ)
    env[_OFFLINE_MODE] = 'false' if allow_download else 'true'
    return subprocess.run(
        [WIKI, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


def _write(path: pathlib.Path, text: str) -> None:
    """Write ``text`` to ``path`` as UTF-8, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _page(name: str, desc: str, body: str) -> str:
    """Return a minimal authored page with frontmatter and a heading."""
    return f'---\nname: {name}\ndesc: {desc}\n---\n\n# {name}\n\n{body}\n'


def _index(name: str, desc: str, body: str) -> str:
    """Return a minimal authored folder index with a content marker."""
    return f'---\nname: {name}\ndesc: {desc}\n---\n\n# {name}\n\n{body}\n\n***\n'
