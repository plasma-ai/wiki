"""Test the ``parity.fixtures`` module.

The runs of the families are covered through the command line; this module
carries what the generator owes a run: the same checkouts every time, a base
that is converged, a refusal of what it cannot write, and stages of a
family's own. It also records what v1 does on a name that is no valid UTF-8,
beside what the registry says of it, on the file systems that keep such a
name.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys

import pytest

from parity import fixtures, manifest, runner, side

from .conftest import NOW, _needs_bytes, _needs_git

__all__ = [
    'test_families_are_written_alike_every_time',
    'test_base_of_every_family_is_converged',
    'test_family_the_file_system_cannot_hold_is_left_out_or_refused',
    'test_folder_the_generator_cannot_use_is_refused',
    'test_family_runs_its_own_stages_after_the_matrix',
    'test_v1_takes_a_managed_name_that_is_not_utf8_as_text',
    'test_v1_leaves_a_name_that_is_not_utf8_to_an_exclude_pattern',
    'test_v1_skips_a_file_that_is_no_page_under_a_name_that_is_not_utf8',
]

# a side that prints one variable of the control family's second stage
_ECHO = (
    sys.executable,
    '-c',
    'import os, sys; sys.stdout.write(os.environ.get("NO_COLOR", "unset"))',
)
# the text the Python engine writes for the bytes of each refused name
_PAGE = 'refused/caf\\udce9'
_FOLDER = 'refused/dir\\udcff'
# a umask that takes every permission from the group and from others
_PRIVATE = 0o077


@_needs_git
def test_families_are_written_alike_every_time(tmp_path: pathlib.Path) -> None:
    """Two runs of the generator give the same trees and the same commits.

    Every family has the same bytes, modes and modification times at each
    path, and a repository family the same commit, whatever umask the
    generator runs under.
    """
    first = fixtures.generate(tmp_path / 'one')
    previous = os.umask(_PRIVATE)
    try:
        second = fixtures.generate(tmp_path / 'two')
    finally:
        os.umask(previous)
    assert [corpus.label for corpus in first] == [corpus.label for corpus in second]
    assert len(first) > 1
    for one, two in zip(first, second):
        skip = frozenset({'.git'})
        held = [
            {
                relpath: (*entry.content, entry.mtime_ns)
                for relpath, entry in manifest.scan(corpus.checkout, skip=skip).items()
            }
            for corpus in (one, two)
        ]
        assert held[0] == held[1], one.label
        assert f'{one.root}/.wiki/settings.json' in held[0]
        commits = [_head(corpus.checkout) for corpus in (one, two)]
        assert commits[0] == commits[1], one.label
    # a family outside any repository has no commit
    assert [corpus.label for corpus in first if not _head(corpus.checkout)] == ['plain']


@_needs_git
def test_base_of_every_family_is_converged(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """v1 finds nothing to update and nothing to report on the base wiki.

    Every family adds its own files to this wiki, so whatever a command
    reports or writes on a family comes from those files.
    """
    family = fixtures.Family(
        name='base',
        tree=lambda: fixtures._base('extra', 'A folder of the base.'),
    )
    checkout = tmp_path / 'base'
    fixtures._write(family, checkout)
    home = tmp_path / 'home'
    home.mkdir()
    env = side.environment(engine, home, now=NOW)
    expected = {'update': b'Nothing to update.\n', 'lint': b'No issues found.\n'}
    for args in (['update', '--check'], ['lint'], ['update']):
        argv = [*args, '--path', f'{checkout / "wiki"}']
        done = side.run(engine, argv, cwd=home, env=env)
        assert done.code == 0, done.stderr
        assert done.stdout == expected[args[0]]


def test_family_the_file_system_cannot_hold_is_left_out_or_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A family is written only where the file system keeps its names.

    Unnamed, the families the file system cannot hold are left out, and so
    are the trees a second engine refuses. A family that is named and
    cannot be written is refused before any folder is made.
    """
    lacks = fixtures.lacking(tmp_path)
    families = fixtures.families()
    expected = [
        family.name
        for family in families
        if (not family.entry) and not (family.needs & lacks)
    ]
    written = fixtures.generate(tmp_path / 'all')
    assert [corpus.label for corpus in written] == expected
    assert {'frontmatter', 'control', 'plain'} <= set(expected)
    # Linux keeps every name, so nothing is left out there
    if sys.platform == 'linux':
        assert not lacks
    unwritable = [family.name for family in families if family.needs & lacks]
    for name in unwritable:
        with pytest.raises(ValueError, match='needs a file system'):
            fixtures.generate(tmp_path / 'new' / 'named', names=[name])
        assert not (tmp_path / 'new').exists()
    with pytest.raises(ValueError, match='No fixture family is named'):
        fixtures.generate(tmp_path / 'new' / 'named', names=['nmaes'])
    assert not (tmp_path / 'new').exists()


