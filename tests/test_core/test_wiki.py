"""Test the ``wiki.core.wiki`` module.

Construction and policy: settings/naming/timestamp validation, the
``validate_name`` matrix, init idempotence and refusals, the
legacy-layout guard, and the ``canonicalize_index`` rewrite the merge
driver runs. Each verb's behavior has its own suite (``test_update``,
``test_plan``, ``test_lint``, ``test_read``, ``test_search``,
``test_map``, ``test_config``).
"""

from __future__ import annotations

import json
import pathlib
import re
import unicodedata

import pytest

from wiki.constants import WIKI_FREEZE_TIME
from wiki.core.wiki import Wiki

from ._helpers import CategorizedWiki, _capture_notices, _make_wiki

__all__ = [
    'test_init_creates_structure',
    'test_validate_name',
    'test_validate_name_strict_via_settings',
    'test_naming_policy_knobs',
    'test_init_scaffolds_settings',
    'test_init_seeds_custom_settings',
    'test_init_rejects_bad_settings_before_writing',
    'test_init_rejects_invalid_wiki_name',
    'test_init_refuses_conflict_markers',
    'test_settings_reject_malformed_values',
    'test_settings_blocks_are_validated_in_the_documented_order',
    'test_settings_error_spells_the_wrong_value_as_json',
    'test_settings_warnings_name_only_keys_nothing_reads',
    'test_leading_digits_warns_without_the_identifier_rule',
    'test_timestamp_format_configurable',
    'test_timestamp_timezone_configurable',
    'test_timestamp_zone_swaps_default_z_for_offset',
    'test_timestamp_format_rejects_blank_or_multiline',
    'test_timestamp_format_rejects_a_nul_character',
    'test_timestamp_format_stamps_parse_back',
    'test_timestamp_format_rejects_an_unsupported_directive',
    'test_timestamp_format_rejects_a_digit_after_the_offset',
    'test_timestamp_format_must_read_back_its_stamp',
    'test_wiki_freeze_time_fixes_the_clock_in_process',
    'test_init_refuses_legacy_layout_before_writing',
    'test_canonicalize_index_puts_the_h1_and_rows_in_form',
]

# the parts of a core index in update's form: the head through its H1 (and
# a titled head), the parent row, two page rows (zeta's desc on two lines),
# two categorized rows in the base class's order, and the content below the
# delimiter, which holds a thematic break of its own
_HEAD = '---\nname: core\ndesc: Core concepts.\n---\n\n# core\n\n'
_TITLED = '---\nname: core\ntitle: Core Concepts\ndesc: Core concepts.\n---\n\n'
_PARENT = '[[_index|..]]\n\n'
_ALPHA = '[[core/alpha|alpha]]: The alpha page.\n\n'
_ZETA = '[[core/zeta|zeta]]: The zeta page.\nIts second line.\n\n'
_API = '[[core/api|[api] api]]: The api page.\n\n'
_STORE = '[[core/db|[store] db]]: The db page.\n\n'
_CAFE = '[[core/café|café]]: The café page.\n\n'
_CAFE_NFD = unicodedata.normalize('NFD', _CAFE)
_CONTENT = '***\n\nBody text.\n\n***\n\nMore text.\n'


# ------ init and naming policy


def test_init_creates_structure(tmp_path: pathlib.Path) -> None:
    """Init creates root index and config."""
    # init creates root _index.md
    root = tmp_path / 'wiki'
    wiki = Wiki(root)
    wiki.init()
    assert (root / '_index.md').is_file()

    # obsidian config template seeded
    assert (root / '.wiki' / 'obsidian').is_dir()

    # init is idempotent (doesn't overwrite user content)
    index = root / '_index.md'
    original = index.read_text(encoding='utf-8')
    wiki.init()
    assert index.read_text(encoding='utf-8') == original


@pytest.mark.parametrize(
    argnames=('name', 'valid'),
    argvalues=[
        ('core', True),
        ('my_page', True),
        ('bad-name', True),
        ('Machine Learning', True),
        ('café', True),
        ('release.notes', True),
        ('123start', True),
        ('a/b', False),
        ('a#b', False),
        ('a|b', False),
        ('.hidden', False),
        ('_index', False),
        ('_notes', True),
        ('', False),
    ],
    ids=[
        'simple',
        'underscore',
        'hyphen',
        'spaces',
        'unicode',
        'interior-dot',
        'leading-digit',
        'path-separator',
        'hash',
        'pipe',
        'leading-dot',
        'reserved',
        'leading-underscore',
        'empty',
    ],
)
def test_validate_name(tmp_path: pathlib.Path, name: str, valid: bool) -> None:
    """The default policy is lenient: any name except the structural characters."""
    wiki = Wiki(tmp_path)
    assert wiki.validate_name(name) is valid


