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

__all__ = ['test_version_strings_agree']

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
