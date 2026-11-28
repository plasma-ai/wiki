"""Test the ``parity.contracts`` module.

The checks run on real wheels in the ``contracts`` job, which builds them
first. This module carries what that job cannot show: that each check
reports the wheel that breaks its contract. The wheel here is a stand-in
that holds a script where the real one holds a binary, and it is installed
by the real installer into real venvs.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

import pytest

from parity import __main__ as cli, contracts, oracle, side

from .conftest import VERSION, _git, _needs_git, _wheel, _workspace

__all__ = [
    'test_wheel_that_keeps_the_contracts_shows_only_that_its_wiki_is_a_script',
    'test_wheel_that_breaks_a_contract_is_reported_by_its_checks',
    'test_run_that_would_check_nothing_is_refused',
    'test_contracts_command_holds_a_wheel_to_the_checkout',
]

pytestmark = _needs_git

# the checks that install the wheel and nothing else: the others build from
# source or fetch v1's packages
_CHECKS = ('name', 'version', 'native', 'venv', 'symlink', 'space')
# the one finding of a stand-in that keeps every contract it can
_SCRIPT = contracts.Finding(
    'native',
    "the wheel's wiki is a script that an interpreter runs",
)


def _package(
    folder: pathlib.Path,
    engine: side.Side,
    **doctored: Any,
) -> contracts.Package:
    """Return a committed checkout and its stand-in wheel as a package.

    The venvs are made from the oracle side's interpreter, so no check
    fetches one. The sdist is a file no check of this module reads.
    """
    repo = _workspace(folder)
    sdist = folder / 'sdist.tar.gz'
    sdist.write_bytes(b'')
    named = oracle.load()
    return contracts.Package(
        wheel=_wheel(folder / 'dist', repo, **doctored),
        sdist=sdist,
        repo=repo,
        commit=_git(repo, 'rev-parse', 'HEAD'),
        version=VERSION,
        python=engine.argv[0],
        v1=named.commit,
        v1_version=named.version,
        v1_constraints=named.builds['oracle'].constraints,
    )


def test_wheel_that_keeps_the_contracts_shows_only_that_its_wiki_is_a_script(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A wheel is held to its checkout, and a script in it is told from a binary.

    The stand-in is found under the distribution name, prints the
    manifest's version in Cargo's spelling, proves the commit of the
    checkout on every command, and writes the skills the commit holds: in a
    venv, through a link in another folder, and in a venv whose path holds
    a space. So every check passes but the one that holds the installed
    ``wiki`` to be a binary, and the report names each check that ran.
    """
    package = _package(tmp_path, engine)
    result = contracts.run(package, work=tmp_path / 'work', checks=_CHECKS)
    assert result.findings == (_SCRIPT,)
    assert [row['name'] for row in result.report['checks']] == list(_CHECKS)
    assert (result.report['commit'], result.report['version']) == (
        package.commit,
        VERSION,
    )
    # what the installer printed is kept with the check that first installed
    assert sorted(result.logs) == ['name/install.txt', 'space/install.txt']


@pytest.mark.parametrize(
    argnames=('doctored', 'checks', 'detail'),
    argvalues=[
        (
            {'name': 'plasma-wiki-fork'},
            {'name'},
            "names its distribution 'plasma-wiki-fork'",
        ),
        (
            {'prints': '2.0.0.dev1'},
            {'version', 'venv', 'symlink', 'space'},
            f'not the bare {VERSION!r}',
        ),
        (
            {'version': '2.0.0.dev2'},
            {'version'},
            'is not the version 2.0.0.dev2 of the wheel',
        ),
        (
            {'build': '0' * 40},
            {'venv', 'symlink', 'space'},
            'left no identity record',
        ),
        (
            {'skills': {'wiki/SKILL.md': 'Another skill.\n'}},
            {'venv', 'symlink', 'space'},
            "unlike wiki/skills at 'wiki/SKILL.md'",
        ),
        (
            {'skills': {'wiki/SKILL.md': 'The skill.\n', 'wiki/more.md': 'More.\n'}},
            {'venv', 'symlink', 'space'},
            "unlike wiki/skills at 'wiki/more.md'",
        ),
    ],
    ids=['name', 'spelling', 'version', 'commit', 'skill', 'added skill'],
)
def test_wheel_that_breaks_a_contract_is_reported_by_its_checks(
    tmp_path: pathlib.Path,
    engine: side.Side,
    doctored: dict[str, Any],
    checks: set[str],
    detail: str,
) -> None:
    """Each contract a wheel breaks is a finding of the checks that hold it.

    A wheel under another distribution name; a ``--version`` that prints the
    spelling an installer normalizes the version to, where the manifest
    states Cargo's; a wheel that states another version than its binary
    prints; a binary built at another commit; a skill whose text is not the
    commit's; and a skill file the commit does not hold. Every other check
    still passes, so a finding names the contract that broke.
    """
    package = _package(tmp_path, engine, **doctored)
    result = contracts.run(package, work=tmp_path / 'work', checks=_CHECKS)
    found = set(result.findings) - {_SCRIPT}
    assert {finding.check for finding in found} == checks
    assert {finding.check for finding in found if detail in finding.detail} == checks


