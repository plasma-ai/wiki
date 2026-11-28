"""End-to-end tests for the ``wiki`` CLI command matrix.

Drives the real ``wiki`` console script as a subprocess against a throwaway
wiki built with two folders (``core``, ``guides``) and a handful of pages.
The suite covers every sub-command -- init, install, update, new, lint,
map, search, match, read, config, trust, and the hidden ``_merge`` driver --
plus ``--version``, exercising option behavior, exit codes, and error
reporting as observable output rather than internal state.
"""

from __future__ import annotations

import datetime as dt
import errno
import json
import os
import pathlib
import re
import shutil
import subprocess
from typing import Optional

import pytest

from wiki import __version__ as WIKI_VERSION
from wiki.cli.utils import configure_git_merge_driver
from wiki.constants import DEFAULT_SEARCH_LIMIT, WIKI_CONFIG_DIR, WIKI_FREEZE_TIME

from .._helpers import _needs_unprivileged
from .conftest import GIT, WIKI, _env, _git, _git_repo, _wiki

__all__ = [
    'test_command_errors_exit_two',
    'test_empty_path_is_a_usage_error',
    'test_empty_path_error_order',
    'test_untrusted_hook_refusal_exits_two',
    'test_update_check_separates_pending_from_error',
    'test_page_that_is_not_utf8_stops_a_command_naming_the_byte',
    'test_json_file_that_is_not_utf8_is_malformed_at_the_byte',
    'test_init_creates_root_index',
    'test_init_guards_existing_wiki',
    'test_init_seeds_settings',
    'test_init_says_when_settings_are_not_applied',
    'test_init_refuses_nested_wiki',
    'test_home_directory_is_never_a_wiki',
    'test_init_quiet_suppresses_chatter',
    'test_settings_file_refuses_text_that_is_not_strict_json',
    'test_init_refuses_settings_that_are_not_strict_json',
    'test_settings_nesting_is_refused_from_128_levels',
    'test_settings_file_that_cannot_be_read_is_named_with_its_cause',
    'test_lost_settings_file_that_git_holds_stops_every_sweep',
    'test_missing_settings_file_that_git_does_not_hold_is_recreated',
    'test_malformed_settings_block_stops_every_policy_command',
    'test_settings_are_validated_before_the_scope',
    'test_timestamp_format_verdict_ignores_the_clock_and_the_local_zone',
    'test_malformed_block_spares_read_config_and_the_merge_driver',
    'test_unknown_settings_keys_draw_one_warning_each',
    'test_settings_warnings_are_lint_notes',
    'test_settings_warnings_stay_out_of_readers_and_merges',
    'test_install_copies_skill_into_home',
    'test_install_project_targets_cwd',
    'test_install_link_swaps_copy_and_symlink',
    'test_update_generates_child_links',
    'test_update_prunes_broken_link',
    'test_update_check_reports_changes_without_writing',
    'test_update_check_full_words_each_change_as_pending',
    'test_update_names_the_page_of_an_overwrite_a_hook_announces',
    'test_update_noop_reports_nothing_to_update',
    'test_update_failed_entry_mutates_nothing',
    'test_update_narrations_condense_by_default',
    'test_update_condenses_batch_adoption',
    'test_new_requires_authored_desc_and_content',
    'test_new_refuses_an_argument_that_is_not_utf8',
    'test_update_counts_files_with_malformed_frontmatter',
    'test_update_names_a_lone_skip_and_counts_a_repeated_kind',
    'test_update_counts_paths_git_ignores_as_lint_does',
    'test_new_refuses_interior_path',
    'test_read_only_commands_are_deterministic',
    'test_path_inside_wiki_resolves_upward',
    'test_path_inside_undeclared_wiki_resolves_upward',
    'test_raw_subfolder_of_undeclared_wiki_resolves_upward',
    'test_bare_invocation_agrees_with_path_dot',
    'test_standalone_wiki_is_never_absorbed_from_above',
    'test_islanded_wiki_refusal_names_both_fixes',
    'test_path_naming_nested_declared_wiki_resolves_to_itself',
    'test_parent_enclosing_declared_wiki_is_refused',
    'test_update_cli_refuses_nested_wiki',
    'test_update_refuses_a_scope_inside_a_nested_wiki',
    'test_update_refuses_an_excluded_dot_directory_scope',
    'test_scope_that_is_not_found_suggests_the_fix',
    'test_exclude_patterns_end_to_end',
    'test_links_external_end_to_end',
    'test_update_cli_refuses_conflict_markers',
    'test_lint_reports_issue_taxonomy_and_exits_nonzero',
    'test_lint_summary_counts_notes',
    'test_lint_condenses_repeated_notes',
    'test_lint_json_reports_typed_findings',
    'test_lint_types_resolver_diagnostics',
    'test_lint_error_exits_two',
    'test_lint_error_keeps_the_notes_before_it',
    'test_lint_details_issues_and_count_condenses',
    'test_map_respects_view_options',
    'test_map_filters_by_category',
    'test_map_empty_wiki_reports_empty',
    'test_map_says_no_entries_match_when_narrowed_to_nothing',
    'test_map_stat_and_desc_limit_bounds',
    'test_match_output_modes',
    'test_match_field_and_ignore_case',
    'test_match_all_includes_non_markdown',
    'test_match_no_match_exits_nonzero',
    'test_match_line_flags_are_mutually_exclusive',
    'test_match_invalid_regex_reports_error',
    'test_match_explains_an_unescaped_wikilink',
    'test_match_hints_follow_the_pattern',
    'test_match_reports_no_line_past_the_end',
    'test_naming_pattern_prints_no_line_for_a_risky_regex',
    'test_match_resolution_failure_exits_two',
    'test_match_all_skips_undecodable_files',
    'test_match_names_a_page_it_cannot_decode',
    'test_search_ranked_and_json_output',
    'test_search_no_match_exits_nonzero',
    'test_search_json_prints_an_empty_list_for_no_match',
    'test_search_invalid_query_reports_error',
    'test_search_prefix_and_raw_are_mutually_exclusive',
    'test_search_limit_must_be_positive',
    'test_number_options_take_plain_digits_only',
    'test_number_arguments_accept_a_sign_and_leading_zeros',
    'test_number_options_are_labeled_integer',
    'test_read_slice_forms',
    'test_read_lines_count_as_match_numbers_them',
    'test_read_resolves_dotted_page_name',
    'test_read_errors',
    'test_read_checks_its_slice_before_the_wiki',
    'test_read_slice_short_aliases',
    'test_colliding_short_flags_are_rejected',
    'test_read_outputs_bytes_verbatim',
    'test_piped_output_keeps_a_pages_escape_sequences',
    'test_help_and_errors_hold_no_escape_byte',
    'test_config_applies_obsidian',
    'test_config_downloads_plugin',
    'test_config_adopts_undeclared_tree',
    'test_lint_clean_after_update',
    'test_merge_driver_no_op_without_git',
    'test_init_writes_gitattributes_without_committing',
    'test_init_and_config_append_to_gitattributes_that_is_not_utf8',
    'test_merge_driver_merges_authored_frontmatter',
    'test_merge_driver_moves_regenerated_keys_with_their_bodies',
    'test_merge_driver_takes_the_other_sides_row_edit',
    'test_merge_driver_normalizes_created_over_a_stampless_base',
    'test_merge_driver_keeps_the_later_updated_stamp',
    'test_merge_driver_keeps_the_earlier_created_over_a_stampless_base',
    'test_merge_driver_keeps_a_row_edit_on_a_crlf_index',
    'test_merge_driver_unions_rows_of_an_add_add_merge',
    'test_merge_driver_leaves_a_shared_blank_line_alone',
    'test_merge_driver_carries_separators_and_comments_verbatim',
    'test_merge_driver_merges_comments_as_authored_lines',
    'test_merge_driver_replaces_a_block_body_whole',
    'test_merge_orders_rows_as_update_writes_them',
    'test_merge_keeps_each_sides_deletions',
    'test_merge_carries_a_merged_title_into_the_h1',
    'test_merge_orders_rows_by_the_wikis_hook',
    'test_merge_leaves_an_index_another_class_wrote',
    'test_merge_keeps_a_row_one_side_changed_and_the_other_deleted',
    'test_merge_leaves_a_non_utf8_index_as_merged',
    'test_merge_leaves_a_conflicted_index_as_merged',
    'test_merge_reads_an_escaped_sides_rows',
    'test_merge_keeps_frontmatter_when_side_is_mangled',
    'test_merge_dispatches_on_pathname',
    'test_merge_driver_takes_no_help_option',
    'test_merge_settles_the_stamps_of_a_managed_page',
    'test_merge_of_a_restamped_page_is_clean',
    'test_markdown_outside_the_wiki_keeps_its_own_merge',
    'test_merge_without_the_wiki_cli_falls_back_to_a_text_merge',
    'test_merge_driver_skips_non_wiki_index_files',
    'test_merge_conflicts_when_side_loses_separator',
    'test_merge_hints_add_add_body_conflicts',
    'test_version_flag_reports_a_version',
    'test_short_help_flag_prints_the_help_page',
    'test_usage_error_points_at_the_long_help_flag',
    'test_wiki_with_no_command_prints_help_as_a_usage_error',
    'test_help_marks_a_required_option_once',
    'test_search_help_states_the_default_limit',
    'test_trust_gates_hook_execution',
    'test_trust_refuses_non_wiki_path',
    'test_trust_refuses_a_store_that_is_not_utf8_naming_the_byte',
    'test_trust_store_does_not_mark_home_as_wiki_root',
    'test_trust_store_exemption_survives_symlinked_home',
    'test_root_resolution_survives_a_symlink_loop_home',
    'test_wiki_freeze_time_fixes_every_stamp',
    'test_wiki_freeze_time_stamps_the_trust_record',
    'test_blank_wiki_freeze_time_leaves_the_clock_running',
    'test_invalid_wiki_freeze_time_fails_a_command_before_it_acts',
    'test_wiki_freeze_time_is_read_when_a_command_runs',
    'test_merge_driver_never_reads_wiki_freeze_time',
]

# a hook ordering the ref category ahead of guide
_ORDERED_HOOK = (
    'from wiki.core.wiki import Wiki\n\n'
    "__all__ = ['Ordered']\n\n\n"
    'class Ordered(Wiki):\n'
    "    category_order = ['ref', 'guide']\n"
)

# a hook that sorts the current branch's rows, then raises on the merged
# ones, where the other side's api row has joined them
_LATE_RAISING_HOOK = (
    'from wiki.core.wiki import Wiki\n\n'
    "__all__ = ['Late']\n\n\n"
    'class Late(Wiki):\n'
    '    def _sort_links(self, links):\n'
    "        if any(target == 'core/api' for target, _, _ in links):\n"
    "            raise RuntimeError('broken hook')\n"
    '        return super()._sort_links(links)\n'
)

# a hook whose construction exits the process, as one calling sys.exit does
_EXITING_HOOK = (
    'from wiki.core.wiki import Wiki\n\n'
    "__all__ = ['Exiting']\n\n\n"
    'class Exiting(Wiki):\n'
    '    def __init__(self, *args, **kwargs):\n'
    '        raise SystemExit(0)\n'
)

# a hook that loads, then raises when it sorts rows
_RAISING_HOOK = (
    'from wiki.core.wiki import Wiki\n\n'
    "__all__ = ['Broken']\n\n\n"
    'class Broken(Wiki):\n'
    '    def _sort_links(self, links):\n'
    "        raise RuntimeError('broken hook')\n"
)

# a hook whose lint notes two placeholder descs, then raises
_NOTING_HOOK = (
    'from wiki.core.wiki import Wiki\n\n'
    "__all__ = ['Noting']\n\n\n"
    'class Noting(Wiki):\n'
    '    def lint(self, name=None):\n'
    "        self.on_desc_missing(path='a.md')\n"
    "        self.on_desc_missing(path='b.md')\n"
    "        raise RuntimeError('boom')\n"
)

# a hook whose update announces an overwritten desc of its own, by entry and
# index alone
_ANNOUNCING_HOOK = (
    'from wiki.core.wiki import Wiki\n\n'
    "__all__ = ['Announcing']\n\n\n"
    'class Announcing(Wiki):\n'
    '    def update(self, name=None, *, check=False):\n'
    "        entry = {'target': 'core/keep', 'label': 'keep'}\n"
    "        self.on_desc_overwrite(path='core/_index.md', **entry)\n"
    '        return super().update(name, check=check)\n'
)

# a hook whose plan is followed by another writer's edit to each core/page*.md
_RACING_HOOK = (
    'from wiki.core.wiki import Wiki\n\n'
    "__all__ = ['Racing']\n\n\n"
    'class Racing(Wiki):\n'
    '    def _plan(self, folder, **kwargs):\n'
    '        result = super()._plan(folder, **kwargs)\n'
    "        for page in self._root.glob('core/page*.md'):\n"
    "            with page.open('a', encoding='utf-8') as file:\n"
    "                file.write('Edited.\\n')\n"
    '        return result\n'
)

# the line a command opens stderr with while WIKI_FREEZE_TIME fixes the clock
_PINNED_CLOCK_NOTICE = (
    'WIKI_FREEZE_TIME is set: timestamps use its fixed instant, not the clock.\n'
)

# the one error an invalid WIKI_FREEZE_TIME gets, formatted with the repr
# of the value
_PINNED_CLOCK_ERROR = (
    'Error: WIKI_FREEZE_TIME must be an RFC 3339 instant in the UTC years'
    ' 1001 to 9998, like 2026-01-02T03:04:05Z, got {now}.\n'
)

# the texts ``json`` reads and strict JSON does not hold, each with the
# reason its refusal words, for the settings file and ``init --settings`` alike
_not_strict_json = pytest.mark.parametrize(
    argnames=('text', 'reason'),
    argvalues=[
        # constants
        ('{"x": NaN}', 'NaN is not a JSON number'),
        ('{"x": Infinity}', 'Infinity is not a JSON number'),
        ('{"x": -Infinity}', '-Infinity is not a JSON number'),
        # numbers past the range of a double
        ('{"x": 1e400}', 'number too large to represent'),
        ('{"x": 1' + '0' * 400 + '}', 'number too large to represent'),
        # surrogate escapes with no pair
        ('{"x": "\\ud800"}', "lone surrogate '\\ud800' in a string"),
        ('{"\\udc00": 1}', "lone surrogate '\\udc00' in a string"),
        # a key named twice in one object, at the top level and in a block
        (
            '{"exclude": {"patterns": ["secret"]}, "exclude": {}}',
            "duplicate key 'exclude'",
        ),
        ('{"naming": {"allow": "-", "allow": ""}}', "duplicate key 'allow'"),
        # an object holding 127 arrays, one inside the next
        ('{"x": ' + '[' * 127 + ']' * 127 + '}', 'nested more than 127 levels deep'),
    ],
    ids=[
        'nan',
        'infinity',
        'negative-infinity',
        'float-too-large',
        'integer-too-large',
        'surrogate-in-value',
        'surrogate-in-key',
        'key-twice-at-top-level',
        'key-twice-in-a-block',
        'nested-128-levels',
    ],
)

# the line match prints for a pattern holding an unescaped [[
_NESTED_SET_HINT = (
    "An unescaped '[[' opens a character class; match a wikilink with '\\[\\['."
)

# the line match prints for a doubled set operator inside a character class
_SET_OPERATOR_HINT = (
    "A doubled '--', '&&', '~~' or '||' inside a character class is not a set"
    ' operation; escape each character to match it as written.'
)

# the line a scaffolding init prints offline, where the suite runs it
_OFFLINE_SKIP = (
    'Skipped obsidian-front-matter-title-plugin download (OFFLINE_MODE). Re-run'
    ' `wiki config` online to finish setup.\n'
)

# the line the resolver prints for a wiki whose settings file is missing,
# formatted with the wiki root
_MISSING_SETTINGS_NOTICE = (
    '{root}: .wiki/settings.json missing; restore it from git, or run'
    ' `wiki update` to recreate it with default settings\n'
)

# the error for a missing settings file that git still holds
_LOST_SETTINGS_ERROR = (
    'Error: .wiki/settings.json is missing, but git still holds it. Restore it'
    ' from git and rerun: the file is recreated with default settings only'
    ' when git does not hold it.\n'
)

# one run of each command that reads policy, each with something to do in
# the wiki the settings tests build
_POLICY_COMMANDS = (
    ('update',),
    ('update', '--check'),
    ('new', 'notes', '--desc', 'Notes.', '--content', 'Body.'),
    ('lint',),
    ('map',),
    ('search', 'design'),
    ('match', 'design'),
)

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
    READ-ONLY by convention for authored content: the few tests that
    must add a file to the shared tree remove it in a ``finally`` block,
    so siblings observe the arrangement unchanged. Product-derived cache
    state under ``.wiki/`` (the search tests' ``search.db``) is
    sanctioned residue -- the tool owns and refreshes it, so it never
    changes what siblings observe.
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


# ------ exit codes


@pytest.mark.parametrize(
    argnames='args',
    argvalues=[
        ('update',),
        ('update', '--check'),
        ('lint',),
        ('map',),
        ('match', 'widget'),
        ('read', 'design'),
        ('new', 'orphan', '--desc', 'A folder.', '--content', 'Body.'),
    ],
    ids=['update', 'update-check', 'lint', 'map', 'match', 'read', 'new'],
)
def test_command_errors_exit_two(tmp_path: pathlib.Path, args: tuple[str, ...]) -> None:
    """An unresolvable wiki is exit 2 in every command, never exit 1.

    Exit 1 belongs to each command's own nonzero outcome -- pending
    changes, issues found, no match -- so a gate branching on one can
    never read a typo'd ``--path`` as that outcome.
    """
    result = _wiki(tmp_path, *args, '--path', str(tmp_path / 'nowhere'))
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Error:' in result.stderr


@pytest.mark.parametrize(
    argnames='args',
    argvalues=[
        ('init',),
        ('config',),
        ('trust',),
        ('read', 'core/design'),
        ('search', 'design'),
        ('match', 'design'),
        ('update',),
        ('update', '--check'),
        ('new', 'notes', '--desc', 'Notes.', '--content', 'Body.'),
        ('lint',),
        ('map',),
    ],
    ids=[
        'init',
        'config',
        'trust',
        'read',
        'search',
        'match',
        'update',
        'update-check',
        'new',
        'lint',
        'map',
    ],
)
def test_empty_path_is_a_usage_error(
    tmp_path: pathlib.Path,
    args: tuple[str, ...],
) -> None:
    """``--path ""`` is a usage error in every command that takes ``--path``.

    A script with an unset variable (``--path "$WIKI"``) passes the empty
    value. Read as no ``--path``, it would send the command to the wiki
    the current folder resolves to, which is not the one the script named.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a page no index lists yet, so the default wiki has work pending
    _write(root / 'core' / 'design.md', _page('Design', 'A design.', 'Body.'))
    before = _tree(tmp_path)
    result = _wiki(tmp_path, *args, '--path', '')
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ''
    assert result.stderr.endswith('Error: Invalid value: --path must not be empty.\n')
    assert _tree(tmp_path) == before


@pytest.mark.parametrize(
    argnames=('args', 'error'),
    argvalues=[
        (
            ('read', 'core/design', '--lines', 'x'),
            'Invalid value: --path must not be empty.',
        ),
        (
            ('read', 'core/design', '--lines', '1:2', '--words', '1:2'),
            'Invalid value: --path must not be empty.',
        ),
        (
            ('search', 'design', '--limit', 'x'),
            "Invalid value for '--limit': 'x' is not a valid integer.",
        ),
    ],
    ids=['bad-slice', 'exclusive-pair', 'bad-number'],
)
def test_empty_path_error_order(
    tmp_path: pathlib.Path,
    args: tuple[str, ...],
    error: str,
) -> None:
    """An empty ``--path`` is the first error a command reports of its own.

    A run with two bad arguments names one of them. The empty ``--path``
    comes ahead of every check a command makes itself, the slice of
    ``read`` included, and behind a value the option parser refuses,
    which ends the run before the command starts.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    result = _wiki(tmp_path, *args, '--path', '')
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ''
    assert result.stderr.endswith(f'Error: {error}\n')


def test_untrusted_hook_refusal_exits_two(tmp_path: pathlib.Path) -> None:
    """The untrusted-hook refusal is exit 2 in every command alike.

    The refusal is a command error, not any command's own nonzero
    outcome, so ``lint``'s issues-found exit and ``update --check``'s
    pending exit stay unmistakable beside it -- no command reads a
    refused hook as its own result.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    (root / '.wiki' / 'wiki.py').write_text('__all__ = []\n', encoding='utf-8')
    for args in (('update',), ('lint',), ('map',), ('read', '_index')):
        result = _wiki(root, *args, '--path', str(root))
        assert result.returncode == 2, (args, result.stdout + result.stderr)
        assert 'untrusted wiki hook' in result.stderr


def test_update_check_separates_pending_from_error(tmp_path: pathlib.Path) -> None:
    """``update --check`` exits 1 for pending changes and 2 for an error.

    The pair is the ambiguity a CI gate turns on: pending drift is a
    result to rerun without ``--check``, while a bad subtree is a
    broken invocation to fix.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / 'design.md', _page('Design', 'A design.', 'Body.'))
    # pending drift: the sweep is the fix
    pending = _wiki(root, 'update', '--check', '--path', str(root))
    assert pending.returncode == 1, pending.stdout + pending.stderr
    assert 'would change' in pending.stdout
    # a bad subtree entry: the invocation is the fix
    entry = _wiki(root, 'update', '--check', 'nowhere', '--path', str(root))
    assert entry.returncode == 2, entry.stdout + entry.stderr
    assert 'Error:' in entry.stderr


@pytest.mark.parametrize(
    argnames=('valid', 'rest'),
    argvalues=[
        (b'', b'\xff# Design\n'),
        ('# Café\n\nBody '.encode(), b'\xff text.\n'),
        (b'# Design\n\nBody ends mid-character: caf', b'\xc3'),
        (b'# Design\n\n' + b'Body text. ' * 1000, b'\xff\n'),
    ],
    ids=['first-byte', 'middle', 'cut-off-final-character', 'past-one-read-buffer'],
)
def test_page_that_is_not_utf8_stops_a_command_naming_the_byte(
    tmp_path: pathlib.Path,
    valid: bytes,
    rest: bytes,
) -> None:
    """A page that is not valid UTF-8 stops a command with its path and the byte.

    The error leads with the page's path, gives the offset of the first
    byte that is not UTF-8 -- a count of bytes, not characters, so an
    editor's go-to-byte lands on it -- and names the remedy. The words
    are wiki's own: the interpreter's sentence for the failure, which
    names its codec, never prints.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    (root / 'core').mkdir()
    (root / 'core' / 'design.md').write_bytes(valid + rest)

    # every command that reads the page stops on it, with the one error line
    error = (
        f'Error: core/design.md: not valid UTF-8 at byte {len(valid)};'
        ' re-save the file as UTF-8'
    )
    commands = (
        ('read', 'core/design'),
        ('update',),
        ('update', '--check'),
        ('lint',),
        ('lint', '--json'),
    )
    for args in commands:
        result = _wiki(root, *args, '--path', str(root))
        assert result.returncode == 2, (args, result.stdout + result.stderr)
        assert result.stderr.splitlines() == [error], args
        assert 'codec' not in result.stdout + result.stderr, args


@pytest.mark.parametrize(
    argnames=('name', 'args'),
    argvalues=[
        ('.wiki/settings.json', ('update',)),
        ('.wiki/settings.json', ('lint',)),
        ('.obsidian/app.json', ('config',)),
        ('.wiki/obsidian/community-plugins.json', ('config',)),
    ],
    ids=['settings-update', 'settings-lint', 'obsidian-config', 'staged-config'],
)
def test_json_file_that_is_not_utf8_is_malformed_at_the_byte(
    tmp_path: pathlib.Path,
    name: str,
    args: tuple[str, ...],
) -> None:
    """A settings or Obsidian JSON file that is not UTF-8 is malformed at a byte.

    The file is refused by name, whether it is the vault's own or the
    copy staged under ``.wiki/obsidian/``, and the reason is the offset
    of its first invalid byte in wiki's own words, never the
    interpreter's sentence naming its codec.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a Latin-1 e-acute, where UTF-8 text holds two bytes for the letter
    valid = b'{"note": "caf'
    (root / name).write_bytes(valid + b'\xe9"}\n')

    result = _wiki(root, *args, '--path', str(root))
    assert result.returncode == 2, result.stdout + result.stderr
    error = f'Error: Malformed JSON in {name}: not valid UTF-8 at byte {len(valid)}'
    assert result.stderr.splitlines() == [error]
    assert 'codec' not in result.stdout + result.stderr


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


