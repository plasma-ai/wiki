"""Test the ``parity.campaign`` module.

Drives the campaign run as a gate does, through the command line: the
three v1 sides of the oracle file run the matrix on a snapshot of a small
checkout, and the verdict is the exit code plus the result file. The scan
of the names and the refusal of an oracle file are held by themselves.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import shutil
import sys
from collections.abc import Mapping, Sequence
from typing import Optional

import pytest

from parity import __main__ as cli, campaign, oracle, runner, side

from .conftest import NOW, WORDED, _git, _needs_git

__all__ = [
    'test_campaign_command_compares_the_sides_and_reads_the_names',
    'test_difference_runs_once_more_with_the_crossed_side',
    'test_excused_command_fails_the_run_and_gets_a_cause',
    'test_scan_reads_every_name_of_the_wiki_and_no_other',
    'test_crossed_side_that_does_not_cross_the_other_two_is_refused',
]

pytestmark = _needs_git

# the properties v1's name rule reads under the default policy: a name is
# printable, and an identifier once its allowed characters are taken out
_NAME_RULE = frozenset({'isprintable', 'identifier start', 'identifier part'})
# a side that runs the command it is given, unless its first argument names
# the wiki command with an exit code: it then prints the code on both streams
# and exits with it; a v1 side's own prefix is three long
_BROKEN = """
import os, sys
codes = dict(item.split(':') for item in sys.argv[1].split(','))
if sys.argv[5] in codes:
    code = codes[sys.argv[5]]
    print(f'made by {code}', flush=True)
    print(f'made by {code}', file=sys.stderr)
    sys.exit(int(code))
