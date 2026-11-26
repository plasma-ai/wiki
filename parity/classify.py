"""Functions for classifying the registered differences.

A classifier reads one command as two sides ran it and says whether its
registry entry explains everything the two did differently. The first run of
a stage is the reference: the classifiers take it for v1, and ask the judge
of its side what v1's own interpreter makes of the command's input.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Optional

from .compare import Difference, Record, Run
from .matrix import Stage

__all__ = []

# the aspects that compare a later run with the first
_PAIRED = ('stdout', 'stderr', 'exit', 'tree', 'touched', 'cache')
# the label of an error line, and the wiki-written prefixes a library's text
# follows
_ERROR = b'Error: '
_NAMING = b'Error: naming.pattern in .wiki/settings.json is not a valid regex: '
_MALFORMED = b'Error: Malformed JSON in .wiki/settings.json: '
_QUERY = re.compile(
    rb'^Error: Invalid FTS5 query (?:\'(?:[^\'\\\n]|\\.)*\'|"(?:[^"\\\n]|\\.)*"): ',
    re.MULTILINE,
)
# a warning of `re` as Python prints it, with the source line below it
_WARNING = rb'^[^\n]*:\d+: FutureWarning: (?:%s)\n(?:  [^\n]*\n)?'
# a usage error as v1 prints it, and the message of one wiki's own code raises
_USAGE = re.compile(
    rb"\AUsage: [^\n]*\nTry '[^\n]*' for help\.\n\nError: (?P<message>.*)\n\Z",
    re.DOTALL,
)
_PARAMETER = b'Invalid value: '
# the lines that frame a usage error, in either layout
_FRAMING = (b'Usage:', b"Try '", b'For more information')
# a refusal: one error line
_REFUSAL = re.compile(rb'\AError: [^\n]+\n\Z')
# the options of `match` that take a value
_VALUED = ('--path', '--field')
# the option of `init` that takes the settings of the new wiki
_SETTINGS = '--settings'


class Judge:
    """The reference side's interpreter, asked what it makes of an input."""

    def __init__(self: Judge, argv: Sequence[str], *, env: Mapping[str, str]) -> None:
        """Initialize the judge.

        Args:
            argv: The whole command of the side's judge.
            env: The environment the side's commands run under.

        """
        self._argv = tuple(argv)
        self._env = dict(env)
        self._answers: dict[str, dict[str, Any]] = {}

    def ask(self: Judge, **request: Any) -> dict[str, Any]:
        """Return the judge's answer to one request, asked once."""
        line = json.dumps(request, sort_keys=True)
        if line not in self._answers:
            result = subprocess.run(
                self._argv,
                input=f'{line}\n',
                env=self._env,
                capture_output=True,
                encoding='utf-8',
                check=True,
            )
            self._answers[line] = json.loads(result.stdout)
        return self._answers[line]


@dataclasses.dataclass(frozen=True)
class Command:
    """One command as two sides ran it.

    Attributes:
        args: The wiki arguments, without ``--path``.
        first: What the command did on the reference side.
        second: What it did on the other side.
        aspects: The aspects the two differ in.

    """

    args: tuple[str, ...]
    first: Record
    second: Record
    aspects: frozenset[str]


@dataclasses.dataclass(frozen=True, order=True)
class Excused:
    """One command whose differences a registry entry explains.

    Attributes:
        stage: Name of the stage.
        run: Name of the run that differs from the stage's first run.
        case: Name of the case.
        entry: Id of the registry entry.

    """

    stage: str
    run: str
    case: str
    entry: str


def library_text(command: Command, judge: Optional[Judge]) -> bool:
    """Return whether the sides differ only in text a library wrote (D1).

    The warnings of ``re`` are v1's alone. Behind them, the two stderr
    streams may part once: at the end of a wiki-written prefix, with a
    library's text after it on both sides.
    """
    if command.aspects != {'stderr'}:
        return False
    first = command.first.outcome.stderr
    second = command.second.outcome.stderr
    pattern = _pattern(command.args, judge)
    settings = _settings(command.first.settings, judge)
    naming = settings['pattern']
    # drop the warnings v1's own patterns give
    texts = [
        text
        for verdict in (pattern, naming)
        if verdict is not None
        for text in verdict['warnings']
    ]
    if texts:
        named = b'|'.join(re.escape(text.encode('utf-8')) for text in texts)
        first = re.sub(_WARNING % named, b'', first, flags=re.MULTILINE)
        if first == second:
            return True
    # excuse the text of an invalid pattern, where v1's is what its own `re`
    # raises with
    for prefix, verdict in ((_ERROR, pattern), (_NAMING, naming)):
        if (verdict is None) or (verdict['error'] is None):
            continue
        tails = _tails(first, second, prefix)
        if (tails is not None) and (tails[0] == verdict['error'].encode('utf-8')):
            return True
    # excuse the text of malformed settings, where v1's own `json` raises
    if settings['error'] is not None:
        if _tails(first, second, _MALFORMED) is not None:
            return True
    # excuse the text of SQLite behind an invalid query
    for opened in _QUERY.finditer(first):
        if _tails(first, second, opened[0]) is not None:
            return True
    return False