def test_init_says_when_settings_are_not_applied(tmp_path: pathlib.Path) -> None:
    """``init --settings`` on an existing wiki says so when it changes nothing.

    ``init`` never changes the settings of an existing wiki and exits 0, so
    one stderr line, printed under ``--quiet`` too, tells a caller that the
    policy it passed is not in force. The same object draws nothing, in
    any key order and spacing and under another wiki name, so a caller
    that sends its seed again on every run stays silent. A settings file
    that is missing or cannot be read holds something else.
    """
    root = tmp_path / 'wiki'
    seed = '{"naming":{"validate":["ascii"],"allow":"-"},"lint":{"strict":true}}'
    line = (
        '--settings was not applied: init leaves the settings of the wiki at'
        f' {root} as they are.\n'
    )
    init = _wiki(tmp_path, 'init', '--path', str(root), '--quiet', '--settings', seed)
    assert init.returncode == 0, init.stdout + init.stderr
    assert line not in init.stderr
    settings = root / '.wiki' / 'settings.json'
    written = settings.read_bytes()

    def reinit(*args: str) -> str:
        """Run ``init --quiet`` on the wiki again and return its stderr."""
        result = _wiki(tmp_path, 'init', '--path', str(root), '--quiet', *args)
        assert result.returncode == 0, (args, result.stdout + result.stderr)
        assert result.stdout == '', args
        return result.stderr

    # the same object, however it is spelled, is in force
    reordered = (
        '{ "lint": {"strict": true},\n'
        '  "naming": {"allow": "-", "validate": ["ascii"]} }'
    )
    assert reinit('--settings', seed) == ''
    assert reinit('--settings', reordered) == ''
    assert reinit('Other Name', '--settings', seed) == ''
    assert reinit() == ''
    # another object is not, and the file stays as it was
    assert reinit('--settings', '{}') == line
    assert reinit('--settings', '{"lint": {"strict": 1}}') == line
    assert settings.read_bytes() == written
    loud = _wiki(tmp_path, 'init', '--path', str(root), '--settings', '{}')
    assert loud.returncode == 0, loud.stdout + loud.stderr
    assert loud.stdout == f'Wiki already initialized at: {root}\n'
    assert loud.stderr == line

    # a wiki that lost its root index is rebuilt around the settings it has
    (root / '_index.md').unlink()
    assert reinit('--settings', seed) == _OFFLINE_SKIP
    (root / '_index.md').unlink()
    assert reinit('--settings', '{}') == line + _OFFLINE_SKIP
    assert settings.read_bytes() == written

    # a file that cannot be read, or is missing, holds something else
    _write(settings, '{"lint": {"strict": NaN}}')
    assert reinit('--settings', seed) == line
    settings.unlink()
    assert reinit('--settings', '{}') == line
    assert not settings.exists()


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
    assert result.returncode == 2
    assert str(outer) in result.stdout + result.stderr
    assert not (outer / 'inner').exists()

    # the default {cwd}/wiki path is refused the same way from inside a wiki
    result = _wiki(outer, 'init')
    assert result.returncode == 2
    assert str(outer) in result.stdout + result.stderr
    assert not (outer / 'wiki').exists()

    # an indexed subfolder is refused as nested, not "already initialized"
    _write(outer / 'topics' / '_index.md', _index('Topics', 'Topic guides.', 'Text.'))
    result = _wiki(tmp_path, 'init', '--path', str(outer / 'topics'))
    combined = result.stdout + result.stderr
    assert result.returncode == 2
    assert str(outer) in combined
    assert 'already initialized' not in combined.lower()

    # with the outer marker lost, the bare index chain still names the
    # enclosing wiki and the refusal holds
    shutil.rmtree(outer / '.wiki')
    result = _wiki(tmp_path, 'init', '--path', str(outer / 'newsub'))
    assert result.returncode == 2
    assert str(outer) in result.stdout + result.stderr
    assert not (outer / 'newsub').exists()


def test_home_directory_is_never_a_wiki(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trust store at ``~/.wiki`` never makes ``$HOME`` a wiki root.

    The store shares its path with a declared root's marker, so a home
    directory holding one would enclose every wiki beneath it and refuse
    every command. Pointing ``WIKI_CONFIG_DIR`` elsewhere leaves that
    store in place -- the case of a process isolated under its own config
    home -- so the exemption cannot key on the active config home alone.
    Init is refused there too: a wiki at ``$HOME`` would write its policy
    into the store.
    """
    home = tmp_path / 'home'
    (home / '.wiki').mkdir(parents=True)
    store = home / '.wiki' / 'settings.json'
    store.write_text('{"trusted": {}}\n', encoding='utf-8')
    root = home / 'project' / 'wiki'
    root.mkdir(parents=True)
    assert _wiki(tmp_path, 'init', '--path', str(root), home=home).returncode == 0

    # a wiki below HOME resolves whether the config home is the default
    # (where the exemption already held) or redirected away from it
    for config_dir in (home / '.wiki', tmp_path / 'elsewhere'):
        monkeypatch.setenv('WIKI_CONFIG_DIR', str(config_dir))
        result = _wiki(root, 'map', home=home)
        assert result.returncode == 0, result.stdout + result.stderr

    # HOME itself is refused, leaving the store and the tree untouched
    refused = _wiki(tmp_path, 'init', '--path', str(home), home=home)
    assert refused.returncode == 2
    assert 'home directory' in refused.stdout + refused.stderr
    assert not (home / '_index.md').exists()
    assert json.loads(store.read_text(encoding='utf-8')) == {'trusted': {}}


def test_init_quiet_suppresses_chatter(tmp_path: pathlib.Path) -> None:
    """``init --quiet`` suppresses the Obsidian hint and non-error output.

    A wrapping tool (e.g. another init command) needs to place its own
    next-step guidance last; --quiet keeps stdout empty while warnings
    still reach stderr and the wiki is fully scaffolded.
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


# ------ settings file


@_not_strict_json
def test_settings_file_refuses_text_that_is_not_strict_json(
    tmp_path: pathlib.Path,
    text: str,
    reason: str,
) -> None:
    """A settings file that is not strict JSON stops a command before it acts.

    ``json`` reads every one of these texts and a reader held to the JSON
    grammar reads none, so the file is refused with its cause worded
    rather than read one way here and another way elsewhere.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a page awaiting adoption, so a sweep that ran would show
    _write(root / 'core' / 'design.md', _page('Design', 'A design.', 'Body.'))
    _write(root / '.wiki' / 'settings.json', text)
    before = _tree(root)

    # each command that reads policy says the one line and writes nothing
    error = f'Error: Malformed JSON in .wiki/settings.json: {reason}\n'
    for args in (('update', '--check'), ('update',), ('lint',), ('map',)):
        result = _wiki(tmp_path, *args, '--path', str(root))
        assert result.returncode == 2, (args, result.stdout + result.stderr)
        assert result.stderr == error, args
        assert _tree(root) == before, args


@_not_strict_json
def test_init_refuses_settings_that_are_not_strict_json(
    tmp_path: pathlib.Path,
    text: str,
    reason: str,
) -> None:
    """``init --settings`` refuses what a settings file is refused for.

    The seed is held to the reader's own rules, so ``init`` never writes
    a settings file the next command refuses.
    """
    root = tmp_path / 'wiki'
    result = _wiki(tmp_path, 'init', '--path', str(root), '--settings', text)
    assert result.returncode == 2, result.stdout + result.stderr
    assert f'--settings must be valid JSON ({reason}).' in result.stderr
    assert not root.exists()


def test_settings_nesting_is_refused_from_128_levels(tmp_path: pathlib.Path) -> None:
    """Settings nested 127 levels deep are read; one level more is refused.

    The depth is counted on the text before it is parsed, so the limit is
    the same on every interpreter, whatever its stack allows.
    """
    limit = 127
    # an object holding arrays, one inside the next
    accepted = '{"x": ' + '[' * (limit - 1) + ']' * (limit - 1) + '}'
    refused = '{"x": ' + '[' * limit + ']' * limit + '}'
    root = tmp_path / 'wiki'

    # the deepest seed is written, and the file it makes is read back
    seeded = _wiki(tmp_path, 'init', '--path', str(root), '--settings', accepted)
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    listed = _wiki(tmp_path, 'map', '--path', str(root))
    assert listed.returncode == 0, listed.stdout + listed.stderr

    # one level more stops both readers
    _write(root / '.wiki' / 'settings.json', refused)
    listed = _wiki(tmp_path, 'map', '--path', str(root))
    assert listed.returncode == 2, listed.stdout + listed.stderr
    assert 'nested more than 127 levels deep' in listed.stderr
    fresh = tmp_path / 'fresh'
    seeded = _wiki(tmp_path, 'init', '--path', str(fresh), '--settings', refused)
    assert seeded.returncode == 2, seeded.stdout + seeded.stderr
    assert 'nested more than 127 levels deep' in seeded.stderr
    assert not fresh.exists()


@pytest.mark.parametrize(
    argnames=('fault', 'reason'),
    argvalues=[
        pytest.param('no-permission', 'Permission denied', marks=_needs_unprivileged),
        ('folder', 'not a regular file'),
        ('looped-symlink', os.strerror(errno.ELOOP)),
        ('byte-order-mark', 'it starts with a byte-order mark; remove the mark'),
    ],
    ids=['no-permission', 'folder', 'looped-symlink', 'byte-order-mark'],
)
def test_settings_file_that_cannot_be_read_is_named_with_its_cause(
    tmp_path: pathlib.Path,
    fault: str,
    reason: str,
) -> None:
    """A settings file that yields no text is refused in one line with its cause.

    The line names the file by its path in the wiki, and a path that holds
    a folder or a symlink loop is never called missing: nothing a command
    could restore is absent there.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    settings = root / '.wiki' / 'settings.json'
    if fault == 'no-permission':
        os.chmod(settings, 0o000)
    elif fault == 'folder':
        settings.unlink()
        settings.mkdir()
    elif fault == 'looped-symlink':
        settings.unlink()
        settings.symlink_to(settings.name)
    else:
        settings.write_bytes(b'\xef\xbb\xbf{}\n')

    # each command that reads policy says the one line
    error = f'Error: Cannot read .wiki/settings.json ({reason}).\n'
    try:
        for args in (('update', '--check'), ('update',), ('lint',), ('map',)):
            result = _wiki(tmp_path, *args, '--path', str(root))
            assert result.returncode == 2, (args, result.stdout + result.stderr)
            assert result.stderr == error, args
    finally:
        if fault == 'no-permission':
            os.chmod(settings, 0o600)


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames='state',
    argvalues=[
        'deleted',
        'git-rm',
        'index-only',
        'sparse-checkout',
        'skip-worktree',
        'assume-unchanged',
        'unmerged',
    ],
)
def test_lost_settings_file_that_git_holds_stops_every_sweep(
    tmp_path: pathlib.Path,
    state: str,
) -> None:
    """A settings file lost from the working tree is not replaced while git holds it.

    A sweep without the file runs on default settings and adopts what its
    ``exclude.patterns`` kept out, so each command that plans a sweep
    refuses and writes nothing, the dry ones too. HEAD holds the file
    after a ``git rm``, as the index does after a plain delete, before a
    first commit, and at the stages of an unmerged path. A file that
    ``git status`` leaves out is held all the same: one a sparse checkout
    does not check out, or one marked skip-worktree or assume-unchanged.
    The commands that only read still run.
    """
    _git_repo(tmp_path)
    root = tmp_path / 'wiki'
    seed = json.dumps({'exclude': {'patterns': ['secret']}})
    seeded = _wiki(tmp_path, 'init', '--path', str(root), '--settings', seed)
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    _write(root / 'core' / 'design.md', _page('design', 'A design.', 'Body.'))
    # a bare page the pattern keeps out of the wiki
    _write(root / 'secret' / 'raw.md', 'Raw notes.\n')
    assert _wiki(tmp_path, 'update', '--path', str(root)).returncode == 0
    assert _git(tmp_path, 'add', '-A').returncode == 0
    settings = root / '.wiki' / 'settings.json'
    tracked = settings.relative_to(tmp_path).as_posix()
    if state != 'index-only':
        assert _git(tmp_path, 'commit', '-q', '-m', 'wiki').returncode == 0
    if state == 'git-rm':
        assert _git(tmp_path, 'rm', '-q', tracked).returncode == 0
    elif state == 'sparse-checkout':
        # every path but the wiki's own .wiki/ folder stays checked out
        patterns = ('/*', '!/wiki/.wiki/')
        sparse = _git(tmp_path, 'sparse-checkout', 'set', '--no-cone', *patterns)
        assert sparse.returncode == 0, sparse.stderr
    elif state in ('skip-worktree', 'assume-unchanged'):
        marked = _git(tmp_path, 'update-index', f'--{state}', tracked)
        assert marked.returncode == 0, marked.stderr
    elif state == 'unmerged':
        # one side changes the file and the other removes it, so the merge
        # stops with the path at its unmerged stages: HEAD lacks it
        assert _git(tmp_path, 'checkout', '-q', '-b', 'other').returncode == 0
        _write(settings, json.dumps({'exclude': {'patterns': ['secret', 'raw']}}))
        assert _git(tmp_path, 'commit', '-q', '-am', 'policy').returncode == 0
        assert _git(tmp_path, 'checkout', '-q', 'main').returncode == 0
        assert _git(tmp_path, 'rm', '-q', tracked).returncode == 0
        assert _git(tmp_path, 'commit', '-q', '-m', 'no settings').returncode == 0
        assert _git(tmp_path, 'merge', 'other').returncode == 1
    settings.unlink(missing_ok=True)
    assert not settings.exists()
    # a bare page awaiting adoption, so a sweep that ran would show
    _write(root / 'core' / 'draft.md', 'A draft.\n')
    # config wires the merge driver into the repository's own git config,
    # outside the wiki root: with the wiring removed, a run that reached it
    # would show there
    assert _git(tmp_path, 'config', '--remove-section', 'merge.wiki').returncode == 0
    wiring = _git(tmp_path, 'config', '--local', '--list').stdout
    before = _tree(root)

    # each command that plans a sweep refuses, and the tree stays as it was:
    # no settings file, no adopted page, nothing indexed under secret/
    notice = _MISSING_SETTINGS_NOTICE.format(root=root)
    commands = (
        ('update',),
        ('update', '--check'),
        ('new', 'notes', '--desc', 'Notes.', '--content', 'Body.'),
        ('lint',),
        ('config',),
    )
    for args in commands:
        result = _wiki(tmp_path, *args, '--path', str(root))
        assert result.returncode == 2, (args, result.stdout + result.stderr)
        assert result.stderr == notice + _LOST_SETTINGS_ERROR, args
        assert _tree(root) == before, args
    assert not settings.exists()
    assert not (root / 'secret' / '_index.md').exists()
    assert _git(tmp_path, 'config', '--local', '--list').stdout == wiring
    # init leaves a wiki whose root index stands as it is
    result = _wiki(tmp_path, 'init', '--path', str(root))
    assert result.returncode == 0, result.stdout + result.stderr
    assert _tree(root) == before

    # the commands that only read still run
    for args in (('read', 'core/design'), ('map',)):
        result = _wiki(tmp_path, *args, '--path', str(root))
        assert result.returncode == 0, (args, result.stdout + result.stderr)
        assert result.stderr == notice, args

    # init refuses where it would write: on a wiki whose root index is gone
    # too, and on one whose whole folder is
    (root / '_index.md').unlink()
    before = _tree(root)
    result = _wiki(tmp_path, 'init', '--path', str(root))
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stderr == _LOST_SETTINGS_ERROR
    assert _tree(root) == before
    shutil.rmtree(root)
    result = _wiki(tmp_path, 'init', '--path', str(root))
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stderr == _LOST_SETTINGS_ERROR
    assert not root.exists()

    # a new wiki is judged by its own path, whatever its name matches as a glob
    fresh = tmp_path / 'wik[ia]'
    result = _wiki(tmp_path, 'init', 'notes', '--path', str(fresh))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames='state',
    argvalues=[
        'no-repository',
        'never-committed',
        'removal-committed',
        'dangling-symlink',
        'unreadable-repository',
    ],
)
def test_missing_settings_file_that_git_does_not_hold_is_recreated(
    tmp_path: pathlib.Path,
    state: str,
) -> None:
    """A missing settings file git holds no copy of is recreated as ``{}``.

    Nothing can be restored from git here, so a writing ``update`` writes
    the file with default settings and says so, and a dry run leaves it
    missing. Committing the file's removal is how a wiki gives up its
    settings for the defaults. A symlink to nothing that git does not
    track is a missing file too, and a repository git cannot read holds
    nothing it can answer for.
    """
    if state != 'no-repository':
        _git_repo(tmp_path)
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / 'design.md', _page('design', 'A design.', 'Body.'))
    assert _wiki(tmp_path, 'update', '--path', str(root)).returncode == 0
    settings = root / '.wiki' / 'settings.json'
    if state in ('never-committed', 'dangling-symlink'):
        assert _git(tmp_path, 'add', '--', str(root / 'core')).returncode == 0
        assert _git(tmp_path, 'commit', '-q', '-m', 'pages').returncode == 0
    elif state in ('removal-committed', 'unreadable-repository'):
        assert _git(tmp_path, 'add', '-A').returncode == 0
        assert _git(tmp_path, 'commit', '-q', '-m', 'wiki').returncode == 0
    if state == 'removal-committed':
        assert _git(tmp_path, 'rm', '-q', '--', str(settings)).returncode == 0
        assert _git(tmp_path, 'commit', '-q', '-m', 'no settings').returncode == 0
    elif state == 'unreadable-repository':
        _write(tmp_path / '.git' / 'HEAD', 'not a ref\n')
    settings.unlink(missing_ok=True)
    if state == 'dangling-symlink':
        settings.symlink_to('nowhere.json')

    # a dry run names the missing file and leaves it missing
    notice = _MISSING_SETTINGS_NOTICE.format(root=root)
    # a run in a repository git cannot read says so for the gitignore fence
    if state == 'unreadable-repository':
        notice += (
            'Gitignore fence unavailable: `git check-ignore` failed inside the'
            ' enclosing repository (is git installed?); indexing proceeds'
            ' unfenced, so paths the repository ignores are adopted\n'
        )
    check = _wiki(tmp_path, 'update', '--check', '--path', str(root))
    assert check.returncode == 0, check.stdout + check.stderr
    assert check.stderr == notice
    assert not settings.exists()

    # a writing run recreates it with default settings
    result = _wiki(tmp_path, 'update', '--path', str(root))
    assert result.returncode == 0, result.stdout + result.stderr
    restored = 'Restored missing .wiki/settings.json ({} -- all defaults)\n'
    assert result.stderr == notice + restored
    assert settings.read_text(encoding='utf-8') == '{}\n'


@pytest.mark.parametrize(
    argnames=('settings', 'message'),
    argvalues=[
        (
            {'naming': {'min_length': 5, 'max_length': 2}},
            'naming.min_length (5) must not exceed naming.max_length (2)',
        ),
        (
            {'timestamp': {'timezone': 'Mars/Olympus'}},
            "Unknown timestamp.timezone 'Mars/Olympus'",
        ),
        (
            {'timestamp': {'format': '%Y %Y'}},
            "timestamp.format must read back the stamp it writes ('2023 2023'"
            ' does not), got "%Y %Y"',
        ),
        (
            {'map': {'desc_limit': 'ten'}},
            'map.desc_limit must be an int >= -1 or null, got "ten"',
        ),
        (
            {'titles': {'required': 'yes'}},
            'titles.required must be a boolean, got "yes"',
        ),
        ({'lint': {'strict': 1}}, 'lint.strict must be a boolean, got 1'),
        (
            {'exclude': {'patterns': ['!keep']}},
            "Invalid exclude.patterns pattern '!keep': negation ('!') is not supported",
        ),
        (
            {'links': {'external': '../src'}},
            'links.external must be a list of folder paths, got "../src"',
        ),
    ],
    ids=[
        'naming',
        'timestamp',
        'timestamp-format',
        'map',
        'titles',
        'lint',
        'exclude',
        'links',
    ],
)
def test_malformed_settings_block_stops_every_policy_command(
    tmp_path: pathlib.Path,
    settings: dict,
    message: str,
) -> None:
    """A malformed block stops every command that reads policy, alike.

    Each command checks the whole settings file before it acts, whichever
    blocks it goes on to use: the same line and exit code from all of
    them, and nothing written. A length minimum above the maximum is one
    such block: no name could pass it, and a sweep would prune every row.
    A timestamp format that cannot parse its own stamps is another: no
    page is stamped under it, and lint reports the format, not each stamp.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    _write(root / 'core' / 'design.md', _page('Design', 'A design.', 'Body.'))
    assert _wiki(tmp_path, 'update', '--path', str(root)).returncode == 0
    # a page awaiting adoption, so a sweep that ran would show
    _write(root / 'core' / 'draft.md', 'A draft.\n')
    _write(root / '.wiki' / 'settings.json', json.dumps(settings))
    before = _tree(root)

    error = f'Error: {message} in .wiki/settings.json.\n'
    for args in _POLICY_COMMANDS:
        result = _wiki(tmp_path, *args, '--path', str(root))
        assert result.returncode == 2, (args, result.stdout + result.stderr)
        assert result.stderr == error, args
        assert _tree(root) == before, args


def test_settings_are_validated_before_the_scope(tmp_path: pathlib.Path) -> None:
    """A malformed block is reported ahead of a scope that names no folder."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / '.wiki' / 'settings.json', '{"map": []}')

    error = 'Error: The map block must be a JSON object in .wiki/settings.json.\n'
    for args in (
        ('update', 'nowhere'),
        ('lint', 'nowhere'),
        ('map', 'nowhere'),
        ('search', 'design', 'nowhere'),
        ('match', 'design', 'nowhere'),
    ):
        result = _wiki(tmp_path, *args, '--path', str(root))
        assert result.returncode == 2, (args, result.stdout + result.stderr)
        assert result.stderr == error, args


@pytest.mark.parametrize(
    argnames='env',
    argvalues=[
        {},
        {'TZ': 'Pacific/Kiritimati'},
        {WIKI_FREEZE_TIME: '2026-01-05T22:30:00Z'},
        {'TZ': 'America/St_Johns', WIKI_FREEZE_TIME: '2026-01-05T22:30:00Z'},
    ],
    ids=['ambient', 'local-zone', 'frozen-clock', 'both'],
)
def test_timestamp_format_verdict_ignores_the_clock_and_the_local_zone(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
) -> None:
    """A timestamp format gets one verdict, whenever and wherever a command runs.

    The read-back check renders two fixed instants in the wiki's own zone
    and never reads the clock or the machine's zone. The refused format
    here names the hour twice, and the error shows the same stamp under
    each: the first fixed instant, at the wiki zone's date and hour.
    """
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    notice = _PINNED_CLOCK_NOTICE if WIKI_FREEZE_TIME in env else ''
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    settings = root / '.wiki' / 'settings.json'

    timestamp = {'format': '%Y-%m-%d %H %H', 'timezone': 'Europe/Moscow'}
    _write(settings, json.dumps({'timestamp': timestamp}))
    refused = _wiki(tmp_path, 'map', '--path', str(root))
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert refused.stderr == (
        f'{notice}Error: timestamp.format must read back the stamp it writes'
        ' (\'2023-11-26 01 01\' does not), got "%Y-%m-%d %H %H" in'
        ' .wiki/settings.json.\n'
    )

    timestamp = {'format': '%d.%m.%Y %H:%M', 'timezone': 'Europe/Moscow'}
    _write(settings, json.dumps({'timestamp': timestamp}))
    accepted = _wiki(tmp_path, 'map', '--path', str(root))
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert accepted.stderr == notice


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_malformed_block_spares_read_config_and_the_merge_driver(
    tmp_path: pathlib.Path,
) -> None:
    """``read``, ``config`` and a merge run on a wiki with a block they do not use.

    Each reads only the blocks it needs: a page stays readable, the
    repair command runnable, and two branches that each edit and restamp
    a page still merge on the later stamp, while ``map`` is refused.
    """
    root = tmp_path / 'wiki'
    _git_repo(tmp_path)
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    page = root / 'notes.md'
    base = (
        '---\nname: notes\ndesc: Notes.\nupdated: 2026-01-01T00:00:00Z\n---\n'
        '\n# notes\n\nFirst line.\n\nMiddle line.\n\nLast line.\n'
    )
    _write(page, base)
    _write(root / '.wiki' / 'settings.json', '{"map": {"desc_limit": "ten"}}')
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')
    assert _wiki(tmp_path, 'map', '--path', str(root)).returncode == 2

    # a page reads, and config installs
    read = _wiki(tmp_path, 'read', 'notes', '--path', str(root))
    assert read.returncode == 0, read.stdout + read.stderr
    assert read.stdout == base
    config = _wiki(tmp_path, 'config', '--path', str(root))
    assert config.returncode == 0, config.stdout + config.stderr

    # each branch edits its own line and restamps the page
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    theirs = base.replace('2026-01-01T00:00:00Z', '2026-01-03T00:00:00Z')
    _write(page, theirs.replace('Last line.', 'Their last line.'))
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    ours = base.replace('2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')
    _write(page, ours.replace('First line.', 'Our first line.'))
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = page.read_text(encoding='utf-8')
    assert re.findall(r'^updated:.*$', merged, re.M) == [
        'updated: 2026-01-03T00:00:00Z'
    ]
    assert 'Our first line.' in merged
    assert 'Their last line.' in merged