os.execv(sys.argv[2], sys.argv[2:])
"""


@pytest.fixture(scope='module')
def changed() -> dict[int, tuple[str, ...]]:
    """Return the code points the oracle and the campaign side do not agree on."""
    named = oracle.load()
    reference = oracle.side(named, campaign.REFERENCE)
    compared = oracle.side(named, campaign.COMPARED)
    return campaign.changed(reference, compared)


@pytest.mark.parametrize(
    argnames='name',
    argvalues=[None, 'alike', 'apart'],
    ids=['plain names', 'a name both sides read alike', 'a name one side refuses'],
)
def test_campaign_command_compares_the_sides_and_reads_the_names(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
    changed: Mapping[int, Sequence[str]],
    name: Optional[str],
) -> None:
    """A campaign run passes on a wiki both sides and both tables agree on.

    The example wiki reads the same to the oracle and the campaign side
    over the whole matrix, and its names are plain, so the command exits 0
    with no second run and removes its snapshot. One more page fails the
    run, named with a code point the two interpreters hold different
    properties for. Where no name rule of v1 reads those properties, the
    sides agree on everything the matrix asks, and the name fails the run
    by itself. Where one interpreter calls the code point printable and
    the other does not, one side lists the page and the other refuses its
    name: the sides differ, and the crossed side, which shares the
    interpreter, lays every difference to it.
    """
    point = None
    if name is not None:
        point = _holdable(tmp_path, changed, printable=name == 'apart')
        if point is None:
            pytest.skip('the two interpreters agree on every such code point')
        corpus = _renamed(tmp_path, corpus, engine, name=chr(point))
    corpora = _corpora_file(tmp_path, corpus)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'campaign',
        *('--corpora', f'{corpora}'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    assert cli.main(argv) == int(name is not None)
    document = json.loads((out / 'example.json').read_text(encoding='utf-8'))
    # the three v1 sides ran
    sides = [campaign.REFERENCE, campaign.COMPARED, campaign.CROSSED]
    assert [record['name'] for record in document['sides']] == sides
    # the two that are compared differ only on the name one of them refuses,
    # and the crossed side repeats each difference
    differences = [
        difference
        for stage in document['stages']
        for difference in stage['differences']
    ]
    rerun = document['rerun']
    if name == 'apart':
        assert differences
        assert len(rerun['causes']) == len(differences)
        assert {row['cause'] for row in rerun['causes']} == {campaign.INTERPRETER}
    else:
        assert differences == []
        assert rerun is None
    # the tables differ exactly when the two sides name other Unicode versions
    builds = oracle.load().builds
    versions = {builds[side].unicode for side in sides[:2]}
    assert (document['points']['changed'] > 0) == (len(versions) == 2)
    # the scan read the names, and found the one that holds the code point
    assert document['names']['read'] > 0
    hits = []
    if point is not None:
        differing = {campaign.spell(point): list(changed[point])}
        hits = [{'path': f'guides/{chr(point)}.md', 'points': differing}]
    assert document['names']['hits'] == hits
    # the snapshot of a run that failed stays, and no other
    assert (work / 'example' / 'snapshot').is_dir() == (name is not None)


def test_difference_runs_once_more_with_the_crossed_side(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A difference is laid to the side the crossed side goes with, or to both.

    The campaign side is handed out as a side that fails every ``read``,
    ``match`` and ``search``, and the crossed side as one that fails every
    ``read`` the same way, every ``search`` its own way and every ``map``.
    The stages that hold those commands run once more, the last of them
    with each side on the tree each side left. A difference on a ``read``
    is laid to the interpreter, which the crossed side shares with the
    campaign side: the two did the same. One on a ``match`` is laid to a
    package: the crossed side did what the oracle did. One on a ``search``
    is laid to both: the crossed side did neither. An exit code the case
    forbids is a rule the run broke by itself and gets no cause, and what
    only the crossed side shows, on a ``map``, is listed apart. The
    command exits 1, keeps the streams of both runs and leaves the
    snapshot in place.
    """
    script = tmp_path / 'broken.py'
    script.write_text(_BROKEN, encoding='utf-8')
    fails = {
        campaign.COMPARED: 'read:7,match:7,search:7',
        campaign.CROSSED: 'read:7,search:9,map:9',
    }
    _doctor(
        monkeypatch,
        {name: (sys.executable, f'{script}', codes) for name, codes in fails.items()},
    )
    corpora = _corpora_file(tmp_path, corpus)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'campaign',
        *('--corpora', f'{corpora}'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    assert cli.main(argv) == 1
    document = json.loads((out / 'example.json').read_text(encoding='utf-8'))
    # the stages that differ ran once more, on all three sides
    reference = campaign.REFERENCE
    compared = campaign.COMPARED
    crossed = campaign.CROSSED
    rerun = document['rerun']
    assert rerun['side'] == crossed
    assert [stage['name'] for stage in rerun['stages']] == ['query', 'write', 'cross']
    assert rerun['stages'][0]['runs'] == [reference, crossed, compared]
    # each stream and exit code that differs has the cause its command stands for
    causes = {
        (row['run'], row['case'].split()[0], row['aspect'], row['cause'])
        for row in rerun['causes']
    }
    streams = {
        (command, cause)
        for _, command, aspect, cause in causes
        if aspect in ('stdout', 'stderr', 'exit')
    }
    assert streams == {
        ('read', campaign.INTERPRETER),
        ('match', campaign.PACKAGES),
        ('search', campaign.BOTH),
    }
    assert 'unexpected' not in {aspect for _, _, aspect, _ in causes}
    # a run on the tree another side left is answered by the crossed run on
    # that tree
    on = f'{compared} on {reference}'
    assert (on, 'search', 'exit', campaign.BOTH) in causes
    # what the crossed side shows alone is recorded under the runs that stand
    # for a run of the campaign side
    alone = rerun['alone']
    stood = {
        crossed,
        f'{crossed} on {reference}',
        f'{reference} on {crossed}',
        f'{crossed} on {crossed}',
    }
    assert {row['run'] for row in alone} <= stood
    exits = {row['case'].split()[0] for row in alone if row['aspect'] == 'exit'}
    assert exits == {'map'}
    # the summary names the cause of each line, none for a broken rule, and
    # lists the crossed side's own lines
    lines = capsys.readouterr().out.splitlines()
    for cause in (campaign.INTERPRETER, campaign.PACKAGES, campaign.BOTH):
        assert any(line.endswith(f': exit ({cause})') for line in lines)
    assert any(line.endswith(': unexpected: exit 7') for line in lines)
    heading = f'example: {len(alone)} differences of the crossed side alone'
    assert f'  query: {crossed}: map: exit' in lines[lines.index(heading) :]
    # the streams of both runs and the snapshot are kept
    assert list((out / 'example' / 'query').glob(f'* {campaign.COMPARED}.out'))
    kept = out / 'example' / campaign.CROSSED / 'query'
    assert list(kept.glob(f'* {campaign.CROSSED}.out'))
    assert (work / 'example' / 'snapshot' / corpus.root).is_dir()


@pytest.mark.parametrize(
    argnames=('worded', 'cause'),
    argvalues=[
        ([campaign.COMPARED], campaign.PACKAGES),
        ([campaign.COMPARED, campaign.CROSSED], campaign.INTERPRETER),
    ],
    ids=['a package words it', 'the interpreter words it'],
)
def test_excused_command_fails_the_run_and_gets_a_cause(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    worded: Sequence[str],
    cause: str,
) -> None:
    """A command the registry excuses fails a campaign run like a difference.

    The campaign side is handed out as a side that words the error text of
    ``re`` its own way, which the registry lets a second engine do (D1).
    Two builds of v1 have nothing registered between them: the command
    exits 1 with no difference, lists the command as registered, runs its
    stage once more and keeps the snapshot. The cause is a package where
    the crossed side prints what the oracle prints, and the interpreter
    where it words the text as the campaign side does.
    """
    _doctor(monkeypatch, dict.fromkeys(worded, (sys.executable, '-c', WORDED)))
    corpora = _corpora_file(tmp_path, corpus)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'campaign',
        *('--corpora', f'{corpora}'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    assert cli.main(argv) == 1
    lines = capsys.readouterr().out.splitlines()
    assert 'example: 0 differences' in lines
    assert f'  registered: query: {campaign.COMPARED}: match (: D1 ({cause})' in lines
    # the second run gives the command its cause
    document = json.loads((out / 'example.json').read_text(encoding='utf-8'))
    (row,) = document['rerun']['causes']
    assert (row['entry'], row['cause']) == ('D1', cause)
    assert document['rerun']['alone'] == []
    assert (work / 'example' / 'snapshot' / corpus.root).is_dir()


def test_scan_reads_every_name_of_the_wiki_and_no_other(
    tmp_path: pathlib.Path,
    engine: side.Side,
    changed: Mapping[int, Sequence[str]],
) -> None:
    """The scan finds a changed code point in any name of the wiki.

    A file and a folder that hold one are hits, wherever they lie and
    whatever their suffix, a symlink is read by its own name, and the wiki
    root by the name of its folder. The files of a ``.git`` folder, the
    folder a symlink points at and the folders above the root are no names
    of the wiki. An interpreter agrees with itself on every code point.
    """
    assert campaign.changed(engine, engine) == {}
    point = _holdable(tmp_path, changed)
    if point is None:
        pytest.skip('the two interpreters agree on every code point')
    char = chr(point)
    root = tmp_path / f'above-{char}' / f'wiki-{char}'
    outside = tmp_path / 'outside'
    for folder in (root / 'plain', root / f'{char}-folder', root / '.git', outside):
        folder.mkdir(parents=True)
    names = ('plain/page.md', f'plain/{char}.txt', f'{char}-folder/page.md')
    for relpath in (*names, f'.git/{char}'):
        (root / relpath).write_text('text\n', encoding='utf-8')
    (outside / f'{char}.md').write_text('text\n', encoding='utf-8')
    (root / f'link-{char}').symlink_to(outside)
    count, hits = campaign.scan(root, changed)
    # the root, two folders, three files and the symlink
    assert count == 7
    paths = sorted(['.', f'{char}-folder', f'link-{char}', f'plain/{char}.txt'])
    assert hits == [campaign.Hit(path=path, points=(point,)) for path in paths]


@pytest.mark.parametrize(
    argnames=('field', 'message'),
    argvalues=[
        ('python', 'must name the interpreter of side campaign'),
        ('unicode', 'must name the interpreter of side campaign'),
        ('constraints', 'must name the packages of side oracle'),
        ('libyaml', 'must name the packages of side oracle'),
        (None, "names no side ['crossed']"),
    ],
    ids=['python', 'unicode', 'constraints', 'libyaml', 'absent'],
)
def test_crossed_side_that_does_not_cross_the_other_two_is_refused(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    field: Optional[str],
    message: str,
) -> None:
    """A campaign run refuses a crossed side that tells the two causes apart no more.

    A difference the crossed side repeats is laid to the interpreter. With
    the oracle's interpreter, or with the campaign side's packages, the
    side is one of the two it stands between, and every cause it names
    would be the same one. The command exits 2 with a message before it
    snapshots anything.
    """
    named = oracle.load()
    builds = dict(named.builds)
    crossed = builds.pop(campaign.CROSSED)
    if field is not None:
        # the crossed side takes the field from the side it must not share it with
        other = campaign.REFERENCE
        if field in ('constraints', 'libyaml'):
            other = campaign.COMPARED
        value = getattr(builds[other], field)
        if value == getattr(crossed, field):
            value = f'{value}.other'
        builds[campaign.CROSSED] = dataclasses.replace(crossed, **{field: value})
    monkeypatch.setattr(
        oracle, 'load', lambda: dataclasses.replace(named, builds=builds)
    )
    work = tmp_path / 'work'
    argv = [
        'campaign',
        *('--corpora', f'{tmp_path / "corpora.toml"}'),
        *('--work', f'{work}', '--out', f'{tmp_path / "out"}'),
    ]
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    assert raised.value.code == 2
    assert message in capsys.readouterr().err
    assert not work.exists()


# ------ helpers


def _doctor(
    monkeypatch: pytest.MonkeyPatch, prefixes: Mapping[str, Sequence[str]]
) -> None:
    """Hand out each v1 side that ``prefixes`` names behind a command of the test's.

    Args:
        monkeypatch: The fixture of the test.
        prefixes: The command that is handed the side's own, by side name.

    """
    real = oracle.side

    def doctored(named: oracle.Oracle, name: str) -> side.Side:
        """Return the v1 side ``name``, behind its prefix where it has one."""
        found = real(named, name)
        if name not in prefixes:
            return found
        return dataclasses.replace(found, argv=(*prefixes[name], *found.argv))

    monkeypatch.setattr(oracle, 'side', doctored)


def _holdable(
    tmp_path: pathlib.Path,
    changed: Mapping[int, Sequence[str]],
    *,
    printable: Optional[bool] = None,
) -> Optional[int]:
    """Return the first changed code point the file system holds in a name.

    A file system may refuse a code point its own tables do not know.

    Args:
        tmp_path: Folder to probe the file system in.
        changed: The changed code points, from :func:`parity.campaign.changed`.
        printable: ``True`` for a code point only one interpreter calls
            printable, which v1's name rule then takes on one side alone;
            ``False`` for one that the two interpreters differ on in no
            property a name rule of v1 reads; ``None`` for any.

    Returns:
        The code point, or ``None`` when the two interpreters agree on
        every such code point the file system holds.

    """
    probe = tmp_path / 'probe'
    probe.mkdir(exist_ok=True)
    for point in sorted(changed):
        differing = set(changed[point])
        if (printable is True) and ('isprintable' not in differing):
            continue
        if (printable is False) and (differing & _NAME_RULE):
            continue
        try:
            (probe / chr(point)).write_bytes(b'')
        except (OSError, UnicodeEncodeError):
            continue
        return point
    return None


def _renamed(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
    *,
    name: str,
) -> runner.Corpus:
    """Return a copy of ``corpus`` with one more page, named ``name``, committed."""
    checkout = tmp_path / 'checkout'
    shutil.copytree(corpus.checkout, checkout, symlinks=True)
    root = checkout / corpus.root
    text = '---\ndesc: A page with a rare name.\n---\n\n# Page\n\nText.\n'
    (root / 'guides' / f'{name}.md').write_text(text, encoding='utf-8')
    # converge the wiki and commit it, as the corpus itself is built
    home = tmp_path / 'home'
    home.mkdir()
    env = side.environment(engine, home, now=NOW)
    updated = side.run(engine, ['update', '--path', f'{root}'], cwd=home, env=env)
    assert updated.code == 0, updated.stderr
    _git(checkout, 'add', '-A')
    _git(checkout, 'commit', '-q', '-m', 'page')
    return dataclasses.replace(corpus, checkout=checkout)


def _corpora_file(tmp_path: pathlib.Path, corpus: runner.Corpus) -> pathlib.Path:
    """Write a corpora file that declares ``corpus`` and return its path."""
    checkout = json.dumps(f'{corpus.checkout}')
    root = json.dumps(corpus.root)
    path = tmp_path / 'corpora.toml'
    text = f'[example]\ncheckout = {checkout}\nroot = {root}\n'
    path.write_text(text, encoding='utf-8')
    return path
