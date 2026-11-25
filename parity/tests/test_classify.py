"""Test the ``parity.classify`` module.

The registry check runs each classifier on whole commands. This module
carries what one command cannot show: where the oracle-side judge draws
the line of the supported set, and the parts of each classifier's frame
that hold a difference just outside an entry apart from one inside it.
"""

from __future__ import annotations

import base64
import pathlib
import shutil
from typing import Any, Optional

import pytest

from parity import cache, classify, compare, side
from parity.compare import Difference, Record, Run
from parity.matrix import Case, Stage
from parity.side import Outcome

from .conftest import NOW

__all__ = [
    'test_judge_draws_the_line_of_the_supported_set',
    'test_classifier_excuses_only_its_own_frame',
    'test_forbidden_exit_code_is_excused_only_with_a_refusal',
]

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='requires git')

# every construct of the supported set in one pattern
_INSIDE = (
    r'^\A\b\B\d\D\w\W\s\S.[a-z\d^]|(?:x)(?P<n>y)(?P=n)(z)\2'
    r'(?=a)(?!b)(?<=c)(?<!d)a*?b+?c??d{1,2}\Z$'
)
# the prefix a library's text follows in a malformed settings file, and a
# usage error as v1 prints one that wiki's own code raised
_MALFORMED = b'Error: Malformed JSON in .wiki/settings.json: '
_USAGE = (
    b"Usage: wiki map [OPTIONS] [name]\nTry 'wiki map --help' for help.\n\n"
    b'Error: Invalid value: --depth must be >= 0.\n'
)
_KEPT = b'Error: Invalid value: --depth must be >= 0.\n\nUsage: wiki map [NAME]\n'


@pytest.fixture(scope='module')
def judge(
    tmp_path_factory: pytest.TempPathFactory,
    engine: side.Side,
) -> classify.Judge:
    """Return the oracle side's judge, with one zone file on its search path."""
    home = tmp_path_factory.mktemp('judge')
    zones = home / 'zones'
    (zones / 'Parity').mkdir(parents=True)
    (zones / 'Parity' / 'Zone').write_bytes(b'')
    env = side.environment(engine, home, now=NOW)
    return classify.Judge(engine.judge, env={**env, 'PYTHONTZPATH': f'{zones}'})


def _settings(text: str) -> dict[str, Any]:
    """Return the request for a settings file that holds ``text``."""
    data = base64.b64encode(text.encode('utf-8')).decode('ascii')
    return {'kind': 'settings', 'data': data}


def _nested(depth: int) -> str:
    """Return settings text whose arrays and objects nest ``depth`` deep."""
    return '{"parity": ' + '[' * (depth - 1) + ']' * (depth - 1) + '}'


@pytest.mark.parametrize(
    argnames=('asked', 'outside'),
    argvalues=[
        # regex constructs
        ({'kind': 'regex', 'pattern': _INSIDE}, []),
        ({'kind': 'regex', 'pattern': 'a*+'}, ['possessive quantifier']),
        ({'kind': 'regex', 'pattern': '(?>a)'}, ['atomic group']),
        ({'kind': 'regex', 'pattern': '(a)(?(1)b|c)'}, ['conditional group']),
        ({'kind': 'regex', 'pattern': r'(?a)\w'}, ['flag a']),
        ({'kind': 'regex', 'pattern': r'(?a:\w)'}, ['flag a']),
        ({'kind': 'regex', 'pattern': r'(?imsx)a . b'}, []),
        # a backreference, with and without case folding in force
        ({'kind': 'regex', 'pattern': r'(a)\1'}, []),
        (
            {'kind': 'regex', 'pattern': r'(a)\1', 'flags': 'i'},
            ['backreference under flag i'],
        ),
        ({'kind': 'regex', 'pattern': r'(?i:(a)\1)'}, ['backreference under flag i']),
        ({'kind': 'regex', 'pattern': r'(?-i:(a)\1)', 'flags': 'i'}, []),
        # timestamp directives
        ({'kind': 'format', 'format': '%Y-%m-%dT%H:%M:%S%z 100%%'}, []),
        ({'kind': 'format', 'format': '%Y-%j %U'}, ['%j', '%U']),
        ({'kind': 'format', 'format': '100%'}, ['%']),
        # zones, by the files on the search path
        ({'kind': 'zone', 'key': 'Parity/Zone'}, []),
        ({'kind': 'zone', 'key': 'Parity/Other'}, ['no zone file on the search path']),
        # values of the settings text
        (_settings('{"parity": [1e999, "NaN Infinity", "\\ud83d\\ude00"]}'), []),
        (_settings('{"parity": [NaN, -Infinity]}'), ['-Infinity', 'NaN']),
        (_settings('{"parity": "\\ud83d"}'), ['a lone surrogate escape']),
        (_settings('{"parity": "\\\\ud83d"}'), []),
        (_settings(_nested(127)), []),
        (_settings(_nested(128)), ['nesting of 128 levels or more']),
        # a later duplicate of a key hides the value, not the text
        (
            _settings('{"parity": ' + _nested(127) + ', "parity": 1}'),
            ['nesting of 128 levels or more'],
        ),
        # the fields of a settings file
        (
            _settings(
                '{"naming": {"pattern": "a*+"},'
                ' "timestamp": {"format": "%j", "timezone": "Parity/Other"}}'
            ),
            [
                'naming.pattern: possessive quantifier',
                'timestamp.format: %j',
                'timestamp.timezone: no zone file on the search path',
            ],
        ),
        (
            _settings(
                '{"naming": {"pattern": "a+"},'
                ' "timestamp": {"format": "%Y", "timezone": "Parity/Zone"}}'
            ),
            [],
        ),
    ],
    ids=[
        'whole set',
        'possessive quantifier',
        'atomic group',
        'conditional group',
        'flag a',
        'flag a in a group',
        'flags imsx',
        'backreference',
        'backreference under i',
        'backreference in an i group',
        'backreference in a group without i',
        'supported directives',
        'other directives',
        'percent at the end',
        'zone with a file',
        'zone without a file',
        'strict values',
        'constants',
        'lone surrogate',
        'escaped backslash',
        'nesting of 127',
        'nesting of 128',
        'nesting under a repeated key',
        'fields outside the set',
        'fields inside the set',
    ],
)
def test_judge_draws_the_line_of_the_supported_set(
    judge: classify.Judge,
    asked: dict[str, Any],
    outside: list[str],
) -> None:
    """The oracle's interpreter calls an input unsupported exactly outside the set.

    Each kind of input is asked on both sides of its line. An input the
    judge wrongly calls unsupported would excuse a refusal of something
    every engine must run; one it wrongly calls supported would report a
    refusal the registry allows.
    """
    answer = judge.ask(**asked)
    assert answer['outside'] == outside
    assert answer.get('error') is None