def test_validate_name_strict_via_settings(tmp_path: pathlib.Path) -> None:
    """A ``settings.json`` naming block can restore the strict identifier rule."""
    config = tmp_path / '.wiki'
    config.mkdir()
    policy = {'naming': {'validate': ['ascii', 'identifier'], 'leading_digits': True}}
    (config / 'settings.json').write_text(json.dumps(policy), encoding='utf-8')
    wiki = Wiki(tmp_path)
    # strict accepts ASCII identifiers, including a leading digit ...
    assert wiki.validate_name('MyPage') is True
    assert wiki.validate_name('123start') is True
    # ... and rejects what the lenient default would allow
    assert wiki.validate_name('bad-name') is False
    assert wiki.validate_name('café') is False
    assert wiki.validate_name('Machine Learning') is False


@pytest.mark.parametrize(
    argnames=('naming', 'name', 'valid'),
    argvalues=[
        ({'pattern': '[a-z]+(_[a-z]+)*'}, 'good_name', True),
        ({'pattern': '[a-z]+(_[a-z]+)*'}, 'BadName', False),
        ({'min_length': 3}, 'ab', False),
        ({'min_length': 3}, 'abc', True),
        ({'max_length': 5}, 'abcde', True),
        ({'max_length': 5}, 'abcdef', False),
        ({'min_length': 3, 'max_length': 3}, 'abc', True),
        ({'deny': '$'}, 'pri$e', False),
        ({'validate': ['identifier']}, 'spin-lock', False),
        ({'validate': ['identifier'], 'allow': '-'}, 'spin-lock', True),
        ({'reserved': ['drafts']}, 'drafts', False),
        ({'validate': ['identifier'], 'leading_digits': False}, '123start', False),
    ],
    ids=[
        'pattern-match',
        'pattern-miss',
        'too-short',
        'min-length-ok',
        'max-length-ok',
        'too-long',
        'min-equals-max',
        'denied-char',
        'identifier-rejects-dash',
        'allow-exempts-dash',
        'reserved-name',
        'leading-digit-rejected',
    ],
)
def test_naming_policy_knobs(
    tmp_path: pathlib.Path,
    naming: dict,
    name: str,
    valid: bool,
) -> None:
    """Each ``settings.json`` naming knob shapes ``validate_name``.

    ``pattern`` requires a full match, ``min_length``/``max_length`` bound
    the name, ``deny`` adds rejected characters, ``allow`` exempts characters
    from the predicates, ``reserved`` blocks exact names, and
    ``leading_digits: false`` drops the identifier rule's leading-digit
    exemption.
    """
    config = tmp_path / '.wiki'
    config.mkdir()
    (config / 'settings.json').write_text(
        json.dumps({'naming': naming}),
        encoding='utf-8',
    )
    wiki = Wiki(tmp_path)
    assert wiki.validate_name(name) is valid


# ------ settings and init refusals


def test_init_scaffolds_settings(tmp_path: pathlib.Path) -> None:
    """``init`` writes a discoverable ``.wiki/settings.json`` with naming defaults."""
    wiki = Wiki(tmp_path)
    wiki.init(name='Root')
    settings = tmp_path / '.wiki' / 'settings.json'
    assert settings.is_file()
    data = json.loads(settings.read_text(encoding='utf-8'))
    assert data['naming']['validate'] == []  # the lenient default, spelled out


def test_init_seeds_custom_settings(tmp_path: pathlib.Path) -> None:
    """``init(settings=...)`` seeds the caller's ``settings.json`` and applies it."""
    policy = {'naming': {'validate': ['ascii', 'identifier']}}
    Wiki(tmp_path).init(name='Root', settings=policy)
    # the seeded settings.json is exactly the caller's policy ...
    settings = tmp_path / '.wiki' / 'settings.json'
    data = json.loads(settings.read_text(encoding='utf-8'))
    assert data == policy
    # ... and a fresh instance reads it: the strict rule rejects a dashed name
    wiki = Wiki(tmp_path)
    assert wiki.validate_name('my_page') is True
    assert wiki.validate_name('bad-name') is False


@pytest.mark.parametrize(
    argnames='settings',
    argvalues=[
        {'naming': {'validate': ['bogus']}},
        {'timestamp': {'timezone': 'Mars/Olympus'}},
        {'timestamp': {'format': '%d %b %Y'}},
        {'timestamp': {'format': '%Y %Y'}},
        {'titles': {'required': 'yes'}},
        {'map': {'desc_limit': 'wide'}},
        {'exclude': {'patterns': ['!vendor']}},
        {'links': {'external': ['src']}},
        {'lint': {'strict': 'yes'}},
        {'lint': ['strict']},
    ],
    ids=[
        'bad-naming',
        'bad-timestamp',
        'bad-timestamp-directive',
        'bad-timestamp-read-back',
        'bad-titles',
        'bad-map',
        'bad-exclude',
        'bad-links',
        'bad-lint',
        'bad-lint-block',
    ],
)
def test_init_rejects_bad_settings_before_writing(
    tmp_path: pathlib.Path,
    settings: dict,
) -> None:
    """A rejected ``settings`` seed aborts ``init`` before writing anything.

    ``init`` seeds ``.wiki/settings.json`` and never overwrites it, so a
    policy the resolvers reject must fail up front -- naming the file -- rather
    than strand a wiki whose written seed every later command (and re-init,
    even with corrected settings) keeps failing on.
    """
    root = tmp_path / 'wiki'

    # init raises an error naming the settings file and writes nothing
    with pytest.raises(ValueError, match=r'settings\.json'):
        Wiki(root).init(settings=settings)
    assert not root.exists()
    # so a corrected re-init succeeds where the bad seed would have stuck
    Wiki(root).init(settings={'naming': {'validate': ['ascii']}})
    assert (root / '_index.md').is_file()