def test_unknown_settings_keys_draw_one_warning_each(tmp_path: pathlib.Path) -> None:
    """A settings key the wiki does not know is named, and still ignored.

    ``init``, ``update``, ``update --check``, ``new`` and ``lint`` each
    open their stderr with one line per unknown key, a key inside a block
    as a dotted path, in the order the keys stand in the file. ``new``
    runs an update inside and says each line once. A warning changes no
    exit code and no written file: the misspelled exclusion is not applied.
    """
    root = tmp_path / 'wiki'
    settings = (
        '{"nameing": {}, "exclude": {"pattern": ["drafts"], "patterns": []},'
        ' "lint": {"strick": true}, "links": {}}'
    )
    warnings = (
        ".wiki/settings.json: unknown key 'nameing' (ignored)\n"
        ".wiki/settings.json: unknown key 'exclude.pattern' (ignored)\n"
        ".wiki/settings.json: unknown key 'lint.strick' (ignored)\n"
    )
    init = _wiki(tmp_path, 'init', '--path', str(root), '--settings', settings)
    assert init.returncode == 0, init.stdout + init.stderr
    assert init.stderr.startswith(warnings)
    assert init.stderr.count('unknown key') == 3
    _write(root / 'drafts' / 'idea.md', 'An idea.\n')

    # the dry run names the page the misspelled exclusion was meant to keep out
    check = _wiki(tmp_path, 'update', '--check', '--path', str(root))
    assert check.returncode == 1, check.stdout + check.stderr
    assert check.stderr.startswith(warnings)
    assert check.stderr.count('unknown key') == 3
    assert 'Would update: drafts/idea.md\n' in check.stdout
    update = _wiki(tmp_path, 'update', '--path', str(root))
    assert update.returncode == 0, update.stdout + update.stderr
    assert update.stderr.startswith(warnings)
    assert update.stderr.count('unknown key') == 3

    new = _wiki(
        tmp_path,
        'new',
        'notes',
        '--desc',
        'Notes.',
        '--content',
        'Body.',
        '--path',
        str(root),
    )
    assert new.returncode == 0, new.stdout + new.stderr
    assert new.stderr == warnings + 'Added 1 new link\n'
    lint = _wiki(tmp_path, 'lint', '--full', '--path', str(root))
    assert lint.returncode == 0, lint.stdout + lint.stderr
    assert lint.stderr.startswith(warnings)
    assert lint.stderr.count('unknown key') == 3


def test_settings_warnings_are_lint_notes(tmp_path: pathlib.Path) -> None:
    """Lint carries a settings warning as a note, never as an issue.

    The ``--json`` document types each warning ``settings_warning`` with
    the file and the dotted key, and the run exits 0 when warnings are all
    it found -- under ``lint.strict`` too, which makes issues of stale
    links alone. ``--count`` shows the warnings in its summary and prints
    no line for them.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    index = root / '_index.md'
    text = index.read_text(encoding='utf-8')
    text = text.replace('desc: ...', 'desc: The root.').replace(
        '***\n', '***\n\nText.\n'
    )
    _write(index, text)
    settings = '{"lint": {"strict": true, "strick": true}, "review": "weekly"}'
    _write(root / '.wiki' / 'settings.json', settings)

    document = _wiki(tmp_path, 'lint', '--json', '--path', str(root))
    assert document.returncode == 0, document.stdout + document.stderr
    assert document.stderr == ''
    assert json.loads(document.stdout) == {
        'issues': [],
        'notes': [
            {
                'severity': 'note',
                'kind': 'settings_warning',
                'path': '.wiki/settings.json',
                'key': 'lint.strick',
                'reason': "unknown key 'lint.strick' (ignored)",
                'text': ".wiki/settings.json: unknown key 'lint.strick' (ignored)",
            },
            {
                'severity': 'note',
                'kind': 'settings_warning',
                'path': '.wiki/settings.json',
                'key': 'review',
                'reason': "unknown key 'review' (ignored)",
                'text': ".wiki/settings.json: unknown key 'review' (ignored)",
            },
        ],
        'summary': {'issues': 0, 'notes': 2},
    }
    prose = _wiki(tmp_path, 'lint', '--path', str(root))
    assert prose.returncode == 0, prose.stdout + prose.stderr
    assert prose.stdout == 'No issues found (2 notes).\n'
    assert prose.stderr == (
        ".wiki/settings.json: unknown key 'lint.strick' (ignored)\n"
        ".wiki/settings.json: unknown key 'review' (ignored)\n"
    )
    count = _wiki(tmp_path, 'lint', '--count', '--path', str(root))
    assert count.returncode == 0, count.stdout + count.stderr
    assert count.stdout == 'No issues found (2 notes).\n'
    assert count.stderr == ''


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_settings_warnings_stay_out_of_readers_and_merges(
    tmp_path: pathlib.Path,
) -> None:
    """Only the commands that maintain a wiki warn about its settings file.

    ``read``, ``search``, ``match``, ``map`` and ``config`` print no
    warning, and neither does a merge through the driver, which builds the
    engine several times for each merged file. A wiki that another wiki's
    ``links.external`` admits keeps its warnings for its own runs.
    """
    root = tmp_path / 'wiki'
    _git_repo(tmp_path)
    settings = '{"review": "weekly", "links": {"external": ["../manual"]}}'
    init = _wiki(tmp_path, 'init', '--path', str(root), '--settings', settings)
    assert init.returncode == 0, init.stdout + init.stderr
    manual = tmp_path / 'manual'
    seed = '{"exclude": {"pattern": []}}'
    init = _wiki(tmp_path, 'init', '--path', str(manual), '--settings', seed)
    assert init.returncode == 0, init.stdout + init.stderr
    _write(manual / 'glossary.md', _page('Glossary', 'Glossary.', 'Body.'))
    _write(root / 'notes.md', _page('Notes', 'Notes.', 'See [[../manual/glossary]].'))
    assert _wiki(tmp_path, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    for args in (
        ('read', 'notes'),
        ('search', 'notes'),
        ('match', 'Notes'),
        ('map',),
        ('config',),
    ):
        result = _wiki(tmp_path, *args, '--path', str(root))
        assert result.returncode == 0, (args, result.stdout + result.stderr)
        assert 'unknown key' not in result.stderr, args
    # lint reads the linked wiki's exclusions and says only its own warning
    lint = _wiki(tmp_path, 'lint', '--full', '--path', str(root))
    assert lint.returncode == 0, lint.stdout + lint.stderr
    assert lint.stderr.count('unknown key') == 1
    assert ".wiki/settings.json: unknown key 'review' (ignored)\n" in lint.stderr

    # each branch adds a page, so the merge runs the driver on the root index
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    _write(root / 'theirs.md', _page('Theirs', 'Their page.', 'Body.'))
    assert _wiki(tmp_path, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _write(root / 'ours.md', _page('Ours', 'Our page.', 'Body.'))
    assert _wiki(tmp_path, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'ours')
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    assert 'unknown key' not in merge.stdout + merge.stderr
    assert {'ours', 'theirs'} <= set(_row_labels((root / '_index.md').read_text()))


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


# ------ update


def test_update_generates_child_links(wiki: pathlib.Path) -> None:
    """An update wires each folder index to its children and parent."""
    core_index = (wiki / 'core' / '_index.md').read_text(encoding='utf-8')
    root_index = (wiki / '_index.md').read_text(encoding='utf-8')
    # the root links down to the folders, the folder links to its page
    assert '[[core/_index|core/]]' in root_index
    assert '[[guides/_index|guides/]]' in root_index
    assert '[[core/design|design]]' in core_index


def test_update_prunes_broken_link(tmp_path: pathlib.Path) -> None:
    """Update prunes a stale link while keeping the live ones."""
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
    # update drops the dangle while keeping the live link
    result = _wiki(root, 'update', '--path', str(root))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Pruned 1 broken link' in result.stderr
    after = index.read_text(encoding='utf-8')
    assert '[[core/gone|gone]]' not in after
    assert '[[core/keep|keep]]' in after
    # a --prune flag does not exist: pruning is the behavior, not a mode
    assert _wiki(root, 'update', '--path', str(root), '--prune').returncode == 2


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


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_update_check_full_words_each_change_as_pending(tmp_path: pathlib.Path) -> None:
    """``update --check --full`` says what would change, never that it did.

    A dry run writes nothing, so each change line reads ``Would ...``;
    the applied run narrates the same plan as done work.
    """
    root = tmp_path / 'wiki'
    _git_repo(tmp_path)
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('core', 'Core concepts.', 'Text.'))
    _write(root / 'core' / 'keep.md', _page('keep', 'Stays.', 'Body.'))
    _write(root / 'core' / 'gone.md', _page('gone', 'Removed soon.', 'Body.'))
    _write(root / 'core' / 'edited.md', _page('edited', 'Edited later.', 'Body.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    assert _git(tmp_path, 'commit', '-q', '-m', 'base').returncode == 0

    # one pending change of each kind: a bare page to adopt, a folder with no
    # index, a row whose page is gone, a row desc edited away from its page,
    # and a committed page edited by hand well after its stamp
    _write(root / 'core' / 'bare.md', '# Bare\n\nBody.\n')
    _write(root / 'guides' / 'setup.md', _page('setup', 'A guide.', 'Body.'))
    (root / 'core' / 'gone.md').unlink()
    index = root / 'core' / '_index.md'
    text = index.read_text(encoding='utf-8')
    index.write_text(text.replace('Stays.', 'Edited in the index.'), encoding='utf-8')
    edited = root / 'core' / 'edited.md'
    edited.write_text(edited.read_text(encoding='utf-8') + 'More.\n', encoding='utf-8')
    later = edited.stat().st_mtime + 300
    os.utime(edited, (later, later))
    before = _tree(root)

    # the dry run words every change as pending and writes nothing
    check = _wiki(root, 'update', '--check', '--full', '--path', str(root))
    assert check.returncode == 1, check.stdout + check.stderr
    assert sorted(check.stderr.splitlines()) == sorted(
        [
            'Would adopt bare page: core/bare.md (would seed title: from its H1)',
            'Would create index: guides/_index.md',
            'Would add link: [[core/bare|bare]] in core/_index.md',
            'Would add link: [[guides/setup|setup]] in guides/_index.md',
            'Would add link: [[guides/_index|guides/]] in _index.md',
            'Would prune link: [[core/gone|gone]] from core/_index.md',
            'Would overwrite desc: [[core/keep|keep]] in core/_index.md (the page'
            ' frontmatter desc wins; edit it in core/keep.md)',
            'Would restamp core/edited.md: edited since its updated: stamp',
        ]
    )
    assert _tree(root) == before

    # the applied run narrates the same changes as done
    applied = _wiki(root, 'update', '--full', '--path', str(root))
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert sorted(applied.stderr.splitlines()) == sorted(
        [
            'Adopted bare page: core/bare.md (frontmatter added; title: seeded'
            ' from its H1)',
            'New index: guides/_index.md (fill in its desc)',
            'New link: [[core/bare|bare]] in core/_index.md',
            'New link: [[guides/setup|setup]] in guides/_index.md',
            'New link: [[guides/_index|guides/]] in _index.md',
            'Pruned link: [[core/gone|gone]] from core/_index.md',
            'Overwrote desc: [[core/keep|keep]] in core/_index.md (the page'
            ' frontmatter desc wins; edit it in core/keep.md)',
            'Restamped core/edited.md: edited since its updated: stamp',
        ]
    )


def test_update_names_the_page_of_an_overwrite_a_hook_announces(
    tmp_path: pathlib.Path,
) -> None:
    """An overwritten-desc notice built without its page still names the file to edit.

    A hook that announces an overwrite itself passes the entry and its
    index. The line names the target plus ``.md``, in a writing run and in
    a dry run.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / '.wiki' / 'wiki.py', _ANNOUNCING_HOOK)
    assert _wiki(tmp_path, 'trust', '--path', str(root)).returncode == 0
    entry = '[[core/keep|keep]] in core/_index.md'
    place = '(the page frontmatter desc wins; edit it in core/keep.md)'

    # the dry run words the hook's notice as pending, the writing run as done
    check = _wiki(root, 'update', '--check', '--full', '--path', str(root))
    assert f'Would overwrite desc: {entry} {place}' in check.stderr.splitlines()
    applied = _wiki(root, 'update', '--full', '--path', str(root))
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert f'Overwrote desc: {entry} {place}' in applied.stderr.splitlines()


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
    assert result.returncode == 2
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

    # a pruned link condenses to a count line
    (root / 'core' / 'api.md').unlink()
    broken = _wiki(root, 'update', '--path', str(root))
    assert 'Pruned link:' not in broken.stderr
    assert 'Pruned 1 broken link' in broken.stderr

    # --full restores the per-line narration
    (root / 'core' / 'extra.md').write_text(
        _page('Extra', 'An extra page.', 'Body.'),
        encoding='utf-8',
    )
    (root / 'core' / 'design.md').unlink()
    full = _wiki(root, 'update', '--path', str(root), '--full')
    assert full.returncode == 0, full.stdout + full.stderr
    assert 'New link: [[core/extra|extra]] in core/_index.md' in full.stderr
    assert 'Pruned link: [[core/design|design]] from core/_index.md' in full.stderr

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


def test_new_requires_authored_desc_and_content(tmp_path: pathlib.Path) -> None:
    """``wiki new`` emits only with authored inputs, and lands converged.

    The generator refuses to run without --desc and --content (usage
    error, nothing written) and refuses placeholder values; with both,
    the index and its parent row land in one pass and lint has nothing
    to say about the new page.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'tools' / '_index.md', _index('Tools', 'The tools.', 'Text.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    (root / 'tools' / 'backup').mkdir()
    (root / 'tools' / 'backup' / 'main.py').write_text('print(0)\n')

    # both inputs are required options: omitting either is a usage error
    missing_desc = _wiki(
        root,
        'new',
        'tools/backup',
        '--path',
        str(root),
        '--content',
        'Added in the first release.',
    )
    assert missing_desc.returncode == 2
    missing_content = _wiki(
        root,
        'new',
        'tools/backup',
        '--path',
        str(root),
        '--desc',
        'The backup tool.',
    )
    assert missing_content.returncode == 2
    placeholder = _wiki(
        root,
        'new',
        'tools/backup',
        '--path',
        str(root),
        '--desc',
        '...',
        '--content',
        'Added in the first release.',
    )
    assert placeholder.returncode == 2
    assert 'never stubbed' in placeholder.stderr
    assert not (root / 'tools' / 'backup' / '_index.md').exists()

    # with both inputs the adoption lands converged in one command
    created = _wiki(
        root,
        'new',
        'tools/backup',
        '--path',
        str(root),
        '--desc',
        'The backup tool.',
        '--content',
        'Nightly copies.',
    )
    assert created.returncode == 0, created.stdout + created.stderr
    assert 'Created tools/backup/_index.md.' in created.stdout
    index = (root / 'tools' / 'backup' / '_index.md').read_text(encoding='utf-8')
    assert 'desc: The backup tool.' in index
    assert 'Nightly copies.' in index
    parent = (root / 'tools' / '_index.md').read_text(encoding='utf-8')
    assert '[[tools/backup/_index|backup/]]: The backup tool.' in parent
    check = _wiki(root, 'update', '--check', '--path', str(root))
    assert check.returncode == 0, check.stdout + check.stderr

    # re-generating an owned index is refused
    again = _wiki(
        root,
        'new',
        'tools/backup',
        '--path',
        str(root),
        '--desc',
        'Another.',
        '--content',
        'More.',
    )
    assert again.returncode == 2
    assert 'Index already exists' in again.stderr


@pytest.mark.parametrize(
    argnames=('command', 'option'),
    argvalues=[
        ('new', 'NAME'),
        ('new', '--desc'),
        ('new', '--content'),
        ('init', 'NAME'),
    ],
    ids=['new-name', 'new-desc', 'new-content', 'init-name'],
)
def test_new_refuses_an_argument_that_is_not_utf8(
    tmp_path: pathlib.Path,
    command: str,
    option: str,
) -> None:
    """An argv byte no UTF-8 decodes is refused, by option name, before anything lands on disk."""
    root = tmp_path / 'wiki'
    bad = 'Caf' + chr(0xDCE9) + ' au lait.'
    if command == 'init':
        args = ['init', bad, '--path', str(root)]
    else:
        assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
        values = {'NAME': 'latin', '--desc': 'A desc.', '--content': 'Body.'}
        values[option] = bad
        args = ['new', values['NAME'], '--desc', values['--desc']]
        args += ['--content', values['--content'], '--path', str(root)]

    # the refusal names the option and creates nothing
    result = _wiki(tmp_path, *args)
    assert result.returncode == 2, result.stdout + result.stderr
    assert f'{option} is not valid UTF-8' in result.stderr
    assert not (root / 'latin').exists()
    if command == 'init':
        assert not root.exists()


def test_update_counts_files_with_malformed_frontmatter(tmp_path: pathlib.Path) -> None:
    """Pages and indexes update cannot repair are counted as files, each reason under ``--full``."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    (root / 'core').mkdir()
    (root / 'core' / 'unclosed.md').write_text(
        '---\nname: core/unclosed\ndesc: Never closes.\n\nBody.\n', encoding='utf-8'
    )
    (root / 'core' / 'listed.md').write_text(
        '---\n- a\n- b\n---\n\n# listed\n\nBody.\n', encoding='utf-8'
    )

    # the condensed run counts both; the full run names each reason
    condensed = _wiki(root, 'update', '--path', str(root))
    assert condensed.returncode == 0, condensed.stdout + condensed.stderr
    assert '2 files with malformed frontmatter' in condensed.stderr
    full = _wiki(root, 'update', '--path', str(root), '--full')
    assert full.returncode == 0, full.stdout + full.stderr
    assert 'Malformed frontmatter (no closing ---) in core/unclosed.md' in full.stderr
    assert (
        'Malformed frontmatter (not a key: value mapping) in core/listed.md'
        in full.stderr
    )


@pytest.mark.parametrize(
    argnames=('path', 'text', 'racing', 'lone', 'many'),
    argvalues=[
        (
            'core/bad|{n}.md',
            '---\nname: x\ndesc: A page.\n---\n\n# x\n\nBody.\n',
            False,
            'Skipping core/bad|1.md: invalid name (contains a denied character)',
            'Skipped 3 invalid names',
        ),
        (
            'core/page{n}.md',
            'A bare page, edited while update runs.\n',
            True,
            'Skipping core/page1.md: changed during update; re-run `wiki update`',
            'Skipped 3 concurrently-edited files (re-run `wiki update`)',
        ),
        (
            'core/page{n}.md',
            '---\nname: x\ndesc: Never closes.\n\nBody.\n',
            False,
            'Malformed frontmatter (no closing ---) in core/page1.md',
            '3 files with malformed frontmatter',
        ),
        (
            'core{n}/_index.md',
            '',
            False,
            'Empty or truncated index (no frontmatter) in core1/_index.md;'
            ' restore it from git or delete it to rebuild',
            '3 empty or truncated indexes (restore from git or delete to rebuild)',
        ),
    ],
    ids=['invalid_name', 'concurrent_edit', 'malformed_frontmatter', 'empty_index'],
)
def test_update_names_a_lone_skip_and_counts_a_repeated_kind(
    tmp_path: pathlib.Path,
    path: str,
    text: str,
    racing: bool,
    lone: str,
    many: str,
) -> None:
    """A lone skip keeps its line; a kind that repeats gets a count and a pointer.

    A skip leaves no diff, so its line is the only record of the file: the
    condensed default names a kind's one skipped file, and from two up it
    prints the kind's count line and says that ``--full`` lists each.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('core', 'Core concepts.', 'Text.'))
    # a trusted hook edits each page between update's plan and its writes
    if racing:
        _write(root / '.wiki' / 'wiki.py', _RACING_HOOK)
        assert _wiki(tmp_path, 'trust', '--path', str(root)).returncode == 0
    pointer = 'Run with --full to list each line.'

    # one skip: its own line names the file, and nothing points to --full
    _write(root / path.format(n=1), text)
    one = _wiki(root, 'update', '--path', str(root))
    assert one.returncode == 0, one.stdout + one.stderr
    assert lone in one.stderr.splitlines()
    assert pointer not in one.stderr

    # three: the count line stands for them and the narration ends on the pointer
    _write(root / path.format(n=2), text)
    _write(root / path.format(n=3), text)
    # (a dry run reports the same, short of the skip only a write can meet)
    runs = [[]] if racing else [[], ['--check']]
    for args in runs:
        three = _wiki(root, 'update', *args, '--path', str(root))
        lines = three.stderr.splitlines()
        assert many in lines
        assert lone not in lines
        assert lines[-1] == pointer

    # --full lists each file and needs no pointer
    full = _wiki(root, 'update', '--full', '--path', str(root))
    assert full.returncode == 0, full.stdout + full.stderr
    for n in (1, 2, 3):
        assert path.format(n=n) in full.stderr
    assert pointer not in full.stderr

    # new sweeps as update does and takes no --full, so it points to a dry
    # run, which lists every skip but the one only a write can meet
    args = ['new', 'fresh', '--desc', 'A desc.', '--content', 'Body.']
    new = _wiki(root, *args, '--path', str(root))
    assert new.returncode == 0, new.stdout + new.stderr
    lines = new.stderr.splitlines()
    assert many in lines
    pointer = 'Run `wiki update --check --full` to list each line.'
    if racing:
        assert pointer not in lines
    else:
        assert lines[-1] == pointer


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_update_counts_paths_git_ignores_as_lint_does(tmp_path: pathlib.Path) -> None:
    """Indexed paths this machine's git ignores condense in update as in lint.

    The note names a row that ships where its file cannot. One such path
    keeps its line; from two up, update prints the count line lint prints,
    and ``--full`` lists each path.
    """
    root = tmp_path / 'wiki'
    _git_repo(tmp_path)
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a personal ignore file, in force for this repository on this machine only
    excludes = tmp_path / 'personal_ignore'
    excludes.write_text('*.draft.md\n', encoding='utf-8')
    _git(tmp_path, 'config', 'core.excludesFile', str(excludes))
    count = (
        "2 indexed paths this machine's git ignores; their rows ship where the"
        ' files cannot'
    )

    # one ignored path: the note's own line names it
    _write(root / 'one.draft.md', _page('one', 'A draft.', 'Body.'))
    one = _wiki(root, 'update', '--path', str(root))
    assert one.returncode == 0, one.stdout + one.stderr
    note = f"one.draft.md: indexed, but this machine's git ignores it ({excludes}:1"
    assert note in one.stderr
    assert '--full' not in one.stderr

    # two: update prints lint's count line, and each command its own pointer
    _write(root / 'two.draft.md', _page('two', 'A draft.', 'Body.'))
    update = _wiki(root, 'update', '--path', str(root))
    assert update.returncode == 0, update.stdout + update.stderr
    lines = update.stderr.splitlines()
    assert lines[0] == count
    assert lines[-1] == 'Run with --full to list each line.'
    lint = _wiki(root, 'lint', '--path', str(root))
    assert count in lint.stderr.splitlines()

    # --full lists each path in place of the count line
    full = _wiki(root, 'update', '--full', '--path', str(root))
    assert full.returncode == 0, full.stdout + full.stderr
    assert count not in full.stderr
    for name in ('one.draft.md', 'two.draft.md'):
        assert f"{name}: indexed, but this machine's git ignores it" in full.stderr


def test_new_refuses_interior_path(tmp_path: pathlib.Path) -> None:
    """``new`` refuses a ``--path`` inside the wiki instead of rebasing it.

    Whole-tree commands resolve an interior ``--path`` upward, but new's
    name argument is a write target relative to the resolved root -- a
    silent rebase would land the folder at a sibling location the user
    never named. The refusal names the enclosing root to pass instead,
    and nothing is created at either location.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'topic' / '_index.md', _index('Topic', 'Topic text.', 'Text.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0

    result = _wiki(
        root,
        'new',
        'notes',
        '--path',
        str(root / 'topic'),
        '--desc',
        'A desc.',
        '--content',
        'Body.',
    )
    assert result.returncode == 2
    assert f'inside the wiki at: {root}' in result.stderr
    assert not (root / 'notes').exists()
    assert not (root / 'topic' / 'notes').exists()


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
        (['update'], 0),
        (['lint'], 0),
        (['map'], 0),
        # match's grep triple: the probe word appears nowhere, so the
        # resolved run lands on the clean no-match leg
        (['match', 'widget'], 1),
    ],
    ids=['update', 'lint', 'map', 'match'],
)
def test_path_inside_wiki_resolves_upward(
    tmp_path: pathlib.Path,
    args: list[str],
    code: int,
) -> None:
    """``--path`` at a folder inside a wiki resolves to the enclosing root.

    Treating a subfolder as a wiki root would grow a second marker/root
    index and rewrite ``name:`` paths relative to the wrong root; the
    command runs against the enclosing root instead, naming it on stderr,
    so the habitual root-relative invocation works from inside the wiki.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    before = (root / 'core' / '_index.md').read_text(encoding='utf-8')

    # the inside path resolves upward, naming the enclosing root
    result = _wiki(root, *args, '--path', str(root / 'core'))
    assert result.returncode == code
    assert f'inside the wiki at {root}' in result.stderr
    # the subfolder was not mangled into a second wiki root
    assert not (root / 'core' / '.wiki').exists()
    assert (root / 'core' / '_index.md').read_text(encoding='utf-8') == before


def test_path_inside_undeclared_wiki_resolves_upward(
    tmp_path: pathlib.Path,
) -> None:
    """The upward resolution holds when the root marker is missing.

    An undeclared wiki (a lost ``.wiki/``) leaves no settings marker for
    the enclosure probe, but the ancestor index chain still names the
    real root; ``--path`` at a subfolder resolves there the same way
    instead of planting a second marker and rewriting its index as a root.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    shutil.rmtree(root / '.wiki')

    # the subfolder resolves to the chain's topmost index as the root
    result = _wiki(root, 'update', '--path', str(root / 'core'))
    assert result.returncode == 0
    assert f'inside the wiki at {root}' in result.stderr
    # no marker planted in the subfolder; update restores the root's own
    assert not (root / 'core' / '.wiki').exists()
    assert (root / '.wiki' / 'settings.json').is_file()