def _record(
    tmp_path: pathlib.Path,
    stdout: bytes,
    stderr: bytes,
    code: int,
    *,
    touched: tuple[str, ...] = (),
    settings: Optional[bytes] = None,
) -> Record:
    """Return what one command did, on a tree with no caches."""
    return Record(
        outcome=Outcome(stdout=stdout, stderr=stderr, code=code),
        touched=touched,
        changes={},
        view=cache.view(tmp_path, {}),
        settings=settings,
    )


@pytest.mark.parametrize(
    argnames=('args', 'settings', 'first', 'second', 'touched', 'entry'),
    argvalues=[
        # D3: v1 ran, the other side refused input outside the set
        (('match', 'a*+'), None, (b'x\n', b'', 0), (b'', b'Error: No.\n', 2), (), 'D3'),
        (
            ('match', 'a*+'),
            None,
            (b'x\n', b'', 0),
            (b'x', b'Error: No.\n', 2),
            (),
            None,
        ),
        (
            ('match', 'a*+'),
            None,
            (b'x\n', b'', 0),
            (b'', b'Error: No.\n', 2),
            ('tree/page.md',),
            None,
        ),
        (
            ('match', 'a*+'),
            None,
            (b'x\n', b'', 0),
            (b'', b'Error: No.\nmore\n', 2),
            (),
            None,
        ),
        (('match', 'a*+'), None, (b'', b'x\n', 2), (b'', b'Error: No.\n', 2), (), None),
        (('match', 'a*+'), None, (b'x\n', b'', 0), (b'', b'Error: No.\n', 1), (), None),
        # D3: a settings value is refused as malformed JSON, and no other way
        (
            ('lint',),
            b'{"parity": NaN}',
            (b'x\n', b'', 0),
            (b'', _MALFORMED + b'NaN\n', 2),
            (),
            'D3',
        ),
        (
            ('lint',),
            b'{"parity": NaN}',
            (b'x\n', b'', 0),
            (b'', b'Error: No.\n', 2),
            (),
            None,
        ),
        # D1: a text after the prefix, where v1's own reader raises
        (
            ('lint',),
            b'{"parity": ',
            (b'', _MALFORMED + b'one\n', 2),
            (b'', _MALFORMED + b'two\n', 2),
            (),
            'D1',
        ),
        (
            ('lint',),
            b'{"parity": ',
            (b'', _MALFORMED + b'one\n', 2),
            (b'', _MALFORMED + b'\n', 2),
            (),
            None,
        ),
        (
            ('lint',),
            b'{"parity": 1}',
            (b'', _MALFORMED + b'one\n', 2),
            (b'', _MALFORMED + b'two\n', 2),
            (),
            None,
        ),
        # D1: v1's text is another error than its `re` raises on the pattern
        (
            ('match', '('),
            None,
            (b'', b'Error: No wiki found.\n', 2),
            (b'', b'Error: Nothing here.\n', 2),
            (),
            None,
        ),
        # D1: a warning the pattern gives, and one it does not
        (
            ('match', '[[a]'),
            None,
            (b'', b'w.py:1: FutureWarning: Possible nested set at position 1\n', 1),
            (b'', b'', 1),
            (),
            'D1',
        ),
        (
            ('match', '[[a]'),
            None,
            (b'', b'w.py:1: FutureWarning: Possible set union at position 1\n', 1),
            (b'', b'', 1),
            (),
            None,
        ),
        # D2: the message line of wiki's own code, alone
        (('map', '--depth', '-1'), None, (b'', _USAGE, 2), (b'', _KEPT, 2), (), 'D2'),
        (
            ('map', '--depth', '-1'),
            None,
            (b'', _USAGE, 2),
            (b'', _KEPT + b'note: more\n', 2),
            (),
            None,
        ),
        (('map', '--depth', '-1'), None, (b'', _USAGE, 1), (b'', _KEPT, 1), (), None),
        # D2: a help page only where help was asked for and shown
        (('lint', '--help'), None, (b'Usage: a\n', b'', 0), (b'b\n', b'', 0), (), 'D2'),
        (('lint',), None, (b'Usage: a\n', b'', 0), (b'b\n', b'', 0), (), None),
        (('lint', '--help'), None, (b'Usage: a\n', b'', 2), (b'b\n', b'', 2), (), None),
    ],
    ids=[
        'refusal',
        'refusal that printed',
        'refusal that wrote',
        'refusal in two lines',
        'refusal where v1 refused',
        'refusal with another exit code',
        'value refused as malformed',
        'value refused another way',
        'malformed text',
        'malformed with no text',
        'malformed where v1 reads',
        'another error',
        'warning',
        'another warning',
        'kept message',
        'kept message with more',
        'usage error that exits 1',
        'help page',
        'page without help',
        'help that exits 2',
    ],
)
def test_classifier_excuses_only_its_own_frame(
    tmp_path: pathlib.Path,
    judge: classify.Judge,
    args: tuple[str, ...],
    settings: Optional[bytes],
    first: tuple[bytes, bytes, int],
    second: tuple[bytes, bytes, int],
    touched: tuple[str, ...],
    entry: Optional[str],
) -> None:
    """A command is excused under an entry only when the whole frame holds.

    Each excused command stands beside neighbors that differ from it in
    one part of the frame: a refusal that printed, wrote, ran long or met
    a v1 that refused too; a library's text with no text, or where v1's
    own reader does not raise; a warning the pattern does not give; a kept
    message with a line beside it; a page where no help was asked for.
    Every neighbor stays a difference.
    """
    one = _record(tmp_path, *first, settings=settings)
    two = _record(tmp_path, *second, touched=touched, settings=settings)
    names = ('stdout', 'stderr', 'exit')
    aspects = {name for name, a, b in zip(names, first, second) if a != b}
    if touched:
        aspects.add('touched')
    command = classify.Command(
        args=args,
        first=one,
        second=two,
        aspects=frozenset(aspects),
    )
    assert classify.explain(command, judge) == entry