@pytest.mark.parametrize(
    argnames=('broken', 'message'),
    argvalues=[
        ('exists', 'exists'),
        ('repository', 'inside the repository'),
        ('wiki', 'which holds .wiki/settings.json'),
        ('wheel', 'is no file'),
        ('uv', 'need uv on PATH'),
    ],
)
def test_run_that_would_check_nothing_is_refused(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: side.Side,
    broken: str,
    message: str,
) -> None:
    """A run that cannot check the wheel is refused before it makes anything.

    A work folder that exists, or that lies inside a repository or below a
    wiki, where an installer and a build would read files that are not the
    run's own; a wheel that is no file; and a caller with no uv to install
    with.
    """
    package = _package(tmp_path / 'package', engine)
    work = tmp_path / 'outer' / 'work'
    if broken == 'exists':
        work.mkdir(parents=True)
    elif broken == 'repository':
        (work.parent / '.git').mkdir(parents=True)
    elif broken == 'wiki':
        settings = work.parent / '.wiki' / 'settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text('{}', encoding='utf-8')
    elif broken == 'wheel':
        package.wheel.unlink()
    elif broken == 'uv':
        monkeypatch.setenv('PATH', '/usr/bin:/bin')
    held = sorted(tmp_path.rglob('*'))
    with pytest.raises(ValueError, match=message):
        contracts.run(package, work=work, checks=_CHECKS)
    assert sorted(tmp_path.rglob('*')) == held


def test_contracts_command_holds_a_wheel_to_the_checkout(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    engine: side.Side,
) -> None:
    """The command reads what a wheel must be from the checkout, and exits by it.

    A wheel of the checkout's commit passes its checks: the command exits
    0, prints each check, writes the result file and removes its work
    folder. After one more commit the same wheel is a build of something
    else: the command exits 1, prints the finding, and keeps the work
    folder and what the installer printed. An edit of a source file that is
    not committed is printed and recorded, because a wheel built over it
    would carry the commit all the same. And a check named twice is refused
    before anything is installed.
    """
    # a checkout, as the oracle file's own, with venvs of the oracle side's
    # interpreter so no check fetches one
    repo = _workspace(tmp_path)
    named = oracle.load()
    build = dataclasses.replace(named.builds['oracle'], python=engine.argv[0])
    held = dataclasses.replace(
        named, repo=repo, builds={**named.builds, 'oracle': build}
    )
    monkeypatch.setattr(oracle, 'load', lambda: held)
    wheel = _wheel(tmp_path / 'dist', repo)
    sdist = tmp_path / 'sdist.tar.gz'
    sdist.write_bytes(b'')
    work = tmp_path / 'work'
    out = tmp_path / 'out'
    argv = ['contracts', '--wheel', f'{wheel}', '--sdist', f'{sdist}']
    argv += ['--check', 'name', '--check', 'venv']
    # the wheel is a build of the commit
    assert cli.main([*argv, '--work', f'{work}', '--out', f'{out}']) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == 'contracts: 2 checks: 0 failed'
    document = json.loads((out / 'contracts.json').read_text(encoding='utf-8'))
    assert [row['name'] for row in document['checks']] == ['name', 'venv']
    assert (document['commit'], document['dirty']) == (
        _git(repo, 'rev-parse', 'HEAD'),
        [],
    )
    assert not (work / 'contracts').exists()
    # the checkout moves on and a source file is edited: the wheel is no
    # build of the commit, and a new one would hide the edit
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'later')
    edited = 'crates/wiki-cli/src/lib.rs'
    (repo / edited).write_text('// the crate, edited\n', encoding='utf-8')
    out = tmp_path / 'out-later'
    assert cli.main([*argv, '--work', f'{work}', '--out', f'{out}']) == 1
    lines = capsys.readouterr().out.splitlines()
    assert f'contracts: the checkout differs from HEAD at {edited}' in lines
    assert any(line.startswith('contracts: venv: FAILED') for line in lines)
    assert lines[-2] == 'contracts: 2 checks: 1 failed'
    assert lines[-1].startswith('  venv: a command left no identity record')
    document = json.loads((out / 'contracts.json').read_text(encoding='utf-8'))
    assert document['dirty'] == [edited]
    assert (out / 'contracts' / 'name' / 'install.txt').is_file()
    assert (work / 'contracts' / 'wheel' / 'venv').is_dir()
    # a check named twice
    out = tmp_path / 'out-twice'
    with pytest.raises(SystemExit) as raised:
        cli.main([*argv, '--check', 'venv', '--work', f'{work}', '--out', f'{out}'])
    assert raised.value.code == 2
    assert "--check names 'venv' twice" in capsys.readouterr().err
    assert not out.exists()
