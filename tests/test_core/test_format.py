"""Test the ``wiki.core.format`` module.

Functions over the on-disk page format, driven only through the engine;
the behavior-named core suites (``test_authoring``, ``test_update``,
``test_lint``) are the primary cover, exercising parse/render
round-trips exactly as the tool does.
"""

from __future__ import annotations

import pathlib

from ._helpers import _make_wiki

__all__ = ['test_plain_multiline_desc_propagates']


def test_plain_multiline_desc_propagates(tmp_path: pathlib.Path) -> None:
    """A bare ``desc:`` over an indented body reads as a plain scalar.

    YAML folds a plain multi-line scalar the way ``>`` does; the reader
    must resolve it the same way so the authored desc propagates to the
    parent index instead of silently reading as absent.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})

    # author a child page whose desc is a bare key over an indented body
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        '---\nname: design\ndesc:\n  A plain multi-line\n  scalar value.\n'
        '---\n\n# design\n\nBody.\n',
        encoding='utf-8',
    )

    # the parent index picks up the folded desc and the tree converges
    wiki.update()
    core_index = (tmp_path / 'core' / '_index.md').read_text(encoding='utf-8')
    assert 'A plain multi-line scalar value.' in core_index
    assert wiki.update() == []