@pytest.mark.parametrize(
    argnames=('settings', 'first', 'second', 'entry', 'kept'),
    argvalues=[
        (
            b'{"parity": NaN}',
            (b'x\n', b'', 0),
            (b'', _MALFORMED + b'NaN\n', 2),
            'D3',
            [],
        ),
        (
            b'{"parity": ',
            (b'', _MALFORMED + b'one\n', 2),
            (b'', _MALFORMED + b'two\n', 2),
            'D1',
            ['one', 'two'],
        ),
    ],
    ids=['refusal', 'text'],
)
def test_forbidden_exit_code_is_excused_only_with_a_refusal(
    tmp_path: pathlib.Path,
    judge: classify.Judge,
    settings: bytes,
    first: tuple[bytes, bytes, int],
    second: tuple[bytes, bytes, int],
    entry: str,
    kept: list[str],
) -> None:
    """An exit code its case forbids passes only as the end of a registered refusal.

    The case allows exit code 0. A refusal the registry allows ends with
    exit code 2 where v1 ended with 0, and that code is excused with the
    rest of the command. Two sides that both end with 2 and differ in a
    library's text are excused in that text alone: neither ran the command
    the case is built for, and each is still reported.
    """
    stage = Stage(name='stage', cases=(Case(name='lint', args=('lint',)),))
    runs = [
        Run(
            name=name,
            baseline={},
            view=cache.view(tmp_path, {}),
            records=(_record(tmp_path, *outcome, settings=settings),),
            final={},
        )
        for name, outcome in (('one', first), ('two', second))
    ]
    found = compare.stage(stage, runs, folder='tree/wiki/.wiki/cache')
    assert Difference('stage', 'two', 'lint', 'unexpected', 'exit 2') in found
    left, excused = classify.apply(stage, runs, found, judge=judge)
    assert excused == [classify.Excused('stage', 'two', 'lint', entry)]
    expected = [
        Difference('stage', name, 'lint', 'unexpected', 'exit 2') for name in kept
    ]
    assert left == expected
