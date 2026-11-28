"""Functions for the contracts: what the built package keeps for whoever installs it.

The tool reaches a host as a package: by a pin of a release or of a commit
of the repository, under a distribution name that is looked up, with a
version that ``wiki --version`` prints, and as the ``wiki`` an installer
puts beside the interpreter. No run of two engines on one tree shows any of
that. Each check here installs the wheel, the sdist or a commit pin the way
an installer does, on a scratch host that holds no Rust, and holds the
``wiki`` it gets to the checkout the files are built from.
"""

from __future__ import annotations

import dataclasses
import email
import email.message
import io
import os
import pathlib
import shutil
import subprocess
import tarfile
import time
import tomllib
import zipfile
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from packaging.version import InvalidVersion, Version

from . import binary, oracle, side
from .side import Side

__all__ = []

#: the distribution name, which a pin and a lookup both use
NAME = 'plasma-wiki'
#: the checks of :func:`run`, in the order they run
CHECKS = (
    'name',
    'version',
    'native',
    'venv',
    'symlink',
    'space',
    'sdist',
    'upgrade',
    'lock',
)

# the v1 side whose interpreter every venv takes, and whose pins v1 is
# installed with
_SIDE = 'oracle'
# the workspace's manifest, which states the version the binary prints
_MANIFEST = 'Cargo.toml'
# the folder of the skills the binary embeds, relative to the checkout
_SKILLS = 'wiki/skills'
# the folder of each agent's skills, below the project `install --project`
# is run in
_AGENTS = ('.claude/skills', '.agents/skills')
# the tools of the caller that the scratch host holds
_TOOLS = ('uv', 'git')
# the folders after the tools' own on the scratch host's PATH
_SYSTEM_PATH = ('/usr/bin', '/bin', '/usr/sbin', '/sbin')
# what a host that builds Rust has on its PATH
_CARGO = 'cargo'
# the variables that send an installer, an interpreter or a Rust toolchain to
# a folder of the user's, each with its folder below the scratch home
_HOMES = {
    'PYTHONUSERBASE': 'userbase',
    'PYENV_ROOT': 'pyenv',
    'PIPX_HOME': 'pipx',
    'PIPX_BIN_DIR': 'pipx-bin',
    'CARGO_HOME': 'cargo',
    'RUSTUP_HOME': 'rustup',
    'UV_CACHE_DIR': 'uv-cache',
}
# the locale of the scratch host: a UTF-8 locale macOS and Linux both ship
_LANG = 'C.UTF-8'
# the installers the upgrade runs with: uv, and the pip of the venv
_INSTALLERS = ('uv', 'pip')
# the project that pins a commit: it builds nothing of its own
_PROJECT = """\
[project]
name = "pinned"
version = "0"
requires-python = ">=3.11"
dependencies = ["{name}"]

[tool.uv.sources]
{name} = {{ git = "{url}", rev = "{commit}" }}
"""
# prints the version an interpreter finds a distribution installed at
_LOOKUP = (
    'import importlib.metadata, sys\nprint(importlib.metadata.version(sys.argv[1]))'
)
# how a script that an interpreter runs starts
_SHEBANG = b'#!'
# the folder of the installers' output, below the work folder
_LOGS = 'logs'
# seconds one command of an installer may run -- one that runs this long has hung
_TIMEOUT = 3_600
# how many lines of an installer's output a finding quotes
_TAIL = 20


@dataclasses.dataclass(frozen=True)
class Package:
    """The built files of one commit, and what its checkout states of them.

    Attributes:
        wheel: The wheel, built for this host.
        sdist: The sdist.
        repo: The checkout both are built from, which a commit pin names.
        commit: The commit the checkout is at: the build id of the wheel's
            binary.
        version: The version the workspace's manifest states, in Cargo's
            spelling.
        python: What every venv is made from: a version, which uv fetches,
            or the path of an interpreter.
        v1: The commit v1's wheel is built at.
        v1_version: What ``wiki --version`` prints at that commit.
        v1_constraints: The file that pins every package v1 is installed
            with.

    """

    wheel: pathlib.Path
    sdist: pathlib.Path
    repo: pathlib.Path
    commit: str
    version: str
    python: str
    v1: str
    v1_version: str
    v1_constraints: pathlib.Path