def test_raw_subfolder_of_undeclared_wiki_resolves_upward(
    tmp_path: pathlib.Path,
) -> None:
    """The undeclared-chain climb holds for a raw (unindexed) subfolder.

    An undeclared wiki's ancestor index chain names the real root
    whether or not the passed subfolder carries an index of its own; a
    raw folder of not-yet-adopted files resolves upward the same way an
    indexed one does, instead of failing as no wiki at all.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    shutil.rmtree(root / '.wiki')
    (root / 'core' / 'raw').mkdir()

    # the raw subfolder resolves to the chain's topmost index as the root
    result = _wiki(root, 'update', '--path', str(root / 'core' / 'raw'))
    assert result.returncode == 0
    assert f'inside the wiki at {root}' in result.stderr
    # no marker planted in the subfolder; update restores the root's own
    assert not (root / 'core' / 'raw' / '.wiki').exists()
    assert (root / '.wiki' / 'settings.json').is_file()


def test_bare_invocation_agrees_with_path_dot(tmp_path: pathlib.Path) -> None:
    """From a raw folder, bare invocation and ``--path .`` name one root.

    The two habitual spellings must never disagree: from a raw
    (unindexed) folder of an undeclared wiki -- any depth -- the bare
    invocation climbs the same ancestor index chain the explicit
    ``--path .`` climbs, instead of erroring or falling through to a
    different wiki via the ``wiki/`` fallback.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    shutil.rmtree(root / '.wiki')
    deep = root / 'core' / 'raw' / 'deep'
    deep.mkdir(parents=True)

    # both spellings agree at every depth, on the same root
    for cwd in (root / 'core' / 'raw', deep):
        bare = _wiki(cwd, 'map', '--depth', '0')
        dotted = _wiki(cwd, 'map', '--depth', '0', '--path', '.')
        assert bare.returncode == 0, bare.stdout + bare.stderr
        assert dotted.returncode == 0, dotted.stdout + dotted.stderr
        assert bare.stdout == dotted.stdout
        assert 'core/' in bare.stdout


def test_standalone_wiki_is_never_absorbed_from_above(
    tmp_path: pathlib.Path,
) -> None:
    """An outer index chain never sweeps up a wiki islanded below it.

    A project directory holding its own wiki sits inside a foreign index
    tree (a docs site, a dropped ``_index.md``). Climbing from that raw
    directory to the outer tree would hand the standalone wiki over to
    be absorbed -- every ``name:`` rewritten against the wrong root, a
    settings marker planted above it -- so the climb declines to it, and
    an outer root named explicitly refuses rather than absorbing.
    """
    outer = tmp_path / 'tree'
    project = outer / 'project'
    standalone = project / 'wiki'
    standalone.mkdir(parents=True)
    _write(outer / '_index.md', _index('tree', 'The outer tree.', 'Body.'))
    _write(standalone / '_index.md', _index('wiki', 'The standalone.', 'Body.'))
    _write(standalone / 'page.md', _page('page', 'A page.', 'Body.'))

    # the bare climb resolves to the standalone wiki, never the outer tree
    bare = _wiki(project, 'update')
    assert bare.returncode == 0, bare.stdout + bare.stderr
    text = (standalone / '_index.md').read_text(encoding='utf-8')
    assert 'name: wiki\n' in text
    assert '[[page|page]]' in text
    # the outer tree was neither swept nor marked
    assert not (outer / '.wiki').exists()
    assert not (project / '_index.md').exists()
    assert 'project' not in (outer / '_index.md').read_text(encoding='utf-8')

    # naming the outer root explicitly refuses too, naming the island
    # (the sweep restored the standalone's own marker, so the declared
    # guard answers first -- either refusal keeps the wiki intact)
    explicit = _wiki(tmp_path, 'update', '--path', str(outer))
    assert explicit.returncode == 2
    assert f'encloses the wiki at: {standalone}' in explicit.stderr
    assert (standalone / '_index.md').read_text(encoding='utf-8') == text


def test_islanded_wiki_refusal_names_both_fixes(tmp_path: pathlib.Path) -> None:
    """A never-swept island refuses on the index gap alone.

    An undeclared wiki carries no marker for the nested-root guard to
    see, so the gap in the index chain is the whole signal: the refusal
    names the island and both ways out -- run against it, or index the
    folder between them to make it part of this tree.
    """
    outer = tmp_path / 'tree'
    island = outer / 'vendor' / 'kb'
    island.mkdir(parents=True)
    _write(outer / '_index.md', _index('tree', 'The outer tree.', 'Body.'))
    _write(island / '_index.md', _index('kb', 'A vendored wiki.', 'Body.'))

    result = _wiki(tmp_path, 'update', '--path', str(outer))
    assert result.returncode == 2
    assert f'encloses the wiki at: {island}' in result.stderr
    assert 'islanded from this tree by an unindexed folder' in result.stderr
    assert f'--path {island}' in result.stderr
    # the bare climb from its raw parent never hands it to the outer tree
    bare = _wiki(outer / 'vendor', 'update')
    assert bare.returncode == 2
    assert 'Could not locate' in bare.stderr
    assert not (outer / 'vendor' / '_index.md').exists()
    assert not (outer / '.wiki').exists()


def test_path_naming_nested_declared_wiki_resolves_to_itself(
    tmp_path: pathlib.Path,
) -> None:
    """``--path`` at a declared nested wiki runs against that wiki.

    A vendored guest wiki (its own ``.wiki/settings.json``, excluded
    from the host's walks) is a sovereign tree: naming it explicitly
    must never silently retarget the command to the enclosing host,
    reporting success against the wrong wiki while the guest stays
    stale.
    """
    host = tmp_path / 'wiki'
    settings = '{"exclude": {"patterns": ["vendor"]}}'
    init = _wiki(tmp_path, 'init', '--path', str(host), '--settings', settings)
    assert init.returncode == 0, init.stdout + init.stderr
    # a declared guest wiki vendored inside the excluded subtree
    guest = host / 'vendor' / 'guest'
    (guest / '.wiki').mkdir(parents=True)
    (guest / '.wiki' / 'settings.json').write_text('{}\n', encoding='utf-8')
    _write(guest / 'page.md', _page('page', 'A guest page.', 'Body.'))
    host_index = (host / '_index.md').read_text(encoding='utf-8')

    # the update runs against the guest itself, wiring its own index
    result = _wiki(host, 'update', '--path', str(guest))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'using that root' not in result.stderr
    guest_index = (guest / '_index.md').read_text(encoding='utf-8')
    assert '[[page|page]]' in guest_index
    # the host stays untouched
    assert (host / '_index.md').read_text(encoding='utf-8') == host_index


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
    assert result.returncode == 2
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
    the enclosure message instead of rewriting anything. A caller tells
    this refusal apart by the message's opening phrase on stderr, and
    relies on it writing nothing, so both are pinned.
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
    index = (root / '_index.md').read_text(encoding='utf-8')

    # the sweep is refused with the enclosure message, naming the root
    result = _wiki(root, 'update', '--path', str(root))
    assert result.returncode == 2
    assert f'Error: Path encloses the wiki at: {nested};' in result.stderr
    assert 'declared root' in result.stderr
    # nothing was written: the page and the root index keep their bytes
    assert (root / 'note.md').read_text(encoding='utf-8') == before
    assert (root / '_index.md').read_text(encoding='utf-8') == index


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
    assert result.returncode == 2
    assert f'inside the wiki at: {nested};' in combined
    assert (nested / 'sub' / 'page.md').read_text(encoding='utf-8') == before
    # lint of the same scope refuses the same way (no misleading diff);
    # its error leg exits 2, keeping 1 to mean exactly "issues found"
    lint = _wiki(root, 'lint', 'backup/sub', '--path', str(root))
    assert lint.returncode == 2
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
    assert result.returncode == 2
    assert 'excluded directory' in combined
    # nothing scaffolded inside the tool directory
    assert not (root / '.wiki' / '_index.md').exists()


