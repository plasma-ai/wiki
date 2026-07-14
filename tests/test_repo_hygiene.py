"""Behavioral tests for tracked repo config that must stay coherent.

The release version ships as a hand-maintained literal in four places --
the package ``__init__``, the build metadata, and both plugin manifests
-- and nothing computes one from another, so a bump that misses one
silently publishes mismatched artifacts; the pin turns that drift into a
test failure.
"""

from __future__ import annotations

import json
import pathlib
import tomllib

import wiki

__all__ = [
    'test_version_strings_agree',
    'test_package_data_ships_in_build',
]

_REPO_ROOT = pathlib.Path(__file__).parent.parent


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
