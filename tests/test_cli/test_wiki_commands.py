"""End-to-end tests for the ``wiki`` CLI command matrix.

Drives the real ``wiki`` console script as a subprocess against a throwaway
wiki built with two folders (``core``, ``guides``) and a handful of pages.
The suite covers every sub-command -- init, install, update (+ ``--prune``),
lint, map, search, read, config, trust, and the hidden ``_merge`` driver --
plus ``--version``, exercising option behavior, exit codes, and error
reporting as observable output rather than internal state.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil

import pytest

from wiki import __version__ as WIKI_VERSION
from wiki.cli.utils import configure_git_merge_driver

from .conftest import GIT, WIKI, _git, _wiki

__all__ = [
    'test_init_creates_root_index',
    'test_init_guards_existing_wiki',
    'test_init_seeds_settings',
    'test_init_refuses_nested_wiki',
    'test_init_quiet_suppresses_chatter',
    'test_install_copies_skill_into_home',
    'test_install_project_targets_cwd',
    'test_install_link_swaps_copy_and_symlink',
    'test_update_generates_child_links',
    'test_update_prune_removes_broken_link',
    'test_update_check_reports_changes_without_writing',
    'test_update_noop_reports_nothing_to_update',
    'test_update_failed_entry_mutates_nothing',
    'test_update_narrations_condense_by_default',
    'test_update_condenses_batch_adoption',
    'test_read_only_commands_are_deterministic',
    'test_path_inside_wiki_is_refused',
    'test_path_inside_undeclared_wiki_is_refused',
    'test_parent_enclosing_declared_wiki_is_refused',
    'test_update_cli_refuses_nested_wiki',
    'test_update_refuses_a_scope_inside_a_nested_wiki',
    'test_update_refuses_an_excluded_dot_directory_scope',
    'test_update_cli_refuses_conflict_markers',
    'test_lint_reports_issue_taxonomy_and_exits_nonzero',
    'test_lint_summary_counts_notes',
    'test_lint_details_issues_and_count_condenses',
    'test_map_respects_view_options',
    'test_map_filters_by_category',
    'test_map_empty_wiki_reports_empty',
    'test_map_stat_and_desc_limit_bounds',
    'test_search_output_modes',
    'test_search_field_and_ignore_case',
    'test_search_all_includes_non_markdown',
    'test_search_no_match_exits_nonzero',
    'test_search_line_flags_are_mutually_exclusive',
    'test_search_invalid_regex_reports_error',
    'test_search_resolution_failure_exits_two',
    'test_search_all_skips_undecodable_files',
    'test_read_slice_forms',
    'test_read_resolves_dotted_page_name',
    'test_read_errors',
    'test_read_slice_short_aliases',
    'test_colliding_short_flags_are_rejected',
    'test_read_outputs_bytes_verbatim',
    'test_config_applies_obsidian',
    'test_config_downloads_plugin',
    'test_config_adopts_undeclared_tree',
    'test_lint_clean_after_update',
    'test_merge_driver_no_op_without_git',
    'test_init_writes_gitattributes_without_committing',
    'test_merge_driver_merges_authored_frontmatter',
    'test_merge_keeps_frontmatter_when_side_is_mangled',
    'test_merge_dispatches_on_pathname',
    'test_merge_driver_skips_non_wiki_index_files',
    'test_merge_conflicts_when_side_loses_separator',
    'test_merge_hints_add_add_body_conflicts',
    'test_version_flag_reports_a_version',
    'test_trust_gates_hook_execution',
    'test_trust_refuses_non_wiki_path',
    'test_trust_store_does_not_mark_home_as_wiki_root',
    'test_trust_store_exemption_survives_symlinked_home',
]

pytestmark = pytest.mark.skipif(
    WIKI is None,
    reason='wiki console script not installed',
)


@pytest.fixture(scope='module')
def wiki(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """A populated wiki rooted at ``{tmp}/wiki``, built via the real CLI.

    Layout (after ``init`` + authored files + ``update``)::

        core/   -- design.md, snippet.txt (non-markdown)
        guides/ -- setup.md

    Built once per module so link generation, frontmatter enrichment, and
    word counts are exercised exactly as a user would drive them.
    READ-ONLY by convention: the few tests that must add a file to the
    shared tree remove it in a ``finally`` block, so siblings observe
    the arrangement unchanged.
    """
    base = tmp_path_factory.mktemp('wiki_cli')
    root = base / 'wiki'
    assert _wiki(base, 'init', 'Knowledge', '--path', str(root)).returncode == 0
    # author two folders with pages and one non-markdown file
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Real text.'))
    design = _page(
        name='Design',
        desc='A design document about widgets.',
        body='The widget subsystem handles rendering.\nWidgets matter.',
    )
    _write(root / 'core' / 'design.md', design)
    _write(root / 'core' / 'snippet.txt', 'widget appears in plain code here\n')
    _write(root / 'guides' / '_index.md', _index('Guides', 'How-to guides.', 'Text.'))
    setup = _page(
        name='Setup',
        desc='Setup instructions for the project.',
        body='Run the installer to set up the environment.',
    )
    _write(root / 'guides' / 'setup.md', setup)
    # generate links and word counts across the tree
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    return root


# ------ init


def test_init_creates_root_index(tmp_path: pathlib.Path) -> None:
    """A fresh init writes a root ``_index.md`` with the chosen display name."""
    root = tmp_path / 'wiki'
    result = _wiki(tmp_path, 'init', 'Handbook', '--path', str(root))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Initialized wiki' in result.stdout
    index_text = (root / '_index.md').read_text(encoding='utf-8')
    assert 'name: Handbook' in index_text
    # a title is authored, never seeded on a fresh index
    assert 'title:' not in index_text
    # init also materializes the Obsidian config; offline (see _wiki)
    # the skipped plugin download surfaces as a warning rather than success
    assert (root / '.obsidian' / 'community-plugins.json').is_file()
    assert 'OFFLINE_MODE' in result.stderr


def test_init_guards_existing_wiki(tmp_path: pathlib.Path) -> None:
    """Re-running init on an existing wiki reports rather than re-creating."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    result = _wiki(tmp_path, 'init', '--path', str(root))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'already initialized' in result.stdout.lower()

    # a foreign outer index does not defeat idempotency: the declared
    # marker names the root, so re-init reports instead of refusing
    _write(tmp_path / '_index.md', '---\ntitle: hugo\n---\ncontent\n')
    rerun = _wiki(tmp_path, 'init', '--path', str(root))
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert 'already initialized' in rerun.stdout.lower()


def test_init_seeds_settings(tmp_path: pathlib.Path) -> None:
    """``init --settings`` seeds the given JSON into ``.wiki/settings.json``."""
    root = tmp_path / 'wiki'
    policy = '{"naming": {"validate": ["ascii", "identifier"]}}'
    result = _wiki(tmp_path, 'init', '--path', str(root), '--settings', policy)
    assert result.returncode == 0, result.stdout + result.stderr
    settings = root / '.wiki' / 'settings.json'
    data = json.loads(settings.read_text(encoding='utf-8'))
    assert data == {'naming': {'validate': ['ascii', 'identifier']}}


def test_init_refuses_nested_wiki(tmp_path: pathlib.Path) -> None:
    """Init inside an existing wiki is refused, naming the enclosing root.

    Nested wikis have no boundary -- the outer update would rewrite the inner
    index and absorb its pages into the outer counts -- so an inner init must
    fail cleanly instead of scaffolding a wiki-inside-a-wiki.
    """
    outer = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(outer)).returncode == 0

    # an explicit --path inside the outer wiki is refused and creates nothing
    result = _wiki(tmp_path, 'init', 'Inner', '--path', str(outer / 'inner'))
    assert result.returncode == 1
    assert str(outer) in result.stdout + result.stderr
    assert not (outer / 'inner').exists()

    # the default {cwd}/wiki path is refused the same way from inside a wiki
    result = _wiki(outer, 'init')
    assert result.returncode == 1
    assert str(outer) in result.stdout + result.stderr
    assert not (outer / 'wiki').exists()

    # an indexed subfolder is refused as nested, not "already initialized"
    _write(outer / 'topics' / '_index.md', _index('Topics', 'Topic guides.', 'Text.'))
    result = _wiki(tmp_path, 'init', '--path', str(outer / 'topics'))
    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert str(outer) in combined
    assert 'already initialized' not in combined.lower()

    # with the outer marker lost, the bare index chain still names the
    # enclosing wiki and the refusal holds
    shutil.rmtree(outer / '.wiki')
    result = _wiki(tmp_path, 'init', '--path', str(outer / 'newsub'))
    assert result.returncode == 1
    assert str(outer) in result.stdout + result.stderr
    assert not (outer / 'newsub').exists()