def test_scope_that_is_not_found_suggests_the_fix(tmp_path: pathlib.Path) -> None:
    """A scope that reaches no folder names the fix the tree offers.

    Two misses are common and each has one fix: the name carries the
    root's own folder name (``wiki/core`` in the wiki at ``wiki``), or it
    is the read key of a page, and a scope is a folder. Any other miss,
    and every other refusal of a scope, keeps its line.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / 'design.md', _page('Design', 'A design.', 'Body.'))
    _write(root / 'note.md', _page('Note', 'A note.', 'Body.'))
    assert _wiki(root, 'update').returncode == 0
    page_hint = 'a scope is a folder and this is a page'
    lines = {
        # the root's own folder name leads a name whose rest is a folder
        'wiki/core': "Wiki folder not found: 'wiki/core' (did you mean core?)",
        # a page, by its read key and by its file name
        'core/design': (
            f"Wiki folder not found: 'core/design' ({page_hint}; did you mean core?)"
        ),
        'core/design.md': (
            f"Wiki folder not found: 'core/design.md' ({page_hint}; did you mean core?)"
        ),
        'note': f"Wiki folder not found: 'note' ({page_hint} in the wiki root)",
        # a name the tree has no fix for
        'nowhere': "Wiki folder not found: 'nowhere'",
        'wiki/nowhere': "Wiki folder not found: 'wiki/nowhere'",
        # the root's folder name twice: the rest is no folder
        'wiki/wiki/core': "Wiki folder not found: 'wiki/wiki/core'",
        # the other refusals of a scope
        '.wiki': "Path is inside an excluded directory: '.wiki'",
        '..': "Path is outside wiki root: '..'",
    }
    for name, line in lines.items():
        result = _wiki(tmp_path, 'lint', name, '--path', 'wiki')
        assert result.returncode == 2, name
        assert result.stderr == f'Error: {line}\n'
    # every command that takes a scope resolves it the same way
    for command in (('update', '--check'), ('map',), ('search', 'x'), ('match', 'x')):
        result = _wiki(tmp_path, *command, 'core/design', '--path', 'wiki')
        assert result.returncode == 2, command
        assert result.stderr == f'Error: {lines["core/design"]}\n'


def test_exclude_patterns_end_to_end(tmp_path: pathlib.Path) -> None:
    """``exclude.patterns`` flows through init, update, and lint.

    An init-seeded pattern keeps its subtree unindexed from the first
    sweep; extending the exclusion to an already-indexed page prunes
    its row with the verbatim cause line (no condensed category), so
    the fence and the tree agree and lint has nothing left to flag.
    """
    root = tmp_path / 'wiki'
    settings = '{"exclude": {"patterns": ["vendor"]}}'
    init = _wiki(tmp_path, 'init', '--path', str(root), '--settings', settings)
    assert init.returncode == 0, init.stdout + init.stderr
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    _write(root / 'core' / 'gone.md', _page('Gone', 'Excluded soon.', 'Body.'))
    _write(root / 'vendor' / 'lib.md', _page('Lib', 'Vendored.', 'Body.'))
    # the first sweep never indexes the excluded subtree
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    assert not (root / 'vendor' / '_index.md').exists()
    index = root / 'core' / '_index.md'
    assert '[[core/gone|gone]]' in index.read_text(encoding='utf-8')
    # extend the exclusion to the already-indexed page
    config = root / '.wiki' / 'settings.json'
    data = json.loads(config.read_text(encoding='utf-8'))
    data['exclude']['patterns'].append('core/gone.md')
    config.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    # the second sweep prunes the row, naming the cause verbatim
    result = _wiki(root, 'update', '--path', str(root))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Link targets an excluded path:' in result.stderr
    assert "excluded by exclude.patterns 'core/gone.md'" in result.stderr
    assert '[[core/gone|gone]]' not in index.read_text(encoding='utf-8')
    # nothing is left to flag: the fence and the tree agree
    lint = _wiki(root, 'lint', '--path', str(root))
    assert lint.returncode == 0, lint.stdout + lint.stderr


def test_links_external_end_to_end(tmp_path: pathlib.Path) -> None:
    """``links.external`` flows through init, lint, ``--json``, and read.

    A seeded allowlist admits a source file and a sibling wiki's page
    from a page at any depth; lint notes a missing file as stale and an
    entry naming no folder once per run, and fails a ``../`` link under
    no entry (naming the entry to add -- the nearest folder above a path
    through a file -- and, for the spelling that climbs once per folder,
    the root-relative form), a prefixed link that lands inside the wiki,
    and a folder the sibling wiki indexes -- each typed in ``--json``;
    issues print on stdout and notes ride stderr, and each suggested fix
    lints clean once adopted. ``wiki read`` stays confined to the root,
    a malformed block fails lint but never update, and a malformed
    sibling settings file is named.
    """
    root = tmp_path / 'wiki'
    settings = '{"links": {"external": ["../src", "../manual", "../missing"]}}'
    init = _wiki(tmp_path, 'init', '--path', str(root), '--settings', settings)
    assert init.returncode == 0, init.stdout + init.stderr
    _write(tmp_path / 'src' / 'main.py', 'print()\n')
    _write(tmp_path / 'docs' / 'y.md', 'Doc.\n')
    _write(tmp_path / 'idx' / '_index.md', 'Idx.\n')
    # a sibling wiki with a page and an indexed folder
    manual = tmp_path / 'manual'
    assert _wiki(tmp_path, 'init', '--path', str(manual)).returncode == 0
    _write(manual / 'glossary.md', _page('Glossary', 'Glossary.', 'Body.'))
    _write(manual / 'guides' / 'topic.md', _page('Topic', 'A topic.', 'Body.'))
    assert _wiki(manual, 'update', '--path', str(manual)).returncode == 0
    # a root-level page and a nested page linking outside
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    links = _page(
        name='Links',
        desc='Links.',
        body='See [[../src/main.py]], [[../src/gone.py]], [[../manual/glossary]],'
        ' [[../docs/x]], [[../docs/y|Doc]], [[../docs/y.md/x]], [[../idx]], and'
        ' [[../missing/z]].',
    )
    _write(root / 'links.md', links)
    page = _page(
        name='Page',
        desc='A page.',
        body='See [[../src/main.py]], [[../../src/main.py]], and [[./page]].',
    )
    _write(root / 'core' / 'page.md', page)
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    # the undeclared and in-wiki links are the issues; the notes sort the rest
    lint = _wiki(root, 'lint', '--path', str(root))
    assert lint.returncode == 1
    clause = 'to links.external in .wiki/settings.json to allow it'
    assert (
        'core/page.md: Link [[../../src/main.py]] points outside every links.external'
        f" folder (add '../../src' {clause}, or use [[../src/main.py]])"
    ) in lint.stdout
    assert (
        "core/page.md: Link [[./page]] points inside the wiki through './' or '../'"
        ' (use [[core/page]])'
    ) in lint.stdout
    assert (
        'links.md: Link [[../docs/x]] points outside every links.external folder'
        f" (add '../docs' {clause})"
    ) in lint.stdout
    assert (
        'links.md: Link [[../docs/y|Doc]] points outside every links.external folder'
        f" (add '../docs' {clause})"
    ) in lint.stdout
    assert (
        'links.md: Link [[../docs/y.md/x]] points outside every links.external'
        f" folder (add '../docs' {clause})"
    ) in lint.stdout
    assert (
        'links.md: Link [[../idx]] points outside every links.external folder'
        f" (add '../idx' {clause})"
    ) in lint.stdout
    assert 'links.md: Stale link [[../src/gone.py]]' in lint.stderr
    assert (
        "links.external entry '../missing' names no folder on this machine;"
        ' links into it are not checked'
    ) in lint.stderr
    assert '[[../src/main.py]]' not in lint.stderr
    assert '[[../manual/glossary]]' not in lint.stderr
    assert '[[../missing/z]]' not in lint.stderr
    # a bare folder of the sibling wiki is that wiki's directory link
    _write(root / 'guides.md', _page('Guides', 'Guides.', 'See [[../manual/guides]].'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    lint = _wiki(root, 'lint', '--path', str(root))
    assert (
        'guides.md: Link [[../manual/guides]] targets a folder, not a page'
        ' (use [[../manual/guides/_index]])'
    ) in lint.stdout
    # --json carries every finding typed
    document = json.loads(_wiki(root, 'lint', '--path', str(root), '--json').stdout)
    kinds = {issue['kind'] for issue in document['issues']}
    assert kinds == {'directory_link', 'outside_link', 'relative_link'}
    outside = [issue for issue in document['issues'] if issue['kind'] == 'outside_link']
    outside_rows = [
        (
            issue['path'],
            issue['target'],
            issue['folder'],
            issue.get('canonical', 'absent'),
        )
        for issue in outside
    ]
    assert outside_rows == [
        ('links.md', '../docs/x', '../docs', 'absent'),
        ('links.md', '../docs/y', '../docs', 'absent'),
        ('links.md', '../docs/y.md/x', '../docs', 'absent'),
        ('links.md', '../idx', '../idx', 'absent'),
        ('core/page.md', '../../src/main.py', '../../src', '../src/main.py'),
    ]
    # the alias rides in the prose alone
    aliased = [issue for issue in outside if issue['target'] == '../docs/y']
    aliased_issue, *_ = aliased
    assert '[[../docs/y|Doc]]' in aliased_issue['text']
    relative = [
        issue for issue in document['issues'] if issue['kind'] == 'relative_link'
    ]
    relative_rows = [(row['path'], row['target'], row['canonical']) for row in relative]
    assert relative_rows == [('core/page.md', './page', 'core/page')]
    relative_issue, *_ = relative
    assert 'external' not in relative_issue
    note_kinds = {note['kind'] for note in document['notes']}
    assert 'link_outside' not in note_kinds
    missing = [
        note for note in document['notes'] if note['kind'] == 'link_folder_missing'
    ]
    missing_folders = [note['folder'] for note in missing]
    assert missing_folders == ['../missing']
    assert 'path' not in missing[0]
    stale = [note for note in document['notes'] if note['kind'] == 'link_stale']
    stale_targets = {note['target'] for note in stale}
    assert stale_targets == {'../src/gone.py'}
    # with every suggested fix adopted, lint is clean: the entries admit the
    # docs page and the folder, and the missing docs files note stale
    config = root / '.wiki' / 'settings.json'
    data = json.loads(config.read_text(encoding='utf-8'))
    data['links']['external'].extend(['../docs', '../idx'])
    config.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    page = _page('Page', 'A page.', 'See [[../src/main.py]].')
    _write(root / 'core' / 'page.md', page)
    _write(
        root / 'guides.md',
        _page('Guides', 'Guides.', 'See [[../manual/guides/_index]].'),
    )
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    lint = _wiki(root, 'lint', '--path', str(root), '--full')
    assert lint.returncode == 0, lint.stdout + lint.stderr
    assert 'links.md: Stale link [[../src/gone.py]]' in lint.stderr
    assert 'links.md: Stale link [[../docs/x]]' in lint.stderr
    assert 'links.md: Stale link [[../docs/y.md/x]]' in lint.stderr
    assert "links.external entry '../missing' names no folder" in lint.stderr
    # the allowlist is a lint rule alone: read stays confined to the root
    read = _wiki(root, 'read', '../src/main.py', '--path', str(root))
    assert read.returncode == 2
    assert 'outside wiki root' in (read.stdout + read.stderr).lower()
    # a malformed block fails update and lint alike, naming the file and key
    data['links'] = {'external': ['src']}
    config.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    for command in ('update', 'lint'):
        refused = _wiki(root, command, '--path', str(root))
        assert refused.returncode == 2, command
        error = "Invalid links.external folder 'src': inside the wiki root"
        assert error in refused.stderr, command
    # a malformed sibling settings file is named as that wiki's -- read the
    # moment a link needs its rules, which a bare folder link does
    data['links'] = {'external': ['../manual']}
    config.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    _write(root / 'guides.md', _page('Guides', 'Guides.', 'See [[../manual/guides]].'))
    (manual / '.wiki' / 'settings.json').write_text('{', encoding='utf-8')
    lint = _wiki(root, 'lint', '--path', str(root))
    assert lint.returncode == 2
    assert "links.external wiki '../manual'" in lint.stderr


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
    assert result.returncode == 2
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


def test_lint_condenses_repeated_notes(tmp_path: pathlib.Path) -> None:
    """A repeated note kind prints one count line; ``--full`` lists each note.

    Notes are advisory, so a corpus with many of one kind must not bury
    the report under them: the default prints one count line per
    repeated kind, a lone note verbatim, and a hint naming ``--full``,
    while the summary and ``--json`` still count every note.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # the root index's placeholder desc plus three placeholder pages make
    # four missing-desc notes; the root's empty content stays a lone note
    pages = ('alpha', 'beta', 'gamma')
    desc_count = len(pages) + 1
    note_count = desc_count + 1
    for page in pages:
        _write(root / f'{page}.md', _page(page, '...', 'Body.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0

    # the default condenses the repeated kind and keeps the lone note
    default = _wiki(root, 'lint', '--path', str(root))
    assert default.returncode == 0, default.stdout + default.stderr
    assert default.stderr.splitlines() == [
        f'{desc_count} pages need a desc',
        '_index.md: Empty content',
        'Run with --full to list each note.',
    ]
    assert f'No issues found ({note_count} notes).' in default.stdout

    # --full lists every note in place of the count line and the hint
    full = _wiki(root, 'lint', '--path', str(root), '--full')
    assert full.returncode == 0, full.stdout + full.stderr
    assert full.stderr.count(': Needs desc') == desc_count
    assert '_index.md: Empty content' in full.stderr
    assert '--full' not in full.stderr
    assert full.stdout == default.stdout

    # --count and --json print no note lines but still count every note
    count = _wiki(root, 'lint', '--path', str(root), '--count')
    assert count.stderr == ''
    assert f'No issues found ({note_count} notes).' in count.stdout
    as_json = _wiki(root, 'lint', '--path', str(root), '--json')
    assert as_json.stderr == ''
    document = json.loads(as_json.stdout)
    assert document['summary'] == {'issues': 0, 'notes': note_count}


def test_lint_json_reports_typed_findings(tmp_path: pathlib.Path) -> None:
    """``--json`` emits one severity-tagged document on stdout.

    A scripted consumer reads one stream and branches on typed fields --
    a note can never be mis-triaged as a blocking issue -- while the
    exit-code contract matches the prose mode: 1 on issues, 0 on notes
    alone.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a fresh wiki carries soft notes only (placeholder desc, empty content)
    clean = _wiki(root, 'lint', '--path', str(root), '--json')
    assert clean.returncode == 0
    document = json.loads(clean.stdout)
    assert document['issues'] == []
    assert all(note['severity'] == 'note' for note in document['notes'])
    assert all(note['path'] and note['text'] for note in document['notes'])
    kinds = {note['kind'] for note in document['notes']}
    assert 'desc_missing' in kinds
    assert document['summary'] == {'issues': 0, 'notes': len(document['notes'])}
    # the whole report is the document: nothing rides stderr
    assert clean.stderr == ''
    # with hard issues on top, the document carries them typed -- kind
    # and payload fields beside the prose -- and the exit flips
    (root / 'Bad#Folder').mkdir()
    dirty = _wiki(root, 'lint', '--path', str(root), '--json')
    assert dirty.returncode == 1
    document = json.loads(dirty.stdout)
    assert all(issue['severity'] == 'issue' for issue in document['issues'])
    assert all(issue['kind'] and issue['path'] for issue in document['issues'])
    kinds = {issue['kind'] for issue in document['issues']}
    assert kinds == {'invalid_folder_name', 'missing_index'}
    named = [
        issue for issue in document['issues'] if issue['kind'] == 'invalid_folder_name'
    ]
    assert named[0]['path'] == 'Bad#Folder'
    assert named[0]['reason']
    assert 'Invalid folder name' in named[0]['text']
    assert document['summary']['issues'] == len(document['issues'])
    # --json owns the report shape; the prose modes cannot combine with it
    both = _wiki(root, 'lint', '--path', str(root), '--json', '--count')
    assert both.returncode == 2


def test_lint_types_resolver_diagnostics(tmp_path: pathlib.Path) -> None:
    """Resolver diagnostics ride lint's report as typed notes.

    The resolution prose (a missing settings marker, an upward
    resolution) streams to stderr for humans; a machine consumer must
    see the same diagnostics typed, so they join the note count and the
    ``--json`` document as ``resolver_notice`` rows instead of living
    on an unparseable stream only.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    shutil.rmtree(root / '.wiki')

    # the prose summary counts the diagnostic beside the engine's notes
    prose = _wiki(root, 'lint', '--path', str(root))
    assert prose.returncode == 0, prose.stdout + prose.stderr
    assert 'settings.json missing' in prose.stderr
    assert 'notes)' in prose.stdout
    # the document carries it typed
    result = _wiki(root, 'lint', '--path', str(root), '--json')
    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    rows = [note for note in document['notes'] if note['kind'] == 'resolver_notice']
    assert len(rows) == 1
    assert 'settings.json missing' in rows[0]['text']
    assert rows[0]['severity'] == 'note'
    assert document['summary']['notes'] == len(document['notes'])
    # a healthy declared wiki records none
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    healthy = _wiki(root, 'lint', '--path', str(root), '--json')
    document = json.loads(healthy.stdout)
    assert not any(note['kind'] == 'resolver_notice' for note in document['notes'])


def test_lint_error_exits_two(tmp_path: pathlib.Path) -> None:
    """Lint's error leg exits 2, keeping 1 to mean exactly issues-found.

    A script gating on lint must never confuse an unresolvable wiki (or
    a bad subtree) with a red corpus: errors land on exit 2 with an
    ``Error:`` line on stderr, and issues alone exit 1.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # an unresolvable wiki root
    missing = _wiki(tmp_path, 'lint', '--path', str(tmp_path / 'nowhere'))
    assert missing.returncode == 2
    assert 'Error:' in missing.stderr
    # a bad subtree entry
    entry = _wiki(root, 'lint', 'nowhere', '--path', str(root))
    assert entry.returncode == 2
    assert 'Error:' in entry.stderr


def test_lint_error_keeps_the_notes_before_it(tmp_path: pathlib.Path) -> None:
    """Notes lint emitted before an error print, condensed, above the error.

    ``--full`` streams each note as it fires, so the condensed default
    flushes the notes it holds when lint raises, ahead of the wrapper's
    ``Error:`` line, rather than dropping them with the run.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a trusted hook whose lint notes two placeholder descs, then raises
    _write(root / '.wiki' / 'wiki.py', _NOTING_HOOK)
    assert _wiki(tmp_path, 'trust', '--path', str(root)).returncode == 0

    # the notes print condensed, then the error, and the run exits 2
    lint = _wiki(root, 'lint', '--path', str(root))
    assert lint.returncode == 2, lint.stdout + lint.stderr
    assert lint.stderr.splitlines() == [
        '2 pages need a desc',
        'Run with --full to list each note.',
        'Error: boom',
    ]


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
    page_count = 8  # one dangling row per deleted page
    pages = [f'page{i}' for i in range(page_count)]
    for page in pages:
        _write(root / 'core' / f'{page}.md', _page(page, f'The {page} page.', 'Body.'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    # delete every page: one dangling row each plus the pending prune diff
    for page in pages:
        (root / 'core' / f'{page}.md').unlink()

    # the default (detailed) view lists every broken link plus the summary
    default = _wiki(root, 'lint', '--path', str(root))
    assert default.returncode == 1
    assert default.stdout.count('Broken link [[') == page_count
    assert f'{page_count + 1} issues' in default.stdout

    # --count condenses to the summary; the notes leave stderr too
    count = _wiki(root, 'lint', '--path', str(root), '--count')
    assert count.returncode == 1
    assert count.stdout.count('Broken link [[') == 0
    assert f'{page_count + 1} issues' in count.stdout
    assert 'Needs desc' not in count.stderr

    # --full changes only the note lines; combining the modes is a usage error
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
    """A map of a wiki with no folders reports emptiness, not a crash.

    The line follows the arguments alone: a run that a filter narrowed
    says that no entries match, on a wiki that holds nothing too.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    result = _wiki(root, 'map', '--path', str(root))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == 'Wiki is empty.\n'
    result = _wiki(root, 'map', '--path', str(root), '--category', 'nosuch')
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == 'No entries match.\n'


def test_map_says_no_entries_match_when_narrowed_to_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """A scope or a filter that shows nothing says so, not that the wiki is empty."""
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a wiki that holds one page, at its root
    _write(root / 'note.md', _page('Note', 'A note.', 'Body.'))
    assert _wiki(root, 'update').returncode == 0
    assert _wiki(root, 'map').stdout.startswith('note ')
    # a filter: a category no entry has, then a kind the wiki lacks
    for narrowing in (('--category', 'nosuch'), ('--no-markdown',)):
        result = _wiki(root, 'map', *narrowing)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == 'No entries match.\n', narrowing
    # a scope: a folder that holds nothing
    args = ('new', 'empty', '--desc', 'Nothing yet.', '--content', 'Body.')
    assert _wiki(root, *args).returncode == 0
    result = _wiki(root, 'map', 'empty')
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == 'No entries match.\n'


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


# ------ match


def test_match_output_modes(wiki: pathlib.Path) -> None:
    """A match prints unique paths by default, and line detail on request."""
    # default mode lists each matching file once
    paths = _wiki(wiki, 'match', 'widget', '--path', str(wiki))
    assert paths.returncode == 0, paths.stdout + paths.stderr
    assert 'core/design.md' in paths.stdout
    assert ':' not in paths.stdout.replace('.md', '').replace('.txt', '')
    # --lines includes line numbers and the matching text
    lines = _wiki(wiki, 'match', 'widget', '--path', str(wiki), '--lines')
    assert lines.returncode == 0, lines.stdout + lines.stderr
    assert 'core/design.md:' in lines.stdout
    assert 'subsystem' in lines.stdout
    # --lineno includes line numbers but not the line text
    lineno = _wiki(wiki, 'match', 'widget', '--path', str(wiki), '--lineno')
    assert lineno.returncode == 0, lineno.stdout + lineno.stderr
    assert 'core/design.md:' in lineno.stdout
    assert 'subsystem' not in lineno.stdout


def test_match_field_and_ignore_case(wiki: pathlib.Path) -> None:
    """A match can target a frontmatter field and match case-insensitively."""
    # a body-content search for 'design' should not match the frontmatter desc
    field = _wiki(
        wiki,
        'match',
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
        'match',
        'WIDGET',
        '--path',
        str(wiki),
        '--ignore-case',
    )
    assert insensitive.returncode == 0, insensitive.stdout + insensitive.stderr
    assert 'core/design.md' in insensitive.stdout
    # without the flag the uppercase query misses the lowercase body
    sensitive = _wiki(wiki, 'match', 'WIDGET', '--path', str(wiki))
    assert sensitive.returncode == 1
    assert 'No matches' in sensitive.stderr


def test_match_all_includes_non_markdown(wiki: pathlib.Path) -> None:
    """--all widens the match to non-markdown files in the tree."""
    without = _wiki(wiki, 'match', 'widget', '--path', str(wiki))
    with_all = _wiki(wiki, 'match', 'widget', '--path', str(wiki), '--all')
    assert without.returncode == 0, without.stdout + without.stderr
    assert with_all.returncode == 0, with_all.stdout + with_all.stderr
    assert 'snippet.txt' not in without.stdout
    assert 'snippet.txt' in with_all.stdout


def test_match_no_match_exits_nonzero(wiki: pathlib.Path) -> None:
    """A pattern with no hits exits 1 with the notice on stderr.

    The grep convention: scripts distinguish no-match from match by exit
    code, and stdout stays reserved for matches so a page named
    'No matches found.' can never be mistaken for the notice.
    """
    result = _wiki(wiki, 'match', 'zzz_no_such_token', '--path', str(wiki))
    assert result.returncode == 1
    assert 'No matches' in result.stderr
    assert result.stdout == ''


def test_match_line_flags_are_mutually_exclusive(wiki: pathlib.Path) -> None:
    """--lines and --lineno cannot be combined (usage error, exit 2)."""
    result = _wiki(
        wiki,
        'match',
        'widget',
        '--path',
        str(wiki),
        '--lines',
        '--lineno',
    )
    assert result.returncode == 2
    assert 'mutually exclusive' in (result.stdout + result.stderr).lower()


@pytest.mark.parametrize(
    argnames='pattern',
    argvalues=['[', 'a{4294967296}', '(' * 3000 + 'a' + ')' * 3000],
    ids=['syntax', 'repeat-count', 'nesting'],
)
def test_match_invalid_regex_reports_error(wiki: pathlib.Path, pattern: str) -> None:
    """A malformed regex is an error (exit 2), distinct from a clean no-match.

    Grep reserves exit 2 for errors so a script following the documented
    branch-on-exit-code contract never reads a failed match (bad regex, no
    wiki) as an absent term (exit 1). A repeat count past the parser's
    limit and a nesting past the interpreter's stack are refused in the
    same words as a syntax error.
    """
    result = _wiki(wiki, 'match', pattern, '--path', str(wiki))
    assert result.returncode == 2
    # one line names the pattern as a regex, ahead of the parser's reason
    assert result.stderr.startswith(f'Error: Invalid regex {pattern!r}: ')
    assert result.stderr.count('\n') == 1
    assert result.stdout == ''


def test_match_explains_an_unescaped_wikilink(tmp_path: pathlib.Path) -> None:
    """An unescaped ``[[`` draws a line that says how to match a wikilink.

    ``[[`` is the wiki's own link syntax and a character class in a
    regex, so the pattern most likely to be typed means something else.
    The line stands in for Python's warning, which names a source line
    of the install; the pattern still runs, or is refused, as the regex
    it is.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', 'notes', '--path', str(root)).returncode == 0
    body = 'see [[foo|bar]] here\ncar] park'
    _write(root / 'p.md', _page(name='p', desc='A page.', body=body))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    # the pattern runs as a class and a literal ], matching what it matched
    result = _wiki(root, 'match', '[[foo|bar]]', '--path', str(root), '--lines')
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == 'p.md:10: see [[foo|bar]] here\np.md:11: car] park\n'
    assert result.stderr.splitlines() == [_NESTED_SET_HINT]
    # a pattern that does not compile gets the line and one error line
    result = _wiki(root, 'match', '[[a/p', '--path', str(root))
    assert result.returncode == 2
    assert result.stdout == ''
    lines = result.stderr.splitlines()
    assert lines[:-1] == [_NESTED_SET_HINT]
    assert lines[-1].startswith("Error: Invalid regex '[[a/p': ")


@pytest.mark.parametrize(
    argnames=('pattern', 'hints'),
    argvalues=[
        (r'[q&&]', [_SET_OPERATOR_HINT]),
        (r'[[q&&]', [_NESTED_SET_HINT, _SET_OPERATOR_HINT]),
        (r'\[\[foo|fish \& chips', []),
    ],
    ids=['doubled-operator', 'both', 'neither'],
)
def test_match_hints_follow_the_pattern(
    tmp_path: pathlib.Path,
    pattern: str,
    hints: list[str],
) -> None:
    """Each risky construct draws one line, in a fixed order, and no other.

    A doubled ``&&`` inside a character class reads as two characters
    where another regex dialect intersects sets; a pattern holding
    neither construct prints nothing beside its matches.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', 'notes', '--path', str(root)).returncode == 0
    body = 'see [[foo|bar]] here\nfish & chips'
    _write(root / 'p.md', _page(name='p', desc='A page.', body=body))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    result = _wiki(root, 'match', pattern, '--path', str(root))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'p.md' in result.stdout.splitlines()
    assert result.stderr.splitlines() == hints


def test_match_reports_no_line_past_the_end(wiki: pathlib.Path) -> None:
    """``match '^$'`` reports the blank lines of a file and none past its end.

    A file that ends with a newline has as many lines as newlines; the
    newline that ends the last line is not an empty line after it.
    """
    text = (wiki / 'core' / 'design.md').read_text(encoding='utf-8')
    assert text.endswith('\n')
    line_count = text.count('\n')
    result = _wiki(wiki, 'match', '^$', 'core', '--path', str(wiki), '--lineno')
    assert result.returncode == 0, result.stdout + result.stderr
    hits = result.stdout.splitlines()
    assert any(hit.startswith('core/design.md:') for hit in hits)
    assert f'core/design.md:{line_count + 1}' not in hits


def test_naming_pattern_prints_no_line_for_a_risky_regex(
    tmp_path: pathlib.Path,
) -> None:
    """A ``naming.pattern`` Python warns about loads without a word.

    The pattern is settings, not a command argument, so the warning's
    source line would say nothing to whoever ran the command, and no
    line of the command's own stands in for it: each run prints what it
    prints under a pattern that draws no warning.
    """
    stderr = {}
    for label, pattern in (('risky', '[[a-z&&]+'), ('plain', '[a-z]+')):
        root = tmp_path / label / 'wiki'
        settings = json.dumps({'naming': {'pattern': pattern}})
        result = _wiki(
            tmp_path,
            'init',
            'notes',
            '--path',
            str(root),
            '--settings',
            settings,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        stderr[label] = [result.stderr]
        # the policy loads again in every run that checks a name
        _write(root / 'p.md', _page(name='p', desc='A page.', body='Text.'))
        result = _wiki(root, 'update', '--path', str(root))
        assert result.returncode == 0, result.stdout + result.stderr
        stderr[label].append(result.stderr)
    assert stderr['risky'] == stderr['plain']


def test_match_resolution_failure_exits_two(
    tmp_path: pathlib.Path,
    wiki: pathlib.Path,
) -> None:
    """A match that cannot resolve its wiki or subtree is an error (exit 2).

    A wiki-less cwd, a missing/out-of-root/excluded subtree argument, and
    an untrusted or broken hook are failed matches, not absent terms;
    exit 1 stays reserved for a clean no-match so the branch-on-exit-code
    contract holds.
    """
    no_wiki = _wiki(tmp_path, 'match', 'widget')
    assert no_wiki.returncode == 2
    assert 'Error:' in no_wiki.stderr
    missing = _wiki(wiki, 'match', 'widget', 'no_such_subtree', '--path', str(wiki))
    assert missing.returncode == 2
    assert 'Error:' in missing.stderr
    # a subtree escaping the root or naming an excluded dot directory
    outside = _wiki(wiki, 'match', 'widget', '../..', '--path', str(wiki))
    assert outside.returncode == 2
    assert 'Error:' in outside.stderr
    excluded = _wiki(wiki, 'match', 'widget', '.wiki', '--path', str(wiki))
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
    untrusted = _wiki(hooked, 'match', 'widget', '--path', str(hooked))
    assert untrusted.returncode == 2
    assert 'wiki trust' in untrusted.stderr
    # trusting the root executes the hook; its failure to load is the
    # error leg too, not a no-match
    assert _wiki(hooked, 'trust', '--path', str(hooked)).returncode == 0
    broken = _wiki(hooked, 'match', 'widget', '--path', str(hooked))
    assert broken.returncode == 2
    assert 'Failed to load' in broken.stderr


def test_match_all_skips_undecodable_files(wiki: pathlib.Path) -> None:
    """``match --all`` skips a non-UTF-8 file instead of crashing the whole run.

    A binary file is expected under ``--all``, so the skip prints nothing.
    """
    binary = wiki / 'diagram.png'
    binary.write_bytes(b'\x89PNG\r\n\x1a\n\xff\xfe\x00\x01')
    try:
        result = _wiki(wiki, 'match', 'widget', '--path', str(wiki), '--all')
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'snippet.txt' in result.stdout
        assert result.stderr == ''
    finally:
        binary.unlink()


def test_match_names_a_page_it_cannot_decode(wiki: pathlib.Path) -> None:
    """``match`` names a page that is not UTF-8 on stderr and matches the rest.

    The page may hold the pattern, so skipping it in silence could turn
    a match into ``No matches found.``; the line says the page was not
    looked at, and the other results and the exit code are unchanged.
    """
    before = _wiki(wiki, 'match', 'widget', '--path', str(wiki))
    assert before.returncode == 0, before.stdout + before.stderr
    # a Latin-1 page holding the pattern, and a word no other page holds
    page = wiki / 'core' / 'legacy.md'
    page.write_bytes('The widget caf\xe9 serves gizmos.\n'.encode('latin-1'))
    skip = 'Skipping core/legacy.md: unreadable, or not valid UTF-8'
    try:
        # the other pages match as before, under the line naming the page
        result = _wiki(wiki, 'match', 'widget', '--path', str(wiki))
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == before.stdout
        assert result.stderr.splitlines() == [skip]
        # a pattern only that page holds is no match, with the reason above it
        result = _wiki(wiki, 'match', 'gizmos', '--path', str(wiki))
        assert result.returncode == 1, result.stdout + result.stderr
        assert result.stderr.splitlines() == [skip, 'No matches found.']
    finally:
        page.unlink()


# ------ search


def test_search_ranked_and_json_output(wiki: pathlib.Path) -> None:
    """Search returns ranked snippets and structured output for agents."""
    # the rendered mode lists ranked snippets
    rendered = _wiki(wiki, 'search', 'widget', '--path', str(wiki))
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    assert 'core/design.md' in rendered.stdout
    assert '>>widget<<' in rendered.stdout

    # --json emits the typed rows
    structured = _wiki(
        wiki,
        'search',
        'widg',
        '--prefix',
        '--json',
        '--path',
        str(wiki),
    )
    assert structured.returncode == 0, structured.stdout + structured.stderr
    results = json.loads(structured.stdout)
    assert results[0]['path'] == 'core/design.md'
    assert set(results[0]) == {'path', 'snippet', 'score'}
    # the raw BM25 float rides through JSON; only the text renderer rounds
    text_mode = _wiki(wiki, 'search', 'widg', '--prefix', '--path', str(wiki))
    assert text_mode.returncode == 0, text_mode.stdout + text_mode.stderr
    score = results[0]['score']
    rendered_score = text_mode.stdout.split()[0]
    assert rendered_score == f'{score:.3f}'


def test_search_no_match_exits_nonzero(wiki: pathlib.Path) -> None:
    """A query with no hits exits 1 with the notice on stderr.

    The grep convention: scripts distinguish no-match from match by exit
    code, and stdout stays reserved for matches so a page named
    'No matches found.' can never be mistaken for the notice.
    """
    result = _wiki(wiki, 'search', 'zzz_no_such_token', '--path', str(wiki))
    assert result.returncode == 1
    assert 'No matches' in result.stderr
    assert result.stdout == ''


def test_search_json_prints_an_empty_list_for_no_match(wiki: pathlib.Path) -> None:
    """``search --json`` with no hits prints ``[]``, a document as any other.

    A consumer parses stdout whatever the exit code, and empty output is
    not JSON. The notice and exit 1 stand beside the document.
    """
    args = ('search', 'zzz_no_such_token', '--json', '--path', str(wiki))
    result = _wiki(wiki, *args)
    assert result.returncode == 1
    assert result.stdout == '[]\n'
    assert json.loads(result.stdout) == []
    assert result.stderr == 'No matches found.\n'


def test_search_invalid_query_reports_error(wiki: pathlib.Path) -> None:
    """Invalid raw FTS input is an error (exit 2), distinct from a clean no-match.

    Grep reserves exit 2 for errors so a script following the documented
    branch-on-exit-code contract never reads a failed search (bad query, no
    wiki) as an absent term (exit 1).
    """
    result = _wiki(wiki, 'search', '[', '--raw', '--path', str(wiki))
    assert result.returncode == 2
    assert 'Error:' in result.stderr


def test_search_prefix_and_raw_are_mutually_exclusive(wiki: pathlib.Path) -> None:
    """--prefix and --raw cannot be combined (usage error, exit 2)."""
    result = _wiki(
        wiki,
        'search',
        'widget',
        '--prefix',
        '--raw',
        '--path',
        str(wiki),
    )
    assert result.returncode == 2
    assert 'mutually exclusive' in (result.stdout + result.stderr).lower()


def test_search_limit_must_be_positive(wiki: pathlib.Path) -> None:
    """--limit rejects values below 1 (usage error, exit 2)."""
    result = _wiki(
        wiki,
        'search',
        'widget',
        '--limit',
        '0',
        '--path',
        str(wiki),
    )
    assert result.returncode == 2
    assert '--limit must be >= 1' in (result.stdout + result.stderr)


# ------ number arguments


@pytest.mark.parametrize(
    argnames='args',
    argvalues=[
        ['map', '--depth', ' 1_0'],
        ['map', '--depth', '\u0661'],
        ['map', '--depth', ''],
        ['map', '--desc-limit', '1.0'],
        ['map', '--desc-limit', '9223372036854775808'],
        ['search', 'widget', '--limit', '1e1'],
    ],
    ids=[
        'padded-and-underscored',
        'non-ascii-digit',
        'empty',
        'decimal-point',
        'past-64-bits',
        'exponent',
    ],
)
def test_number_options_take_plain_digits_only(
    wiki: pathlib.Path,
    args: list[str],
) -> None:
    """A number option is an optional sign and ASCII digits, in 64 bits.

    Anything else Python's ``int`` would read -- padding, ``_`` between
    digits, a digit of another script (here an Arabic-Indic one) -- is a
    usage error that names the option and shows the value, as is a
    number outside the signed 64-bit range.
    """
    option, value = args[-2:]
    result = _wiki(wiki, *args, '--path', str(wiki))
    assert result.returncode == 2
    assert result.stdout == ''
    error = f"Error: Invalid value for '{option}': {value!r} is not a valid integer."
    assert result.stderr.splitlines()[-1] == error


@pytest.mark.parametrize(
    argnames=('args', 'plain'),
    argvalues=[
        (['map', '--depth', '+01'], ['map', '--depth', '1']),
        (['map', '--desc-limit', '-01'], ['map', '--desc-limit', '-1']),
        (['map', '--depth', '9223372036854775807'], ['map']),
        (['search', 'widget', '--limit', '01'], ['search', 'widget', '--limit', '1']),
        (
            ['read', 'core/design', '--lines', '+1:03'],
            ['read', 'core/design', '--lines', '1:3'],
        ),
        (
            ['read', 'core/design', '--lines', '-02:'],
            ['read', 'core/design', '--lines', '-2:'],
        ),
    ],
    ids=[
        'depth-signed',
        'desc-limit-negative',
        'depth-at-the-64-bit-bound',
        'limit-zero-led',
        'slice-signed',
        'slice-negative',
    ],
)
def test_number_arguments_accept_a_sign_and_leading_zeros(
    wiki: pathlib.Path,
    args: list[str],
    plain: list[str],
) -> None:
    """A sign and leading zeros spell the same number as its plain digits."""
    result = _wiki(wiki, *args, '--path', str(wiki))
    expected = _wiki(wiki, *plain, '--path', str(wiki))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == expected.stdout


@pytest.mark.parametrize(
    argnames=('command', 'options'),
    argvalues=[
        ('map', ['--depth', '--desc-limit']),
        ('search', ['--limit']),
    ],
    ids=['map', 'search'],
)
def test_number_options_are_labeled_integer(
    wiki: pathlib.Path,
    command: str,
    options: list[str],
) -> None:
    """Help labels each number option ``INTEGER``."""
    result = _wiki(wiki, command, '--help')
    assert result.returncode == 0, result.stdout + result.stderr
    for option in options:
        assert re.search(rf'^ +{option} INTEGER ', result.stdout, re.MULTILINE)


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


def test_read_lines_count_as_match_numbers_them(tmp_path: pathlib.Path) -> None:
    """``read --lines`` counts lines as ``match --lines`` numbers them.

    Both break a page on newlines alone, so a form feed inside a line
    shifts neither count, and the slice returns the line with the form
    feed where the page has it.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', 'notes', '--path', str(root)).returncode == 0
    body = 'alpha\nbe\x0cta\ngamma'
    _write(root / 'p.md', _page(name='p', desc='A page.', body=body))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    # the slice's index 0 is the first body line, the H1
    result = _wiki(root, 'match', '^# p$|^gamma$', '--path', str(root), '--lineno')
    assert result.returncode == 0, result.stdout + result.stderr
    heading, gamma = (
        int(hit.removeprefix('p.md:'))
        for hit in result.stdout.splitlines()
        if hit.startswith('p.md:')
    )
    index = gamma - heading
    # the two lines up to that index: the form-feed line whole, then gamma
    spec = f'{index - 1}:{index + 1}'
    result = _wiki(root, 'read', 'p', '--path', str(root), '--lines', spec)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.endswith('---\n\nbe\x0cta\ngamma\n')


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
        # an empty slice value is a malformed slice, not an absent one
        ('core/design', ['--lines', ''], 2, 'slice format'),
        ('core/design', ['--words', ''], 2, 'slice format'),
        ('core/design', ['--chars', ''], 2, 'slice format'),
        # ... and still excludes another unit
        ('core/design', ['--lines', '', '--words', '0:2'], 2, 'mutually exclusive'),
        # a slice bound is an optional sign and plain digits, in 64 bits
        ('core/design', ['--lines', ' +0:\u0661'], 2, 'slice format'),
        ('core/design', ['--chars', '1_0:'], 2, 'slice format'),
        ('core/design', ['--words', ':9223372036854775808'], 2, 'slice format'),
        # a missing entry is a clean runtime error, not a traceback
        ('core/missing_entry', [], 2, 'not found'),
        # a name escaping the wiki root is refused, not resolved
        ('../escape', [], 2, 'outside wiki root'),
        # a blank name is not found (it must not resolve to the root index)
        (' ', [], 2, 'not found'),
    ],
    ids=[
        'non-colon-slice',
        'non-integer-slice',
        'empty-lines-slice',
        'empty-words-slice',
        'empty-chars-slice',
        'empty-slice-beside-another',
        'padded-and-non-ascii-slice',
        'underscored-slice',
        'slice-past-64-bits',
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


def test_read_checks_its_slice_before_the_wiki(tmp_path: pathlib.Path) -> None:
    """A malformed slice is reported as one whatever ``--path`` names.

    The slice is checked with the other arguments, before the wiki is
    looked for, so a command line that is wrong in two ways is reported
    as malformed first.
    """
    missing = tmp_path / 'nonexistent'
    result = _wiki(tmp_path, 'read', 'a/p', '--path', str(missing), '--lines', 'zz')
    assert result.returncode == 2
    assert "Invalid slice format: 'zz'" in result.stderr
    assert 'No wiki' not in result.stderr
    # a well-formed slice leaves the missing wiki as the error
    result = _wiki(tmp_path, 'read', 'a/p', '--path', str(missing), '--lines', '0:1')
    assert result.returncode == 2
    assert 'No wiki' in result.stderr


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
        ['match', 'widget', '-l'],
        ['match', 'widget', '-n'],
        ['map', '-c', 'guides'],
    ],
    ids=['match-lines', 'match-lineno', 'map-category'],
)
def test_colliding_short_flags_are_rejected(
    wiki: pathlib.Path,
    args: list[str],
) -> None:
    """Colliding short flags do not exist; only the long options do.

    Every short alias is unique across the entire CLI (read: l/w/c;
    match: f/i/a), so match's ``-l``/``-n`` and map's ``-c`` -- whose
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


# ------ piped output


def test_piped_output_keeps_a_pages_escape_sequences(tmp_path: pathlib.Path) -> None:
    """Output through a pipe carries the escape sequences a page holds.

    A script or an agent reads a command through a pipe or a file, where
    it needs the page's own bytes: ``wiki read page > file`` must give the
    page back, color sequences included.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a page with a color sequence in its desc (YAML's \e) and in its body
    desc = '"A \\e[31mred\\e[0m page."'
    line = 'An \x1b[31malert\x1b[0m line.'
    _write(root / 'alert.md', f'---\ndesc: {desc}\n---\n\n# alert\n\n{line}\n')
    assert _wiki(root, 'update').returncode == 0
    text = (root / 'alert.md').read_text(encoding='utf-8')
    # read: the page as it is on disk
    read = _wiki(root, 'read', 'alert')
    assert read.returncode == 0, read.stdout + read.stderr
    assert read.stdout == text
    # match --lines: the matching line as written
    number = text.split('\n').index(line) + 1
    match = _wiki(root, 'match', 'alert', '--lines')
    assert match.returncode == 0, match.stdout + match.stderr
    assert f'alert.md:{number}: {line}\n' in match.stdout
    # map: the desc as YAML decodes it
    tree = _wiki(root, 'map')
    assert tree.returncode == 0, tree.stdout + tree.stderr
    assert 'A \x1b[31mred\x1b[0m page.' in tree.stdout
    # search: the body snippet around the hit
    search = _wiki(root, 'search', 'line')
    assert search.returncode == 0, search.stdout + search.stderr
    assert '\x1b[31malert\x1b[0m' in search.stdout


@pytest.mark.parametrize(
    argnames='args',
    argvalues=[
        ('--help',),
        ('read', '--help'),
        ('map', '--depth', 'deep'),
        (),
        ('--version',),
    ],
    ids=['help', 'command-help', 'usage-error', 'no-command', 'version'],
)
def test_help_and_errors_hold_no_escape_byte(
    tmp_path: pathlib.Path,
    args: tuple[str, ...],
) -> None:
    """Help, a usage error and the version print plain text through a pipe.

    The command line styles nothing of its own, so the only escape
    sequences a pipe ever carries are a page's.
    """
    result = _wiki(tmp_path, *args)
    assert result.stdout + result.stderr
    assert '\x1b' not in result.stdout + result.stderr


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
    enabled = json.loads(cp_file.read_text(encoding='utf-8'))
    assert plugin_id in enabled
    assert (root / '.obsidian' / 'plugins' / plugin_id / 'data.json').is_file()
    # the bundled plugin is copied from the package and enabled on every run
    bundled = root / '.obsidian' / 'plugins' / 'wiki-root-links'
    assert 'wiki-root-links' in enabled
    assert (bundled / 'main.js').is_file()
    assert (bundled / 'manifest.json').is_file()


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
    enabled = json.loads(cp_file.read_text(encoding='utf-8'))
    assert plugin_id in enabled
    # the bundled plugin is copied from the package and enabled as well
    bundled = root / '.obsidian' / 'plugins' / 'wiki-root-links'
    assert 'wiki-root-links' in enabled
    assert (bundled / 'main.js').is_file()
    assert (bundled / 'manifest.json').is_file()
    # the merge driver setup completes: repo config plus attribute map
    driver = _git(tmp_path, 'config', 'merge.wiki.driver').stdout
    assert 'wiki _merge %O %A %B %L %P' in driver
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
def test_init_and_config_append_to_gitattributes_that_is_not_utf8(
    tmp_path: pathlib.Path,
) -> None:
    """A ``.gitattributes`` that is not UTF-8 gains its mapping, bytes intact.

    Git reads the file as bytes, so a comment in another encoding is a
    working file: ``init`` and ``config`` append the block it lacks and
    leave every existing byte as it is.
    """
    # a repo whose committed .gitattributes holds a Latin-1 comment
    _git_repo(tmp_path)
    latin1 = '# café assets\n*.png binary\n'.encode('latin-1')
    attributes = tmp_path / '.gitattributes'
    attributes.write_bytes(latin1)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'attributes')
    root = tmp_path / 'wiki'

    # init appends the index mapping after the file's own bytes
    init = _wiki(tmp_path, 'init', '--path', str(root))
    assert init.returncode == 0, init.stdout + init.stderr
    assert 'codec' not in init.stdout + init.stderr
    index_block = b'\n# Wiki index merge driver\n**/_index.md merge=wiki\n'
    assert attributes.read_bytes() == latin1 + index_block

    # config does the same for the wiki's own file, once it holds such bytes
    (root / '.gitattributes').write_bytes(latin1)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'wiki')
    config = _wiki(root, 'config', '--path', str(root))
    assert config.returncode == 0, config.stdout + config.stderr
    assert 'codec' not in config.stdout + config.stderr
    page_block = b'\n# Wiki page merge driver\n*.md merge=wiki\n'
    assert (root / '.gitattributes').read_bytes() == latin1 + page_block
    assert attributes.read_bytes() == latin1 + index_block


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
@pytest.mark.parametrize(
    argnames=('side', 'block'),
    argvalues=[
        ('theirs', 'name: |\n  core\n'),
        ('ours', 'name: |\n  core\n'),
        ('theirs', 'name : core\n'),
        ('theirs', 'name:\n  # which name\n  core\n'),
        ('ours', 'name:\n  # which name\n  core\n'),
    ],
    ids=[
        'theirs-block',
        'ours-block',
        'theirs-spaced-colon',
        'theirs-comment-then-body',
        'ours-comment-then-body',
    ],
)
def test_merge_driver_moves_regenerated_keys_with_their_bodies(
    tmp_path: pathlib.Path,
    side: str,
    block: str,
) -> None:
    """A regenerated key is normalized as a whole extent, indented body included.

    The driver normalizes ``name:`` to ours on every input; a value
    written as a block scalar spans two lines, and swapping the key line
    alone would strand the other side's body under ours' one-liner,
    where a strict reader folds it into the name. Ours' whole extent
    replaces theirs' whole extent -- whichever side wrote the block, and
    whether theirs spells the key ``name:`` or ``name :`` -- and
    ``wiki update`` then joins a surviving block onto one line.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    index = root / 'core' / '_index.md'
    base = (
        '---\n'
        'name: core\n'
        'desc: Original section.\n'
        'tags: []\n'
        'sources: []\n'
        'created: 2026-01-01T00:00:00Z\n'
        'updated: 2026-01-01T00:00:00Z\n'
        '---\n'
        '\n'
        '# core\n'
        '\n'
        '***\n'
        '\n'
        'Body prose.\n'
    )
    _write(index, base)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # one side writes the name in its own shape; each side edits something
    # of its own so the merge reaches the driver
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    theirs = base.replace('desc: Original section.', 'desc: Edited by theirs.')
    if side == 'theirs':
        theirs = theirs.replace('name: core\n', block)
    _write(index, theirs)
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    ours = base.replace(
        'updated: 2026-01-01T00:00:00Z',
        'updated: 2026-01-03T12:00:00Z',
    )
    if side == 'ours':
        ours = ours.replace('name: core\n', block)
    _write(index, ours)
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge is clean and carries exactly ours' name extent
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    frontmatter = index.read_text(encoding='utf-8').split('---\n')[1]
    assert len(re.findall(r'^name[ \t]*:', frontmatter, re.M)) == 1
    if side == 'ours':
        assert block in frontmatter
    else:
        assert frontmatter.startswith('name: core\n')
        assert '\n  core\n' not in frontmatter
    assert 'desc: Edited by theirs.' in frontmatter

    # update joins a surviving block onto one line and converges
    update = _wiki(root, 'update', '--path', str(root))
    assert update.returncode == 0, update.stdout + update.stderr
    frontmatter = index.read_text(encoding='utf-8').split('---\n')[1]
    assert frontmatter.startswith('name: core\n')
    assert '\n  core\n' not in frontmatter
    check = _wiki(root, 'update', '--path', str(root), '--check')
    assert check.returncode == 0, check.stdout + check.stderr


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_driver_takes_the_other_sides_row_edit(tmp_path: pathlib.Path) -> None:
    """A row both sides carry takes theirs' text when only theirs changed it against the base.

    An asset row's desc is authored in the index alone -- ``wiki update``
    regenerates no desc for it -- so ours' unchanged copy must not win over
    theirs' edit, or the edit is lost with a clean merge.
    """
    root = tmp_path / 'wiki'
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    (root / 'core').mkdir()
    (root / 'core' / 'diagram.png').write_bytes(b'\x89PNG\r\n')
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    index = root / 'core' / '_index.md'
    base = index.read_text(encoding='utf-8')
    row = '[[core/diagram.png|diagram.png]]: ...'
    assert row in base
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # theirs authors the asset row's desc; ours adds a row directly under it
    # (no blank between the rows) and edits the prose below ***
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    _write(index, base.replace(row, '[[core/diagram.png|diagram.png]]: The diagram.'))
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    (root / 'core' / 'zeta.png').write_bytes(b'\x89PNG\r\n')
    ours = base.replace(f'{row}\n\n', f'{row}\n[[core/zeta.png|zeta.png]]: Zeta.\n\n')
    _write(index, ours + '\nOurs wrote prose.\n')
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'ours')

    # the merge is clean and carries theirs' desc on the shared row, in
    # ours' layout
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = index.read_text(encoding='utf-8')
    assert (
        '[[core/diagram.png|diagram.png]]: The diagram.\n[[core/zeta.png|zeta.png]]: Zeta.\n\n'
        in merged
    )
    assert 'Ours wrote prose.' in merged
    # update re-spaces the rows and keeps the authored asset desc
    update = _wiki(root, 'update', '--path', str(root))
    assert update.returncode == 0, update.stdout + update.stderr
    assert '[[core/diagram.png|diagram.png]]: The diagram.' in index.read_text(
        encoding='utf-8'
    )
    check = _wiki(root, 'update', '--path', str(root), '--check')
    assert check.returncode == 0, check.stdout + check.stderr


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames=('base_block', 'crlf'),
    argvalues=[
        ('name: core\ndesc: Original section.', False),
        ('desc: Original section.', False),
        ('name: core\ndesc: Original section.\ncreated:\nupdated:', False),
        ('name: core\ndesc: Original section.\ncreated:\nupdated:', True),
    ],
    ids=['stampless', 'nameless', 'valueless-stamps', 'valueless-stamps-crlf'],
)
def test_merge_driver_normalizes_created_over_a_stampless_base(
    tmp_path: pathlib.Path,
    base_block: str,
    crlf: bool,
) -> None:
    """A base index without stamps (or a name) gets them from each side's own update: churn, not a conflict.

    A side lacking a regenerated key gains ours' copy where ``wiki
    update`` puts it -- ``name:`` under the opening fence, the stamps
    above the closing one -- so the other side adding the key is no
    change beside its authored edit.
    """
    root = tmp_path / 'wiki'
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    index = root / 'core' / '_index.md'
    base = f'---\n{base_block}\n---\n\n# core\n\n***\n\nBody prose.\n'
    if crlf:
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_bytes(base.replace('\n', '\r\n').encode('utf-8'))
    else:
        _write(index, base)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # each side's wiki update names and stamps the block on its own clock
    def updated(text: str, clock: str) -> str:
        stamps = f'created: {clock}\nupdated: {clock}\n'
        if 'created:' in text:
            text = text.replace('created:\nupdated:\n', stamps)
        else:
            text = text.replace('---\n\n# core', f'{stamps}---\n\n# core')
        if 'name:' not in text:
            text = text.replace('---\n', '---\nname: core\n', 1)
        return text

    # a CRLF base is normalized to LF by both sides' updates identically, so
    # only the stamps differ there (an authored edit beside re-ended lines is
    # an ordinary three-way conflict); an LF base carries authored edits too
    edit = 'Original section.' if crlf else 'Edited by theirs.'
    prose = '' if crlf else 'Ours wrote prose.\n'
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    theirs = updated(base.replace('Original section.', edit), '2026-01-01T00:00:07Z')
    _write(index, theirs)
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _write(index, updated(base, '2026-01-01T00:00:00Z') + prose)
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge is clean: ours' name and stamps, theirs' desc, both bodies
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = index.read_text(encoding='utf-8')
    assert merged.startswith('---\nname: core\n')
    assert 'created: 2026-01-01T00:00:00Z\n' in merged
    assert merged.count('created:') == 1
    assert merged.count('name:') == 1
    assert f'desc: {edit}' in merged
    if prose:
        assert prose in merged


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames=('timestamp', 'stamps', 'kept', 'conflict'),
    argvalues=[
        (
            None,
            ('2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', '2026-01-02T09:00:00Z'),
            '2026-01-02T09:00:00Z',
            False,
        ),
        (
            None,
            ('2026-01-01T00:00:00Z', '2026-01-02T09:00:00Z', '2026-01-03T12:00:00Z'),
            '2026-01-03T12:00:00Z',
            False,
        ),
        (
            None,
            ('2026-01-01T00:00:00Z', '2026-01-03T12:00:00Z', '2026-01-02T09:00:00Z'),
            '2026-01-03T12:00:00Z',
            False,
        ),
        (
            {'format': '%Y-%m-%dT%H:%M:%S%z'},
            (
                '2026-01-01T00:00:00+0000',
                '2026-01-02T10:00:00+0200',
                '2026-01-02T09:00:00+0000',
            ),
            '2026-01-02T09:00:00+0000',
            False,
        ),
        (
            None,
            ('2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'soon'),
            '2026-01-01T00:00:00Z',
            False,
        ),
        (
            None,
            ('2026-01-01T00:00:00Z', 'soon', '2026-01-02T09:00:00Z'),
            'soon',
            False,
        ),
        (
            None,
            ('2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', '2026-01-02T09:00:00Z'),
            '2026-01-02T09:00:00Z',
            True,
        ),
    ],
    ids=[
        'only-theirs-moved',
        'theirs-later',
        'ours-later',
        'offsets',
        'theirs-unparseable',
        'ours-unparseable',
        'conflicted',
    ],
)
def test_merge_driver_keeps_the_later_updated_stamp(
    tmp_path: pathlib.Path,
    timestamp: Optional[dict],
    stamps: tuple[str, str, str],
    kept: str,
    conflict: bool,
) -> None:
    """A merged index keeps the later of its two sides' ``updated`` stamps.

    The stamps compare as instants under the wiki's timestamp policy, so
    an offset stamp sorts by its moment rather than its text, and a
    stamp either side spells in a form the policy does not parse leaves
    ours. A merge that conflicts carries the later stamp beside its
    markers.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    _git_repo(tmp_path)
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    if timestamp is not None:
        settings_path = root / '.wiki' / 'settings.json'
        settings = json.loads(settings_path.read_text(encoding='utf-8'))
        settings['timestamp'] = timestamp
        _write(settings_path, json.dumps(settings))
    index = root / 'core' / '_index.md'
    base_stamp, ours_stamp, theirs_stamp = stamps
    base = (
        '---\n'
        'name: core\n'
        'desc: Original section.\n'
        f'updated: {base_stamp}\n'
        '---\n'
        '\n'
        '# core\n'
        '\n'
        '***\n'
        '\n'
        'Body prose.\n'
    )
    _write(index, base)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # theirs edits the desc and ours the body, each under its own stamp; for
    # a conflict, theirs rewords the body too
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    theirs = base.replace(f'updated: {base_stamp}', f'updated: {theirs_stamp}')
    theirs = theirs.replace('desc: Original section.', 'desc: Edited by theirs.')
    if conflict:
        theirs = theirs.replace('Body prose.', 'Their prose.')
    _write(index, theirs)
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    ours = base.replace(f'updated: {base_stamp}', f'updated: {ours_stamp}')
    _write(index, ours.replace('Body prose.', 'Our prose.'))
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge keeps one stamp, the later, and conflicts only on the body
    merge = _git(tmp_path, 'merge', 'theirs')
    assert (merge.returncode != 0) == conflict, merge.stdout + merge.stderr
    merged = index.read_text(encoding='utf-8')
    assert re.findall(r'^updated:.*$', merged, re.M) == [f'updated: {kept}']
    assert 'desc: Edited by theirs.' in merged
    assert ('<<<<<<< ours' in merged) == conflict


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames=('ours_stamp', 'theirs_stamp'),
    argvalues=[
        ('2026-01-02T00:00:00Z', '2026-01-01T00:00:00Z'),
        ('2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z'),
    ],
    ids=['theirs-earlier', 'ours-earlier'],
)
def test_merge_driver_keeps_the_earlier_created_over_a_stampless_base(
    tmp_path: pathlib.Path,
    ours_stamp: str,
    theirs_stamp: str,
) -> None:
    """Sides that each stamped an unstamped base index keep the earlier ``created``.

    Each side's ``wiki update`` seeds both stamps on its own clock, as
    when sibling branches create one folder's index, so the earlier
    ``created`` is when the index came to be, and the later ``updated``
    is its last write.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    _git_repo(tmp_path)
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    index = root / 'core' / '_index.md'
    base = '---\nname: core\ndesc: Original section.\n---\n\n# core\n\n***\n\nBody prose.\n'
    _write(index, base)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # each side stamps the block on its own clock beside an edit of its own
    def stamped(text: str, clock: str) -> str:
        stamps = f'created: {clock}\nupdated: {clock}\n'
        return text.replace('---\n\n# core', f'{stamps}---\n\n# core')

    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    theirs = base.replace('desc: Original section.', 'desc: Edited by theirs.')
    _write(index, stamped(theirs, theirs_stamp))
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _write(index, stamped(base.replace('Body prose.', 'Our prose.'), ours_stamp))
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge is clean, with the earlier created and the later updated
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = index.read_text(encoding='utf-8')
    assert re.findall(r'^created:.*$', merged, re.M) == [
        'created: 2026-01-01T00:00:00Z'
    ]
    assert re.findall(r'^updated:.*$', merged, re.M) == [
        'updated: 2026-01-02T00:00:00Z'
    ]
    assert 'desc: Edited by theirs.' in merged
    assert 'Our prose.' in merged


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames=('endings', 'blank'),
    argvalues=[('\r\n', '\r\n'), ('\n', ' \n')],
    ids=['crlf', 'whitespace-separator'],
)
def test_merge_driver_keeps_a_row_edit_on_a_crlf_index(
    tmp_path: pathlib.Path,
    endings: str,
    blank: str,
) -> None:
    r"""The row rule compares rows without their trailing blanks, CRLF and whitespace-only ones too.

    A CRLF blank line is ``\r\n`` and a whitespace-only separator holds
    spaces: a strip that only knows ``\n`` leaves ours' re-spaced block
    unequal to base's, and theirs' authored row edit silently loses to
    ours' unchanged text.
    """
    root = tmp_path / 'wiki'
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    (root / 'core').mkdir()
    index = root / 'core' / '_index.md'
    row = '[[core/diagram.png|diagram.png]]: Base diagram.'
    base = (
        '---\nname: core\ndesc: Core.\ncreated: 2026-01-01T00:00:00Z\n'
        f'updated: 2026-01-01T00:00:00Z\n---\n\n# core\n\n{row}\n***\n'
    ).replace('\n', endings)
    index.write_bytes(base.encode('utf-8'))
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # theirs edits the row's desc; ours only re-spaces the block
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    index.write_bytes(base.replace('Base diagram.', 'Theirs diagram.').encode('utf-8'))
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    index.write_bytes(
        base.replace(f'{row}{endings}', f'{row}{endings}{blank}').encode('utf-8')
    )
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge is clean and theirs' edit lands in ours' spacing
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = index.read_bytes().decode('utf-8')
    assert (
        f'[[core/diagram.png|diagram.png]]: Theirs diagram.{endings}{blank}' in merged
    )
    assert 'Base diagram.' not in merged


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_driver_unions_rows_of_an_add_add_merge(tmp_path: pathlib.Path) -> None:
    """Two indexes created independently merge to both sides' rows, the H1, and the ``***`` line kept.

    With no base file the driver's first input is empty; the parts must
    still be told apart, or ours' link block is read as a side to
    collect and never printed.
    """
    root = tmp_path / 'wiki'
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')
    index = root / 'core' / '_index.md'
    frontmatter = (
        '---\nname: core\ndesc: Core section.\ntags: []\nsources: []\n'
        'created: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n---\n\n# core\n\n'
    )

    # each side creates the index with its own child row
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    _write(index, frontmatter + '[[core/beta|beta]]: Beta page.\n\n***\n')
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _write(index, frontmatter + '[[core/alpha|alpha]]: Alpha page.\n\n***\n')
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'ours')

    # the merge keeps the H1, both rows, and the delimiter
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = index.read_text(encoding='utf-8')
    assert '\n# core\n' in merged
    assert '[[core/alpha|alpha]]: Alpha page.' in merged
    assert '[[core/beta|beta]]: Beta page.' in merged
    assert merged.rstrip().endswith('***')


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_driver_leaves_a_shared_blank_line_alone(tmp_path: pathlib.Path) -> None:
    """A blank line every side shares under ``name:`` never becomes a conflict.

    The normalization moves a regenerated key with the indented lines under
    it; a blank line that separates the key from the next field is not
    part of the value, so it must stay on every input -- dropping it from
    base and theirs alone would make a shared line look like ours' edit.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    index = root / 'core' / '_index.md'
    base = (
        '---\n'
        'name: core\n'
        '\n'
        'desc: Original section.\n'
        'created: 2026-01-01T00:00:00Z\n'
        'updated: 2026-01-01T00:00:00Z\n'
        '---\n'
        '\n'
        '# core\n'
        '\n'
        '***\n'
        '\n'
        'Body prose.\n'
    )
    _write(index, base)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # theirs edits the desc, ours carries regenerated churn only
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    _write(index, base.replace('desc: Original section.', 'desc: Edited by theirs.'))
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _write(
        index,
        base.replace('updated: 2026-01-01T00:00:00Z', 'updated: 2026-01-03T12:00:00Z'),
    )
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge is clean, theirs' desc lands, and the blank line is still one
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    frontmatter = index.read_text(encoding='utf-8').split('---\n')[1]
    assert frontmatter.startswith('name: core\n\ndesc: Edited by theirs.\n')
    assert 'updated: 2026-01-03T12:00:00Z' in frontmatter


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames=('separator', 'theirs_extra', 'ours_name', 'survives'),
    argvalues=[
        ('\r\n', '', 'name: core\r\n', 'name: core\r\n\r\ndesc: Edited by theirs.\r\n'),
        (
            '\n',
            '  # note\n',
            'name: core\n',
            'name: core\n  # note\n\ndesc: Edited by theirs.\n',
        ),
        ('\n', '', 'name: |\n' + '  core\n' * 40_000, 'name: |\n  core\n  core\n'),
        (
            '\n',
            '# section\n',
            'name: core\n# section\n',
            'name: core\n# section\n\ndesc: Edited by theirs.\n',
        ),
    ],
    ids=['crlf-separator', 'theirs-comment', 'huge-extent', 'shared-comment'],
)
def test_merge_driver_carries_separators_and_comments_verbatim(
    tmp_path: pathlib.Path,
    separator: str,
    theirs_extra: str,
    ours_name: str,
    survives: str,
) -> None:
    """The normalization moves bytes verbatim and keeps what is authored.

    A shared separator line keeps its CRLF ending on every side, so it
    never reads as ours' edit; a comment theirs wrote under a regenerated
    key is authored and reaches the merge; and an extent of any size
    travels through a file, not the environment.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    index = root / 'core' / '_index.md'
    index.parent.mkdir()
    base = separator.join(
        [
            '---',
            'name: core',
            '',
            'desc: Original section.',
            'created: 2026-01-01T00:00:00Z',
            'updated: 2026-01-01T00:00:00Z',
            '---',
            '',
            '# core',
            '',
            '***',
            '',
            'Body prose.',
            '',
        ]
    )
    index.write_bytes(base.encode('utf-8'))
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # theirs edits the desc (and may annotate the name); ours regenerates
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    theirs = base.replace('desc: Original section.', 'desc: Edited by theirs.')
    theirs = theirs.replace(
        f'name: core{separator}', f'name: core{separator}{theirs_extra}'
    )
    index.write_bytes(theirs.encode('utf-8'))
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    ours = base.replace(
        'updated: 2026-01-01T00:00:00Z', 'updated: 2026-01-03T12:00:00Z'
    )
    ours = ours.replace(f'name: core{separator}', ours_name)
    index.write_bytes(ours.encode('utf-8'))
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge is clean and the bytes that must survive do
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = index.read_bytes().decode('utf-8')
    assert survives in merged
    assert 'desc: Edited by theirs.' in merged
    assert 'updated: 2026-01-03T12:00:00Z' in merged


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames=('theirs_comment', 'present', 'absent'),
    argvalues=[
        ('', 'name: core\ndesc: Edited by theirs.\n', '# section slug'),
        (
            '# section renamed\n',
            'name: core\n# section renamed\ndesc:',
            '# section slug',
        ),
        ('  # section slug\n', 'name: core\n  # section slug\ndesc:', '\n\n'),
    ],
    ids=['deleted', 'reworded', 'indented-shared'],
)
def test_merge_driver_merges_comments_as_authored_lines(
    tmp_path: pathlib.Path,
    theirs_comment: str,
    present: str,
    absent: str,
) -> None:
    """A comment under a regenerated key is authored: a deletion or rewording on one side lands.

    The driver normalizes the key's value lines alone; a comment line
    (indented or not) is outside the extent, so a side that deletes or
    rewords it wins the ordinary three-way merge instead of having ours'
    copy resurrect it or a spurious conflict raised.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    index = root / 'core' / '_index.md'
    index.parent.mkdir()
    base_comment = (
        '  # section slug\n' if theirs_comment.startswith('  ') else '# section slug\n'
    )
    base = (
        f'---\nname: core\n{base_comment}desc: Original section.\n'
        'created: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n---\n\n'
        '# core\n\n***\n\nBody prose.\n'
    )
    _write(index, base)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # theirs edits the comment and the desc; ours regenerates the stamp
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    theirs = base.replace(base_comment, theirs_comment)
    theirs = theirs.replace('desc: Original section.', 'desc: Edited by theirs.')
    _write(index, theirs)
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _write(
        index,
        base.replace('updated: 2026-01-01T00:00:00Z', 'updated: 2026-01-03T12:00:00Z'),
    )
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge is clean and theirs' comment edit lands beside ours' stamp
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = index.read_text(encoding='utf-8')
    assert present in merged
    assert absent not in merged.split('---\n')[1]
    assert 'updated: 2026-01-03T12:00:00Z' in merged


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_driver_replaces_a_block_body_whole(tmp_path: pathlib.Path) -> None:
    """An indented comment inside a block-scalar name is value, replaced with the rest of the body."""
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    index = root / 'core' / '_index.md'
    index.parent.mkdir()
    base = (
        '---\nname: core\ndesc: Original section.\n'
        'created: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n---\n\n'
        '# core\n\n***\n\nBody prose.\n'
    )
    _write(index, base)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # theirs writes the name as a block whose body holds a hash line
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    theirs = base.replace('name: core\n', 'name: |\n  core\n  # note\n')
    _write(index, theirs.replace('desc: Original section.', 'desc: Edited by theirs.'))
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _write(
        index,
        base.replace('updated: 2026-01-01T00:00:00Z', 'updated: 2026-01-03T12:00:00Z'),
    )
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge is clean; ours' one-line name replaces theirs' whole block
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    frontmatter = index.read_text(encoding='utf-8').split('---\n')[1]
    assert frontmatter.startswith('name: core\ndesc: Edited by theirs.\n')
    assert '# note' not in frontmatter


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames=('theirs', 'ours', 'labels'),
    argvalues=[
        (('api', None), ('extra', None), ['api', 'design', 'extra']),
        (('api', 'ref'), ('extra', 'guide'), ['[guide] extra', '[ref] api', 'design']),
        (('alpha/', None), ('beta', None), ['alpha/', 'beta', 'design']),
    ],
    ids=['pages', 'categories', 'folder'],
)
def test_merge_orders_rows_as_update_writes_them(
    tmp_path: pathlib.Path,
    theirs: tuple[str, Optional[str]],
    ours: tuple[str, Optional[str]],
    labels: list[str],
) -> None:
    """A clean merge of two current indexes is current: its rows sit where update writes them.

    The driver keeps both sides' rows, the other side's riding in below
    ours; left there, a row that sorts above ours' would fail
    ``update --check`` and ``lint`` on a merged tree that each side had
    passed. Desc continuations travel with their rows.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    _add_entry(root / 'core', 'design', None)
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # each side adds its own entry under core/ and updates
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    _add_entry(root / 'core', *theirs)
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _add_entry(root / 'core', *ours)
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'ours')

    # the merge is clean and lays the rows out in update's order
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = (root / 'core' / '_index.md').read_text(encoding='utf-8')
    assert _row_labels(merged) == ['..', *labels]

    # the merged tree is current as merged, continuation lines included
    check = _wiki(root, 'update', '--path', str(root), '--check')
    assert check.returncode == 0, check.stdout + check.stderr
    lint = _wiki(root, 'lint', '--path', str(root))
    assert lint.returncode == 0, lint.stdout + lint.stderr


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_keeps_each_sides_deletions(tmp_path: pathlib.Path) -> None:
    r"""A row one side deleted and the other left alone stays deleted.

    The union never drops a row only one side added, but a row base had
    is the deleting side's to remove: ridden back in, it would point at a
    page the merged tree no longer has, failing ``lint`` and
    ``update --check`` until an update pruned it. A desc carrying display
    math puts a ``\[`` line in the link block, which is no formatter's
    escape damage and so suspends no deletion, and a desc line that opens
    on a link to the deleted page is no row of it.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    for name in ('shared', 'zeta'):
        _add_entry(root / 'core', name, None)
    _write(
        root / 'core' / 'design.md',
        '---\ndesc: |\n  The design page draws on\n  [[core/zeta|zeta]] for its notes.\n---\n\nBody.\n',
    )
    _write(
        root / 'core' / 'math.md',
        '---\ndesc: |\n  The math page shows\n  \\[ x^2 \\]\n  is never negative.\n---\n\nBody.\n',
    )
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # theirs adds api and deletes zeta; ours adds extra and deletes shared
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    _add_entry(root / 'core', 'api', None)
    (root / 'core' / 'zeta.md').unlink()
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _add_entry(root / 'core', 'extra', None)
    (root / 'core' / 'shared.md').unlink()
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'ours')

    # the merge keeps both additions and both deletions, and is current
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = (root / 'core' / '_index.md').read_text(encoding='utf-8')
    assert _row_labels(merged) == ['..', 'api', 'design', 'extra', 'math']
    check = _wiki(root, 'update', '--path', str(root), '--check')
    assert check.returncode == 0, check.stdout + check.stderr
    lint = _wiki(root, 'lint', '--path', str(root))
    assert lint.returncode == 0, lint.stdout + lint.stderr


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames=('title', 'heading'),
    argvalues=[('Core Concepts', 'Core Concepts'), ('null', 'core')],
    ids=['retitled', 'untitled'],
)
def test_merge_carries_a_merged_title_into_the_h1(
    tmp_path: pathlib.Path,
    title: str,
    heading: str,
) -> None:
    """A clean merge's H1 follows the merged title, else the name.

    The H1 rides the current branch's layout through the driver, so when
    the other side retitles an index -- or unsets its title -- while this
    side changes its rows, a merged H1 left as it was would fail
    ``update --check`` on a tree that each side had passed.
    """
    root = tmp_path / 'wiki'
    index = root / 'core' / '_index.md'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(index, '---\ntitle: Core Things\ndesc: Core concepts.\n---\n\n***\n')
    _add_entry(root / 'core', 'design', None)
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # theirs sets the index's title; ours adds a page
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    base = index.read_text(encoding='utf-8')
    _write(index, base.replace('title: Core Things', f'title: {title}'))
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _add_entry(root / 'core', 'extra', None)
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'ours')

    # the merge is clean, its H1 follows the merged frontmatter, and the
    # merged tree is current as merged
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    assert f'\n# {heading}\n' in index.read_text(encoding='utf-8')
    check = _wiki(root, 'update', '--path', str(root), '--check')
    assert check.returncode == 0, check.stdout + check.stderr


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames=('hook', 'trusted', 'labels'),
    argvalues=[
        (_ORDERED_HOOK, True, ['[ref] api', '[guide] extra', 'design']),
        (_ORDERED_HOOK, False, ['[guide] extra', 'design', '[ref] api']),
        ('import nonexistent_module\n', True, ['[guide] extra', 'design', '[ref] api']),
        (_RAISING_HOOK, True, ['[guide] extra', 'design', '[ref] api']),
        (_LATE_RAISING_HOOK, True, ['[guide] extra', 'design', '[ref] api']),
        (_EXITING_HOOK, True, ['[guide] extra', 'design', '[ref] api']),
    ],
    ids=[
        'trusted',
        'untrusted',
        'import-fails',
        'sort-raises',
        'merge-raises',
        'hook-exits',
    ],
)
def test_merge_orders_rows_by_the_wikis_hook(
    tmp_path: pathlib.Path,
    hook: str,
    trusted: bool,
    labels: list[str],
) -> None:
    """The merge orders rows by the wiki's own class, and only a hook that runs.

    A ``.wiki/wiki.py`` hook may reorder categories, so the driver sorts
    with the class the hook names. A hook that does not run -- untrusted,
    failing on import, raising once loaded, on ours' rows or only on the
    merged ones, or exiting as it constructs -- leaves the rows as merged,
    the other side's below ours,
    for the update that reports it, and the merge itself stays clean: the
    driver's exit code is the merge's.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(root / 'core' / '_index.md', _index('Core', 'Core concepts.', 'Text.'))
    _add_entry(root / 'core', 'design', None)
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # each side adds its own categorized entry under core/ and updates
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    _add_entry(root / 'core', 'api', 'ref')
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _add_entry(root / 'core', 'extra', 'guide')
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'ours')

    # the hook arrives after both sides updated without it
    _write(root / '.wiki' / 'wiki.py', hook)
    if trusted:
        assert _wiki(tmp_path, 'trust', '--path', str(root)).returncode == 0

    # the merge is clean every way; only a hook that runs orders the rows
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = (root / 'core' / '_index.md').read_text(encoding='utf-8')
    assert _row_labels(merged) == ['..', *labels]
    if (hook == _ORDERED_HOOK) and trusted:
        check = _wiki(root, 'update', '--path', str(root), '--check')
        assert check.returncode == 0, check.stdout + check.stderr


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_leaves_an_index_another_class_wrote(tmp_path: pathlib.Path) -> None:
    """Rows the current branch's index holds in another class's order stay as merged.

    A subclass's update may order categories its own way with no hook in
    the wiki (``ref`` ahead of ``guide`` here), so the driver's own class
    would re-sort a current index into a stale one. It sorts only when it
    leaves ours' pre-merge index as written.
    """
    root = tmp_path / 'wiki'
    index = root / 'core' / '_index.md'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    head = (
        '---\nname: core\ndesc: Core concepts.\ntags: []\nsources: []\n'
        'created: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n---\n\n'
        '# core\n\n[[_index|..]]\n\n[[core/api|[ref] api]]: The api page.\n\n'
        '[[core/extra|[guide] extra]]: The extra page.\n\n'
    )
    design = '[[core/design|design]]: The design page.\n\n'
    _write(index, f'{head}{design}***\n')
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # each side adds a row where that class orders it
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    _write(index, f'{head}[[core/beta|beta]]: The beta page.\n\n{design}***\n')
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _write(index, f'{head}{design}[[core/zeta|zeta]]: The zeta page.\n\n***\n')
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge is clean, and ours' order stands with theirs' row below
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    labels = _row_labels(index.read_text(encoding='utf-8'))
    assert labels == ['..', '[ref] api', '[guide] extra', 'design', 'zeta', 'beta']


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames='deleting',
    argvalues=['theirs', 'main'],
    ids=['theirs-deletes', 'ours-deletes'],
)
@pytest.mark.parametrize(
    argnames='edited',
    argvalues=[
        '[[core/diagram.png|diagram.png]]: The flow diagram.',
        '[[core/diagram.png|diagram.png]]: ...\n[[core/design|design]] renders it.',
    ],
    ids=['first-line', 'continuation'],
)
def test_merge_keeps_a_row_one_side_changed_and_the_other_deleted(
    tmp_path: pathlib.Path,
    deleting: str,
    edited: str,
) -> None:
    """A row one side changed and the other deleted stays, never silently dropped.

    Only a row left as base had it is the deleting side's to remove: an
    asset row's desc is authored in the index alone, so dropping the
    edited row would lose the edit for good. A desc line that opens on
    a wikilink belongs to its row, as the index parser reads it, so an
    edit that adds one keeps the row and never moves to the row above.
    """
    root = tmp_path / 'wiki'
    index = root / 'core' / '_index.md'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    (root / 'core').mkdir()
    (root / 'core' / 'diagram.png').write_bytes(b'\x89PNG\r\n')
    _add_entry(root / 'core', 'design', None)
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # one side deletes the asset; the other authors its row's desc
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    for branch in ('theirs', 'main'):
        _git(tmp_path, 'checkout', '-q', branch)
        if branch == deleting:
            (root / 'core' / 'diagram.png').unlink()
            assert _wiki(root, 'update', '--path', str(root)).returncode == 0
        else:
            text = index.read_text(encoding='utf-8')
            _write(index, text.replace('[[core/diagram.png|diagram.png]]: ...', edited))
        _git(tmp_path, 'add', '-A')
        _git(tmp_path, 'commit', '-q', '-m', branch)

    # the merge is clean and keeps the edited row whole, right below the
    # design row as base had it
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    design = '[[core/design|design]]: The design page.\nSecond continuation line.\n\n'
    merged = index.read_text(encoding='utf-8')
    assert f'{design}{edited}\n' in merged, merged


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize('carrier', ['base', 'theirs'])
def test_merge_leaves_a_non_utf8_index_as_merged(
    tmp_path: pathlib.Path,
    carrier: str,
) -> None:
    """An index no UTF-8 reader parses merges clean, its bytes and rows as merged.

    The driver's script merges bytes; the ordering step reads text, so a
    byte that does not decode leaves the result as the script wrote it
    rather than failing the merge, whether the current branch's index
    already carries the byte or only the other side brings it in.
    """
    root = tmp_path / 'wiki'
    index = root / 'core' / '_index.md'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(index, _index('Core', 'Core concepts.', 'Text.'))
    _add_entry(root / 'core', 'design', None)
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    note = b'\nCaf\xe9 notes.\n'
    base = index.read_bytes()
    if carrier == 'base':
        base += note
    index.write_bytes(base)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # each side adds a row by hand, theirs one that sorts first, and
    # theirs brings in the byte when base does not carry it
    design = b'[[core/design|design]]'
    alpha = b'[[core/alpha|alpha]]: Alpha.\n\n'
    zeta = b'[[core/zeta|zeta]]: Zeta.\n\n'
    theirs = base.replace(design, alpha + design)
    if carrier == 'theirs':
        theirs += note
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    index.write_bytes(theirs)
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    index.write_bytes(base.replace(b'\n\n***', b'\n\n' + zeta + b'***'))
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge is clean, the byte survives, and the rows stay as merged
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = index.read_bytes()
    assert b'Caf\xe9 notes.' in merged
    assert _row_labels(merged.decode('latin-1')) == ['..', 'design', 'zeta', 'alpha']


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_leaves_a_conflicted_index_as_merged(tmp_path: pathlib.Path) -> None:
    """A merge that conflicts keeps its rows where the driver's script put them.

    A conflicted index is resolved by hand and then updated, so the
    driver sets no H1 or row order on it: the other side's row stays
    below ours', beside the markers.
    """
    root = tmp_path / 'wiki'
    index = root / 'core' / '_index.md'
    # a real repo whose wiki has the driver registered by init
    _git_repo(tmp_path)
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    _write(index, _index('Core', 'Core concepts.', 'Text.'))
    _add_entry(root / 'core', 'design', None)
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    base = index.read_text(encoding='utf-8') + '\nShared notes.\n'
    _write(index, base)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # both sides reword one body line; theirs also adds a row that sorts first
    design = '[[core/design|design]]'
    alpha = '[[core/alpha|alpha]]: Alpha.\n\n'
    theirs = base.replace(design, alpha + design)
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    _write(index, theirs.replace('Shared notes.', 'Their notes.'))
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _write(index, base.replace('Shared notes.', 'Our notes.'))
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # the merge conflicts, and the rows stay as the script wrote them
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode != 0, merge.stdout + merge.stderr
    merged = index.read_text(encoding='utf-8')
    assert '<<<<<<< ours' in merged
    assert _row_labels(merged) == ['..', 'design', 'alpha']


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames=('damaged', 'unread', 'added'),
    argvalues=[
        ('theirs', False, 'chart.png'),
        ('main', False, 'chart.png'),
        ('theirs', True, 'chart.png'),
        ('main', True, 'chart.png'),
        ('theirs', False, 'plot.png'),
    ],
    ids=[
        'theirs',
        'ours',
        'theirs-unread-line',
        'ours-unread-line',
        'theirs-in-order',
    ],
)
def test_merge_reads_an_escaped_sides_rows(
    tmp_path: pathlib.Path,
    damaged: str,
    unread: bool,
    added: str,
) -> None:
    r"""A side whose rows a formatter escaped merges them as ``wiki update`` reads them.

    The driver reads a row in the escape-damage shape (``\[\[``) with the
    formatter's escapes undone, so the escaped side's added row, its edit
    to an asset row's authored desc -- which no update regenerates -- and
    its deletion all land, as does the other side's deletion of a row the
    escaped side left alone. An escaped row and the other side's copy of
    it are one entry, so the merge writes neither out twice, and every row
    theirs brings in lands repaired, so the merge adds no escapes even
    when its rows keep update's order and nothing re-renders them. A line
    in the damage shape that still reads as no row -- a desc line opening
    on a wikilink -- says nothing of what that side deleted, so its
    deletion waits for the update after the merge.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    assert _git(tmp_path, 'init', '-q', '-b', 'main').returncode == 0
    _git(tmp_path, 'config', 'user.email', 't@t')
    _git(tmp_path, 'config', 'user.name', 't')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    (root / 'core').mkdir()
    for asset in ('diagram.png', 'old.png', 'retired.png'):
        (root / 'core' / asset).write_bytes(b'\x89PNG\r\n')
    _add_entry(root / 'core', 'design', None)
    if unread:
        _write(
            root / 'core' / 'design.md',
            '---\ndesc: |\n  The design page.\n  [[core/diagram.png|diagram.png]] draws it.\n'
            '---\n\nBody.\n',
        )
    assert _wiki(root, 'update', '--path', str(root)).returncode == 0
    index = root / 'core' / '_index.md'
    diagram = '[[core/diagram.png|diagram.png]]: '
    base = index.read_text(encoding='utf-8')
    _write(index, base.replace(f'{diagram}...', f'{diagram}The diagram.'))
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # the damaged side adds an asset row, edits one and deletes one, then its
    # formatter escapes every row; the other side adds a page and deletes an
    # asset
    chart = f'[[core/{added}|{added}]]: '
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    for branch in ('theirs', 'main'):
        _git(tmp_path, 'checkout', '-q', branch)
        if branch == damaged:
            (root / 'core' / added).write_bytes(b'\x89PNG\r\n')
            (root / 'core' / 'old.png').unlink()
            assert _wiki(root, 'update', '--path', str(root)).returncode == 0
            text = index.read_text(encoding='utf-8')
            text = text.replace(f'{chart}...', f'{chart}The chart.')
            text = text.replace('The diagram.', 'The revised diagram.')
            _write(index, text.replace('[[', '\\[\\[').replace(']]', '\\]\\]'))
        else:
            (root / 'core' / 'retired.png').unlink()
            _add_entry(root / 'core', 'extra', None)
            assert _wiki(root, 'update', '--path', str(root)).returncode == 0
        _git(tmp_path, 'add', '-A')
        _git(tmp_path, 'commit', '-q', '-m', branch)

    # the merge is clean and carries each row once, the damaged side's
    # addition, edit and (when it holds no unread line) deletion included,
    # and the other side's deletion holds
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = index.read_text(encoding='utf-8')
    readable = merged.replace('\\[', '[').replace('\\]', ']')
    labels = _row_labels(readable)
    kept = ['old.png'] if unread else []
    assert sorted(labels) == sorted(
        ['..', added, 'design', 'diagram.png', 'extra', *kept]
    )
    assert f'{diagram}The revised diagram.' in readable
    assert f'{chart}The chart.' in readable

    # every row theirs brings in lands repaired; an unread line is no row,
    # so it stays as written
    if (damaged == 'theirs') and not unread:
        assert '\\[' not in merged, merged

    # the update after it repairs any escapes, keeping both authored descs
    update = _wiki(root, 'update', '--path', str(root))
    assert update.returncode == 0, update.stdout + update.stderr
    converged = index.read_text(encoding='utf-8')
    assert _row_labels(converged) == sorted(
        ['..', added, 'design', 'diagram.png', 'extra']
    )
    assert f'{diagram}The revised diagram.' in converged
    assert f'{chart}The chart.' in converged
    lint = _wiki(root, 'lint', '--path', str(root))
    assert lint.returncode == 0, lint.stdout + lint.stderr


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

    An ``_index.md`` below a declared wiki root takes the index merge, a
    page the wiki manages a text merge over settled stamps, and any other
    pathname -- including an ``_index.md`` outside every wiki -- a plain
    text merge. Every route honors %L and labels its conflicts ours, base
    and theirs.
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
    theirs.write_text(fm.replace('name: core', 'name: renamed'), encoding='utf-8')
    # declared roots for the wiki-owned pathnames below
    _write(tmp_path / 'wiki' / '.wiki' / 'settings.json', '{}\n')
    _write(tmp_path / '-notes' / '.wiki' / 'settings.json', '{}\n')

    # an _index.md pathname: name is a regenerated key, so ours wins
    args = [str(base), str(ours), str(theirs), '7', 'wiki/core/_index.md']
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'name: core' in ours.read_text(encoding='utf-8')

    # a dash-leading pathname still routes as a pathname, not an option
    ours.write_text(fm, encoding='utf-8')
    args = [str(base), str(ours), str(theirs), '7', '-notes/_index.md']
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'name: core' in ours.read_text(encoding='utf-8')

    # a page outside every declared wiki: a plain text merge takes theirs'
    # line edit
    ours.write_text(fm, encoding='utf-8')
    args = [str(base), str(ours), str(theirs), '7', 'docs/notes.md']
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'name: renamed' in ours.read_text(encoding='utf-8')

    # an _index.md outside every declared wiki (a site generator's content
    # page): the default text merge, so theirs' line edit still lands
    ours.write_text(fm, encoding='utf-8')
    args = [str(base), str(ours), str(theirs), '7', 'content/_index.md']
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'name: renamed' in ours.read_text(encoding='utf-8')

    # the marker size flows through to labeled conflict markers on every
    # route: an index, a managed page, and a page outside every wiki
    marker = '<' * 15
    for pathname in ('wiki/core/_index.md', 'wiki/core/notes.md', 'docs/notes.md'):
        ours.write_text(fm.replace('Body.', 'Ours body.'), encoding='utf-8')
        theirs.write_text(fm.replace('Body.', 'Theirs body.'), encoding='utf-8')
        args = [str(base), str(ours), str(theirs), '15', pathname]
        result = _wiki(tmp_path, '_merge', *args)
        assert result.returncode != 0
        assert f'{marker} ours\n' in ours.read_text(encoding='utf-8')


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames='pathname',
    argvalues=['-h.md', '-h', '--help.md', '--help'],
)
def test_merge_driver_takes_no_help_option(
    tmp_path: pathlib.Path,
    pathname: str,
) -> None:
    """A pathname that reads as a help flag is merged like any other.

    git hands the driver the pathname of every mapped file. A help page
    printed for one would exit 0 with the current branch's file as it was,
    which git takes for a clean merge, so the other branch's edit would
    be lost in silence.
    """
    base = tmp_path / 'base'
    ours = tmp_path / 'ours'
    theirs = tmp_path / 'theirs'
    base.write_text('one\ntwo\nthree\n', encoding='utf-8')
    ours.write_text('ONE\ntwo\nthree\n', encoding='utf-8')
    theirs.write_text('one\ntwo\nTHREE\n', encoding='utf-8')
    args = [str(base), str(ours), str(theirs), '7', pathname]
    result = _wiki(tmp_path, '_merge', *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ''
    assert ours.read_text(encoding='utf-8') == 'ONE\ntwo\nTHREE\n'


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames=('pathname', 'later', 'hook', 'eol', 'settled'),
    argvalues=[
        ('wiki/core/notes.md', 'ours', None, '\n', True),
        ('wiki/core/notes.md', 'theirs', None, '\n', True),
        ('wiki/core/notes.md', 'ours', None, '\r\n', True),
        ('wiki/frozen/copy.md', 'ours', None, '\n', False),
        ('wiki/scratch/draft.md', 'ours', None, '\n', False),
        ('wiki/.drafts/note.md', 'ours', None, '\n', False),
        ('wiki/core/notes.txt', 'ours', None, '\n', False),
        ('docs/notes.md', 'ours', None, '\n', False),
        ('wiki/core/notes.md', 'ours', 'untrusted', '\n', False),
        ('wiki/core/notes.md', 'ours', 'exits', '\n', False),
    ],
    ids=[
        'page-ours-later',
        'page-theirs-later',
        'crlf-page',
        'excluded',
        'gitignored',
        'dot-folder',
        'not-markdown',
        'outside-every-wiki',
        'untrusted-hook',
        'hook-exits',
    ],
)
def test_merge_settles_the_stamps_of_a_managed_page(
    tmp_path: pathlib.Path,
    pathname: str,
    later: str,
    hook: Optional[str],
    eol: str,
    settled: bool,
) -> None:
    """A page the wiki manages merges on the later of its sides' ``updated`` stamps.

    Each side edits its own body line under its own stamp, so the stamp
    line is the merge's only conflict: the driver settles both sides on
    the later stamp before the text merge, which then comes out clean,
    whichever side stamped later and whatever line ending the page
    keeps. A path the wiki does not manage -- excluded, gitignored,
    under a dot folder, not Markdown, or outside every wiki -- takes
    git's default text merge with its stamps untouched, so the stamp
    line conflicts, as does a page whose wiki's hook will not load, or
    exits, when the driver judges it.
    """
    _git_repo(tmp_path)
    settings = {'exclude': {'patterns': ['frozen']}}
    _write(tmp_path / 'wiki' / '.wiki' / 'settings.json', json.dumps(settings))
    _write(tmp_path / '.gitignore', 'scratch/\n')
    if hook == 'untrusted':
        _write(tmp_path / 'wiki' / '.wiki' / 'wiki.py', _ORDERED_HOOK)
    if hook == 'exits':
        _write(tmp_path / 'wiki' / '.wiki' / 'wiki.py', _EXITING_HOOK)
        assert _wiki(tmp_path, 'trust', '--path', 'wiki').returncode == 0
    base_text = (
        '---\nname: notes\ndesc: Notes.\nupdated: 2026-01-01T00:00:00Z\n---\n'
        '\n# notes\n\nFirst line.\n\nMiddle line.\n\nLast line.\n'
    )
    stamps = {'ours': '2026-01-03T00:00:00Z', 'theirs': '2026-01-02T00:00:00Z'}
    if later == 'theirs':
        stamps = {'ours': stamps['theirs'], 'theirs': stamps['ours']}
    ours_text = base_text.replace('2026-01-01T00:00:00Z', stamps['ours'])
    ours_text = ours_text.replace('First line.', 'Our first line.')
    theirs_text = base_text.replace('2026-01-01T00:00:00Z', stamps['theirs'])
    theirs_text = theirs_text.replace('Last line.', 'Their last line.')
    base = tmp_path / 'base'
    ours = tmp_path / 'ours'
    theirs = tmp_path / 'theirs'
    _write(base, base_text.replace('\n', eol))
    _write(ours, ours_text.replace('\n', eol))
    _write(theirs, theirs_text.replace('\n', eol))

    args = [str(base), str(ours), str(theirs), '7', pathname]
    result = _wiki(tmp_path, '_merge', *args)
    merged = ours.read_text(encoding='utf-8')
    if settled:
        assert result.returncode == 0, result.stdout + result.stderr
        stamp = re.findall(r'^updated:.*$', merged, re.M)
        assert stamp == ['updated: 2026-01-03T00:00:00Z']
        assert 'Our first line.' in merged
        assert 'Their last line.' in merged
    else:
        assert result.returncode != 0
        assert '<<<<<<<' in merged


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_of_a_restamped_page_is_clean(tmp_path: pathlib.Path) -> None:
    """Two branches that each edit and restamp a page merge without a conflict.

    ``wiki init`` maps every Markdown file to the driver, so a real merge
    routes the page through it: the later ``updated`` stamp stands, and
    each branch's body edit lands.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    _git_repo(tmp_path)
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    page = root / 'notes.md'
    base = (
        '---\nname: notes\ndesc: Notes.\nupdated: 2026-01-01T00:00:00Z\n---\n'
        '\n# notes\n\nFirst line.\n\nMiddle line.\n\nLast line.\n'
    )
    _write(page, base)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # each branch edits its own line and restamps the page
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    theirs = base.replace('2026-01-01T00:00:00Z', '2026-01-03T00:00:00Z')
    _write(page, theirs.replace('Last line.', 'Their last line.'))
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    ours = base.replace('2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')
    _write(page, ours.replace('First line.', 'Our first line.'))
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    merged = page.read_text(encoding='utf-8')
    assert re.findall(r'^updated:.*$', merged, re.M) == [
        'updated: 2026-01-03T00:00:00Z'
    ]
    assert 'Our first line.' in merged
    assert 'Their last line.' in merged


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_markdown_outside_the_wiki_keeps_its_own_merge(tmp_path: pathlib.Path) -> None:
    """Markdown outside the wiki merges by git's rules and its own, untouched.

    ``wiki init`` maps pages in the wiki's own ``.gitattributes``, so a
    file outside it keeps whatever the repository set for it -- a
    ``merge=union`` changelog unions both sides' lines -- and any other
    Markdown conflicts as git labels it, ``HEAD`` against the branch.
    """
    root = tmp_path / 'wiki'
    _git_repo(tmp_path)
    # a committed attribute rule of the repository's own, then the wiki
    _write(tmp_path / '.gitattributes', 'CHANGELOG.md merge=union\n')
    _write(tmp_path / 'CHANGELOG.md', 'Base entry.\n')
    _write(tmp_path / 'README.md', 'Base line.\n')
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'project')
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    attributes = (tmp_path / '.gitattributes').read_text(encoding='utf-8')
    assert '**/_index.md merge=wiki' in attributes.splitlines()
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # each branch adds its own changelog entry and rewords the readme line
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    _write(tmp_path / 'CHANGELOG.md', 'Base entry.\nTheir entry.\n')
    _write(tmp_path / 'README.md', 'Their line.\n')
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _write(tmp_path / 'CHANGELOG.md', 'Base entry.\nOur entry.\n')
    _write(tmp_path / 'README.md', 'Our line.\n')
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode != 0
    changelog = (tmp_path / 'CHANGELOG.md').read_text(encoding='utf-8')
    assert changelog == 'Base entry.\nOur entry.\nTheir entry.\n'
    readme = (tmp_path / 'README.md').read_text(encoding='utf-8')
    assert '<<<<<<< HEAD' in readme
    assert '>>>>>>> theirs' in readme


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
def test_merge_without_the_wiki_cli_falls_back_to_a_text_merge(
    tmp_path: pathlib.Path,
) -> None:
    """A merge that cannot find ``wiki`` still merges, with conflict markers.

    The registered driver runs git's own text merge when the CLI is not
    on PATH, labeled as the wiki's merge labels its conflicts, so a
    conflict shows both sides rather than leaving ours alone without
    markers for a ``git add`` to settle silently.
    """
    root = tmp_path / 'wiki'
    _git_repo(tmp_path)
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    page = root / 'notes.md'
    _write(page, _page('notes', 'Notes.', 'Base line.'))
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    _write(page, _page('notes', 'Notes.', 'Their line.'))
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    _write(page, _page('notes', 'Notes.', 'Our line.'))
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    # a PATH holding git alone: the driver cannot find the wiki CLI
    bin_dir = tmp_path.parent / f'{tmp_path.name}_bin'
    bin_dir.mkdir()
    (bin_dir / 'git').symlink_to(GIT)
    env = _env()
    env['PATH'] = str(bin_dir)
    merge = subprocess.run(
        [GIT, '-C', str(tmp_path), 'merge', 'theirs'],
        capture_output=True,
        text=True,
        env=env,
    )
    assert merge.returncode != 0
    merged = page.read_text(encoding='utf-8')
    assert '<<<<<<< ours' in merged
    assert 'Our line.' in merged
    assert 'Their line.' in merged


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


# ------ help


@pytest.mark.parametrize(
    argnames='command',
    argvalues=[
        (),
        ('install',),
        ('init',),
        ('config',),
        ('trust',),
        ('read',),
        ('search',),
        ('match',),
        ('update',),
        ('new',),
        ('lint',),
        ('map',),
    ],
    ids=lambda command: command[0] if command else 'wiki',
)
def test_short_help_flag_prints_the_help_page(
    tmp_path: pathlib.Path,
    command: tuple[str, ...],
) -> None:
    """``-h`` prints what ``--help`` prints, on ``wiki`` and on every command."""
    short = _wiki(tmp_path, *command, '-h')
    assert short.returncode == 0, short.stdout + short.stderr
    assert short.stdout.startswith('Usage: wiki')
    assert '-h, --help' in short.stdout
    long = _wiki(tmp_path, *command, '--help')
    assert (short.stdout, short.stderr) == (long.stdout, long.stderr)


def test_usage_error_points_at_the_long_help_flag(tmp_path: pathlib.Path) -> None:
    """A usage error names ``--help`` as the way to the help page."""
    result = _wiki(tmp_path, 'map', '--depth', 'deep')
    assert result.returncode == 2
    assert "Try 'wiki map --help' for help.\n" in result.stderr


def test_wiki_with_no_command_prints_help_as_a_usage_error(
    tmp_path: pathlib.Path,
) -> None:
    """Bare ``wiki`` prints the help page on stderr and exits 2.

    The page tells a person what to run next, and the stream and the exit
    code tell a script that nothing ran.
    """
    result = _wiki(tmp_path)
    assert result.returncode == 2
    assert result.stdout == ''
    assert result.stderr == _wiki(tmp_path, '--help').stdout
    assert result.stderr.startswith('Usage: wiki [OPTIONS] COMMAND [ARGS]...\n')


def test_help_marks_a_required_option_once(tmp_path: pathlib.Path) -> None:
    """The help of ``wiki new`` says ``required`` once per required input."""
    result = _wiki(tmp_path, 'new', '--help')
    assert result.returncode == 0, result.stdout + result.stderr
    # the name, --desc and --content each carry the one generated marker
    assert result.stdout.count('[required]') == 3
    assert result.stdout.count('required') == 3


def test_search_help_states_the_default_limit(tmp_path: pathlib.Path) -> None:
    """The help of ``wiki search --limit`` names the limit an unset one gets."""
    result = _wiki(tmp_path, 'search', '--help')
    assert result.returncode == 0, result.stdout + result.stderr
    entry = result.stdout.partition('--limit INTEGER')[2].partition('--tag')[0]
    assert f'defaults to {DEFAULT_SEARCH_LIMIT}' in ' '.join(entry.split())


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
    assert result.returncode == 2
    assert 'No wiki at' in result.stderr


def test_trust_refuses_a_store_that_is_not_utf8_naming_the_byte(
    tmp_path: pathlib.Path,
) -> None:
    """``wiki trust`` refuses a trust store that is not UTF-8, at the byte.

    The refusal names the store and the offset of its first invalid
    byte in wiki's own words, never the interpreter's sentence naming
    its codec, and leaves the store's bytes for repair.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    # a trusted path spelled with a Latin-1 e-acute
    store = pathlib.Path(os.environ[WIKI_CONFIG_DIR]) / 'settings.json'
    valid = b'{"trusted": {"/caf'
    corrupt = valid + b'\xe9": "2000-01-01T00:00:00Z"}}\n'
    store.write_bytes(corrupt)

    result = _wiki(root, 'trust', '--path', str(root))
    assert result.returncode == 2, result.stdout + result.stderr
    reason = f'not valid UTF-8 at byte {len(valid)}'
    assert f'Error: Trust store is corrupt: {store} ({reason});' in result.stderr
    assert 'codec' not in result.stdout + result.stderr
    assert store.read_bytes() == corrupt


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


def test_root_resolution_survives_a_symlink_loop_home(tmp_path: pathlib.Path) -> None:
    """A ``$HOME`` that is a symlink loop leaves root resolution intact.

    The home and config-home exemptions compare real paths, which read a
    looped path as itself where ``Path.resolve()`` raises before Python
    3.13, so a broken home directory never fails a command resolving its
    root.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', 'Loop', '--path', str(root)).returncode == 0
    loop = tmp_path / 'loop'
    loop.symlink_to('loop')
    # resolving from cwd walks the ancestor chain under the looped $HOME
    result = _wiki(root, 'lint', home=loop)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Symlink loop' not in result.stderr


# ------ clock seam


@pytest.mark.parametrize(
    argnames=('now', 'timestamp', 'stamp'),
    argvalues=[
        ('2026-01-02T03:04:05Z', {}, '2026-01-02T03:04:05Z'),
        ('2026-01-02T03:04:05+02:00', {}, '2026-01-02T01:04:05Z'),
        (
            '2026-01-02T03:04:05Z',
            {'timezone': 'America/New_York'},
            '2026-01-01T22:04:05-0500',
        ),
        ('2026-01-02T20:04:05-10:30', {'format': '%Y-%m-%d'}, '2026-01-03'),
        ('2026-01-02T03:04:05.25Z', {}, '2026-01-02T03:04:05Z'),
        (' \x1f2026-01-02T03:04:05Z\n', {}, '2026-01-02T03:04:05Z'),
        (
            '1001-01-01T00:00:00Z',
            {'timezone': 'Etc/GMT+12'},
            '1000-12-31T12:00:00-1200',
        ),
        (
            '9998-12-31T23:59:59Z',
            {'timezone': 'Etc/GMT-14'},
            '9999-01-01T13:59:59+1400',
        ),
    ],
    ids=[
        'utc',
        'offset',
        'configured-zone',
        'authored-format',
        'fraction',
        'padded',
        'first-year',
        'last-year',
    ],
)
def test_wiki_freeze_time_fixes_every_stamp(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    now: str,
    timestamp: dict,
    stamp: str,
) -> None:
    """Every stamp is the ``WIKI_FREEZE_TIME`` instant, in the wiki's policy.

    The seam replaces the clock, not the rendering: the instant passes
    through the wiki's own zone and format, so two runs given one value
    write the same bytes whenever they run. Each command says so in a
    fixed line that opens its stderr. A fraction of a second is dropped,
    since no format renders one. Whitespace around the value is stripped
    as Python strips it, the ASCII separators included, and the first
    and last years the seam takes render in the zones farthest from UTC.
    """
    monkeypatch.setenv(WIKI_FREEZE_TIME, now)
    root = tmp_path / 'wiki'
    settings = json.dumps({'timestamp': timestamp})
    init = _wiki(tmp_path, 'init', '--path', str(root), '--settings', settings)
    assert init.returncode == 0, init.stdout + init.stderr
    _write(root / 'core' / 'design.md', 'Body.\n')
    # lint previews the stamps an update would write
    lint = _wiki(root, 'lint', '--full', '--path', str(root))
    assert f'+created: {stamp}\n' in lint.stdout
    assert f'+updated: {stamp}\n' in lint.stdout
    update = _wiki(root, 'update', '--path', str(root))
    assert update.returncode == 0, update.stdout + update.stderr
    (root / 'guides').mkdir()
    new = _wiki(
        root,
        'new',
        'guides',
        '--desc',
        'How-to guides.',
        '--content',
        'Text.',
        '--path',
        str(root),
    )
    assert new.returncode == 0, new.stdout + new.stderr
    for result in (init, lint, update, new):
        assert result.stderr.startswith(_PINNED_CLOCK_NOTICE)
    # the root index, the adopted page and both folder indexes carry it
    stamps = []
    for path in sorted(root.rglob('*.md')):
        text = path.read_text(encoding='utf-8')
        stamps += re.findall(r'^(?:created|updated): (.*)$', text, re.M)
    assert stamps == [stamp] * 8


def test_wiki_freeze_time_stamps_the_trust_record(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``wiki trust`` records the instant ``WIKI_FREEZE_TIME`` names, in UTC.

    The trust store is the one clock read outside a wiki's stamps, and it
    keeps its own fixed format whatever the wiki's timestamp policy.
    """
    root = tmp_path / 'wiki'
    settings = json.dumps({'timestamp': {'timezone': 'America/New_York'}})
    init = _wiki(tmp_path, 'init', '--path', str(root), '--settings', settings)
    assert init.returncode == 0, init.stdout + init.stderr
    monkeypatch.setenv(WIKI_FREEZE_TIME, '2026-01-02T03:04:05+02:00')
    result = _wiki(root, 'trust', '--path', str(root))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == _PINNED_CLOCK_NOTICE
    store = pathlib.Path(os.environ[WIKI_CONFIG_DIR]) / 'settings.json'
    trusted = json.loads(store.read_text(encoding='utf-8'))['trusted']
    assert trusted == {str(root.resolve()): '2026-01-02T01:04:05Z'}


@pytest.mark.parametrize(
    argnames='now',
    argvalues=['', ' \t'],
    ids=['empty', 'whitespace'],
)
def test_blank_wiki_freeze_time_leaves_the_clock_running(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    now: str,
) -> None:
    """A blank ``WIKI_FREEZE_TIME`` is unset: the real clock, and no notice."""
    monkeypatch.setenv(WIKI_FREEZE_TIME, now)
    root = tmp_path / 'wiki'
    before = dt.datetime.now(dt.UTC).replace(microsecond=0)
    result = _wiki(tmp_path, 'init', '--path', str(root))
    after = dt.datetime.now(dt.UTC)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _PINNED_CLOCK_NOTICE not in result.stderr
    text = (root / '_index.md').read_text(encoding='utf-8')
    stamp = re.search(r'^updated: (.*)$', text, re.M).group(1)
    written = dt.datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.UTC)
    assert before <= written <= after


