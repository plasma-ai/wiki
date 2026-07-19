"""Test the ``wiki.core.wiki`` module.

Construction and policy: settings/naming/timestamp validation, the
``validate_name`` matrix, init idempotence and refusals, and the
legacy-layout guard. Each verb's behavior has its own suite
(``test_update``, ``test_plan``, ``test_lint``, ``test_read``,
``test_search``, ``test_map``, ``test_config``).
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from wiki.core.wiki import Wiki

from ._helpers import _make_wiki

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
    'test_timestamp_format_configurable',
    'test_timestamp_timezone_configurable',
    'test_timestamp_zone_swaps_default_z_for_offset',
    'test_timestamp_format_rejects_blank_or_multiline',
    'test_init_refuses_legacy_layout_before_writing',
]


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
        {'titles': {'required': 'yes'}},
        {'map': {'desc_limit': 'wide'}},
    ],
    ids=[
        'bad-naming',
        'bad-timestamp',
        'bad-titles',
        'bad-map',
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
    argvalues=['', '%Y%n%H'],
    ids=['empty', 'embedded-newline'],
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
