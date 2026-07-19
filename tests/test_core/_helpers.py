"""Shared helpers for the ``wiki`` core tests."""

from __future__ import annotations

import pathlib
from typing import Any, Optional

import pytest

from wiki.core.event import Event
from wiki.core.wiki import Wiki

__all__ = [
    'page_index',
    '_capture_notices',
    'CategorizedWiki',
    '_make_wiki',
    '_make_category_folder',
]

page_index = pytest.mark.parametrize('kind', ['page', 'index'])


def _capture_notices(wiki: Wiki) -> list[Event]:
    """Swap ``wiki.on_notice`` for a capture sink; return its event list.

    Every per-kind hook delegates to the instance's ``on_notice``, so
    the one-attribute swap sees every notice the wiki emits. The
    returned list grows as notices fire; clear it between phases the
    way ``capsys.readouterr()`` drains a stream.
    """
    notices: list[Event] = []

    def _capture(event: Event, **kwargs: Any) -> Event:
        """Append a notice to the capture list."""
        notices.append(event)
        return event

    wiki.on_notice = _capture
    return notices


class CategorizedWiki(Wiki):
    """Wiki subclass with category ordering for tests."""

    category_order = ['node', 'store']


def _make_wiki(
    path: pathlib.Path,
    folders: Optional[dict[str, list[str]]] = None,
) -> Wiki:
    """Create a wiki with optional folders and pages."""
    # init the root wiki
    wiki = Wiki(path)
    wiki.init(name='root')
    # set root desc
    root_index = path / '_index.md'
    content = root_index.read_text(encoding='utf-8')
    content = content.replace('desc: ...', 'desc: The root wiki.')
    content = content.replace('***\n', '***\n\nRoot overview.\n')
    root_index.write_text(content, encoding='utf-8')
    # create folders and pages
    if folders:
        for folder_name, pages in folders.items():
            folder = path / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / '_index.md').write_text(
                f'---\nname: {folder_name}\n'
                f'desc: The {folder_name} section.\n'
                f'---\n\n# {folder_name}\n\n***\n\n'
                f'Overview of {folder_name}.\n',
                encoding='utf-8',
            )
            for page in pages:
                (folder / f'{page}.md').write_text(
                    f'---\nname: {page}\n'
                    f'desc: The {page} page.\n'
                    f'---\n\n# {page}\n\nContent for {page}.\n',
                    encoding='utf-8',
                )
    wiki.update()
    return wiki


def _make_category_folder(
    path: pathlib.Path,
    name: str,
    category: str = '',
    desc: str = 'A section.',
) -> pathlib.Path:
    """Create a child folder whose ``_index.md`` carries a category and desc."""
    folder = path / name
    folder.mkdir(parents=True, exist_ok=True)
    frontmatter = f'---\nname: {name}\ndesc: {desc}\n'
    if category:
        frontmatter += f'category: {category}\n'
    frontmatter += 'tags: []\n---\n\n# x\n\n***\n\nOverview.\n'
    (folder / '_index.md').write_text(frontmatter, encoding='utf-8')
    return folder