def test_init_rejects_invalid_wiki_name(tmp_path: pathlib.Path) -> None:
    """``init`` refuses a wiki name the naming policy rejects, writing nothing."""
    root = tmp_path / 'wiki'
    with pytest.raises(ValueError, match='Invalid wiki name'):
        Wiki(root).init(name='bad|name')
    assert not root.exists()


def test_init_refuses_conflict_markers(tmp_path: pathlib.Path) -> None:
    """Re-init over a conflict-marked tree refuses before the sweep writes.

    Init runs the same plan/apply sweep as update, which reads the markers
    as index structure and would rewrite the file with one conflict side
    silently dropped -- so init refuses alike, naming every marked file.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    # plant a real conflict in an index
    index = tmp_path / 'core' / '_index.md'
    conflict = '\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n'
    index.write_text(
        index.read_text(encoding='utf-8') + conflict,
        encoding='utf-8',
    )
    before = {path: path.read_text(encoding='utf-8') for path in tmp_path.rglob('*.md')}

    # init refuses, naming the marked file, and nothing was rewritten
    message = r'Merge conflict markers in: core/_index\.md'
    with pytest.raises(ValueError, match=message):
        Wiki(tmp_path).init()
    after = {path: path.read_text(encoding='utf-8') for path in tmp_path.rglob('*.md')}
    assert after == before


@pytest.mark.parametrize(
    argnames=('content', 'match'),
    argvalues=[
        ('{bad json', r'Malformed JSON'),
        ('{"a": ' + '[' * 1_000_000 + ']' * 1_000_000 + '}', r'Malformed JSON'),
        ('[]', r'must be a JSON object'),
        ('{"naming": "strict"}', r'naming block must be a JSON object'),
        ('{"naming": {"validate": "identifier"}}', r'validate must be a list'),
        ('{"naming": {"validate": ["bogus"]}}', r'Unknown naming predicate'),
        ('{"naming": {"min_length": 0}}', r'min_length must be an int'),
        ('{"naming": {"max_length": 0}}', r'max_length must be an int'),
        ('{"naming": {"deny": ["|"]}}', r'deny must be a string'),
        ('{"naming": {"reserved": "drafts"}}', r'reserved must be a list'),
        ('{"naming": {"leading_digits": "yes"}}', r'leading_digits must be a boolean'),
        ('{"naming": {"pattern": 5}}', r'pattern must be a string'),
        ('{"naming": {"pattern": "["}}', r'not a valid regex'),
        ('{"timestamp": "now"}', r'timestamp block must be a JSON object'),
        ('{"timestamp": {"format": 5}}', r'format must be a string'),
        ('{"timestamp": {"timezone": 5}}', r'timezone must be a string'),
        ('{"timestamp": {"timezone": "Mars/Olympus"}}', r'Unknown timestamp.timezone'),
    ],
    ids=[
        'malformed-json',
        'nested-past-recursion-limit',
        'non-object-top-level',
        'non-object-naming',
        'non-list-validate',
        'bad-predicate',
        'bad-min-length',
        'bad-max-length',
        'bad-deny',
        'bad-reserved',
        'bad-leading-digits',
        'non-string-pattern',
        'bad-pattern',
        'non-object-timestamp',
        'non-string-format',
        'non-string-timezone',
        'bad-timezone',
    ],
)
def test_settings_reject_malformed_values(
    tmp_path: pathlib.Path,
    content: str,
    match: str,
) -> None:
    """Malformed ``settings.json`` values fail loudly through a public command.

    ``settings.json`` is user-editable input: an unparseable file or an
    out-of-range/wrong-typed knob raises ``ValueError`` naming the file
    rather than silently falling back to a default, and the error surfaces
    through any command that reads the policy (here ``lint``).
    """
    # build a valid wiki, then corrupt its settings.json
    _make_wiki(tmp_path, folders={'core': ['design']})
    settings = tmp_path / '.wiki' / 'settings.json'
    settings.write_text(content, encoding='utf-8')

    # a fresh instance fails loudly, naming the settings file
    with pytest.raises(ValueError, match=match) as excinfo:
        Wiki(tmp_path).lint()
    assert 'settings.json' in str(excinfo.value)


def test_settings_blocks_are_validated_in_the_documented_order(
    tmp_path: pathlib.Path,
) -> None:
    """Of several malformed blocks, the first in the documented order is named.

    A command checks every block before it acts, so the block an error
    names depends on the settings file alone: not on the command, and not
    on the order the file lists its blocks in.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    order = ['naming', 'timestamp', 'map', 'titles', 'lint', 'exclude', 'links']
    settings = dict.fromkeys(reversed(order), 'malformed')
    path = tmp_path / '.wiki' / 'settings.json'

    # mend the named block each time, and the next one in order is named
    for block in order:
        path.write_text(json.dumps(settings), encoding='utf-8')
        message = f'The {block} block must be a JSON object'
        with pytest.raises(ValueError, match=message):
            Wiki(tmp_path).update(check=True)
        del settings[block]