def test_folder_the_generator_cannot_use_is_refused(tmp_path: pathlib.Path) -> None:
    """A folder that exists, or lies in a repository or a wiki, is refused.

    A family with no repository of its own would be read as part of an
    enclosing one, and every family as nested in an enclosing wiki.
    """
    (tmp_path / 'taken').mkdir()
    with pytest.raises(ValueError, match='exists'):
        fixtures.generate(tmp_path / 'taken')
    (tmp_path / 'repository' / '.git').mkdir(parents=True)
    with pytest.raises(ValueError, match='inside the repository'):
        fixtures.generate(tmp_path / 'repository' / 'fixtures')
    (tmp_path / 'wiki' / '.wiki').mkdir(parents=True)
    (tmp_path / 'wiki' / '.wiki' / 'settings.json').write_text('{}\n')
    with pytest.raises(ValueError, match='lies below'):
        fixtures.generate(tmp_path / 'wiki' / 'fixtures')


@_needs_git
def test_family_runs_its_own_stages_after_the_matrix(tmp_path: pathlib.Path) -> None:
    """A family's stages follow the matrix's, each under its own variables.

    The control family runs its commands twice, the second time under
    the variables that change what a terminal library writes to a pipe.
    A family that runs alone has no stage of the matrix.
    """
    control, nested = fixtures.generate(tmp_path / 'trees', names=['control', 'nested'])
    plan = runner.prepare(control, work=tmp_path / 'control')
    names = [stage.name for stage in plan.stages]
    assert names[-2:] == ['fixture', 'fixture forced']
    assert set(names[:-2]) == {'pristine', 'query', 'write', 'full', 'cross'}
    # a side that prints one variable shows what each stage ran under
    echo = side.Side(name='echo', argv=_ECHO, notice=False)
    result = runner.run(plan, [echo], now=NOW, only=['fixture', 'fixture forced'])
    printed = {
        stage['name']: {
            case['runs']['echo']['stdout']['sha256'] for case in stage['cases']
        }
        for stage in result.report['stages']
    }
    assert printed == {
        'fixture': {hashlib.sha256(b'unset').hexdigest()},
        'fixture forced': {hashlib.sha256(b'1').hexdigest()},
    }
    plan = runner.prepare(nested, work=tmp_path / 'nested')
    assert [stage.name for stage in plan.stages] == ['fixture']


@_needs_git
@_needs_bytes
@pytest.mark.parametrize(
    argnames=('family', 'page', 'name', 'kind', 'code'),
    argvalues=[
        ('refused-page', b'refused/caf\xe9.md', _PAGE, 'invalid_page_name', 0),
        (
            'refused-folder',
            b'refused/dir\xff/inner.md',
            f'{_FOLDER}/inner',
            'invalid_folder_name',
            2,
        ),
    ],
    ids=['page', 'folder'],
)
def test_v1_takes_a_managed_name_that_is_not_utf8_as_text(
    tmp_path: pathlib.Path,
    engine: side.Side,
    family: str,
    page: bytes,
    name: str,
    kind: str,
    code: int,
) -> None:
    """v1 drops the entry's row, still writes its name, and then rejects it.

    This is what the registry's entry on such names says of v1, and why a
    second engine refuses the tree. ``update`` adopts the page under a
    ``name`` that spells the byte as an escape, and gives the entry no
    row; ``lint`` then reports that block as one no YAML reader takes, in
    a JSON document that is itself no valid UTF-8. A folder of such a name
    goes further: its index cannot be written, so ``update`` ends in an
    error with the pages before it already written. A search fails on
    either tree.
    """
    (corpus,) = fixtures.generate(tmp_path / 'trees', names=[family])
    root = corpus.checkout / corpus.root
    home = tmp_path / 'home'
    home.mkdir()
    env = side.environment(engine, home, now=NOW)

    def wiki(*args: str) -> side.Outcome:
        return side.run(engine, [*args, '--path', f'{root}'], cwd=home, env=env)

    # the entry gets no row, and its page a name that spells the byte out
    done = wiki('update')
    assert done.code == code, done.stderr
    path = os.fsencode(root) + b'/' + page
    with open(path, 'rb') as handle:
        block = handle.read().split(b'\n---\n')[0]
    assert f'name: "{name}"'.encode('ascii') in block.splitlines()
    index = (root / 'refused' / '_index.md').read_bytes()
    assert b'refused/sound' in index
    assert b'caf' not in index
    assert b'dir' not in index
    # a folder of such a name ends the update before its index is written
    if code:
        assert b"codec can't encode character '\\udcff'" in done.stderr
        assert not os.path.lexists(os.path.join(os.path.dirname(path), b'_index.md'))
    # the name the engine wrote is one no YAML reader takes; the report names
    # the path by its bytes, so the document itself is no valid UTF-8
    listed = wiki('lint', '--json').stdout
    with pytest.raises(UnicodeDecodeError):
        listed.decode('utf-8')
    report = json.loads(listed.decode('utf-8', 'surrogateescape'))
    reasons = {issue['kind']: issue['text'] for issue in report['issues']}
    assert 'invalid Unicode character escape code' in reasons['invalid_yaml']
    assert kind in reasons
    found = wiki('search', 'refuseword')
    assert found.code == 2
    assert b"codec can't encode character" in found.stderr


