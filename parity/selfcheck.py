"""The harness self-check: one side against itself, clean and doctored.

The registry has a check of its own: one side against a stand-in that prints
what a second engine may print, on the fixtures of each classifier.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import shutil
import struct
import sys
from collections.abc import Mapping
from typing import Any, Optional

from . import cache, doctor, runner, standin
from .classify import Excused
from .compare import Difference
from .matrix import _ABSENT, Case, Stage
from .runner import _SNAPSHOT, Corpus, Plan
from .side import NOTICE, Side

__all__ = [
    'CHECKS',
    'TREES',
    'FIXTURES',
    'Check',
    'Fixture',
    'run',
    'clean',
    'doctored',
    'registry',
    'fixtures',
]

#: the doctored checks: the name of each, what its doctored side changes
#: (a kind of :data:`doctor.KINDS`), and the stage whose last command it
#: changes it in -- nothing runs after that command, so the one change
#: cannot spread into another command's output
CHECKS = (
    ('stdout', 'stdout', 'full'),
    ('stderr', 'stderr', 'full'),
    ('exit', 'exit', 'full'),
    ('file', 'file', 'full'),
    ('mode', 'mode', 'full'),
    ('extra', 'extra', 'full'),
    ('rewrite', 'rewrite', 'full'),
    ('stray', 'extra', 'write'),
    ('counts', 'counts', 'write'),
    ('index', 'index', 'write'),
    ('cache-mode', 'mode', 'write'),
    ('schema', 'schema', 'write'),
    ('notice', 'notice', 'full'),
    ('identity', 'identity', 'full'),
)

#: what each tree of the registry check sets in the settings file the
#: snapshot holds, by the name of the tree: one input of one classifier, so
#: that every other setting of the wiki stays as it is
TREES: Mapping[str, Mapping[str, Any]] = {
    'snapshot': {},
    'malformed': {},
    'pattern': {'naming': {'pattern': '('}},
    'warned': {'naming': {'pattern': '[[a]|.*'}},
    'value': {'parity': float('nan')},
    'directive': {'timestamp': {'format': '%Y-%j'}},
    'zone': {'timestamp': {'timezone': 'Parity/Zone'}},
}

# the two sides of one engine differ only in their hash seed
_SEEDS = ('1', '2')
# the paths a doctored side acts on, relative to the wiki root
_EXTRA = 'parity_doctored.md'
_COUNTS = f'{cache.CACHE}/word_counts.json'
_SEARCH = f'{cache.CACHE}/search.db'
# where the trees of a failed check are filed, below the work folder
_FAILED = 'failed'
# the name of the stand-in side, and of a stage of the registry check
_STANDIN = 'stand-in'
_STAGE = 'registry {tree}'
# the tree whose settings file ends one character early
_CUT = 'malformed'
# the wiki-written prefix of malformed settings, as v1 prints it, as an
# expression, and as a stand-in changes it
_MALFORMED = 'Error: Malformed JSON in .wiki/settings.json: '
_PREFIX = r'Error: Malformed JSON in \.wiki/settings\.json: '
_RENAMED = 'Error: Malformed JSON in settings.json: '
# what a stand-in changes: a whole stream, the text behind each wiki-written
# prefix, the warning of `re` with its source line, and the frame of a usage
# error
_WHOLE = r'(?s)\A.*\Z'
_PATTERN_TEXT = r'(?m)^(Error: ).*$'
_SETTINGS_TEXT = rf'(?m)^({_PREFIX}).*$'
_NAMING_TEXT = (
    r'(?m)^(Error: naming\.pattern in \.wiki/settings\.json is not a valid regex: ).*$'
)
_QUERY_TEXT = r"""(?m)^(Error: Invalid FTS5 query '"': ).*$"""
_WARNING = r'(?m)^.*: FutureWarning: .*\n  .*\n'
_FRAME = r"Usage: .*\nTry '.*' for help\.\n\n(Error: .*\n)"
_REFRAMED = "\n\nUsage: wiki [OPTIONS]\n\nFor more information, try '--help'.\n"
# a refusal: nothing on stdout, one error line behind the notice, exit code 2
_NOTICE = re.escape(NOTICE.decode('ascii'))
_KEPT = rf'(?s)\A({_NOTICE})?.*\Z'
_REFUSED = {
    'stdout': [_WHOLE, ''],
    'stderr': [_KEPT, r'\1Error: Refused by the stand-in.\n'],
    'exit': 2,
}
# a refusal of a settings value, under the prefix of malformed settings
_REFUSED_VALUE = {
    'stdout': [_WHOLE, ''],
    'stderr': [_KEPT, rf'\1{_MALFORMED}NaN at column 12\n'],
    'exit': 2,
}
# a zone file that only the `tzdata` package holds: TZif, one type, UTC
_ZONES = 'zones'
_TZIF = (
    b'TZif'
    + bytes(16)
    + struct.pack('>6L', 0, 0, 0, 0, 1, 4)
    + struct.pack('>lBB', 0, 0, 0)
    + b'UTC\x00'
)


@dataclasses.dataclass(frozen=True)
class Check:
    """One part of the self-check.

    Attributes:
        name: ``clean``, ``doctored`` and the name of the check, or
            ``registry`` and the name of the tree.
        expected: The differences the harness must report, and no other.
        found: The differences it reported.
        report: The matrix run as JSON data.
        streams: The streams of each case whose streams differ, as a
            matrix run returns them.
        registered: The commands whose differences the harness must excuse
            under a registry entry, and no other.
        excused: The commands it excused.

    """

    name: str
    expected: tuple[Difference, ...]
    found: tuple[Difference, ...]
    report: Mapping[str, Any]
    streams: Mapping[str, bytes]
    registered: tuple[Excused, ...] = ()
    excused: tuple[Excused, ...] = ()

    @property
    def passed(self: Check) -> bool:
        """Return whether the harness reported and excused exactly what it must."""
        reported = sorted(self.expected) == sorted(self.found)
        return reported and (sorted(self.registered) == sorted(self.excused))


@dataclasses.dataclass(frozen=True)
class Fixture:
    """One command that a classifier of the registry is checked on.

    Attributes:
        name: Label of the fixture, and the name of its case.
        entry: Id of the registry entry whose classifier it checks.
        tree: Name of the tree it runs on, a key of :data:`TREES`.
        args: The wiki arguments, without ``--path``.
        edit: What the stand-in changes in the command's answer, as
            :mod:`parity.standin` reads it.
        codes: The exit codes the real side may end the command with.
        notice: Whether the command reads the clock.
        shows: For a negative fixture, a difference just outside the
            entry's scope: the aspect and detail of each difference the
            harness must report. Empty for a fixture the classifier must
            excuse.

    """

    name: str
    entry: str
    tree: str
    args: tuple[str, ...]
    edit: Mapping[str, Any]
    codes: frozenset[int]
    notice: bool = True
    shows: tuple[tuple[str, str], ...] = ()


#: the fixtures of the registry: for each classifier, the differences it
#: must excuse and at least one just outside its scope that it must not
FIXTURES = (
    # D1: the same error with a library's text in other words
    Fixture(
        name='pattern text',
        entry='D1',
        tree='snapshot',
        args=('match', '('),
        edit={'stderr': [_PATTERN_TEXT, r'\1unclosed group']},
        codes=frozenset({2}),
    ),
    Fixture(
        name='pattern warning',
        entry='D1',
        tree='snapshot',
        args=('match', f'[[a]{_ABSENT}'),
        edit={'stderr': [_WARNING, '']},
        codes=frozenset({1}),
    ),
    Fixture(
        name='query text',
        entry='D1',
        tree='snapshot',
        args=('search', '"', '--raw'),
        edit={'stderr': [_QUERY_TEXT, r'\1unclosed string']},
        codes=frozenset({2}),
    ),
    Fixture(
        name='settings text',
        entry='D1',
        tree='malformed',
        args=('lint',),
        edit={'stderr': [_SETTINGS_TEXT, r'\1EOF at column 11']},
        codes=frozenset({2}),
    ),
    Fixture(
        name='changed prefix',
        entry='D1',
        tree='malformed',
        args=('update', '--check'),
        edit={'stderr': [_PREFIX, _RENAMED]},
        codes=frozenset({2}),
        shows=(('stderr', ''),),
    ),
    Fixture(
        name='naming text',
        entry='D1',
        tree='pattern',
        args=('lint',),
        edit={'stderr': [_NAMING_TEXT, r'\1unclosed group']},
        codes=frozenset({2}),
    ),
    Fixture(
        name='naming warning',
        entry='D1',
        tree='warned',
        args=('lint',),
        edit={'stderr': [_WARNING, '']},
        codes=frozenset({0, 1}),
    ),
    # D2: the same usage error or help page in another layout
    Fixture(
        name='parser message',
        entry='D2',
        tree='snapshot',
        args=('lint', '--parity'),
        edit={'stderr': [_WHOLE, f"error: unexpected argument '--parity'{_REFRAMED}"]},
        codes=frozenset({2}),
        notice=False,
    ),
    Fixture(
        name='parameter message',
        entry='D2',
        tree='snapshot',
        args=('search', _ABSENT, '--limit', '0'),
        edit={'stderr': [_FRAME, rf'\1{_REFRAMED}']},
        codes=frozenset({2}),
    ),
    Fixture(
        name='changed parameter message',
        entry='D2',
        tree='snapshot',
        args=('map', '--depth', '-1'),
        edit={'stderr': [_FRAME, f'Error: Invalid value: Reworded.{_REFRAMED}']},
        codes=frozenset({2}),
        shows=(('stderr', ''),),
    ),
    Fixture(
        name='help page',
        entry='D2',
        tree='snapshot',
        args=('lint', '--help'),
        edit={'stdout': [_WHOLE, f'Check wiki health.{_REFRAMED}']},
        codes=frozenset({0}),
        notice=False,
    ),
    # D3: a refusal of input that v1 runs
    Fixture(
        name='regex construct',
        entry='D3',
        tree='snapshot',
        args=('match', f'{_ABSENT}*+'),
        edit=_REFUSED,
        codes=frozenset({0, 1}),
    ),
    Fixture(
        name='supported construct',
        entry='D3',
        tree='snapshot',
        args=('match', f'{_ABSENT}+'),
        edit=_REFUSED,
        codes=frozenset({0, 1}),
        shows=(('stderr', ''), ('exit', ''), ('unexpected', 'exit 2')),
    ),
    Fixture(
        name='settings value',
        entry='D3',
        tree='value',
        args=('lint',),
        edit=_REFUSED_VALUE,
        codes=frozenset({0, 1}),
    ),
    Fixture(
        name='timestamp directive',
        entry='D3',
        tree='directive',
        args=('lint',),
        edit=_REFUSED,
        codes=frozenset({0, 1}),
    ),
    Fixture(
        name='named zone',
        entry='D3',
        tree='zone',
        args=('lint',),
        edit=_REFUSED,
        codes=frozenset({0, 1}),
    ),
)


def run(
    corpus: Corpus,
    base: Side,
    *,
    work: pathlib.Path,
    now: str,
    seed: int = 0,
) -> list[Check]:
    """Check the harness on ``corpus`` with one engine on both sides.

    Args:
        corpus: The wiki to run on.
        base: The engine both sides run.
        work: New folder for the snapshot and the clones. It is removed at
            the end when every check passed. Otherwise it keeps the
            snapshot and, under ``failed/<check>``, the trees of every run
            of each failed check.
        now: RFC 3339 instant for ``WIKI_NOW``.
        seed: Seed of the matrix sample.

    Returns:
        The clean check, then one check per row of :data:`CHECKS`. The
        ``notice`` row is left out for an engine that prints no notice, and
        the ``identity`` row for one that writes no identity record.

    """
    plan = runner.prepare(corpus, work=work, seed=seed)
    result = [clean(plan, base, now=now, keep=True)]
    _settle(plan, result[-1])
    for name, kind, _ in CHECKS:
        if (kind == 'notice') and not base.notice:
            continue
        if (kind == 'identity') and not base.identity:
            continue
        result.append(doctored(plan, base, name, now=now, keep=True))
        _settle(plan, result[-1])
    if all(check.passed for check in result):
        runner.discard(plan)
    return result


def clean(plan: Plan, base: Side, *, now: str, keep: bool = False) -> Check:
    """Run the whole matrix with ``base`` on both sides, under two hash seeds.

    Args:
        plan: The snapshot to run on.
        base: The engine both sides run.
        now: RFC 3339 instant for ``WIKI_NOW``.
        keep: Whether to leave the tree of every run in the plan's
            :attr:`~parity.runner.Plan.kept` folder.

    Returns:
        The check, which expects no difference.

    """
    found = runner.run(plan, _seeded(base), now=now, keep=keep)
    return Check(
        name='clean',
        expected=(),
        found=found.differences,
        report=found.report,
        streams=found.streams,
        excused=found.excused,
    )


def doctored(
    plan: Plan,
    base: Side,
    name: str,
    *,
    now: str,
    keep: bool = False,
) -> Check:
    """Run one stage with ``base`` against a doctored copy of itself.

    The doctored side changes one thing in the stage's last command.

    Args:
        plan: The snapshot to run on.
        base: The engine both sides run.
        name: Name of a row of :data:`CHECKS`.
        now: RFC 3339 instant for ``WIKI_NOW``.
        keep: Whether to leave the tree of every run in the plan's
            :attr:`~parity.runner.Plan.kept` folder.

    Returns:
        The check, which expects exactly the differences the one change
        must show as.

    Raises:
        ValueError: If the stage runs its last command twice: the doctored
            side tells its target by the command's arguments.

    """
    (kind, stage) = next(row[1:] for row in CHECKS if row[0] == name)
    (planned,) = (planned for planned in plan.stages if planned.name == stage)
    case = planned.cases[-1]
    target = runner.command(case, plan.root)
    commands = [runner.command(other, plan.root) for other in planned.cases]
    if commands.count(target) != 1:
        raise ValueError(f'Stage {stage} runs its last command more than once.')
    one, two = _seeded(base)
    # -P keeps the harness's own modules off the wrapper's import path
    argv = (
        sys.executable,
        '-P',
        f'{doctor.__file__}',
        kind,
        json.dumps(target),
        f'{plan.root / _acts_on(name)}',
        f'{len(two.argv)}',
        *two.argv,
    )
    changed = dataclasses.replace(two, name=f'doctored {name}', argv=argv)
    expected = tuple(
        Difference(stage, changed.name, case.name, aspect, detail)
        for aspect, detail in _shows_as(name, kind, case, plan.corpus)
    )
    found = runner.run(plan, [one, changed], now=now, only=[stage], keep=keep)
    return Check(
        name=f'doctored {name}',
        expected=expected,
        found=found.differences,
        report=found.report,
        streams=found.streams,
        excused=found.excused,
    )


def registry(
    corpus: Corpus,
    base: Side,
    *,
    work: pathlib.Path,
    now: str,
) -> list[Check]:
    """Check the classifiers of the registry on ``corpus`` with one engine.

    Args:
        corpus: The wiki to run on. The fixtures bring their own inputs
            and arguments, so any wiki proves them.
        base: The engine both sides run: by itself as the reference, and
            behind a stand-in. Its judge answers the classifiers.
        work: New folder for the snapshot and the clones. It is removed at
            the end when every check passed. Otherwise it keeps the
            snapshot and, under ``failed/<check>``, the trees of every run
            of each failed check.
        now: RFC 3339 instant for ``WIKI_NOW``.

    Returns:
        One check per tree of :data:`TREES`.

    """
    plan = runner.prepare(corpus, work=work)
    result = []
    for tree in TREES:
        result.append(fixtures(plan, base, tree, now=now, keep=True))
        _settle(plan, result[-1])
    if all(check.passed for check in result):
        runner.discard(plan)
    return result


def fixtures(
    plan: Plan,
    base: Side,
    tree: str,
    *,
    now: str,
    keep: bool = False,
) -> Check:
    """Run the fixtures of one tree with ``base`` against a stand-in.

    The stand-in runs ``base`` and answers each fixture's command as its
    edit says. Both sides run with no zone file on the search path of
    ``zoneinfo`` and with a ``tzdata`` package that holds one zone, so the
    interpreter loads a zone an engine that reads zone files cannot.

    Args:
        plan: The snapshot to run on.
        base: The engine both sides run.
        tree: Name of a tree of :data:`TREES`.
        now: RFC 3339 instant for ``WIKI_NOW``.
        keep: Whether to leave the tree of every run in the plan's
            :attr:`~parity.runner.Plan.kept` folder.

    Returns:
        The check, which expects each fixture excused under its entry, or
        reported with exactly the differences a negative fixture shows.

    """
    chosen = [fixture for fixture in FIXTURES if fixture.tree == tree]
    cases = tuple(
        Case(
            name=fixture.name,
            args=fixture.args,
            codes=fixture.codes,
            notice=fixture.notice,
        )
        for fixture in chosen
    )
    name = _STAGE.format(tree=tree)
    stage = Stage(name=name, cases=cases, settings=_settings(plan, tree))
    # write a zone the interpreter loads from the package, and no file to
    # find it in
    zones = plan.work / _ZONES
    zone = zones / 'tzdata' / 'zoneinfo' / 'Parity' / 'Zone'
    empty = zones / 'none'
    if not zone.exists():
        zone.parent.mkdir(parents=True)
        zone.write_bytes(_TZIF)
        empty.mkdir()
    env = {'PYTHONPATH': f'{zones}', 'PYTHONTZPATH': f'{empty}'}
    one, two = (
        dataclasses.replace(seeded, env={**seeded.env, **env})
        for seeded in _seeded(base)
    )
    edits = [
        [runner.command(case, plan.root), fixture.edit]
        for case, fixture in zip(cases, chosen)
    ]
    # -P keeps the harness's own modules off the wrapper's import path
    argv = (
        sys.executable,
        '-P',
        f'{standin.__file__}',
        json.dumps(edits),
        f'{len(two.argv)}',
        *two.argv,
    )
    other = dataclasses.replace(two, name=_STANDIN, argv=argv)
    expected = tuple(
        Difference(stage.name, other.name, fixture.name, aspect, detail)
        for fixture in chosen
        for aspect, detail in fixture.shows
    )
    registered = tuple(
        Excused(stage.name, other.name, fixture.name, fixture.entry)
        for fixture in chosen
        if not fixture.shows
    )
    staged = dataclasses.replace(plan, stages=(stage,))
    found = runner.run(staged, [one, other], now=now, keep=keep)
    return Check(
        name=stage.name,
        expected=expected,
        found=found.differences,
        report=found.report,
        streams=found.streams,
        registered=registered,
        excused=found.excused,
    )


# ------ helper functions


def _settle(plan: Plan, check: Check) -> None:
    """Remove the trees a passed check kept, and file those of a failed one."""
    if check.passed:
        shutil.rmtree(plan.kept)
        return
    target = plan.work / _FAILED / check.name
    target.parent.mkdir(exist_ok=True)
    plan.kept.rename(target)


def _settings(plan: Plan, tree: str) -> Optional[str]:
    """Return the text of the settings file ``tree`` starts with.

    ``None`` for the tree that keeps the file the snapshot holds.
    """
    path = plan.work / _SNAPSHOT / plan.corpus.root / runner.WIKI_SETTINGS
    text = path.read_text(encoding='utf-8')
    if tree == _CUT:
        return text.rstrip()[:-1]
    if not TREES[tree]:
        return None
    held = json.loads(text)
    for key, value in TREES[tree].items():
        # keep the members of a block that the tree does not set
        if isinstance(value, dict) and isinstance(held.get(key), dict):
            value = {**held[key], **value}
        held[key] = value
    return json.dumps(held, indent=2) + '\n'


def _seeded(base: Side) -> list[Side]:
    """Return ``base`` twice, each under its own fixed hash seed."""
    return [
        dataclasses.replace(
            base,
            name=f'{base.name} seed {value}',
            env={**base.env, 'PYTHONHASHSEED': value},
        )
        for value in _SEEDS
    ]


def _acts_on(name: str) -> str:
    """Return the path the doctored side of check ``name`` acts on, below the wiki root."""
    if name in ('extra', 'stray'):
        return _EXTRA
    if name in ('counts', 'cache-mode'):
        return _COUNTS
    if name in ('index', 'schema'):
        return _SEARCH
    return runner.WIKI_SETTINGS


def _shows_as(
    name: str,
    kind: str,
    case: Case,
    corpus: Corpus,
) -> list[tuple[str, str]]:
    """Return the aspect and detail of each difference a doctored command must show."""
    if kind in ('stdout', 'stderr', 'notice', 'identity'):
        return [(kind, '')]
    # the low bit flips in the one exit code the case allows
    if kind == 'exit':
        (code,) = case.codes
        return [('exit', ''), ('unexpected', f'exit {code ^ 1}')]
    if kind == 'counts':
        return [('cache', f'word_counts.json: {doctor.COUNTS_KEY}')]
    if kind == 'index':
        return [
            ('cache', f'search.db: files: {doctor.INDEX_PATH}'),
            ('cache', f'search.db: notes_fts: {doctor.INDEX_PATH}'),
        ]
    if kind == 'schema':
        return [('cache', f'search.db: schema: {doctor.SCHEMA_TABLE}')]
    target = _acts_on(name)
    # a path of the cache folder is compared by the cache rule alone
    if target.startswith(f'{cache.CACHE}/'):
        return [('cache', f'file list: {target[len(cache.CACHE) + 1 :]}')]
    relpath = runner.manifest_path(corpus, target)
    # a byte or a mode changed in place leaves the file's inode and mtime
    if kind in ('file', 'mode'):
        return [('tree', relpath)]
    # the same bytes under a new inode leave the manifest as it was
    if kind == 'rewrite':
        return [('touched', relpath)]
    result = [('tree', relpath), ('touched', relpath)]
    if not case.writes:
        result.append(('wrote', relpath))
    return result