@pytest.mark.parametrize(
    argnames=('settings', 'message'),
    argvalues=[
        # the JSON spelling of each kind of value
        (
            {'naming': {'leading_digits': None}},
            'naming.leading_digits must be a boolean, got null',
        ),
        (
            {'map': {'desc_limit': True}},
            'map.desc_limit must be an int >= -1 or null, got true',
        ),
        ({'map': {'indent': 1.5}}, 'map.indent must be a string, got 1.5'),
        # a float with an exponent prints in the interpreter's spelling
        ({'map': {'indent': 1e104}}, 'map.indent must be a string, got 1e+104'),
        (
            {'links': {'external': '../src'}},
            'links.external must be a list of folder paths, got "../src"',
        ),
        (
            {'timestamp': {'format': 'a\nb'}},
            'timestamp.format must render a single non-empty line, got "a\\nb"',
        ),
        # text outside ASCII prints as escapes
        (
            {'links': {'external': 'café'}},
            'links.external must be a list of folder paths, got "caf\\u00e9"',
        ),
        (
            {'exclude': {'patterns': {'a': True, 'b': [1.5, None]}}},
            'exclude.patterns must be a list of glob strings, got'
            ' {"a": true, "b": [1.5, null]}',
        ),
        # a value of 80 characters prints whole, and a longer one is cut there
        (
            {'naming': {'reserved': 'x' * 78}},
            'naming.reserved must be a list of strings, got "' + 'x' * 78 + '"',
        ),
        (
            {'naming': {'reserved': 'x' * 79}},
            'naming.reserved must be a list of strings, got "' + 'x' * 79 + '...',
        ),
        # an entry of a naming list that is not a string
        (
            {'naming': {'validate': [{'a': 1}]}},
            'naming.validate entry must be a string, got {"a": 1}',
        ),
        (
            {'naming': {'reserved': [['x']]}},
            'naming.reserved entry must be a string, got ["x"]',
        ),
        (
            {'naming': {'reserved': ['drafts', 1]}},
            'naming.reserved entry must be a string, got 1',
        ),
    ],
    ids=[
        'null',
        'true',
        'number',
        'number-with-an-exponent',
        'string',
        'string-with-a-line-break',
        'string-outside-ascii',
        'object',
        'eighty-characters',
        'cut-past-eighty',
        'object-in-validate',
        'list-in-reserved',
        'number-in-reserved',
    ],
)
def test_settings_error_spells_the_wrong_value_as_json(
    tmp_path: pathlib.Path,
    settings: dict,
    message: str,
) -> None:
    """A settings error shows the wrong value in the settings file's notation.

    The value prints as JSON, cut at 80 characters, so the error answers
    in the language the file is written in. An entry of ``naming.validate``
    or ``naming.reserved`` that is not a string gets the same error shape,
    naming its key, on every interpreter.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    path = tmp_path / '.wiki' / 'settings.json'
    path.write_text(json.dumps(settings), encoding='utf-8')

    with pytest.raises(ValueError) as excinfo:
        Wiki(tmp_path).update(check=True)
    assert str(excinfo.value) == f'{message} in .wiki/settings.json.'


def test_settings_warnings_name_only_keys_nothing_reads(
    tmp_path: pathlib.Path,
) -> None:
    """A settings file draws a warning for each key no reader takes, and no other.

    A file that sets every documented key is silent. A subclass that reads
    a key of its own lists it in ``settings_keys``: the key is then known,
    and a key beside it or inside it that the table does not list is named.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    path = tmp_path / '.wiki' / 'settings.json'
    settings: dict = {
        'naming': {
            'validate': ['ascii', 'identifier'],
            'allow': '-',
            'deny': '~',
            'pattern': '[a-z_-]+',
            'min_length': 2,
            'max_length': 40,
            'leading_digits': False,
            'reserved': ['tmp'],
        },
        'timestamp': {'timezone': 'Europe/Paris', 'format': '%Y-%m-%d %H:%M:%S%z'},
        'map': {'desc_limit': 80, 'indent': '    ', 'ellipsis': '~'},
        'titles': {'required': False},
        'lint': {'strict': True},
        'exclude': {'patterns': ['drafts/']},
        'links': {'external': ['../src']},
    }
    path.write_text(json.dumps(settings), encoding='utf-8')
    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)
    wiki.update(check=True)
    assert [type(event).__name__ for event in notices] == []

    class ReviewedWiki(Wiki):
        """Wiki subclass that reads a ``review`` block of its own."""

        settings_keys = {**Wiki.settings_keys, 'review': ('owner',)}

    settings = {'review': {'owner': 'ada', 'cadence': 7}, 'labels': ['draft']}
    path.write_text(json.dumps(settings), encoding='utf-8')
    wiki = ReviewedWiki(tmp_path)
    notices = _capture_notices(wiki)
    wiki.update(check=True)
    assert [event.description for event in notices] == [
        ".wiki/settings.json: unknown key 'review.cadence' (ignored)",
        ".wiki/settings.json: unknown key 'labels' (ignored)",
    ]