@_needs_git
@_needs_bytes
def test_v1_leaves_a_name_that_is_not_utf8_to_an_exclude_pattern(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """v1 runs a tree whose one such name an exclude pattern keeps out.

    The pattern is matched against the name as text, so the page is never
    written, listed or reported, and the wiki updates and lints as one
    without it. A second engine refuses this tree too: its matcher works
    on text, which the name is not.
    """
    (corpus,) = fixtures.generate(tmp_path / 'trees', names=['refused-pattern'])
    root = corpus.checkout / corpus.root
    home = tmp_path / 'home'
    home.mkdir()
    env = side.environment(engine, home, now=NOW)
    path = os.fsencode(root) + b'/refused/raw-\xe9.md'
    with open(path, 'rb') as handle:
        before = handle.read()
    for args in (['update'], ['lint'], ['search', 'sound']):
        done = side.run(engine, [*args, '--path', f'{root}'], cwd=home, env=env)
        assert done.code == 0, done.stderr
        assert b'raw-' not in done.stdout + done.stderr
    with open(path, 'rb') as handle:
        assert handle.read() == before
    assert b'raw-' not in (root / 'refused' / '_index.md').read_bytes()


@_needs_git
@_needs_bytes
def test_v1_skips_a_file_that_is_no_page_under_a_name_that_is_not_utf8(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """v1 leaves such a file out, writes nothing, and reports it on every lint.

    The registry's entry covers every managed name, and what it says of v1
    holds for a page and a folder only. A file that is no page gets no
    ``name``, so there is no block for a YAML reader to reject: ``update``
    names the skip, exits 0 and changes no file, a second one finds
    nothing to do, and a search runs. ``lint`` reports the name each time,
    in a JSON document that holds the byte itself and so is no valid
    UTF-8.
    """
    (corpus,) = fixtures.generate(tmp_path / 'trees', names=['refused-file'])
    root = corpus.checkout / corpus.root
    home = tmp_path / 'home'
    home.mkdir()
    env = side.environment(engine, home, now=NOW)

    def wiki(*args: str) -> side.Outcome:
        return side.run(engine, [*args, '--path', f'{root}'], cwd=home, env=env)

    def held() -> dict[str, tuple]:
        skip = frozenset({'.git', f'{corpus.root}/.wiki/cache'})
        entries = manifest.scan(corpus.checkout, skip=skip)
        return {relpath: entry.content for relpath, entry in entries.items()}

    # the update names the skip and changes no file
    before = held()
    assert f'{corpus.root}/refused/data\udce9.bin' in before
    done = wiki('update')
    assert done.code == 0, done.stderr
    assert b'Skipped 1 invalid name' in done.stdout + done.stderr
    assert held() == before
    again = wiki('update', '--check')
    assert again.code == 0, again.stderr
    assert b'Nothing to update.' in again.stdout
    found = wiki('search', 'sound')
    assert found.code == 0, found.stderr
    # the report names the path by its bytes, and no block as one no reader takes
    listed = wiki('lint', '--json')
    assert listed.code == 1
    with pytest.raises(UnicodeDecodeError):
        listed.stdout.decode('utf-8')
    report = json.loads(listed.stdout.decode('utf-8', 'surrogateescape'))
    assert [issue['kind'] for issue in report['issues']] == ['invalid_page_name']


# ------ helper functions


def _head(checkout: pathlib.Path) -> str:
    """Return the commit of the repository at ``checkout``; empty if it is none."""
    if not (checkout / '.git').is_dir():
        return ''
    result = subprocess.run(
        ['git', '-C', f'{checkout}', 'rev-parse', 'HEAD'],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