@pytest.mark.parametrize(
    argnames='now',
    argvalues=[
        'now',
        '2026-01-02',
        '2026-01-02T03:04:05',
        '2026-01-02 03:04:05Z',
        '2026-01-02t03:04:05z',
        '2026-01-02T03:04Z',
        '2026-01-02T03:04:05+0200',
        '2026-01-02T03:04:05+01:60',
        '2026-01-02T24:00:00Z',
        '2026-01-02T23:59:60Z',
        '2026-01-02T03:04:05.1234567Z',
        '2026-01-02T03:04:05,5Z',
        '2026-02-30T03:04:05Z',
        '0001-01-01T00:00:00+01:00',
        '1000-12-31T23:59:59Z',
        '9999-01-01T00:00:00+00:00',
    ],
    ids=[
        'word',
        'date-only',
        'no-offset',
        'space-separator',
        'lower-case',
        'no-seconds',
        'offset-without-colon',
        'offset-minute-60',
        'hour-24',
        'leap-second',
        'seven-fraction-digits',
        'comma-fraction',
        'no-such-day',
        'before-year-one',
        'before-first-year',
        'after-last-year',
    ],
)
def test_invalid_wiki_freeze_time_fails_a_command_before_it_acts(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    now: str,
) -> None:
    """An invalid ``WIKI_FREEZE_TIME`` is exit 2 before a command does a thing.

    The value takes one form -- an RFC 3339 date-time with a capital
    ``T`` and ``Z``, no leap second, and at most six fraction digits --
    so nothing reads it by a looser rule than the next reader's. Its
    UTC year lies within the years ``_PINNED_YEARS`` allows, so no
    zone renders it outside the calendar or below the four digits
    ``%Y`` pads to. A command that never reads the clock refuses
    alike, and a refused update writes nothing.
    """
    root = tmp_path / 'wiki'
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    page = root / 'core' / 'design.md'
    _write(page, 'Body.\n')
    monkeypatch.setenv(WIKI_FREEZE_TIME, now)
    error = _PINNED_CLOCK_ERROR.format(now=repr(now))
    for args in (('update',), ('map',)):
        result = _wiki(root, *args, '--path', str(root))
        assert result.returncode == 2, (args, result.stdout + result.stderr)
        assert result.stderr == error
    assert page.read_text(encoding='utf-8') == 'Body.\n'
    assert not (root / 'core' / '_index.md').exists()