def usage(command: Command, judge: Optional[Judge]) -> bool:
    """Return whether the sides differ only in the parser's layout (D2).

    A help page keeps its exit code and its stream. A usage error keeps
    exit code 2, its stream and an empty stdout; a message wiki's own code
    raised keeps its line, and one the parser detected is the parser's.
    """
    first, second = command.first.outcome, command.second.outcome
    if command.aspects == {'stdout'}:
        shown = ('--help' in command.args) and (first.code == second.code == 0)
        return shown and first.stdout.startswith(b'Usage: ') and bool(second.stdout)
    if (command.aspects != {'stderr'}) or not (first.code == second.code == 2):
        return False
    if first.stdout or second.stdout:
        return False
    error = _USAGE.match(first.stderr)
    if error is None:
        return False
    # leave the parser's own message to the second side's parser
    if not error['message'].startswith(_PARAMETER):
        return bool(second.stderr)
    lines = [
        line
        for line in second.stderr.splitlines(keepends=True)
        if line.strip() and not line.startswith(_FRAMING)
    ]
    return b''.join(lines) == _ERROR + error['message'] + b'\n'


def refusal(command: Command, judge: Optional[Judge]) -> bool:
    """Return whether the second side refused input outside the supported set (D3).

    v1 ran the command. The second side wrote nothing, printed one error
    line and exited 2, and v1's own interpreter calls an input of the
    command unsupported. A settings value is refused as malformed JSON, and
    a settings field in a line that names the field.
    """
    first, second = command.first.outcome, command.second.outcome
    if (first.code not in (0, 1)) or (second.code != 2):
        return False
    if second.stdout or command.second.touched or command.second.changes:
        return False
    if _REFUSAL.match(second.stderr) is None:
        return False
    settings = _settings(command.first.settings, judge)
    if settings['values']:
        return second.stderr.startswith(_MALFORMED)
    # hold the refusal of a settings field to the field's name: v1 reads the
    # field in some commands only, so any other error would pass for one
    if any(field.encode('utf-8') in second.stderr for field in settings['fields']):
        return True
    pattern = _pattern(command.args, judge)
    return bool(pattern and pattern['outside'])


def outside(
    args: Sequence[str],
    files: Iterable[bytes],
    judge: Optional[Judge],
) -> list[str]:
    """Return why an input of a command lies outside the supported set.

    The inputs are those of D3: the settings files the command may read,
    the settings an ``init`` command is given, and the pattern of a
    ``match`` command. A side with no judge leaves every input unjudged.

    Args:
        args: The wiki arguments of the command.
        files: The bytes of each settings file.
        judge: The judge of the reference side, or ``None``.

    Returns:
        Each reason, once; none for inputs inside the set.

    """
    inputs = list(files)
    given = _given(args)
    if given is not None:
        inputs.append(given)
    reasons: dict[str, None] = {}
    for data in inputs:
        reasons.update(dict.fromkeys(_settings(data, judge)['outside']))
    pattern = _pattern(args, judge)
    if pattern is not None:
        reasons.update(
            dict.fromkeys(f'pattern: {reason}' for reason in pattern['outside'])
        )
    return list(reasons)


#: the classifier of each registry entry that has one, by the entry's id
CLASSIFIERS: Mapping[str, Callable[[Command, Optional[Judge]], bool]] = {
    'D1': library_text,
    'D2': usage,
    'D3': refusal,
}


def explain(command: Command, judge: Optional[Judge]) -> Optional[str]:
    """Return the id of the registry entry that explains ``command``, if any.

    Args:
        command: A command whose two sides differ.
        judge: The judge of the reference side; ``None`` for a side that
            has none, which no classifier that needs one then excuses.

    """
    for id, classifier in CLASSIFIERS.items():
        if classifier(command, judge):
            return id
    return None


