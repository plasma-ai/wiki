"""Functions for the Obsidian integration."""

from __future__ import annotations

import pathlib
import shutil
from typing import Union

import wiki.util
from wiki.constants import WIKI_DIR

__all__ = []

# NOTE: plugin versions should be periodically updated
_OBSIDIAN_PLUGINS = {
    'obsidian-front-matter-title-plugin': (
        'https://github.com/snezhig/obsidian-front-matter-title'
        '/releases/download/4.1.0/{asset}'
    ),
}
_OBSIDIAN_PLUGIN_ASSETS = ('main.js', 'manifest.json')


def seed_template(root: pathlib.Path) -> None:
    """Seed a missing ``.wiki/obsidian/`` from the stock template.

    ``init`` seeds a fresh wiki and ``update_config`` re-seeds an
    adopted tree (or one whose ``.wiki/`` was lost), so both get the
    full setup. An existing config directory is left untouched.
    """
    config_dir = root / WIKI_DIR / 'obsidian'
    if not config_dir.exists():
        path = pathlib.Path(__file__).parent
        template_dir = path.parent / '_assets' / 'obsidian'
        if template_dir.exists():
            config_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(template_dir, config_dir)


def merge_settings(
    target_data: Union[dict, list],
    source_data: Union[dict, list],
    *,
    name: str,
) -> Union[dict, list]:
    """Merge a top-level Obsidian ``.json`` file's source into its target.

    The install merge policy: arrays are union-merged (source items
    appended when absent) and dicts deep-merged with source winning.
    ``name`` is the merging filename, so the type-mismatch error names
    the offending ``.obsidian/`` file.

    Raises:
        TypeError: If the top-level types cannot merge.

    """
    # union merge for arrays
    if isinstance(source_data, list) and isinstance(target_data, list):
        merged = target_data[:]
        for item in source_data:
            if item not in merged:
                merged.append(item)
        return merged
    # deep merge for dicts
    if isinstance(source_data, dict) and isinstance(target_data, dict):
        return wiki.util.dict.merge(target_data, source_data)
    # handle invalid type
    raise TypeError(
        f'Cannot merge {type(source_data).__name__} into'
        f' {type(target_data).__name__}: .obsidian/{name}'
    )
