"""Behavioral tests for tracked repo config that must stay coherent.

The release version ships as a hand-maintained literal in four places --
the package ``__init__``, the build metadata, and both plugin manifests
-- and nothing computes one from another, so a bump that misses one
silently publishes mismatched artifacts; the pin turns that drift into a
test failure.

The committed ``.gitignore`` ships to every clone, so an over-broad
pattern there silently eats tracked config; the ignore test probes a
copy in a throwaway repo with ``git check-ignore``, hermetic from any
host excludes.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tomllib

import wiki

__all__ = [
    'test_version_strings_agree',
    'test_package_data_ships_in_build',
    'test_gitignore_spares_tracked_lookalike_paths',
]

_REPO_ROOT = pathlib.Path(__file__).parent.parent


def _check_ignore(cwd: pathlib.Path, path: str) -> bool:
    """Return whether git ignores ``path`` (exit 0 = ignored, 1 = not)."""
    result = subprocess.run(
        ['git', 'check-ignore', '-q', path],
        cwd=cwd,
        capture_output=True,
    )
    return result.returncode == 0


def test_version_strings_agree() -> None:
    """The four version literals ship in lockstep.

    ``wiki.__version__`` and the pyproject version are the CI-parsed
    pair, and each plugin manifest repeats the literal for its
    marketplace listing.
    """
    # the build metadata must carry the package literal
    pyproject = tomllib.loads(
        (_REPO_ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    )
    assert pyproject['project']['version'] == wiki.__version__, (
        'pyproject.toml [project] version must match wiki.__version__ '
        '(both are hand-maintained release literals)'
    )
    # each plugin manifest repeats the release version for its marketplace
    for folder in ('.claude-plugin', '.codex-plugin'):
        manifest = json.loads(
            (_REPO_ROOT / folder / 'plugin.json').read_text(encoding='utf-8')
        )
        assert manifest['version'] == wiki.__version__, (
            f'{folder}/plugin.json version must match wiki.__version__ '
            '(plugin releases ship the same literal as the package)'
        )


def test_package_data_ships_in_build() -> None:
    """The runtime-consumed package data is present and listed for the build.

    ``config`` seeds Obsidian config from ``_assets/obsidian``, ``_merge``
    dispatches to ``_assets/git/merge_index.sh``, and ``install`` copies
    ``skills/`` -- all resolved beside the modules at runtime, so a build
    that omits them fails at install time, long after the change that
    caused it. Poetry ships these non-Python trees only because
    ``[tool.poetry] include`` lists them, so each must be present in the
    package *and* listed in the build config.
    """
    pyproject = tomllib.loads(
        (_REPO_ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    )
    included = {entry['path'] for entry in pyproject['tool']['poetry']['include']}
    for tree, probe in (
        ('wiki/_assets', 'git/merge_index.sh'),
        ('wiki/skills', 'wiki/SKILL.md'),
    ):
        assert (_REPO_ROOT / tree / probe).is_file(), (
            f'{tree}/{probe} is consumed at runtime and must ship in the package'
        )
        assert f'{tree}/**/*' in included, (
            f'[tool.poetry] include must list {tree}/**/* so built artifacts ship it'
        )


def test_gitignore_spares_tracked_lookalike_paths(tmp_path: pathlib.Path) -> None:
    """The ignore patterns spare the tracked paths they nearly name.

    The agent-config ignores (``.claude``, ``.codex``) sit one character
    away from the tracked plugin manifests, and the ``.obsidian/`` ignore
    sits beside the packaged seed assets under ``wiki/_assets/obsidian``
    -- an over-broadened pattern would silently drop those files from
    every clone, surfacing only at the next fresh checkout.
    """
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(
        ['git', 'init', '-q', '-b', 'main'],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    # neutralize any global excludes file so only the copied .gitignore decides
    subprocess.run(
        ['git', 'config', 'core.excludesFile', os.devnull],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    shutil.copy(_REPO_ROOT / '.gitignore', repo / '.gitignore')
    # tracked manifests, seed assets, and wiki config stay committable
    assert not _check_ignore(repo, '.claude-plugin/plugin.json')
    assert not _check_ignore(repo, '.codex-plugin/plugin.json')
    assert not _check_ignore(repo, 'wiki/_assets/obsidian/community-plugins.json')
    assert not _check_ignore(repo, 'examples/hello/.wiki/settings.json')
    # genuine junk stays hidden (the copy carries the real rules), including
    # the deliberate library-convention uv.lock ignore and the Obsidian
    # sidecar a `wiki config` run leaves inside the committed sample wiki
    assert _check_ignore(repo, 'uv.lock')
    assert _check_ignore(repo, '__pycache__/mod.pyc')
    assert _check_ignore(repo, 'examples/hello/.obsidian/app.json')
