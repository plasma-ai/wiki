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

from .conftest import NOW, _git, _needs_git

__all__ = [
    'test_campaign_command_compares_the_sides_and_reads_the_names',
    'test_difference_runs_once_more_on_the_crossed_side',
    'test_scan_reads_every_name_of_the_wiki_and_no_other',
    'test_crossed_side_that_does_not_cross_the_other_two_is_refused',
]

pytestmark = _needs_git

# a side that runs the command it is given, unless the wiki command is one of
# those its first argument names; a v1 side's own prefix is three long
_BROKEN = """
import os, sys
if sys.argv[5] in sys.argv[1].split(','):
    sys.exit(7)
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
    argnames='named',
    argvalues=[False, True],
    ids=['plain names', 'a changed code point'],
)
def test_campaign_command_compares_the_sides_and_reads_the_names(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
    changed: Mapping[int, Sequence[str]],
    named: bool,
) -> None:
    """A campaign run passes on a wiki both sides and both tables agree on.

    The example wiki reads the same to the oracle and the campaign side
    over the whole matrix, and its names are plain, so the command exits 0
    with no second run and removes its snapshot. One more page, named with
    a code point the two interpreters hold different properties for, fails
    the run by itself: the two sides may agree on everything the matrix
    asks of the name today, and the result file still lists it with the
    properties that differ.
    """
    point = _holdable(tmp_path, changed)
    if named:
        if point is None:
            pytest.skip('the two interpreters agree on every code point')
        corpus = _renamed(tmp_path, corpus, engine, name=chr(point))
    corpora = _corpora_file(tmp_path, corpus)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'campaign',
        *('--corpora', f'{corpora}'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    assert cli.main(argv) == int(named)
    document = json.loads((out / 'example.json').read_text(encoding='utf-8'))
    # the three v1 sides ran, and the two that are compared agree
    sides = [campaign.REFERENCE, campaign.COMPARED, campaign.CROSSED]
    assert [record['name'] for record in document['sides']] == sides
    assert [stage['differences'] for stage in document['stages']] == [[]] * 5
    assert document['rerun'] is None
    # the tables differ exactly when the two sides name other Unicode versions
    builds = oracle.load().builds
    versions = {builds[name].unicode for name in sides[:2]}
    assert (document['points']['changed'] > 0) == (len(versions) == 2)
    assert document['points']['changed'] == len(changed)
    # the scan read the names, and found the one that holds the code point
    assert document['names']['read'] > 0
    hits = []
    if named and (point is not None):
        differing = {campaign.spell(point): list(changed[point])}
        hits = [{'path': f'guides/{chr(point)}.md', 'points': differing}]
    assert document['names']['hits'] == hits
    # the snapshot of a run that failed stays, and no other
    assert (work / 'example' / 'snapshot').is_dir() == named


def test_difference_runs_once_more_on_the_crossed_side(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the crossed side repeats is the interpreter's, the rest a package's.

    The campaign side is handed out as a side that fails every ``read``
    and every ``match``, and the crossed side as one that fails every
    ``read``. Both commands run in the ``query`` stage alone, so that
    stage runs once more: a difference on a ``read`` is laid to the
    interpreter, which the crossed side shares, and one on a ``match`` to
    a package. An exit code the case forbids is a rule the run broke by
    itself and gets no cause. The command exits 1, keeps the streams of
    both runs and leaves the snapshot in place.
    """
    script = tmp_path / 'broken.py'
    script.write_text(_BROKEN, encoding='utf-8')
    fails = {campaign.COMPARED: 'read,match', campaign.CROSSED: 'read'}
    real = oracle.side

    def broken(named: oracle.Oracle, name: str) -> side.Side:
        """Return the v1 side ``name``, failing the commands the test names."""
        found = real(named, name)
        if name not in fails:
            return found
        argv = (sys.executable, f'{script}', fails[name], *found.argv)
        return dataclasses.replace(found, argv=argv)

    monkeypatch.setattr(oracle, 'side', broken)
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
    # the stage that differs ran once more, on the crossed side
    rerun = document['rerun']
    assert rerun['side'] == campaign.CROSSED
    (stage,) = rerun['stages']
    assert stage['name'] == 'query'
    assert stage['runs'] == [campaign.REFERENCE, campaign.CROSSED]
    # each difference between the sides has the cause its command stands for
    causes = {(row['case'].split()[0], row['cause']) for row in rerun['causes']}
    assert causes == {('read', campaign.INTERPRETER), ('match', campaign.PACKAGES)}
    assert {row['run'] for row in rerun['causes']} == {campaign.COMPARED}
    aspects = {row['aspect'] for row in rerun['causes']}
    assert 'exit' in aspects
    assert 'unexpected' not in aspects
    # the summary names the cause of each line, and none for a broken rule
    lines = capsys.readouterr().out.splitlines()
    assert any(line.endswith(f': exit ({campaign.INTERPRETER})') for line in lines)
    assert any(line.endswith(f': exit ({campaign.PACKAGES})') for line in lines)
    assert any(line.endswith(': unexpected: exit 7') for line in lines)
    # the streams of both runs and the snapshot are kept
    assert list((out / 'example' / 'query').glob(f'* {campaign.COMPARED}.out'))
    kept = out / 'example' / campaign.CROSSED / 'query'
    assert list(kept.glob(f'* {campaign.CROSSED}.out'))
    assert (work / 'example' / 'snapshot' / corpus.root).is_dir()


def test_scan_reads_every_name_of_the_wiki_and_no_other(
    tmp_path: pathlib.Path,
    engine: side.Side,
    changed: Mapping[int, Sequence[str]],
) -> None:
    """The scan finds a changed code point in any name below the wiki root.

    A file and a folder that hold one are hits, wherever they lie and
    whatever their suffix, and a symlink is read by its own name. The
    files of a ``.git`` folder and the folder a symlink points at are no
    names of the wiki. An interpreter agrees with itself on every code
    point.
    """
    assert campaign.changed(engine, engine) == {}
    point = _holdable(tmp_path, changed)
    if point is None:
        pytest.skip('the two interpreters agree on every code point')
    char = chr(point)
    root = tmp_path / 'wiki'
    outside = tmp_path / 'outside'
    for folder in (root / 'plain', root / f'{char}-folder', root / '.git', outside):
        folder.mkdir(parents=True)
    names = ('plain/page.md', f'plain/{char}.txt', f'{char}-folder/page.md')
    for relpath in (*names, f'.git/{char}', f'../outside/{char}.md'):
        (root / relpath).write_text('text\n', encoding='utf-8')
    (root / f'link-{char}').symlink_to(outside)
    count, hits = campaign.scan(root, changed)
    # two folders, three files and the symlink
    assert count == 6
    paths = sorted([f'{char}-folder', f'link-{char}', f'plain/{char}.txt'])
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


def _holdable(
    tmp_path: pathlib.Path,
    changed: Mapping[int, Sequence[str]],
) -> Optional[int]:
    """Return the first changed code point the file system holds in a name.

    A file system may refuse a code point its own tables do not know, as
    APFS does. ``None`` when the two interpreters agree on every code point
    the file system holds.
    """
    probe = tmp_path / 'probe'
    probe.mkdir(exist_ok=True)
    for point in sorted(changed):
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
