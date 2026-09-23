"""Behavioral tests for tracked repo config that must stay coherent.

The release version ships as a hand-maintained literal in five places --
the package ``__init__``, the build metadata, both plugin manifests, and
the cruft context -- and nothing computes one from another, so a bump
that misses one silently publishes mismatched artifacts; the pin turns
that drift into a test failure.

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
from wiki.core import _obsidian

__all__ = [
    'test_version_strings_agree',
    'test_package_data_ships_in_build',
    'test_gitignore_spares_tracked_lookalike_paths',
    'test_bundled_plugin_manifest_agrees_with_its_install',
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
    """The five version literals ship in lockstep.

    ``wiki.__version__`` and the pyproject version are the CI-parsed
    pair, each plugin manifest repeats the literal for its marketplace
    listing, and the cruft context carries it for template renders. The
    tag-time build gate checks all five together, so a literal this test
    leaves unguarded fails only once the tag is already pushed.
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
    # the cruft context carries the literal into template renders
    cruft = json.loads((_REPO_ROOT / '.cruft.json').read_text(encoding='utf-8'))
    recorded = cruft['context']['cookiecutter']['project']['version']
    assert recorded == wiki.__version__, (
        '.cruft.json project version must match wiki.__version__ '
        '(the tag-time build gate checks it, so drift fails after the push)'
    )


def test_package_data_ships_in_build() -> None:
    """The runtime-consumed package data is present and listed for the build.

    ``config`` seeds Obsidian config from ``_assets/obsidian`` and copies
    the bundled plugin from ``_assets/plugins``, ``_merge`` dispatches to
    ``_assets/git/merge_index.sh``, and ``install`` copies ``skills/`` --
    all resolved beside the modules at runtime, so a build
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
        ('wiki/_assets', 'plugins/wiki-root-links/main.js'),
        ('wiki/_assets', 'plugins/wiki-root-links/manifest.json'),
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
    away from the tracked plugin manifests, the ``.obsidian/`` ignore
    sits beside the packaged seed assets under ``wiki/_assets/obsidian``,
    and the ``*.manifest`` ignore sits beside the bundled Obsidian plugin's
    ``manifest.json`` -- an over-broadened pattern would silently drop
    those files from every clone, surfacing only at the next fresh
    checkout.
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
    # tracked manifests, seed assets, wiki config, and the lockfile stay committable
    assert not _check_ignore(repo, '.claude-plugin/plugin.json')
    assert not _check_ignore(repo, '.codex-plugin/plugin.json')
    assert not _check_ignore(repo, 'wiki/_assets/obsidian/community-plugins.json')
    assert not _check_ignore(repo, 'wiki/_assets/plugins/wiki-root-links/main.js')
    assert not _check_ignore(repo, 'wiki/_assets/plugins/wiki-root-links/manifest.json')
    assert not _check_ignore(repo, 'examples/hello/.wiki/settings.json')
    assert not _check_ignore(repo, 'uv.lock')
    # genuine junk stays hidden (the copy carries the real rules), including
    # the Obsidian sidecar a `wiki config` run leaves inside the committed
    # sample wiki
    assert _check_ignore(repo, '__pycache__/mod.pyc')
    assert _check_ignore(repo, 'examples/hello/.obsidian/app.json')


def test_bundled_plugin_manifest_agrees_with_its_install() -> None:
    """Each bundled Obsidian plugin's manifest agrees with its install.

    ``update_config`` copies the plugin's files, ``main.js`` and
    ``manifest.json``, from ``_assets/plugins/<id>/`` into a vault's
    ``.obsidian/plugins/<id>/`` by name and enables ``<id>``, and
    Obsidian loads the folder only when the manifest's ``id`` spells
    that name; the plugin reads the vault's filesystem, so it declares
    itself desktop only. The version is the plugin's own semver, bumped
    only when the plugin changes, so it stays outside the release
    lockstep.
    """
    # every plugin folder is a declared bundled plugin, and vice versa
    plugins = _REPO_ROOT / 'wiki' / '_assets' / 'plugins'
    bundled = sorted(folder.name for folder in plugins.iterdir() if folder.is_dir())
    assert bundled == sorted(_obsidian._BUNDLED_PLUGINS), (
        '_assets/plugins folders must match _BUNDLED_PLUGINS '
        '(update_config installs exactly the ids the tuple names)'
    )
    # each manifest spells its folder name, is desktop only, and ships every asset
    for plugin_id in _obsidian._BUNDLED_PLUGINS:
        folder = plugins / plugin_id
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        assert manifest['id'] == plugin_id, (
            f'{plugin_id}/manifest.json id must spell the folder name '
            '(Obsidian loads .obsidian/plugins/<id>/ only when the manifest agrees)'
        )
        assert manifest['isDesktopOnly'] is True, (
            f'{plugin_id}/manifest.json must declare isDesktopOnly '
            '(the plugin reads the vault filesystem, which mobile lacks)'
        )
        for asset in _obsidian._BUNDLED_PLUGIN_ASSETS:
            assert (folder / asset).is_file(), (
                f'{plugin_id}/{asset} is copied by name from '
                '_BUNDLED_PLUGIN_ASSETS and must exist in the package'
            )