def test_wiki_freeze_time_is_read_when_a_command_runs(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``WIKI_FREEZE_TIME`` is read once a command line parses, as it starts.

    What typer answers while it parses -- help, the version, an unknown
    option, a missing argument -- prints no notice and judges no value.
    A usage error a command raises over its own options comes after the
    read: an invalid value is the error reported in its place, and a
    valid value's notice precedes it.
    """
    monkeypatch.setenv(WIKI_FREEZE_TIME, 'now')
    # typer answers these while it parses, with no notice
    for args in (('--version',), ('--help',), ('lint', '--help')):
        result = _wiki(tmp_path, *args)
        assert result.returncode == 0, (args, result.stdout + result.stderr)
        assert result.stderr == ''
    # typer's own usage errors come before the read
    for args in (('lint', '--no-such-option'), ('read',)):
        usage = _wiki(tmp_path, *args)
        assert usage.returncode == 2, (args, usage.stdout + usage.stderr)
        assert 'WIKI_FREEZE_TIME' not in usage.stderr
    # lint itself refuses the pair of options, after the wrapper's read
    args = ('lint', '--full', '--count')
    refused = _wiki(tmp_path, *args)
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert refused.stderr == _PINNED_CLOCK_ERROR.format(now=repr('now'))
    monkeypatch.setenv(WIKI_FREEZE_TIME, '2026-01-02T03:04:05Z')
    usage = _wiki(tmp_path, *args)
    assert usage.returncode == 2, usage.stdout + usage.stderr
    assert usage.stderr.startswith(f'{_PINNED_CLOCK_NOTICE}Usage: wiki lint')
    assert '--full and --count are mutually exclusive.' in usage.stderr


@pytest.mark.skipif(GIT is None, reason='git not on PATH')
@pytest.mark.parametrize(
    argnames='now',
    argvalues=[None, '2026-01-02T03:04:05Z', 'now'],
    ids=['unset', 'valid', 'invalid'],
)
def test_merge_driver_never_reads_wiki_freeze_time(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    now: Optional[str],
) -> None:
    """A merge comes out the same whatever ``WIKI_FREEZE_TIME`` holds.

    Git reads the driver's exit code as the merge outcome, and the driver
    reads no clock: a value that fails every other command leaves a clean
    merge clean, and a valid one adds no line to git's output.
    """
    root = tmp_path / 'wiki'
    # a real repo whose wiki has the driver registered by init
    _git_repo(tmp_path)
    assert _wiki(tmp_path, 'init', '--path', str(root)).returncode == 0
    page = root / 'notes.md'
    base = (
        '---\nname: notes\ndesc: Notes.\nupdated: 2026-01-01T00:00:00Z\n---\n'
        '\n# notes\n\nFirst line.\n\nMiddle line.\n\nLast line.\n'
    )
    _write(page, base)
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-q', '-m', 'base')

    # each branch edits its own line and restamps the page, so the merge is
    # clean only when the driver settles the stamps
    _git(tmp_path, 'checkout', '-q', '-b', 'theirs')
    theirs = base.replace('2026-01-01T00:00:00Z', '2026-01-03T00:00:00Z')
    _write(page, theirs.replace('Last line.', 'Their last line.'))
    _git(tmp_path, 'commit', '-q', '-am', 'theirs')
    _git(tmp_path, 'checkout', '-q', 'main')
    ours = base.replace('2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')
    _write(page, ours.replace('First line.', 'Our first line.'))
    _git(tmp_path, 'commit', '-q', '-am', 'ours')

    if now is not None:
        monkeypatch.setenv(WIKI_FREEZE_TIME, now)
    merge = _git(tmp_path, 'merge', 'theirs')
    assert merge.returncode == 0, merge.stdout + merge.stderr
    assert 'WIKI_FREEZE_TIME' not in merge.stdout + merge.stderr
    assert _git(tmp_path, 'status', '--porcelain').stdout == ''
    merged = page.read_text(encoding='utf-8')
    assert re.findall(r'^updated:.*$', merged, re.M) == [
        'updated: 2026-01-03T00:00:00Z'
    ]
    assert 'Our first line.' in merged
    assert 'Their last line.' in merged


# ------ helpers


def _write(path: pathlib.Path, text: str) -> None:
    """Write ``text`` to ``path`` as UTF-8, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _tree(root: pathlib.Path) -> dict[str, bytes]:
    """Return the bytes of every file under ``root``, keyed by relative path."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob('*'))
        if path.is_file()
    }


def _page(name: str, desc: str, body: str) -> str:
    """Return a minimal authored page with frontmatter and a heading."""
    return f'---\nname: {name}\ndesc: {desc}\n---\n\n# {name}\n\n{body}\n'


def _index(name: str, desc: str, body: str) -> str:
    """Return a minimal authored folder index with a content marker."""
    return f'---\nname: {name}\ndesc: {desc}\n---\n\n# {name}\n\n{body}\n\n***\n'


def _add_entry(folder: pathlib.Path, name: str, category: Optional[str]) -> None:
    """Add a page under ``folder``, or for a ``name/`` a folder holding one.

    The page's desc is a two-line block scalar, so its index row carries a
    continuation line; ``category`` sets its ``category:`` when given.
    """
    page = folder / name / 'note.md' if name.endswith('/') else folder / f'{name}.md'
    lines = ['---', 'desc: |', f'  The {name} page.', '  Second continuation line.']
    if category is not None:
        lines.append(f'category: {category}')
    _write(page, '\n'.join([*lines, '---', '', 'Body.', '']))


def _row_labels(index: str) -> list[str]:
    """Return the labels of an index's link rows, top to bottom.

    A row is a whole line, as the index parser reads it, so a desc
    continuation that opens on a wikilink is no row.
    """
    labels = []
    for line in index.split('\n'):
        match = re.match(r'^\[\[(.+?)\|(.+?)\]\](?::\s*(.*))?$', line.strip())
        if match is not None:
            labels.append(match.group(2))
    return labels