@dataclasses.dataclass(frozen=True, order=True)
class Finding:
    """One way a check does not hold.

    Attributes:
        check: A name of :data:`CHECKS`.
        detail: What the check found, as one line.

    """

    check: str
    detail: str


@dataclasses.dataclass(frozen=True)
class Result:
    """What one run of the checks found.

    Attributes:
        findings: Every way a check does not hold.
        report: The run as JSON data: the files, what the checkout states
            of them, and each check with its findings and its seconds.
        logs: What each command of an installer printed, by a file name
            that carries the check.

    """

    findings: tuple[Finding, ...]
    report: Mapping[str, Any]
    logs: Mapping[str, bytes]


def load(wheel: pathlib.Path, sdist: pathlib.Path) -> Package:
    """Return the package of this checkout, with ``wheel`` and ``sdist`` as its files.

    What the files must be is read from the checkout and not from them: the
    version its manifest states and the commit it is at. So a wheel of
    another commit, or under another version, fails the checks. v1 is the
    oracle commit, under the oracle side's interpreter and pins.
    """
    named = oracle.load()
    build = named.builds[_SIDE]
    manifest = (named.repo / _MANIFEST).read_text(encoding='utf-8')
    return Package(
        wheel=wheel.resolve(),
        sdist=sdist.resolve(),
        repo=named.repo,
        commit=oracle._git(['rev-parse', 'HEAD'], cwd=named.repo),
        version=tomllib.loads(manifest)['workspace']['package']['version'],
        python=build.python,
        v1=named.commit,
        v1_version=named.version,
        v1_constraints=build.constraints,
    )


def validate(work: pathlib.Path) -> None:
    """Refuse a work folder that :func:`run` cannot use; make nothing.

    Raises:
        ValueError: If ``work`` lies inside a repository (the build of
            v1's wheel asks git which of its source files the repository
            ignores, and an installer reads the settings of a project above
            the folder it runs in), or if ``work`` exists.

    """
    for parent in work.resolve().parents:
        if os.path.lexists(parent / '.git'):
            raise ValueError(
                f'Work folder {work} lies inside the repository {parent};'
                f' choose one outside it.'
            )
    if os.path.lexists(work):
        raise ValueError(f'Work folder {work} exists; the harness makes it new.')


def run(
    package: Package,
    *,
    work: pathlib.Path,
    checks: Sequence[str] = CHECKS,
) -> Result:
    """Run the checks on ``package`` and return what they found.

    Every installer and every ``wiki`` runs on a scratch host below
    ``work``: a home of its own, with every folder an installer or a
    toolchain keeps for a user below it, and a ``PATH`` of the system's
    folders and the two tools the checks call. Nothing of the caller's
    environment is inherited, and the host holds no ``cargo``: a build
    there is the build of a host with no Rust.

    Args:
        package: The built files, from :func:`load`.
        work: New folder for the scratch host, the venvs and the projects.
        checks: The checks to run, of :data:`CHECKS`.

    Returns:
        The findings, the report and the installers' output.

    Raises:
        ValueError: If :func:`validate` refuses ``work``, if the wheel or
            the sdist is no file, if the caller's ``PATH`` holds no uv or
            no git, or if a system folder holds a ``cargo``.
        RuntimeError: If a step that is no part of a check fails: a venv
            that cannot be made, or v1's wheel that cannot be built.

    """
    validate(work)
    for path in (package.wheel, package.sdist):
        if not path.is_file():
            raise ValueError(f'{path} is no file.')
    tools = {name: shutil.which(name) for name in _TOOLS}
    missing = [name for name, path in tools.items() if path is None]
    if missing:
        raise ValueError(f'The checks need {missing[0]} on PATH.')
    cargo = shutil.which(_CARGO, path=os.pathsep.join(_SYSTEM_PATH))
    if cargo is not None:
        raise ValueError(
            f'The scratch host must hold no Rust, and {cargo} is on its PATH.'
        )
    # make the scratch host: its tools by links, so nothing else that lies
    # in a tool's folder is on the PATH
    home = work / 'home'
    links = work / 'bin'
    for folder in (home, links):
        folder.mkdir(parents=True)
    for name, path in tools.items():
        (links / name).symlink_to(f'{path}')
    (home / '.gitconfig').write_bytes(b'')
    env = {
        'PATH': os.pathsep.join([f'{links}', *_SYSTEM_PATH]),
        'HOME': f'{home}',
        'GIT_CONFIG_GLOBAL': f'{home / ".gitconfig"}',
        'GIT_CONFIG_NOSYSTEM': '1',
        'LANG': _LANG,
    }
    env.update({name: f'{home / folder}' for name, folder in _HOMES.items()})
    skills = _skills(package)
    # run each check, and time it
    table = {
        'name': _check_name,
        'version': _check_version,
        'native': _check_native,
        'venv': _check_venv,
        'symlink': _check_symlink,
        'space': _check_space,
        'sdist': _check_sdist,
        'upgrade': _check_upgrade,
        'lock': _check_lock,
    }
    findings = []
    rows = []
    for name in checks:
        host = _Host(
            package=package,
            work=work,
            env=env,
            uv=f'{links / "uv"}',
            skills=skills,
            check=name,
        )
        started = time.monotonic()
        try:
            problems = table[name](host)
        except _Failed as e:
            problems = [f'{e}']
        row = {
            'name': name,
            'findings': problems,
            'seconds': round(time.monotonic() - started, 1),
        }
        rows.append(row)
        findings.extend(Finding(name, problem) for problem in problems)
    report = {
        'wheel': package.wheel.name,
        'sdist': package.sdist.name,
        'commit': package.commit,
        'version': package.version,
        'python': package.python,
        'v1': {'commit': package.v1, 'version': package.v1_version},
        'checks': rows,
    }
    # a run whose checks called no installer has no output to hand back
    logs = {}
    if (work / _LOGS).is_dir():
        logs = _tree(work / _LOGS)
    return Result(findings=tuple(findings), report=report, logs=logs)