@pytest.mark.parametrize(
    argnames=('naming', 'warned'),
    argvalues=[
        ({'leading_digits': False}, True),
        ({'validate': ['ascii'], 'leading_digits': False}, True),
        ({'validate': ['ascii', 'identifier'], 'leading_digits': False}, False),
        ({'leading_digits': False, 'validate': ['identifier']}, False),
        ({'leading_digits': True}, False),
    ],
    ids=[
        'no-rules',
        'another-rule',
        'identifier',
        'identifier-listed-after',
        'default-value',
    ],
)
def test_leading_digits_warns_without_the_identifier_rule(
    tmp_path: pathlib.Path,
    naming: dict,
    warned: bool,
) -> None:
    """``leading_digits: false`` is named when no ``identifier`` rule reads it.

    The flag only narrows the ``identifier`` rule, so without that rule a
    name that opens on a digit still passes. The default ``true`` asks for
    nothing and draws no warning, with or without the rule.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    path = tmp_path / '.wiki' / 'settings.json'
    path.write_text(json.dumps({'naming': naming}), encoding='utf-8')
    wiki = Wiki(tmp_path)
    notices = _capture_notices(wiki)
    wiki.update(check=True)

    line = (
        ".wiki/settings.json: naming.leading_digits is false without 'identifier'"
        ' in naming.validate (it has no effect)'
    )
    assert [event.description for event in notices] == [line] * warned
    assert [event.key for event in notices] == ['naming.leading_digits'] * warned
    if warned:
        assert wiki.validate_name('2026-plan')


# ------ timestamps


def test_timestamp_format_configurable(tmp_path: pathlib.Path) -> None:
    """``timestamp.format`` controls the timestamp string format."""
    config = tmp_path / '.wiki'
    config.mkdir()
    (config / 'settings.json').write_text(
        json.dumps({'timestamp': {'format': '%Y'}}), encoding='utf-8'
    )
    stamp = Wiki(tmp_path)._utc_now()
    assert stamp.isdigit()
    assert len(stamp) == 4  # just the year


def test_timestamp_timezone_configurable(tmp_path: pathlib.Path) -> None:
    """``timestamp.timezone`` renders timestamps in the configured zone."""
    config = tmp_path / '.wiki'
    config.mkdir()
    (config / 'settings.json').write_text(
        json.dumps({'timestamp': {'timezone': 'America/New_York', 'format': '%z'}}),
        encoding='utf-8',
    )
    stamp = Wiki(tmp_path)._utc_now()
    assert stamp in ('-0400', '-0500')  # EDT/EST offset, never UTC's +0000


@pytest.mark.parametrize(
    argnames=('timestamp', 'pattern'),
    argvalues=[
        ({}, r'\d{2}:\d{2}Z$'),
        ({'timezone': 'America/New_York'}, r'\d{2}:\d{2}-0[45]00$'),
        ({'timezone': 'America/New_York', 'format': '%Y'}, r'^\d{4}$'),
    ],
    ids=['no-timezone-default', 'configured-zone', 'authored-format'],
)
def test_timestamp_zone_swaps_default_z_for_offset(
    tmp_path: pathlib.Path,
    timestamp: dict,
    pattern: str,
) -> None:
    """A configured zone renders the default format with a real offset.

    The default format's literal trailing ``Z`` asserts UTC, while a
    configured ``timestamp.timezone`` renders local wall-clock time, so
    the default swaps the ``Z`` for ``%z`` and the stamp carries the
    zone's honest offset. An authored format passes through untouched,
    and the stamps round-trip lint because rendering and parsing share
    the policy's format string.
    """
    wiki = Wiki(tmp_path)
    wiki.init(name='root', settings={'timestamp': timestamp})
    stamp = wiki._utc_now()
    assert re.search(pattern, stamp)
    # the stamps init wrote parse back under the same policy
    issues = wiki.lint()
    assert not any('Unparseable' in issue for issue in issues)


@pytest.mark.parametrize(
    argnames='format',
    argvalues=['', ' \t', '%Y\n%H'],
    ids=['empty', 'whitespace', 'embedded-newline'],
)
def test_timestamp_format_rejects_blank_or_multiline(
    tmp_path: pathlib.Path,
    format: str,
) -> None:
    """A ``timestamp.format`` that yields a blank or multi-line value is rejected.

    Such a value would write a blank or newline-bearing ``created:`` into the
    YAML frontmatter; the resolver rejects it loudly at config load, the way the
    naming policy rejects a bad name.
    """
    config = tmp_path / '.wiki'
    config.mkdir()
    (config / 'settings.json').write_text(
        json.dumps({'timestamp': {'format': format}}),
        encoding='utf-8',
    )
    # the bad format fails loudly when update resolves the timestamp policy
    with pytest.raises(ValueError, match='single non-empty line'):
        Wiki(tmp_path).update()


@pytest.mark.parametrize(
    argnames='format',
    argvalues=['\0%Y', '%Y\0%m', '%Y\0'],
    ids=['first', 'inside', 'last'],
)
def test_timestamp_format_rejects_a_nul_character(
    tmp_path: pathlib.Path,
    format: str,
) -> None:
    """A ``timestamp.format`` that holds a NUL character is a settings error.

    Interpreters disagree on the stamp such a format renders, and none
    reads it back from frontmatter, so the format is refused wherever
    the NUL sits. Nothing is stamped under it, and lint refuses it alike
    rather than fail on every stamp.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'page.md').write_text('Body.\n', encoding='utf-8')
    path = tmp_path / '.wiki' / 'settings.json'
    path.write_text(json.dumps({'timestamp': {'format': format}}), encoding='utf-8')
    message = (
        f'timestamp.format must not hold a NUL character, got {json.dumps(format)}'
        ' in .wiki/settings.json.'
    )

    for operation in (Wiki(tmp_path).update, Wiki(tmp_path).lint):
        with pytest.raises(ValueError) as excinfo:
            operation()
        assert str(excinfo.value) == message
    assert (tmp_path / 'page.md').read_text(encoding='utf-8') == 'Body.\n'