def test_init_quiet_suppresses_chatter(tmp_path: pathlib.Path) -> None:
    """``init --quiet`` suppresses the Obsidian hint and non-error output.

    A wrapping tool (e.g. fractal init) needs to place its own next-step
    guidance last; --quiet keeps stdout empty while warnings still reach
    stderr and the wiki is fully scaffolded.
    """
    root = tmp_path / 'wiki'
    result = _wiki(tmp_path, 'init', '--path', str(root), '--quiet')
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == ''
    # warnings still surface (the offline plugin-download skip; see _wiki)
    assert 'OFFLINE_MODE' in result.stderr
    assert (root / '_index.md').is_file()
    # the already-initialized notice is non-error chatter too
    rerun = _wiki(tmp_path, 'init', '--path', str(root), '--quiet')
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert rerun.stdout.strip() == ''


# ------ install


def test_install_copies_skill_into_home(tmp_path: pathlib.Path) -> None:
    """``install`` copies the bundled skill into HOME's agent skill dirs.

    The default path writes into the user's real home directory, so the
    test isolates HOME (an env override ``pathlib.Path.home`` honors).
    """
    home = tmp_path / 'home'
    home.mkdir()
    result = _wiki(tmp_path, 'install', home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    for agent in ('.claude', '.agents'):
        skill = home / agent / 'skills' / 'wiki' / 'SKILL.md'
        assert skill.is_file()
        assert f'{agent}' in result.stdout
    # a re-run replaces the prior copy rather than erroring or nesting
    rerun = _wiki(tmp_path, 'install', home=home)
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert (home / '.claude' / 'skills' / 'wiki' / 'SKILL.md').is_file()


def test_install_project_targets_cwd(tmp_path: pathlib.Path) -> None:
    """``install --project`` copies the skill under the cwd, not HOME."""
    home = tmp_path / 'home'
    project = tmp_path / 'project'
    home.mkdir()
    project.mkdir()
    result = _wiki(project, 'install', '--project', home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (project / '.claude' / 'skills' / 'wiki' / 'SKILL.md').is_file()
    assert (project / '.agents' / 'skills' / 'wiki' / 'SKILL.md').is_file()
    assert not (home / '.claude').exists()


def test_install_link_swaps_copy_and_symlink(tmp_path: pathlib.Path) -> None:
    """``install --link`` symlinks the skill; re-installs swap either way.

    The symlink is the editable-install dev setup -- source edits apply
    without re-installing -- and a plain re-install must replace the
    link with a real copy just as --link replaces a prior copy.
    """
    home = tmp_path / 'home'
    home.mkdir()
    # a plain install lays down real copies
    assert _wiki(tmp_path, 'install', home=home).returncode == 0
    # --link replaces each copy with a symlink to the package source
    result = _wiki(tmp_path, 'install', '--link', home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    for agent in ('.claude', '.agents'):
        skill = home / agent / 'skills' / 'wiki'
        assert skill.is_symlink()
        assert (skill / 'SKILL.md').is_file()
    # a plain re-install swaps the link back to a real copy
    rerun = _wiki(tmp_path, 'install', home=home)
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    skill = home / '.claude' / 'skills' / 'wiki'
    assert not skill.is_symlink()
    assert (skill / 'SKILL.md').is_file()


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
    # an index-less folder so the plan includes an index creation
    _write(root / 'guides' / 'setup.md', _page('Setup', 'A guide.', 'Body.'))
    # a dry run reports the files and exits non-zero
    result = _wiki(root, 'update', '--check', '--path', str(root))
    assert result.returncode == 1
    assert 'Would update: core/design.md' in result.stdout
    assert 'would change' in result.stdout
    # the condensed narration is worded for the dry run, not as done work
    assert 'Would create 1 new index' in result.stderr
    assert 'Would add 4 new links' in result.stderr
    # nothing was written, so a second check still reports changes
    assert not (root / 'guides' / '_index.md').exists()
    assert _wiki(root, 'update', '--check', '--path', str(root)).returncode == 1
    # a real update applies the same plan, narrated as completed work
    applied = _wiki(root, 'update', '--path', str(root))
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert 'Created 1 new index (fill in its desc)' in applied.stderr
    assert 'Added 4 new links' in applied.stderr
    # the applied tree makes a follow-up check clean
    clean = _wiki(root, 'update', '--check', '--path', str(root))
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert 'Nothing to update.' in clean.stdout


def test_update_noop_reports_nothing_to_update(tmp_path: pathlib.Path) -> None:
    """A second update on an up-to-date tree writes nothing and says so."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    _write(root / 'core' / 'design.md', _page('Design', 'A design.', 'Body.'))
    # the first update brings the tree current and reports the files it changed
    first = _wiki(root, 'update', '--path', str(root))
    assert first.returncode == 0, first.stdout + first.stderr
    assert 'Updated' in first.stdout
    # a second update finds nothing to change and reports the no-op
    second = _wiki(root, 'update', '--path', str(root))
    assert second.returncode == 0, second.stdout + second.stderr
    assert 'Nothing to update.' in second.stdout


def test_update_failed_entry_mutates_nothing(tmp_path: pathlib.Path) -> None:
    """``update <entry>`` with a bad entry fails before the write sweeps.

    Scope resolution precedes the marker restore: a command that exits 1
    must not have quietly rewritten the tree on its way to the error.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    (root / '.wiki' / 'settings.json').unlink()

    # the bad entry is named and the missing marker stays missing
    result = _wiki(root, 'update', 'no_such_entry', '--path', str(root))
    assert result.returncode == 1
    assert "Wiki folder not found: 'no_such_entry'" in result.stderr
    assert not (root / '.wiki' / 'settings.json').exists()


def test_update_narrations_condense_by_default(tmp_path: pathlib.Path) -> None:
    """Update collapses its narrations to per-category counts by default.

    The diff is the record, so write narrations are a side report:
    condensed to per-category counts by default, every line with ``--full``.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a folder with no index and two pages: update creates the index and
    # adds three links (the folder's, plus one per page)
    _write(root / 'core' / 'design.md', _page('Design', 'A design.', 'Body.'))
    _write(root / 'core' / 'api.md', _page('Api', 'An api.', 'Body.'))

    # the default run condenses each category to one count line
    condensed = _wiki(root, 'update', '--path', str(root))
    assert condensed.returncode == 0, condensed.stdout + condensed.stderr
    assert 'New link:' not in condensed.stderr
    assert 'New index:' not in condensed.stderr
    assert 'Created 1 new index (fill in its desc)' in condensed.stderr
    assert 'Added 3 new links' in condensed.stderr

    # a broken link condenses to a count with a pointer at lint
    (root / 'core' / 'api.md').unlink()
    broken = _wiki(root, 'update', '--path', str(root))
    assert 'Broken link:' not in broken.stderr
    assert '1 broken link (run `wiki lint` to list it)' in broken.stderr

    # --full restores the per-line narration
    (root / 'core' / 'extra.md').write_text(
        _page('Extra', 'An extra page.', 'Body.'),
        encoding='utf-8',
    )
    full = _wiki(root, 'update', '--path', str(root), '--full')
    assert full.returncode == 0, full.stdout + full.stderr
    assert 'New link: [[core/extra|extra]] in core/_index.md' in full.stderr
    assert 'Broken link: [[core/api|api]] in core/_index.md' in full.stderr

    # --count is the explicit default; combining the modes is a usage error
    default = _wiki(root, 'update', '--path', str(root))
    count = _wiki(root, 'update', '--path', str(root), '--count')
    assert count.stdout == default.stdout
    assert count.stderr == default.stderr
    both = _wiki(root, 'update', '--path', str(root), '--full', '--count')
    assert both.returncode == 2
    assert 'mutually exclusive' in (both.stdout + both.stderr).lower()


def test_update_condenses_batch_adoption(tmp_path: pathlib.Path) -> None:
    """A batch of bare-page adoptions condenses to one count line.

    Adoption announcements are update narration like any other: the
    default mode counts them per category, ``--check`` words them as
    pending, and ``--full`` prints the per-page lines.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    _write(root / 'core' / 'one.md', '# One\n\nBody.\n')
    _write(root / 'core' / 'two.md', 'Body only.\n')

    # a dry run words the pending adoptions without writing them
    check = _wiki(root, 'update', '--check', '--path', str(root))
    assert check.returncode == 1
    assert 'Would adopt 2 bare pages' in check.stderr

    # the applied run counts the adoptions; the per-page lines need --full
    applied = _wiki(root, 'update', '--path', str(root))
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert 'Adopted 2 bare pages (frontmatter added)' in applied.stderr
    assert 'Adopted bare page:' not in applied.stderr


def test_read_only_commands_are_deterministic(wiki: pathlib.Path) -> None:
    """Lint and map repeat byte-identically with no write-style notices.

    Read-only commands are deterministic run-over-run on an unchanged
    wiki -- no since-last-run state anywhere -- and never emit update's
    write narrations.
    """
    for args in (['lint'], ['map']):
        first = _wiki(wiki, *args, '--path', str(wiki))
        second = _wiki(wiki, *args, '--path', str(wiki))
        # byte-identical output, run over run
        assert first.returncode == second.returncode
        assert first.stdout == second.stdout
        assert first.stderr == second.stderr
        # no write-style notices from a read-only command
        combined = first.stdout + first.stderr
        for token in ('New index:', 'New link:', 'Overwrote desc:', 'Restored'):
            assert token not in combined


@pytest.mark.parametrize(
    argnames=('args', 'code'),
    argvalues=[
        (['update'], 1),
        (['lint'], 1),
        (['map'], 1),
        # search's grep triple reserves exit 1 for a clean no-match, so
        # its resolution failure lands on the error leg
        (['search', 'widget'], 2),
    ],
    ids=['update', 'lint', 'map', 'search'],
)
def test_path_inside_wiki_is_refused(
    tmp_path: pathlib.Path,
    args: list[str],
    code: int,
) -> None:
    """``--path`` at a folder inside a wiki aborts, naming the enclosing root.

    Treating a subfolder as a wiki root grows a second marker/root index
    and rewrites ``name:`` paths relative to the wrong root; the command must
    refuse and point at the entry argument for scoped work instead.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    before = (root / 'core' / '_index.md').read_text(encoding='utf-8')

    # the inside path is refused, naming the enclosing root and the fix
    result = _wiki(root, *args, '--path', str(root / 'core'))
    combined = result.stdout + result.stderr
    assert result.returncode == code
    assert 'inside the wiki' in combined
    assert str(root) in combined
    assert '<entry>' in combined
    # the subfolder was not mangled into a second wiki root
    assert not (root / 'core' / '.wiki').exists()
    assert (root / 'core' / '_index.md').read_text(encoding='utf-8') == before


def test_path_inside_undeclared_wiki_is_refused(tmp_path: pathlib.Path) -> None:
    """The inside-a-wiki refusal holds when the root marker is missing.

    An undeclared wiki (a lost ``.wiki/``) leaves no settings marker for
    the enclosure probe, but the ancestor index chain still names the
    real root; ``--path`` at a subfolder must refuse the same way instead
    of planting a second marker there and rewriting its index as a root.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    shutil.rmtree(root / '.wiki')
    before = (root / 'core' / '_index.md').read_text(encoding='utf-8')

    # the subfolder is refused, naming the chain's topmost index as root
    result = _wiki(root, 'update', '--path', str(root / 'core'))
    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert f'inside the wiki at: {root};' in combined
    assert '<entry>' in combined
    # no marker planted, no index rewritten against the wrong root
    assert not (root / 'core' / '.wiki').exists()
    assert (root / 'core' / '_index.md').read_text(encoding='utf-8') == before


@pytest.mark.parametrize(
    argnames='args',
    argvalues=[['update'], ['lint'], ['map']],
    ids=['update', 'lint', 'map'],
)
def test_parent_enclosing_declared_wiki_is_refused(
    tmp_path: pathlib.Path,
    args: list[str],
) -> None:
    """A stray index above a declared wiki never re-roots resolution there.

    A foreign ``_index.md`` in the project root (a Hugo site, a dropped
    file) makes cwd resolution land on the parent as an undeclared root;
    adopting it would absorb the wiki below -- rewriting every ``name:``
    against the wrong root and planting a second settings marker -- so the
    command must refuse, naming the declared root to run from instead.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'note.md', _page('note', 'A page.', 'Body.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    (tmp_path / '_index.md').write_text('# stray\n', encoding='utf-8')
    before = (root / 'note.md').read_text(encoding='utf-8')

    # cwd resolution from the parent refuses, naming the nested root
    result = _wiki(tmp_path, *args)
    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert f'encloses the wiki at: {root};' in combined
    assert 'declared root' in combined
    # nothing was absorbed: no marker planted, no name: rewritten
    assert not (tmp_path / '.wiki').exists()
    assert (root / 'note.md').read_text(encoding='utf-8') == before


def test_update_cli_refuses_nested_wiki(tmp_path: pathlib.Path) -> None:
    """A stray declared wiki inside the tree fails update, naming it.

    A wiki copy dropped inside another wiki (a backup, a vendored
    snapshot) would be absorbed by the sweep -- every nested ``name:``
    rewritten against the outer root -- so update exits nonzero with
    the enclosure message instead of rewriting anything.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'note.md', _page('note', 'A page.', 'Body.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    # a stray declared wiki dropped inside (its marker is what matters)
    nested = root / 'backup'
    (nested / '.wiki').mkdir(parents=True)
    (nested / '.wiki' / 'settings.json').write_text('{}\n', encoding='utf-8')
    before = (root / 'note.md').read_text(encoding='utf-8')

    # the sweep is refused with the enclosure message, naming the root
    result = _wiki(root, 'update', '--path', str(root))
    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert f'encloses the wiki at: {nested};' in combined
    assert 'declared root' in combined
    assert (root / 'note.md').read_text(encoding='utf-8') == before


def test_update_refuses_a_scope_inside_a_nested_wiki(tmp_path: pathlib.Path) -> None:
    """A scope strictly inside a nested declared wiki is refused, naming it.

    A wiki copy dropped inside another declares its own root; sweeping a
    subfolder of that inner wiki from the outer root would rewrite the inner
    pages' ``name:`` against the outer root. update refuses before mutating
    (the descendant scan catches an enclosed marker; this catches an
    enclosing one), and lint refuses alike instead of previewing the wrong
    plan.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a nested declared wiki with a subfolder holding a page
    nested = root / 'backup'
    (nested / '.wiki').mkdir(parents=True)
    (nested / '.wiki' / 'settings.json').write_text('{}\n', encoding='utf-8')
    _write(nested / 'sub' / 'page.md', _page('sub/page', 'A page.', 'Body.'))
    before = (nested / 'sub' / 'page.md').read_text(encoding='utf-8')
    # updating a subfolder of the inner wiki from the outer root refuses
    result = _wiki(root, 'update', 'backup/sub', '--path', str(root))
    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert f'inside the wiki at: {nested};' in combined
    assert (nested / 'sub' / 'page.md').read_text(encoding='utf-8') == before
    # lint of the same scope refuses the same way (no misleading diff)
    lint = _wiki(root, 'lint', 'backup/sub', '--path', str(root))
    assert lint.returncode == 1
    assert f'inside the wiki at: {nested};' in lint.stdout + lint.stderr


def test_update_refuses_an_excluded_dot_directory_scope(
    tmp_path: pathlib.Path,
) -> None:
    """A scope naming an excluded (dot) directory is refused, scaffolding nothing.

    ``.wiki``/``.git``/``.obsidian`` are dot-excluded from every walk, so
    scaffolding indexes into one would leave junk no later update or lint can
    see or repair (and ``wiki config`` would copy it into the user's vault).
    The scope must be refused as an excluded directory.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    result = _wiki(root, 'update', '.wiki', '--path', str(root))
    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert 'excluded directory' in combined
    # nothing scaffolded inside the tool directory
    assert not (root / '.wiki' / '_index.md').exists()


def test_update_cli_refuses_conflict_markers(tmp_path: pathlib.Path) -> None:
    """A conflict-marked file fails update, naming it, and nothing lands.

    A half-resolved merge would otherwise ride the sweep into the
    regenerated files, so update exits nonzero naming the marked file
    instead of rewriting anything.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'note.md', _page('note', 'A page.', 'Body.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    # plant a real conflict in the page
    note = root / 'note.md'
    conflicted = note.read_text(encoding='utf-8') + (
        '\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n'
    )
    note.write_text(conflicted, encoding='utf-8')

    # the sweep is refused, naming the marked file, and nothing is rewritten
    result = _wiki(root, 'update', '--path', str(root))
    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert 'Merge conflict markers in: note.md;' in combined
    assert note.read_text(encoding='utf-8') == conflicted


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


def test_lint_summary_counts_notes(tmp_path: pathlib.Path) -> None:
    """The closing summary counts the stderr notes instead of contradicting them.

    Soft notes go to stderr with exit 0 by design, but a bare 'No issues
    found.' beneath the notes still on screen reads as a contradiction --
    the summary must carry both counts while the exit codes stay unchanged.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a fresh wiki carries soft notes only (placeholder desc, empty content)
    clean = _wiki(root, 'lint', '--path', str(root))
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert 'Needs desc' in clean.stderr
    assert 'No issues found (2 notes).' in clean.stdout
    # with hard issues on top, the summary counts both kinds (the bad name
    # and its missing index)
    (root / 'Bad#Folder').mkdir()
    dirty = _wiki(root, 'lint', '--path', str(root))
    assert dirty.returncode == 1
    assert '2 issues, 2 notes.' in dirty.stdout


def test_lint_details_issues_and_count_condenses(
    tmp_path: pathlib.Path,
) -> None:
    """Lint details every issue by default; ``--count`` prints the summary alone.

    Naming problems is lint's product, so the default mode lists every
    issue -- no cap, no collapse, no ``--broken`` escape -- while
    ``--count`` condenses the run to its closing summary.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    pages = [f'page{i}' for i in range(8)]
    for page in pages:
        _write(root / 'core' / f'{page}.md', _page(page, f'The {page} page.', 'Body.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    # delete every page, then converge so only the broken links remain
    for page in pages:
        (root / 'core' / f'{page}.md').unlink()
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0

    # the default (detailed) view lists every broken link plus the summary
    default = _wiki(root, 'lint', '--path', str(root))
    assert default.returncode == 1
    assert default.stdout.count('Broken link [[') == 8
    assert '8 issues' in default.stdout

    # --count condenses to the summary; the notes leave stderr too
    count = _wiki(root, 'lint', '--path', str(root), '--count')
    assert count.returncode == 1
    assert count.stdout.count('Broken link [[') == 0
    assert '8 issues' in count.stdout
    assert 'Needs desc' not in count.stderr

    # --full is the explicit default; combining the modes is a usage error
    full = _wiki(root, 'lint', '--path', str(root), '--full')
    assert full.stdout == default.stdout
    both = _wiki(root, 'lint', '--path', str(root), '--full', '--count')
    assert both.returncode == 2
    assert 'mutually exclusive' in (both.stdout + both.stderr).lower()

    # a --broken flag does not exist
    broken = _wiki(root, 'lint', '--path', str(root), '--broken')
    assert broken.returncode == 2


# ------ map


@pytest.mark.parametrize(
    argnames=('args', 'present', 'absent'),
    argvalues=[
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
        # -1 explicitly lifts the default desc budget
        (['--desc-limit', '-1'], ['Core concepts.'], []),
    ],
    ids=['default', 'depth-0', 'no-desc', 'no-words', 'desc-limit', 'desc-unlimited'],
)
def test_map_respects_view_options(
    wiki: pathlib.Path,
    args: list[str],
    present: list[str],
    absent: list[str],
) -> None:
    """The map view honors --depth, --desc, --no-words, and --desc-limit."""
    result = _wiki(wiki, 'map', '--path', str(wiki), *args)
    assert result.returncode == 0, result.stdout + result.stderr
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
        path=root / 'backend' / '_index.md',
        text='---\nname: Backend\ndesc: Server side.\ncategory: services\n---\n'
        '\n# Backend\n\nText.\n\n***\n',
    )
    _write(root / 'misc' / '_index.md', _index('Misc', 'Other notes.', 'Text.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    # filtering to the category keeps only the matching subtree
    matched = _wiki(root, 'map', '--path', str(root), '--category', 'services')
    assert matched.returncode == 0, matched.stdout + matched.stderr
    assert 'backend/' in matched.stdout
    assert 'misc/' not in matched.stdout
    # an empty category string keeps only uncategorized entries
    uncategorized = _wiki(root, 'map', '--path', str(root), '--category', '')
    assert uncategorized.returncode == 0, uncategorized.stdout + uncategorized.stderr
    assert 'misc/' in uncategorized.stdout
    assert 'backend/' not in uncategorized.stdout


def test_map_empty_wiki_reports_empty(tmp_path: pathlib.Path) -> None:
    """A map of a wiki with no folders reports emptiness, not a crash."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    result = _wiki(root, 'map', '--path', str(root))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'empty' in result.stdout.lower()


def test_map_stat_and_desc_limit_bounds(wiki: pathlib.Path) -> None:
    """``--stat`` prints a one-line size summary; ``--desc-limit`` floors at -1.

    The summary sizes exactly the tree the same flags would print -- the
    cheap probe before dumping a large wiki -- and -1 (unlimited) is the
    lowest accepted budget.
    """
    stat = _wiki(wiki, 'map', '--path', str(wiki), '--stat')
    assert stat.returncode == 0, stat.stdout + stat.stderr
    assert re.fullmatch(r'\d+ lines?, \d+ chars?, \d+ words?\n', stat.stdout)
    # the summary counts the tree the same flags would dump
    tree = _wiki(wiki, 'map', '--path', str(wiki))
    line_count = len(tree.stdout.splitlines())
    assert stat.stdout.startswith(f'{line_count} lines')
    # anything below the -1 floor is rejected, naming the bound
    below = _wiki(wiki, 'map', '--path', str(wiki), '--desc-limit', '-2')
    assert below.returncode != 0
    assert '-1' in below.stdout + below.stderr


# ------ search


def test_search_output_modes(wiki: pathlib.Path) -> None:
    """A search prints unique paths by default, and line detail on request."""
    # default mode lists each matching file once
    paths = _wiki(wiki, 'search', 'widget', '--path', str(wiki))
    assert paths.returncode == 0, paths.stdout + paths.stderr
    assert 'core/design.md' in paths.stdout
    assert ':' not in paths.stdout.replace('.md', '').replace('.txt', '')
    # --lines includes line numbers and the matching text
    lines = _wiki(wiki, 'search', 'widget', '--path', str(wiki), '--lines')
    assert lines.returncode == 0, lines.stdout + lines.stderr
    assert 'core/design.md:' in lines.stdout
    assert 'subsystem' in lines.stdout
    # --lineno includes line numbers but not the line text
    lineno = _wiki(wiki, 'search', 'widget', '--path', str(wiki), '--lineno')
    assert lineno.returncode == 0, lineno.stdout + lineno.stderr
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
    assert field.returncode == 0, field.stdout + field.stderr
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
    assert insensitive.returncode == 0, insensitive.stdout + insensitive.stderr
    assert 'core/design.md' in insensitive.stdout
    # without the flag the uppercase query misses the lowercase body
    sensitive = _wiki(wiki, 'search', 'WIDGET', '--path', str(wiki))
    assert sensitive.returncode == 1
    assert 'No matches' in sensitive.stderr


def test_search_all_includes_non_markdown(wiki: pathlib.Path) -> None:
    """--all widens the search to non-markdown files in the tree."""
    without = _wiki(wiki, 'search', 'widget', '--path', str(wiki))
    with_all = _wiki(wiki, 'search', 'widget', '--path', str(wiki), '--all')
    assert without.returncode == 0, without.stdout + without.stderr
    assert with_all.returncode == 0, with_all.stdout + with_all.stderr
    assert 'snippet.txt' not in without.stdout
    assert 'snippet.txt' in with_all.stdout


def test_search_no_match_exits_nonzero(wiki: pathlib.Path) -> None:
    """A pattern with no hits exits 1 with the notice on stderr.

    The grep convention: scripts distinguish no-match from match by exit
    code, and stdout stays reserved for matches so a page named
    'No matches found.' can never be mistaken for the notice.
    """
    result = _wiki(wiki, 'search', 'zzz_no_such_token', '--path', str(wiki))
    assert result.returncode == 1
    assert 'No matches' in result.stderr
    assert result.stdout == ''


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
    """A malformed regex is an error (exit 2), distinct from a clean no-match.

    Grep reserves exit 2 for errors so a script following the documented
    branch-on-exit-code contract never reads a failed search (bad regex, no
    wiki) as an absent term (exit 1).
    """
    result = _wiki(wiki, 'search', '[', '--path', str(wiki))
    assert result.returncode == 2
    assert 'error' in (result.stdout + result.stderr).lower()


def test_search_resolution_failure_exits_two(
    tmp_path: pathlib.Path,
    wiki: pathlib.Path,
) -> None:
    """A search that cannot resolve its wiki or subtree is an error (exit 2).

    A wiki-less cwd, a missing/out-of-root/excluded subtree argument, and
    an untrusted or broken hook are failed searches, not absent terms;
    exit 1 stays reserved for a clean no-match so the branch-on-exit-code
    contract holds.
    """
    no_wiki = _wiki(tmp_path, 'search', 'widget')
    assert no_wiki.returncode == 2
    assert 'Error:' in no_wiki.stderr
    missing = _wiki(wiki, 'search', 'widget', 'no_such_subtree', '--path', str(wiki))
    assert missing.returncode == 2
    assert 'Error:' in missing.stderr
    # a subtree escaping the root or naming an excluded dot directory
    outside = _wiki(wiki, 'search', 'widget', '../..', '--path', str(wiki))
    assert outside.returncode == 2
    assert 'Error:' in outside.stderr
    excluded = _wiki(wiki, 'search', 'widget', '.wiki', '--path', str(wiki))
    assert excluded.returncode == 2
    assert 'Error:' in excluded.stderr
    # an untrusted .wiki/wiki.py hook is refused, not read as an absent
    # term (the hook never executes, so its broken import is inert here)
    hooked = tmp_path / 'hooked'
    assert _wiki(tmp_path, 'init', 'Hooked', '--path', str(hooked)).returncode == 0
    (hooked / '.wiki' / 'wiki.py').write_text(
        'import nonexistent_module\n',
        encoding='utf-8',
    )
    untrusted = _wiki(hooked, 'search', 'widget', '--path', str(hooked))
    assert untrusted.returncode == 2
    assert 'wiki trust' in untrusted.stderr
    # trusting the root executes the hook; its failure to load is the
    # error leg too, not a no-match
    assert _wiki(hooked, 'trust', '--path', str(hooked)).returncode == 0
    broken = _wiki(hooked, 'search', 'widget', '--path', str(hooked))
    assert broken.returncode == 2
    assert 'Failed to load' in broken.stderr


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
    argnames=('slice_arg', 'expected', 'unexpected'),
    argvalues=[
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
    assert result.returncode == 0, result.stdout + result.stderr
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
    argnames=('name', 'args', 'returncode', 'needle'),
    argvalues=[
        # a slice without a colon is a usage error
        ('core/design', ['--words', 'abc'], 2, 'slice format'),
        # a slice with non-integer bounds is a usage error
        ('core/design', ['--words', 'a:b'], 2, 'slice format'),
        # a missing entry is a clean runtime error, not a traceback
        ('core/missing_entry', [], 1, 'not found'),
        # a name escaping the wiki root is refused, not resolved
        ('../escape', [], 1, 'outside wiki root'),
        # a blank name is not found (it must not resolve to the root index)
        (' ', [], 1, 'not found'),
    ],
    ids=[
        'non-colon-slice',
        'non-integer-slice',
        'missing-entry',
        'escaping-name',
        'blank-name',
    ],
)
def test_read_errors(
    wiki: pathlib.Path,
    name: str,
    args: list[str],
    returncode: int,
    needle: str,
) -> None:
    """A read rejects malformed slices, missing entries, and escaping names."""
    result = _wiki(wiki, 'read', name, '--path', str(wiki), *args)
    assert result.returncode == returncode
    assert needle in (result.stdout + result.stderr).lower()


@pytest.mark.parametrize(
    argnames=('alias', 'long'),
    argvalues=[
        ('-l', '--lines'),
        ('-w', '--words'),
        ('-c', '--chars'),
    ],
    ids=['lines', 'words', 'chars'],
)
def test_read_slice_short_aliases(
    wiki: pathlib.Path,
    alias: str,
    long: str,
) -> None:
    """Read's ``-l``/``-w``/``-c`` slice aliases match their long forms.

    The slice specs are the tool's highest-frequency interactive flags,
    so read keeps their short aliases -- each letter unique across the
    whole CLI, assigned to the command where it earns its keystrokes.
    """
    short = _wiki(wiki, 'read', 'core/design', alias, '0:2', '--path', str(wiki))
    spelled = _wiki(wiki, 'read', 'core/design', long, '0:2', '--path', str(wiki))
    assert short.returncode == 0, short.stdout + short.stderr
    assert short.stdout == spelled.stdout


@pytest.mark.parametrize(
    argnames='args',
    argvalues=[
        ['search', 'widget', '-l'],
        ['search', 'widget', '-n'],
        ['map', '-c', 'guides'],
    ],
    ids=['search-lines', 'search-lineno', 'map-category'],
)
def test_colliding_short_flags_are_rejected(
    wiki: pathlib.Path,
    args: list[str],
) -> None:
    """Colliding short flags do not exist; only the long options do.

    Every short alias is unique across the entire CLI (read: l/w/c;
    search: f/i/a), so search's ``-l``/``-n`` and map's ``-c`` -- whose
    letters belong to read -- exist only as long options.
    """
    result = _wiki(wiki, *args, '--path', str(wiki))
    assert result.returncode == 2
    assert 'no such option' in (result.stdout + result.stderr).lower()


def test_read_outputs_bytes_verbatim(wiki: pathlib.Path) -> None:
    """``read`` returns the file byte-for-byte -- no appended newline.

    Redirecting read output must round-trip: a page ending in a single
    newline stays a single newline.
    """
    page = wiki / 'core' / 'design.md'
    result = _wiki(wiki, 'read', 'core/design', '--path', str(wiki))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == page.read_text(encoding='utf-8')


# ------ config


def test_config_applies_obsidian(tmp_path: pathlib.Path) -> None:
    """Config enables the plugin and writes its settings into ``.obsidian/``.

    The plugin download is skipped here (see ``_wiki``) so the suite stays
    offline; the live fetch is covered by ``test_config_downloads_plugin``.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    result = _wiki(root, 'config', '--path', str(root))
    assert result.returncode == 0, result.stdout + result.stderr
    # the plugin is enabled and its curated settings are written
    plugin_id = 'obsidian-front-matter-title-plugin'
    cp_file = root / '.obsidian' / 'community-plugins.json'
    assert plugin_id in json.loads(cp_file.read_text(encoding='utf-8'))
    assert (root / '.obsidian' / 'plugins' / plugin_id / 'data.json').is_file()


@pytest.mark.online
def test_config_downloads_plugin(tmp_path: pathlib.Path) -> None:
    """With downloads allowed, config fetches the plugin code into the vault.

    Marked ``online`` and excluded by default (``-m 'not online'``); run
    with ``uv run --no-sync pytest -m online`` when online.
    """
    root = tmp_path / 'wiki'
    init = _wiki(tmp_path, 'init', '--path', str(root), allow_download=True)
    assert init.returncode == 0
    result = _wiki(root, 'config', '--path', str(root), allow_download=True)
    assert result.returncode == 0, result.stdout + result.stderr
    # the downloaded plugin code lands in the vault
    plugin = root / '.obsidian' / 'plugins' / 'obsidian-front-matter-title-plugin'
    assert (plugin / 'main.js').is_file()
    assert (plugin / 'manifest.json').is_file()


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_config_adopts_undeclared_tree(tmp_path: pathlib.Path) -> None:
    """``config`` gives an adopted index tree the full setup in one run.

    A hand-built tree (or a wiki whose ``.wiki/`` was lost) has no staged
    Obsidian config, and ``init`` refuses to re-run on it, so config must
    seed ``.wiki/obsidian`` from the stock template, apply it, and still
    register the merge driver instead of aborting on an internal path.
    """

    # a hand-built index tree inside a git repo, never wiki-initialized
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    root = tmp_path / 'kb'
    _write(root / '_index.md', _index('kb', 'Root.', 'Text.'))
    _write(root / 'topic' / '_index.md', _index('topic', 'Topic.', 'Text.'))

    result = _wiki(root, 'config')
    assert result.returncode == 0, result.stdout + result.stderr
    # the staging directory is seeded and applied into the vault
    plugin_id = 'obsidian-front-matter-title-plugin'
    assert (root / '.wiki' / 'obsidian' / 'community-plugins.json').is_file()
    cp_file = root / '.obsidian' / 'community-plugins.json'
    assert plugin_id in json.loads(cp_file.read_text(encoding='utf-8'))
    # the merge driver setup completes: repo config plus attribute map
    driver = _git(tmp_path, 'config', 'merge.wiki.driver').stdout.strip()
    assert driver == 'wiki _merge %O %A %B %L %P'
    attributes = (tmp_path / '.gitattributes').read_text(encoding='utf-8')
    assert '**/_index.md merge=wiki' in attributes.splitlines()


# ------ lint after update


def test_lint_clean_after_update(wiki: pathlib.Path) -> None:
    """A wiki that has just been updated passes lint with exit 0."""
    result = _wiki(wiki, 'lint', '--path', str(wiki))
    assert result.returncode == 0, result.stdout + result.stderr


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

    Per the org's never-auto-commit rule, init writes the attribute map
    to the working tree only; it leaves HEAD and the index untouched.
    """

    # a real repo with one commit so .gitattributes would be brand-new
    _git(tmp_path, 'init', '-q', '-b', 'main')
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    (tmp_path / 'README').write_text('x', encoding='utf-8')
    _git(tmp_path, 'add', 'README')
    _git(tmp_path, 'commit', '-q', '-m', 'init')
    head = _git(tmp_path, 'rev-parse', 'HEAD').stdout

    # init wires the driver: .gitattributes is written but neither staged nor committed
    assert _wiki(tmp_path, 'init', '--path', str(tmp_path / 'wiki')).returncode == 0
    attributes = (tmp_path / '.gitattributes').read_text(encoding='utf-8')
    assert '**/_index.md merge=wiki' in attributes.splitlines()
    assert _git(tmp_path, 'rev-parse', 'HEAD').stdout == head
    staged = _git(tmp_path, 'diff', '--cached', '--name-only').stdout
    assert '.gitattributes' not in staged


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_driver_merges_authored_frontmatter(tmp_path: pathlib.Path) -> None:
    """Concurrent ``_index.md`` merges keep authored frontmatter from both sides.

    The driver normalizes the regenerated keys and the link block to
    ours on all three inputs, then three-way merges the authored
    remainder -- a whole-file ours resolution would silently revert
    theirs' desc edit (or discard its authored ``title:``) with a clean
    exit.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a hand-authored index so each side's edit is byte-precise
    index = root / 'core' / '_index.md'
    base = (
        '---\n'
        'name: core\n'
        'desc: Original section.\n'
        'category: docs\n'
        'tags: []\n'
        'sources: []\n'
        'created: 2026-01-01T00:00:00Z\n'
        'updated: 2026-01-01T00:00:00Z\n'
        '---\n'
        '\n'
        '# core\n'
        '\n'
        '[[core/design|design]]: The design page.\n'
        '\n'
        '***\n'
        '\n'
        'Body prose.\n'
    )
    _write(index, base)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # theirs edits the authored desc and authors a title (plus
    # regenerated churn of its own)
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    theirs = (
        base.replace('desc: Original section.', 'desc: Edited by theirs.')
        .replace('name: core\n', 'name: core\ntitle: Their Title\n')
        .replace(
            'updated: 2026-01-01T00:00:00Z',
            'updated: 2026-01-02T09:00:00Z',
        )
    )
    _write(index, theirs)
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    # ours carries regenerated churn only (an update re-stamped updated:)
    _git(tmp_path, 'checkout', '-q', 'main')
    ours = base.replace(
        'updated: 2026-01-01T00:00:00Z',
        'updated: 2026-01-03T12:00:00Z',
    )
    _write(index, ours)
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge is clean: theirs' desc and title land (the title is
    # authored, never normalized to ours), ours' regenerated churn wins
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = index.read_text(encoding='utf-8')
    assert 'desc: Edited by theirs.' in merged
    assert 'title: Their Title' in merged
    assert 'updated: 2026-01-03T12:00:00Z' in merged
    assert '<<<<<<<' not in merged

    # a second wave where BOTH sides edit desc and title conflicts like prose
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs2')
    theirs2 = merged.replace('desc: Edited by theirs.', 'desc: Theirs again.').replace(
        'title: Their Title',
        'title: Theirs retitled',
    )
    _write(index, theirs2)
    _git(tmp_path, 'commit', '-q', '-am', 'theirs2')
    _git(tmp_path, 'checkout', '-q', 'main')
    ours2 = merged.replace('desc: Edited by theirs.', 'desc: Ours now.').replace(
        'title: Their Title',
        'title: Ours retitled',
    )
    _write(index, ours2)
    _git(tmp_path, 'commit', '-q', '-am', 'ours2')
    conflicted = _git(tmp_path, 'merge', 'theirs2')
    assert conflicted.returncode != 0
    text = index.read_text(encoding='utf-8')
    assert '<<<<<<<' in text
    assert 'desc: Ours now.' in text
    assert 'desc: Theirs again.' in text
    assert 'title: Ours retitled' in text
    assert 'title: Theirs retitled' in text


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_keeps_frontmatter_when_side_is_mangled(tmp_path: pathlib.Path) -> None:
    """A mangled or BOM'd opener never corrupts the block.

    An unclosed opener leaves that side's frontmatter unextractable; the
    driver treats its whole above-``***`` region as unchanged from base, so
    the other side's block survives the merge exactly once -- neither deleted
    wholesale nor doubled by the mangled side's residual bytes. A BOM'd
    opener is instead tolerated: its frontmatter is extracted and three-way
    merged, and the rebuilt opener drops the BOM -- the authored keys survive
    with no BOM residue.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a hand-authored index so each side's mangle is byte-precise
    index = root / 'core' / '_index.md'
    base = (
        '---\n'
        'name: core\n'
        'desc: Original section.\n'
        'created: 2026-01-01T00:00:00Z\n'
        'updated: 2026-01-01T00:00:00Z\n'
        '---\n'
        '\n'
        '# core\n'
        '\n'
        '[[core/design|design]]: The design page.\n'
        '\n'
        '***\n'
        '\n'
        'Body prose.\n'
    )
    _write(index, base)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # theirs loses its frontmatter closer (an unclosed block extracts as
    # none); ours carries regenerated churn only
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    _write(index, base.replace('---\n\n# core', '\n# core'))
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    ours = base.replace(
        'updated: 2026-01-01T00:00:00Z',
        'updated: 2026-01-03T12:00:00Z',
    )
    _write(index, ours)
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge is clean and ours' block survives -- exactly one block
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = index.read_text(encoding='utf-8')
    assert merged.startswith('---\nname: core\n')
    assert 'updated: 2026-01-03T12:00:00Z' in merged
    assert merged.count('name: core') == 1

    # a second wave where OURS carries a BOM before the opener: the driver
    # tolerates it, three-way merges the frontmatter so theirs' authored edit
    # wins, and rebuilds the opener BOM-free -- no residual block, no BOM
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs2')
    _write(index, merged.replace('desc: Original section.', 'desc: Edited by theirs.'))
    _git(tmp_path, 'commit', '-q', '-am', 'theirs2')
    _git(tmp_path, 'checkout', '-q', 'main')
    _write(index, '\ufeff' + merged)
    _git(tmp_path, 'commit', '-q', '-am', 'ours2')
    merge = _git(tmp_path, 'merge', 'theirs2')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = index.read_text(encoding='utf-8')
    assert merged.startswith('---\nname: core\n')
    assert 'desc: Edited by theirs.' in merged
    # exactly one frontmatter block, with no BOM residue anywhere
    assert merged.count('name: core') == 1
    assert '\ufeff' not in merged


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_dispatches_on_pathname(tmp_path: pathlib.Path) -> None:
    """The kindless ``_merge`` driver routes by the real pathname (%P).

    An ``_index.md`` below a declared wiki root takes the index merge;
    any other pathname -- including an ``_index.md`` outside every wiki
    -- takes git's default text merge (%L honored on both routes).
    """
    fm = (
        '---\nname: core\ndesc: Original.\nupdated: 2026-01-01T00:00:00Z\n---\n'
        '\n# core\n\n***\n\nBody.\n'
    )
    base = tmp_path / 'base'
    ours = tmp_path / 'ours'
    theirs = tmp_path / 'theirs'
    base.write_text(fm, encoding='utf-8')
    ours.write_text(fm, encoding='utf-8')
    theirs.write_text(
        fm.replace('updated: 2026-01-01T00:00:00Z', 'updated: 2026-02-02T00:00:00Z'),
        encoding='utf-8',
    )
    # declared roots for the wiki-owned pathnames below
    _write(tmp_path / 'wiki' / '.wiki' / 'settings.json', '{}\n')
    _write(tmp_path / '-notes' / '.wiki' / 'settings.json', '{}\n')

    # an _index.md pathname: updated is a regenerated key, so ours wins
    args = [str(base), str(ours), str(theirs), '7', 'wiki/core/_index.md']
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'updated: 2026-01-01T00:00:00Z' in ours.read_text(encoding='utf-8')

    # a dash-leading pathname still routes as a pathname, not an option
    ours.write_text(fm, encoding='utf-8')
    args = [str(base), str(ours), str(theirs), '7', '-notes/_index.md']
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'updated: 2026-01-01T00:00:00Z' in ours.read_text(encoding='utf-8')

    # any other pathname: a default text merge takes theirs' line edit
    ours.write_text(fm, encoding='utf-8')
    args = [str(base), str(ours), str(theirs), '7', 'wiki/core/notes.md']
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'updated: 2026-02-02T00:00:00Z' in ours.read_text(encoding='utf-8')

    # an _index.md outside every declared wiki (a site generator's content
    # page): the default text merge, so theirs' line edit still lands
    ours.write_text(fm, encoding='utf-8')
    args = [str(base), str(ours), str(theirs), '7', 'content/_index.md']
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'updated: 2026-02-02T00:00:00Z' in ours.read_text(encoding='utf-8')

    # the marker size flows through to conflict markers on both routes
    ours.write_text(fm.replace('Body.', 'Ours body.'), encoding='utf-8')
    theirs.write_text(fm.replace('Body.', 'Theirs body.'), encoding='utf-8')
    args = [str(base), str(ours), str(theirs), '15', 'wiki/core/_index.md']
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode != 0
    assert '<' * 15 in ours.read_text(encoding='utf-8')


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_driver_skips_non_wiki_index_files(tmp_path: pathlib.Path) -> None:
    """A non-wiki ``_index.md`` merges with git's default, never the driver.

    The committed ``**/_index.md`` attribute matches every so-named file
    in the repo -- e.g. a Hugo content page whose ``***`` is an ordinary
    thematic break. Routing such a file through the index merge would
    resolve everything above its first ``***`` to ours and silently drop
    theirs' committed edits on a clean exit; outside a declared wiki root
    the driver takes the default text merge, so both sides' edits land.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a site generator's content page outside the wiki, with a thematic break
    page = tmp_path / 'content' / '_index.md'
    base = (
        '---\ntitle: Home\n---\n\nWelcome to the site.\n\n***\n\nMore content below.\n'
    )
    _write(page, base)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # theirs edits above the thematic break, ours below it
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    _write(page, base.replace('Welcome to the site.', 'Welcome, edited by theirs.'))
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _write(page, base.replace('More content below.', 'More content, edited by ours.'))
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge is clean and both sides' edits land -- the index merge
    # would have taken ours above *** and dropped theirs' edit silently
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = page.read_text(encoding='utf-8')
    assert 'Welcome, edited by theirs.' in merged
    assert 'More content, edited by ours.' in merged
    assert '<<<<<<<' not in merged


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_conflicts_when_side_loses_separator(tmp_path: pathlib.Path) -> None:
    """A side that lost its ``***`` separator conflicts loudly, never cleanly.

    Without the separator the side's generated bytes and authored edits
    are indistinguishable, so the driver refuses to guess: a whole-file
    conflict with a repair hint above the first marker -- never a clean
    exit that routes the side's frontmatter and link block below ``***``
    as duplicated body content, and never a resolution that drops the
    side's authored edits.
    """
    fm = (
        '---\nname: core\ndesc: Original.\nupdated: 2026-01-01T00:00:00Z\n---\n'
        '\n# core\n\n[[core/design|design]]: The design page.\n\n***\n\nBody.\n'
    )
    base = tmp_path / 'base'
    ours = tmp_path / 'ours'
    theirs = tmp_path / 'theirs'
    base.write_text(fm, encoding='utf-8')
    # a declared root, so the pathname routes to the index merge
    _write(tmp_path / 'wiki' / '.wiki' / 'settings.json', '{}\n')
    args = [str(base), str(ours), str(theirs), '7', 'wiki/core/_index.md']

    # theirs mangled (mdformat *** -> ---) alongside a genuine body edit
    ours.write_text(fm.replace('desc: Original.', 'desc: Ours.'), encoding='utf-8')
    theirs.write_text(
        fm.replace('***', '---').replace('Body.', 'Body.\nTheir paragraph.'),
        encoding='utf-8',
    )
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode != 0
    merged = ours.read_text(encoding='utf-8')
    # no silent duplication: the generated region appears exactly once
    assert merged.count('name: core') == 1
    assert merged.count('[[core/design|design]]') == 1
    # the mangled side's authored edit survives inside the markers
    assert 'Their paragraph.' in merged
    lines = merged.splitlines()
    markers = [i for i, line in enumerate(lines) if line.startswith('<<<<<<<')]
    hint = lines[markers[0] - 1]
    assert hint.startswith('<!--')
    assert 'separator missing' in hint
    assert 'wiki update' in hint
    # comment innards never contain '--' (corruption under strict parsers)
    assert '--' not in hint.removeprefix('<!--').removesuffix('-->')

    # ours mangled: the same refusal, with ours' authored edit intact
    ours.write_text(
        fm.replace('***', '---').replace('Body.', 'Body.\nOur paragraph.'),
        encoding='utf-8',
    )
    theirs.write_text(fm.replace('desc: Original.', 'desc: Theirs.'), encoding='utf-8')
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode != 0
    merged = ours.read_text(encoding='utf-8')
    assert merged.count('name: core') == 1
    assert 'Our paragraph.' in merged
    assert 'separator missing' in merged


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_hints_add_add_body_conflicts(tmp_path: pathlib.Path) -> None:
    """An add/add body conflict gains a one-line hint above the markers.

    Sibling branches authoring the same new directory's index body hit
    conflict markers below ``***`` by design; the driver plants an HTML
    comment naming the empty-bodies-until-merged convention in situ.
    With empty bodies the sides differ only in their seeded ``created:``
    stamps -- wiki update churn on both -- so the merge resolves clean.
    The hint keys on the ancestor file being absent, not on its body
    being empty (the state every freshly generated index is in).
    """
    fm = (
        '---\nname: core\ndesc: Section.\ncreated: 2026-01-01T00:00:11Z\n---\n'
        '\n# core\n\n***\n\n'
    )
    theirs_fm = fm.replace(
        'created: 2026-01-01T00:00:11Z',
        'created: 2026-01-01T00:00:13Z',
    )
    base = tmp_path / 'base'
    ours = tmp_path / 'ours'
    theirs = tmp_path / 'theirs'
    # add/add: git hands the driver an empty base file
    base.write_text('', encoding='utf-8')
    # a declared root, so the pathname routes to the index merge
    _write(tmp_path / 'wiki' / '.wiki' / 'settings.json', '{}\n')
    args = [str(base), str(ours), str(theirs), '7', 'wiki/core/_index.md']

    # empty bodies leave only the created: stamps apart, and ours' stamp
    # wins like any regenerated key instead of conflicting
    ours.write_text(fm, encoding='utf-8')
    theirs.write_text(theirs_fm, encoding='utf-8')
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode == 0, result.stdout + result.stderr
    merged = ours.read_text(encoding='utf-8')
    assert 'created: 2026-01-01T00:00:11Z' in merged
    assert '<<<<<<<' not in merged

    # authored bodies conflict by design, hinted above the markers
    ours.write_text(fm + 'Ours body.\n', encoding='utf-8')
    theirs.write_text(theirs_fm + 'Theirs body.\n', encoding='utf-8')
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode != 0
    lines = ours.read_text(encoding='utf-8').splitlines()
    markers = [i for i, line in enumerate(lines) if line.startswith('<<<<<<<')]
    hint = lines[markers[0] - 1]
    assert hint.startswith('<!--')
    assert 'empty' in hint
    # comment innards never contain '--' (corruption under strict parsers)
    assert '--' not in hint.removeprefix('<!--').removesuffix('-->')

    # a conflict with a real common ancestor is ordinary -- no hint
    base.write_text(fm + 'Base body.\n', encoding='utf-8')
    ours.write_text(fm + 'Ours body.\n', encoding='utf-8')
    theirs.write_text(fm + 'Theirs body.\n', encoding='utf-8')
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode != 0
    assert '<!--' not in ours.read_text(encoding='utf-8')

    # an ancestor whose body is empty (a generated index ends right at
    # ***) is still a real ancestor -- no hint
    base.write_text(fm.removesuffix('\n'), encoding='utf-8')
    ours.write_text(fm + 'Ours body.\n', encoding='utf-8')
    theirs.write_text(theirs_fm + 'Theirs body.\n', encoding='utf-8')
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode != 0
    assert '<!--' not in ours.read_text(encoding='utf-8')


# ------ version


def test_version_flag_reports_a_version(tmp_path: pathlib.Path) -> None:
    """``wiki --version`` prints the package's own version and exits 0.

    The first-install smoke test for a distributed CLI: an eager root
    option, so it resolves before any command. The subprocess imports
    this worktree's package, so the output must equal its
    ``__version__`` -- the code that runs, not install-time dist-info.
    """
    result = _wiki(tmp_path, '--version')
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == WIKI_VERSION


# ------ trust


def test_trust_gates_hook_execution(tmp_path: pathlib.Path) -> None:
    """A ``.wiki/wiki.py`` hook runs only after ``wiki trust``.

    An untrusted hook makes a resolving command refuse -- naming the hook
    and pointing at ``wiki trust`` -- without executing it; trusting the
    root lets the same command run. The suite's ``WIKI_CONFIG_DIR`` keeps
    the trust store hermetic across the subprocess calls.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', 'Trusted', '--path', str(root)).returncode == 0
    # a benign custom-subclass hook
    (root / '.wiki' / 'wiki.py').write_text(
        'from wiki.core.wiki import Wiki\n\n'
        'class MyWiki(Wiki):\n    pass\n\n'
        "__all__ = ['MyWiki']\n",
        encoding='utf-8',
    )
    # a resolving command refuses the untrusted hook, naming the fix
    refused = _wiki(root, 'map', '--path', str(root))
    assert refused.returncode != 0
    assert 'wiki trust' in refused.stderr
    # trusting the root lets the same command run
    trusted = _wiki(root, 'trust', '--path', str(root))
    assert trusted.returncode == 0, trusted.stdout + trusted.stderr
    assert 'Trusted wiki' in trusted.stdout
    allowed = _wiki(root, 'map', '--path', str(root))
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr


def test_trust_refuses_non_wiki_path(tmp_path: pathlib.Path) -> None:
    """``wiki trust`` never records a path that is not a wiki.

    Trust pre-authorizes a root's ``.wiki/wiki.py`` to run arbitrary
    code, so a typo'd or not-yet-created path must error instead of
    being silently added to the store.
    """
    result = _wiki(tmp_path, 'trust', '--path', str(tmp_path / 'nope'))
    assert result.returncode == 1
    assert 'No wiki at' in result.stderr


def test_trust_store_does_not_mark_home_as_wiki_root(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default ``~/.wiki`` trust store never turns ``$HOME`` into a wiki root.

    With ``WIKI_CONFIG_DIR`` unset the store lives at
    ``~/.wiki/settings.json`` -- the same shape as the root marker -- so
    root detection exempts the config home; otherwise one ``wiki trust``
    run would make every wiki under the home directory resolve as nested
    inside a wiki at ``$HOME``.
    """
    monkeypatch.delenv('WIKI_CONFIG_DIR')
    root = tmp_path / 'projects' / 'wiki'
    assert _wiki(tmp_path, 'init', 'Home', '--path', str(root)).returncode == 0
    # trust writes the default store under $HOME (the runner pins it to cwd)
    assert _wiki(tmp_path, 'trust', '--path', str(root)).returncode == 0
    # the wiki nested under $HOME still resolves (same home as the trust run)
    result = _wiki(root, 'update', '--path', str(root), home=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_trust_store_exemption_survives_symlinked_home(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The config-home exemption holds when ``$HOME`` is a symlink.

    Root detection resolves candidate paths, while the trust store's
    home is spelled from ``$HOME`` as given -- so with a symlinked home
    (an automounted or relocated home directory) the exemption must
    compare resolved paths; otherwise one ``wiki trust`` run declares
    the physical home directory itself a wiki root and commands
    resolving from inside it sweep the entire home tree.
    """
    monkeypatch.delenv('WIKI_CONFIG_DIR')
    physical = tmp_path / 'physical_home'
    physical.mkdir()
    home = tmp_path / 'home'
    home.symlink_to(physical)
    root = physical / 'projects' / 'wiki'
    init = _wiki(tmp_path, 'init', 'Home', '--path', str(root), home=home)
    assert init.returncode == 0, init.stdout + init.stderr
    # trust writes the default store under the symlinked $HOME
    assert _wiki(tmp_path, 'trust', '--path', str(root), home=home).returncode == 0
    # resolving from cwd walks the resolved (physical) ancestor chain;
    # the store must stay exempt rather than shadow the project root
    result = _wiki(root, 'update', home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (physical / '_index.md').exists()


# ------ helpers


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