def apply(
    stage: Stage,
    runs: Sequence[Run],
    found: Sequence[Difference],
    *,
    judge: Optional[Judge],
) -> tuple[list[Difference], list[Excused]]:
    """Take the differences a registry entry explains out of ``found``.

    A classifier judges a command whole: every difference between a later
    run and the first run on that command is excused together, or none is.
    A difference before the first command, after the last, or in a rule a
    run breaks by itself is never excused, except the exit code of a
    refusal its entry allows.

    Args:
        stage: The stage the runs covered.
        runs: The runs; the first is the reference.
        found: Every difference of the stage.
        judge: The judge of the reference side, or ``None``.

    Returns:
        The differences no entry explains, and the commands one does.

    """
    position = {case.name: index for index, case in enumerate(stage.cases)}
    later = {run.name: run for run in runs[1:]}
    # gather what each later run's command differs in
    aspects: dict[tuple[str, str], set[str]] = {}
    for difference in found:
        paired = (difference.run in later) and (difference.aspect in _PAIRED)
        if paired and (difference.case in position):
            key = (difference.run, difference.case)
            aspects.setdefault(key, set()).add(difference.aspect)
    # ask the classifiers about each such command
    entries = {}
    for (run, case), differing in aspects.items():
        index = position[case]
        command = Command(
            args=stage.cases[index].args,
            first=runs[0].records[index],
            second=later[run].records[index],
            aspects=frozenset(differing),
        )
        entry = explain(command, judge)
        if entry is not None:
            entries[(run, case)] = entry
    # keep what no entry explains
    kept = []
    for difference in found:
        differing = aspects.get((difference.run, difference.case), set())
        covered = difference.aspect in _PAIRED
        # cover the exit code a refusal ends with, which its case may forbid
        if (difference.aspect == 'unexpected') and ('exit' in differing):
            covered = True
        if covered and ((difference.run, difference.case) in entries):
            continue
        kept.append(difference)
    excused = [
        Excused(stage.name, run, case, entry) for (run, case), entry in entries.items()
    ]
    return kept, excused


# ------ helper functions


def _pattern(args: Sequence[str], judge: Optional[Judge]) -> Optional[dict[str, Any]]:
    """Return the judge's answer for the pattern of a ``match`` command line.

    The pattern is the command's first positional argument, and
    ``--ignore-case`` compiles it with the flag ``i``.
    """
    if (judge is None) or (tuple(args[:1]) != ('match',)):
        return None
    flags = ''
    positionals: list[str] = []
    rest = list(args[1:])
    while rest:
        item = rest.pop(0)
        if item == '--':
            positionals.extend(rest)
            break
        if item in _VALUED:
            rest[:1] = []
        elif item.startswith('--'):
            if item == '--ignore-case':
                flags = 'i'
        elif item.startswith('-') and (item != '-'):
            # skip the value of -f: the rest of its cluster, or the next argument
            letters, valued, value = item[1:].partition('f')
            if valued and not value:
                rest[:1] = []
            if 'i' in letters:
                flags = 'i'
        else:
            positionals.append(item)
    if not positionals:
        return None
    return judge.ask(kind='regex', pattern=positionals[0], flags=flags)


def _settings(data: Optional[bytes], judge: Optional[Judge]) -> dict[str, Any]:
    """Return the judge's answer for the bytes of a settings file, if there is one."""
    if (judge is None) or (data is None):
        return {
            'error': None,
            'values': [],
            'fields': [],
            'outside': [],
            'pattern': None,
        }
    return judge.ask(kind='settings', data=base64.b64encode(data).decode('ascii'))


def _given(args: Sequence[str]) -> Optional[bytes]:
    """Return the settings an ``init`` command line gives the new wiki, as a file's bytes.

    The option's value is the next argument or follows ``=``, and the last
    ``--settings`` of a command line counts.
    """
    if tuple(args[:1]) != ('init',):
        return None
    given = None
    rest = list(args[1:])
    while rest:
        item = rest.pop(0)
        if item == '--':
            break
        name, joined, value = item.partition('=')
        if name != _SETTINGS:
            continue
        if not joined:
            value = rest.pop(0) if rest else ''
        given = value.encode('utf-8', 'surrogateescape')
    return given


def _tails(first: bytes, second: bytes, prefix: bytes) -> Optional[tuple[bytes, bytes]]:
    """Return what each stream holds behind its last line that opens with ``prefix``.

    ``None`` unless the streams are equal up to the end of that prefix and
    each then ends with one line of text.
    """
    start = first.rfind(b'\n' + prefix) + 1
    if (start == 0) and not first.startswith(prefix):
        return None
    cut = start + len(prefix)
    if first[:cut] != second[:cut]:
        return None
    tails = (first[cut:], second[cut:])
    # hold each text to its line: a line behind it is no library's text
    if any((len(tail) < 2) or (tail.find(b'\n') != len(tail) - 1) for tail in tails):
        return None
    return tails[0][:-1], tails[1][:-1]