# ------ helper functions


class _Failed(Exception):
    """A command of an installer that failed inside a check: the check's finding."""


@dataclasses.dataclass(frozen=True)
class _Host:
    """The scratch host of a run, as one check sees it.

    Attributes:
        package: The built files under check.
        work: The folder of the run.
        env: The full environment of every command; nothing else is
            inherited.
        uv: The uv of the host.
        skills: The bytes of every skill file the commit holds, by its path
            below the skills folder.
        check: The check that is running, which names its folder below
            ``work`` and the folder of its installers' output.

    """

    package: Package
    work: pathlib.Path
    env: Mapping[str, str]
    uv: str
    skills: Mapping[str, bytes]
    check: str


def _check_name(host: _Host) -> list[str]:
    """Check the distribution name, in the wheel and as an interpreter finds it."""
    problems = []
    metadata = _metadata(host.package.wheel)
    if metadata['Name'] != NAME:
        problems.append(
            f'the wheel names its distribution {metadata["Name"]!r}, not {NAME!r}'
        )
    found = _lookup(host, _installed(host))
    if found != metadata['Version']:
        problems.append(
            f'the interpreter finds {NAME} at {found or "no version"}, and the'
            f' wheel holds {metadata["Version"]}'
        )
    return problems


def _check_version(host: _Host) -> list[str]:
    """Check the bare version: Cargo's spelling, which reads as the wheel's.

    The manifest states the version as Cargo spells it, and the binary
    prints that text. An installer records the wheel under the spelling
    Python normalizes it to, so the two are held equal as versions, not as
    text.
    """
    wiki = _wiki(host, _installed(host) / 'bin' / 'wiki', build=host.package.commit)
    shown = side.run(wiki, ['--version'], cwd=host.work, env=host.env, notice=False)
    problems = _bare(host, shown)
    text = shown.stdout.decode('utf-8', 'replace').removesuffix('\n')
    recorded = _metadata(host.package.wheel)['Version']
    try:
        same = Version(text) == Version(recorded)
    except InvalidVersion:
        same = False
    if not same:
        problems.append(
            f'--version prints {text!r}, which is not the version {recorded}'
            f' of the wheel'
        )
    return problems


def _check_native(host: _Host) -> list[str]:
    """Check that the installed ``wiki`` is the wheel's binary, and no script."""
    held = _binary(host.package.wheel)
    if held is None:
        return ['the wheel holds no wiki among its scripts']
    problems = []
    installed = _installed(host) / 'bin' / 'wiki'
    if installed.is_symlink() or (installed.read_bytes() != held):
        problems.append(f'{installed} is not the file the wheel holds')
    if held.startswith(_SHEBANG):
        problems.append("the wheel's wiki is a script that an interpreter runs")
    return problems