@pytest.mark.parametrize(
    argnames=('timestamp', 'stamp'),
    argvalues=[
        ({'format': '%Y-%m-%dT%H:%M:%SZ'}, '2026-01-02T03:04:05Z'),
        (
            {'format': '%Y-%m-%dT%H:%M:%S%z', 'timezone': 'America/New_York'},
            '2026-01-01T22:04:05-0500',
        ),
        ({'format': '%Y'}, '2026'),
        ({'format': '%z'}, '+0000'),
        ({'format': '%Y-%m-%d'}, '2026-01-02'),
        ({'format': '%d.%m.%Y %H:%M'}, '02.01.2026 03:04'),
        ({'format': '%Y%m%d%H%M%S'}, '20260102030405'),
        ({'format': '%z:%H:%M'}, '+0000:03:04'),
        ({'format': '%Y%%n%m'}, '2026%n01'),
        ({'format': '%H%%z5'}, '03%z5'),
    ],
    ids=[
        'utc-default',
        'zone-default',
        'year',
        'offset',
        'date',
        'dotted-date',
        'unseparated',
        'offset-then-colon',
        'percent-sign',
        'percent-sign-then-z',
    ],
)
def test_timestamp_format_stamps_parse_back(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    timestamp: dict,
    stamp: str,
) -> None:
    """A format of the supported directives stamps pages lint parses back.

    The set is ``%Y``, ``%m``, ``%d``, ``%H``, ``%M``, ``%S``, ``%z``,
    ``%%`` and literal text. A date-only format is one of them: a stamp
    reads back as text, not as the instant it was taken at. A separator
    after ``%z`` keeps the offset apart from the digits behind it, a
    colon included, and ``%%`` is a percent sign whatever letter follows
    it: before a ``z`` it opens no offset, so a digit may come next.
    """
    monkeypatch.setenv(WIKI_FREEZE_TIME, '2026-01-02T03:04:05Z')
    wiki = Wiki(tmp_path)
    wiki.init(name='root', settings={'timestamp': timestamp})
    (tmp_path / 'page.md').write_text('Body.\n', encoding='utf-8')
    wiki.update()
    text = (tmp_path / 'page.md').read_text(encoding='utf-8')
    assert re.findall(r'^(?:created|updated): (.*)$', text, re.M) == [stamp] * 2
    assert not any('Unparseable' in issue for issue in wiki.lint())