def _check_venv(host: _Host) -> list[str]:
    """Check that the ``wiki`` of a venv works."""
    wiki = _wiki(host, _installed(host) / 'bin' / 'wiki', build=host.package.commit)
    return _works(host, wiki, 'project')


def _check_symlink(host: _Host) -> list[str]:
    """Check that the ``wiki`` of a venv works through a link in another folder."""
    link = host.work / host.check / 'elsewhere' / 'wiki'
    link.parent.mkdir(parents=True)
    link.symlink_to(_installed(host) / 'bin' / 'wiki')
    wiki = _wiki(host, link, build=host.package.commit)
    return _works(host, wiki, 'project')


def _check_space(host: _Host) -> list[str]:
    """Check that the ``wiki`` of a venv whose path holds a space works."""
    venv = _installed(host, 'a venv')
    wiki = _wiki(host, venv / 'bin' / 'wiki', build=host.package.commit)
    return _works(host, wiki, 'project')


def _check_sdist(host: _Host) -> list[str]:
    """Check that an install from the sdist builds a working ``wiki``.

    The build runs outside every checkout, so its binary has no build id.
    """
    venv = _venv(host, host.work / host.check / 'venv')
    _install(host, 'install', venv, host.package.sdist)
    wiki = _wiki(host, venv / 'bin' / 'wiki', build='')
    return _works(host, wiki, 'project')


def _check_upgrade(host: _Host) -> list[str]:
    """Check an upgrade from v1's wheel and a downgrade back, with each installer.

    v1 is a Python package with a console script, and the wheel holds one
    binary under the same name. Each of the three installs must leave the
    ``wiki`` of the version it installed.
    """
    package = host.package
    previous = _v1_wheel(host)
    problems = []
    for installer in _INSTALLERS:
        pip = installer == 'pip'
        venv = _venv(host, host.work / host.check / installer / 'venv', seed=pip)
        v1 = Side(name='v1', argv=(f'{venv / "bin" / "wiki"}',))
        wiki = _wiki(host, venv / 'bin' / 'wiki', build=package.commit)
        found = []
        # install v1, the wheel over it, and v1 over the wheel
        for step in ('v1', 'wheel', 'v1-again'):
            label = f'{installer}/{step}'
            if step == 'wheel':
                _install(host, label, venv, package.wheel, pip=pip)
                found += _works(host, wiki, f'{installer}/project')
                continue
            constraints = package.v1_constraints
            _install(host, label, venv, previous, pip=pip, constraints=constraints)
            shown = side.run(
                v1,
                ['--version'],
                cwd=host.work,
                env=host.env,
                notice=False,
            )
            expected = (f'{package.v1_version}\n'.encode(), 0)
            if (shown.stdout, shown.code) != expected:
                found.append(
                    f'after {step}, --version prints {shown.stdout!r} and exits'
                    f' {shown.code}'
                )
        problems += [f'{installer}: {problem}' for problem in found]
    return problems


def _check_lock(host: _Host) -> list[str]:
    """Check that a project which pins the commit locks it and builds it.

    The project names the distribution as its one dependency, from the
    repository of the checkout at the checkout's commit. ``uv lock`` must
    record that commit as the source, and ``uv sync --locked`` must build
    it on the scratch host, whose binary then carries the commit as its
    build id.
    """
    package = host.package
    project = host.work / host.check / 'pinned'
    project.mkdir(parents=True)
    text = _PROJECT.format(name=NAME, url=package.repo.as_uri(), commit=package.commit)
    (project / 'pyproject.toml').write_text(text, encoding='utf-8')
    python = ['--python', package.python]
    _tool(host, 'lock', [host.uv, 'lock', *python], cwd=project)
    lock = tomllib.loads((project / 'uv.lock').read_text(encoding='utf-8'))
    sources = [
        row['source'].get('git', '') for row in lock['package'] if row['name'] == NAME
    ]
    problems = []
    # the fragment of a git source is the commit the lock resolved the pin to
    if [source.rpartition('#')[2] for source in sources] != [package.commit]:
        problems.append(
            f'the lock takes {NAME} from {sources!r}, not from the commit'
            f' {package.commit}'
        )
    _tool(host, 'sync', [host.uv, 'sync', '--locked', *python], cwd=project)
    wiki = _wiki(host, project / '.venv' / 'bin' / 'wiki', build=package.commit)
    return problems + _works(host, wiki, 'project')


def _works(host: _Host, wiki: Side, label: str) -> list[str]:
    """Return each way the ``wiki`` of the side ``wiki`` is no working one.

    A working ``wiki`` prints the bare version, proves on both commands
    that it is the build the check expects, and writes the skills the
    commit holds into the project it is run in, for each agent.
    """
    project = host.work / host.check / label
    project.mkdir(parents=True)
    try:
        shown = side.run(wiki, ['--version'], cwd=project, env=host.env, notice=False)
        made = side.run(
            wiki,
            ['install', '--project'],
            cwd=project,
            env=host.env,
            notice=False,
        )
    except OSError as e:
        return [f'wiki does not start: {e}']
    problems = _bare(host, shown)
    if made.code != 0:
        error = made.stderr.decode('utf-8', 'replace').strip()
        problems.append(f'install --project exits {made.code}: {error!r}')
    if not (shown.identity and made.identity):
        problems.append(f'a command left no identity record of {dict(wiki.identity)!r}')
    for agent in _AGENTS:
        written = _tree(project / agent)
        differs = sorted(
            name
            for name in set(host.skills) | set(written)
            if host.skills.get(name) != written.get(name)
        )
        if differs:
            problems.append(
                f'install --project leaves {agent} unlike {_SKILLS} at {differs[0]!r}'
            )
    return problems


def _bare(host: _Host, shown: side.Outcome) -> list[str]:
    """Return each way ``shown``, a run of ``--version``, is not the bare version."""
    expected = (f'{host.package.version}\n'.encode(), b'', 0)
    if (shown.stdout, shown.stderr, shown.code) == expected:
        return []
    return [
        f'--version prints {shown.stdout!r} and {shown.stderr!r} on stderr and'
        f' exits {shown.code}, not the bare {host.package.version!r}'
    ]


def _wiki(host: _Host, path: pathlib.Path, *, build: str) -> Side:
    """Return the ``wiki`` at ``path`` as a side that must prove the build id ``build``."""
    identity = {
        'engine': binary.ENGINE,
        'version': host.package.version,
        'build': build,
    }
    return Side(name=host.check, argv=(f'{path}',), identity=identity)


def _installed(host: _Host, name: str = 'venv') -> pathlib.Path:
    """Make a venv of the check's own, install the wheel into it, and return it.

    Raises:
        RuntimeError: If uv cannot make the venv.
        _Failed: If the installer fails.

    """
    venv = _venv(host, host.work / host.check / name)
    _install(host, 'install', venv, host.package.wheel)
    return venv


def _venv(host: _Host, folder: pathlib.Path, *, seed: bool = False) -> pathlib.Path:
    """Make a venv at ``folder``, with a pip of its own if ``seed`` is set.

    Raises:
        RuntimeError: If uv cannot make it.

    """
    cmd = [host.uv, 'venv', '--python', host.package.python]
    if seed:
        cmd.append('--seed')
    _setup(host, [*cmd, f'{folder}'])
    return folder


def _install(
    host: _Host,
    label: str,
    venv: pathlib.Path,
    target: pathlib.Path,
    *,
    pip: bool = False,
    constraints: Optional[pathlib.Path] = None,
) -> None:
    """Install the file ``target`` into ``venv``, with uv or with the venv's pip.

    Raises:
        _Failed: If the installer fails.

    """
    python = f'{venv / "bin" / "python"}'
    if pip:
        cmd = [python, '-m', 'pip', 'install']
    else:
        cmd = [host.uv, 'pip', 'install', '--python', python]
    if constraints is not None:
        cmd += ['-c', f'{constraints}']
    _tool(host, label, [*cmd, f'{target}'])


def _v1_wheel(host: _Host) -> pathlib.Path:
    """Build v1's wheel from its commit as git holds it, and return the file.

    Raises:
        RuntimeError: If git cannot hand out the commit, or if the build
            fails.

    """
    package = host.package
    folder = host.work / host.check / 'v1'
    source = folder / 'source'
    with _archive(package.repo, package.v1) as archive:
        archive.extractall(source, filter='data')
    cmd = [host.uv, 'build', '--wheel', '--python', package.python]
    _setup(host, [*cmd, '--out-dir', f'{folder / "dist"}', f'{source}'])
    (wheel,) = (folder / 'dist').glob('*.whl')
    return wheel