@pytest.mark.parametrize(
    argnames=('format', 'directive'),
    argvalues=[
        ('%d %b %Y', '%b'),
        ('%Y-%j', '%j'),
        ('%H:%M:%S.%f', '%f'),
        ('%H:%M %Z', '%Z'),
        ('%s', '%s'),
        ('%Y-%m-%d %Ez', '%E'),
        ('%G-W%V', '%G'),
        ('%Y%n%H', '%n'),
        ('100%', '%'),
    ],
    ids=[
        'month-name',
        'day-of-year',
        'microsecond',
        'zone-name',
        'epoch-seconds',
        'modified-offset',
        'iso-week',
        'newline',
        'trailing-percent',
    ],
)
def test_timestamp_format_rejects_an_unsupported_directive(
    tmp_path: pathlib.Path,
    format: str,
    directive: str,
) -> None:
    """A ``%`` sequence outside the supported set is a settings error naming it.

    The first one at fault is named by its ``%`` and the character after
    it, a ``%`` that ends the format by itself, and the message lists the
    set. Nothing is stamped under the format first.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'page.md').write_text('Body.\n', encoding='utf-8')
    path = tmp_path / '.wiki' / 'settings.json'
    path.write_text(json.dumps({'timestamp': {'format': format}}), encoding='utf-8')

    with pytest.raises(ValueError) as excinfo:
        Wiki(tmp_path).update()
    assert str(excinfo.value) == (
        f'Unsupported timestamp.format directive {directive!r} (must be one of'
        f' %Y, %m, %d, %H, %M, %S, %z, %%), got {json.dumps(format)} in'
        ' .wiki/settings.json.'
    )
    assert (tmp_path / 'page.md').read_text(encoding='utf-8') == 'Body.\n'


@pytest.mark.parametrize(
    argnames=('format', 'pair'),
    argvalues=[
        ('%z%H', '%z%H'),
        ('%z%H%M', '%z%H'),
        ('%Y%z%d%H', '%z%d'),
        ('%H:%M%z%Y', '%z%Y'),
        ('%z3%H%M', '%z3'),
        ('%Y-%m-%d%z0', '%z0'),
    ],
    ids=[
        'hour',
        'hour-and-minute',
        'day',
        'year',
        'literal-digit',
        'literal-digit-last',
    ],
)
def test_timestamp_format_rejects_a_digit_after_the_offset(
    tmp_path: pathlib.Path,
    format: str,
    pair: str,
) -> None:
    """A format with a digit straight after ``%z`` is a settings error.

    A parser takes two digits after the offset for its seconds wherever
    the rest of the stamp still parses, so such a format reads some
    stamps back as another instant. The rule covers a directive that
    renders digits and a literal digit alike, whether or not any stamp
    of that one format would misread, and the error names the pair.
    Nothing is stamped under the format, and lint refuses it alike.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'page.md').write_text('Body.\n', encoding='utf-8')
    path = tmp_path / '.wiki' / 'settings.json'
    path.write_text(json.dumps({'timestamp': {'format': format}}), encoding='utf-8')
    message = (
        f'timestamp.format must not follow %z with a digit ({pair!r} does), got'
        f' {json.dumps(format)} in .wiki/settings.json.'
    )

    for operation in (Wiki(tmp_path).update, Wiki(tmp_path).lint):
        with pytest.raises(ValueError) as excinfo:
            operation()
        assert str(excinfo.value) == message
    assert (tmp_path / 'page.md').read_text(encoding='utf-8') == 'Body.\n'


@pytest.mark.parametrize(
    argnames=('timestamp', 'stamp'),
    argvalues=[
        ({'format': '%Y %Y'}, '2023 2023'),
        ({'format': '%H:%M %H', 'timezone': 'Asia/Kolkata'}, '03:44 03'),
        ({'format': '%z %z', 'timezone': 'America/St_Johns'}, '-0330 -0330'),
    ],
    ids=['year-twice', 'hour-twice-in-a-zone', 'offset-twice-in-a-zone'],
)
def test_timestamp_format_must_read_back_its_stamp(
    tmp_path: pathlib.Path,
    timestamp: dict,
    stamp: str,
) -> None:
    """A format that does not parse back the stamp it renders is refused.

    Two fixed instants are rendered in the wiki's zone, parsed under the
    format and rendered again, and the error shows the first stamp that
    fails to parse or comes back as other text. A directive named twice
    never parses. Nothing is stamped under the format, and lint refuses
    it alike rather than fail on every stamp.
    """
    _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'page.md').write_text('Body.\n', encoding='utf-8')
    path = tmp_path / '.wiki' / 'settings.json'
    path.write_text(json.dumps({'timestamp': timestamp}), encoding='utf-8')
    format = json.dumps(timestamp['format'])
    message = (
        f'timestamp.format must read back the stamp it writes ({stamp!r} does'
        f' not), got {format} in .wiki/settings.json.'
    )

    for operation in (Wiki(tmp_path).update, Wiki(tmp_path).lint):
        with pytest.raises(ValueError) as excinfo:
            operation()
        assert str(excinfo.value) == message
    assert (tmp_path / 'page.md').read_text(encoding='utf-8') == 'Body.\n'