def _tool(
    host: _Host,
    label: str,
    cmd: Sequence[str],
    *,
    cwd: Optional[pathlib.Path] = None,
) -> None:
    """Run one command of an installer on the scratch host and keep what it printed.

    What it printed is written below ``work`` at once, so a run that ends in
    an error leaves it behind.

    Raises:
        _Failed: If the command fails.

    """
    done = subprocess.run(
        cmd,
        cwd=cwd or host.work,
        env=host.env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=_TIMEOUT,
    )
    log = host.work / _LOGS / host.check / f'{label}.txt'
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(done.stdout)
    if done.returncode != 0:
        tail = done.stdout.decode('utf-8', 'replace').splitlines()[-_TAIL:]
        raise _Failed(f'{label} failed (exit {done.returncode}): {tail!r}')


def _setup(host: _Host, cmd: Sequence[str]) -> None:
    """Run one uv command that a check needs before it can start.

    Raises:
        RuntimeError: If uv fails.

    """
    done = subprocess.run(
        cmd,
        cwd=host.work,
        env=host.env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=_TIMEOUT,
    )
    if done.returncode != 0:
        tail = done.stdout.decode('utf-8', 'replace').splitlines()[-_TAIL:]
        raise RuntimeError(f'uv failed (exit {done.returncode}): {tail!r}')


def _lookup(host: _Host, venv: pathlib.Path) -> str:
    """Return the version the interpreter of ``venv`` finds the distribution at.

    Empty when it finds none.
    """
    done = subprocess.run(
        [f'{venv / "bin" / "python"}', '-c', _LOOKUP, NAME],
        cwd=host.work,
        env=host.env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        return ''
    return done.stdout.strip()


def _metadata(wheel: pathlib.Path) -> email.message.Message:
    """Return the fields of the metadata file of ``wheel``."""
    with zipfile.ZipFile(wheel) as archive:
        (name,) = (
            item for item in archive.namelist() if item.endswith('.dist-info/METADATA')
        )
        return email.message_from_bytes(archive.read(name))


def _binary(wheel: pathlib.Path) -> Optional[bytes]:
    """Return the ``wiki`` that ``wheel`` holds among its scripts, if it holds one."""
    with zipfile.ZipFile(wheel) as archive:
        names = [
            item for item in archive.namelist() if item.endswith('.data/scripts/wiki')
        ]
        if not names:
            return None
        return archive.read(names[0])


def _skills(package: Package) -> dict[str, bytes]:
    """Return the bytes of every skill file the commit holds, by relative path.

    The files are read from the commit and not from the folder: a file that
    is edited or added there since is in no build of the commit.

    Raises:
        RuntimeError: If git cannot hand out the commit.

    """
    files = {}
    with _archive(package.repo, package.commit, _SKILLS) as archive:
        for member in archive.getmembers():
            held = archive.extractfile(member)
            # a folder of the archive holds no bytes
            if held is not None:
                name = pathlib.PurePosixPath(member.name).relative_to(_SKILLS)
                files[name.as_posix()] = held.read()
    return files


def _archive(repo: pathlib.Path, commit: str, *paths: str) -> tarfile.TarFile:
    """Return the files of ``commit`` in ``repo``, or those below ``paths``, as an archive.

    Raises:
        RuntimeError: If git fails.

    """
    # NOTE: the archive is bytes, and the package's git helper returns text
    done = subprocess.run(
        ['git', '-C', f'{repo}', 'archive', '--format=tar', commit, '--', *paths],
        capture_output=True,
    )
    if done.returncode != 0:
        error = done.stderr.decode('utf-8', 'replace').strip()
        raise RuntimeError(f'git archive failed (exit {done.returncode}): {error!r}')
    return tarfile.open(fileobj=io.BytesIO(done.stdout))


def _tree(folder: pathlib.Path) -> dict[str, bytes]:
    """Return the bytes of every file below ``folder``, by relative path."""
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob('*'))
        if path.is_file()
    }