def test_wiki_freeze_time_fixes_the_clock_in_process(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``WIKI_FREEZE_TIME`` fixes the stamps of a host that drives the class.

    The class reads the seam, not the CLI, so a host that sets it gets the
    clock a CLI run under the same value gets -- and an invalid value
    raises where the clock is read rather than falling back to the clock.
    """
    # a valid value fixes both stamps
    monkeypatch.setenv(WIKI_FREEZE_TIME, '2026-01-02T03:04:05+02:00')
    wiki = Wiki(tmp_path)
    wiki.init(name='root', settings={'timestamp': {'timezone': 'America/New_York'}})
    text = (tmp_path / '_index.md').read_text(encoding='utf-8')
    assert 'created: 2026-01-01T20:04:05-0500\n' in text
    assert 'updated: 2026-01-01T20:04:05-0500\n' in text
    # an invalid value raises at the next clock read and writes nothing
    monkeypatch.setenv(WIKI_FREEZE_TIME, 'now')
    (tmp_path / 'page.md').write_text('Body.\n', encoding='utf-8')
    with pytest.raises(ValueError, match='must be an RFC 3339 instant'):
        wiki.update()
    assert (tmp_path / 'page.md').read_text(encoding='utf-8') == 'Body.\n'


# ------ legacy layout


def test_init_refuses_legacy_layout_before_writing(
    tmp_path: pathlib.Path,
) -> None:
    """Init on a legacy-layout wiki refuses up front, writing nothing.

    Init plans the same sweep update and lint refuse on: proceeding would
    seed ``.wiki/settings.json`` from defaults -- silently masking the
    legacy ``_config/settings.json`` policy -- and index ``_config/`` as
    content, so init must raise the migration steps before the settings
    seed, the root index, or any sweep write lands.
    """
    (tmp_path / '_config').mkdir()
    (tmp_path / '_config' / 'settings.json').write_text('{}\n', encoding='utf-8')
    (tmp_path / 'notes.md').write_text('# notes\n\nLegacy content.\n', encoding='utf-8')
    wiki = Wiki(tmp_path)
    with pytest.raises(ValueError, match=r'(?s)Legacy wiki layout.*wiki update'):
        wiki.init(name='root')

    # nothing was written: no marker, no root index, no indexed _config/
    assert not (tmp_path / '.wiki').exists()
    assert not (tmp_path / '_index.md').exists()
    assert not (tmp_path / '_config' / '_index.md').exists()


# ------ index canonicalization


@pytest.mark.parametrize(
    argnames=('wiki_class', 'text', 'expected'),
    argvalues=[
        # an index in form keeps every byte
        (
            Wiki,
            f'{_HEAD}{_PARENT}{_ALPHA}{_ZETA}{_CONTENT}',
            f'{_HEAD}{_PARENT}{_ALPHA}{_ZETA}{_CONTENT}',
        ),
        # a retitled index takes the title into its H1 alone
        (
            Wiki,
            f'{_TITLED}# core\n\n{_PARENT}{_ALPHA}{_CONTENT}',
            f'{_TITLED}# Core Concepts\n\n{_PARENT}{_ALPHA}{_CONTENT}',
        ),
        # rows out of order move to update's order, rendered as it renders them
        (
            Wiki,
            f'{_HEAD}{_PARENT}{_ZETA}{_ALPHA}{_CONTENT}',
            f'{_HEAD}{_PARENT}{_ALPHA}{_ZETA}{_CONTENT}',
        ),
        # the class's category order places the categorized rows
        (
            CategorizedWiki,
            f'{_HEAD}{_PARENT}{_API}{_STORE}{_CONTENT}',
            f'{_HEAD}{_PARENT}{_STORE}{_API}{_CONTENT}',
        ),
        # a text with no delimiter, or with two rows for one target, stays whole
        (
            Wiki,
            f'{_HEAD}{_PARENT}{_ZETA}{_ALPHA}',
            f'{_HEAD}{_PARENT}{_ZETA}{_ALPHA}',
        ),
        (
            Wiki,
            f'{_HEAD}{_PARENT}{_ZETA}{_ALPHA}{_ZETA}{_CONTENT}',
            f'{_HEAD}{_PARENT}{_ZETA}{_ALPHA}{_ZETA}{_CONTENT}',
        ),
        # one target spelled composed on one row and decomposed on the other
        (
            Wiki,
            f'{_HEAD}{_PARENT}{_ZETA}{_CAFE}{_CAFE_NFD}{_CONTENT}',
            f'{_HEAD}{_PARENT}{_ZETA}{_CAFE}{_CAFE_NFD}{_CONTENT}',
        ),
    ],
    ids=[
        'in-form',
        'retitled',
        'out-of-order',
        'category-order',
        'no-delimiter',
        'repeated-target',
        'repeated-target-nfd',
    ],
)
def test_canonicalize_index_puts_the_h1_and_rows_in_form(
    tmp_path: pathlib.Path,
    wiki_class: type[Wiki],
    text: str,
    expected: str,
) -> None:
    """``canonicalize_index`` sets the H1 and the row order, and nothing else.

    The merge driver brings a clean merge to update's form with it: the
    H1 follows the title, else the name, and the rows take the order the
    wiki's class gives them, rendered as update renders them, while the
    frontmatter and the content below the delimiter keep their bytes. A
    text with no delimiter, or with two rows for one target, comes back
    whole.
    """
    assert wiki_class(tmp_path).canonicalize_index(text) == expected
