"""Behavioral tests for the plan/apply engine behind ``Wiki.update``.

The overlay/baseline split observed through the verb: ``updated:``
re-stamped only on real writes, dry-run reporting, the
concurrent-edit baseline skip, atomic writes under real reader
threads, mid-plan deletion, and the CRLF byte probe.
"""

from __future__ import annotations

import pathlib
import re
import threading
from typing import Any, Optional

import pytest

from wiki.core.wiki import Wiki

from ._helpers import _capture_notices, _make_wiki

__all__ = [
    'test_noop_update_leaves_updated_alone',
    'test_update_check_reports_without_writing',
    'test_new_file_created_equals_updated',
    'test_update_preserves_concurrent_edit',
    'test_update_writes_are_atomic_for_concurrent_readers',
    'test_update_preserves_file_mode',
    'test_update_survives_page_deleted_mid_plan',
    'test_update_normalizes_crlf_file',
]


# ------ timestamps and dry runs


def test_noop_update_leaves_updated_alone(tmp_path: pathlib.Path) -> None:
    """A touch-nothing update re-run never moves ``updated:``.

    With derived counts out of frontmatter, rewrites happen only on real
    generated-content changes, so a converged tree's files -- timestamps
    included -- are byte-stable across arbitrary update re-runs.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    stamped = re.search(r'^updated: .+$', page.read_text(encoding='utf-8'), re.M)
    assert wiki.update() == []
    text = page.read_text(encoding='utf-8')
    assert re.search(r'^updated: .+$', text, re.M).group(0) == stamped.group(0)


def test_update_check_reports_without_writing(tmp_path: pathlib.Path) -> None:
    """update(check=True) reports would-change files but writes nothing."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    page = tmp_path / 'core' / 'design.md'
    page.write_text(
        page.read_text(encoding='utf-8').replace('# core/design', '# Wrong Title'),
        encoding='utf-8',
    )
    perturbed = page.read_text(encoding='utf-8')
    # a dry run reports the page without touching disk
    would_change = wiki.update(check=True)
    assert 'core/design.md' in would_change
    assert page.read_text(encoding='utf-8') == perturbed
    # a real update then writes and clears the report
    assert wiki.update() != []
    assert wiki.update(check=True) == []


def test_new_file_created_equals_updated(tmp_path: pathlib.Path) -> None:
    """A file created and stamped in one update run gets created == updated."""
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    (tmp_path / 'core' / 'fresh.md').write_text(
        '# fresh\n\nA brand new page body.\n',
        encoding='utf-8',
    )
    wiki.update()
    fresh = (tmp_path / 'core' / 'fresh.md').read_text(encoding='utf-8')
    created = re.search(r'^created:\s*(.+)$', fresh, re.M).group(1)
    updated = re.search(r'^updated:\s*(.+)$', fresh, re.M).group(1)
    assert created == updated


# ------ concurrency and atomicity


def test_update_preserves_concurrent_edit(
    tmp_path: pathlib.Path,
) -> None:
    """An edit landing between plan and apply survives the update.

    ``update`` snapshots every file while planning and writes corrected
    content afterwards; an edit another writer (e.g. a sibling fractal
    node) lands inside that window must not be silently reverted to the
    plan-time snapshot.
    """
    _make_wiki(tmp_path, folders={'notes': ['readme']})
    page = tmp_path / 'notes' / 'readme.md'

    class RacingWiki(Wiki):
        """Wiki whose plan is immediately followed by a concurrent edit."""

        def _plan(
            self: RacingWiki,
            folder: pathlib.Path,
            **kwargs: Any,
        ) -> tuple:
            """Plan, then land another writer's edit inside the window."""
            result = super()._plan(folder, **kwargs)
            text = page.read_text(encoding='utf-8')
            page.write_text(text + '\nConcurrent paragraph.\n', encoding='utf-8')
            return result

    # the concurrent edit survives and the skipped file is named
    racing = RacingWiki(tmp_path)
    notices = _capture_notices(racing)
    racing.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Concurrent paragraph.' in page.read_text(encoding='utf-8')
    assert 'notes/readme.md' in err
    assert 'changed during update' in err

    # the next (unraced) run converges without losing the edit
    Wiki(tmp_path).update()
    assert 'Concurrent paragraph.' in page.read_text(encoding='utf-8')
    assert Wiki(tmp_path).update() == []


def test_update_writes_are_atomic_for_concurrent_readers(
    tmp_path: pathlib.Path,
) -> None:
    """A concurrent reader never observes an empty or truncated file.

    ``update`` rewrites files in place; a truncate-then-write briefly
    empties each file, so a concurrent reader (another node's plan) sees
    ``''`` or a prefix, and a crash mid-write leaves a torn file. Writes
    must stage to a temp file in the same directory and rename into
    place, keeping every read all-or-nothing.
    """
    wiki = _make_wiki(tmp_path)
    page = tmp_path / 'big.md'
    # two complete page variants, large enough that a truncate-then-write
    # window is observable, each ending in a sentinel a torn read loses
    variants = []
    for word in ('alpha', 'omega'):
        body = f'{word} ' * 300_000
        variants.append(
            f'---\nname: big\ndesc: A big page.\nupdated: x\n---\n\n'
            f'# big\n\n{body}END\n'
        )
    page.write_text(variants[0], encoding='utf-8')

    # a reader polling for torn reads while the writer rewrites the page
    torn = []
    stop = threading.Event()

    def read_loop() -> None:
        """Record any read that is not a complete page variant."""
        while not stop.is_set():
            # a missing file is as torn as a truncated one: record the
            # error instead of letting it kill the reader thread
            try:
                text = page.read_text(encoding='utf-8')
            except OSError as e:
                torn.append(repr(e))
                continue
            if not text.endswith('END\n'):
                torn.append(len(text))

    reader = threading.Thread(target=read_loop)
    reader.start()
    try:
        # alternate staged variants so every apply performs a real write
        now = wiki._utc_now()
        for i in range(1, 60):
            content = variants[i % 2]
            baseline = {page: page.read_text(encoding='utf-8')}
            wiki._apply_plan({page: content}, baseline, now)
    finally:
        stop.set()
        reader.join()
    assert torn == []


def test_update_preserves_file_mode(tmp_path: pathlib.Path) -> None:
    """A rewrite keeps the page's permission bits.

    The atomic-write staging file is created 0600 by ``mkstemp`` and
    ``os.replace`` carries the temp's mode onto the target, so the helper
    must restore the original mode (and honor the umask for fresh files)
    or every update strips group/other read bits.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['readme']})
    page = tmp_path / 'notes' / 'readme.md'
    page.chmod(0o604)
    # perturb the generated H1 so update rewrites the page
    text = page.read_text(encoding='utf-8')
    page.write_text(text.replace('# notes/readme', '# Wrong Title'), encoding='utf-8')
    assert wiki.update() == ['notes/readme.md']
    assert (page.stat().st_mode & 0o777) == 0o604


def test_update_survives_page_deleted_mid_plan(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page deleted while update plans (a concurrent node) never crashes.

    In ``_read_child_labels``, reading pages without the ``None`` guard
    its folder branch has would let a page vanishing between enumeration
    and read raise ``AttributeError`` instead of degrading to the
    broken-link warning the next run reports.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['doomed', 'readme']})
    doomed = tmp_path / 'notes' / 'doomed.md'
    real = Wiki._current_text

    def racy(
        self: Wiki,
        path: pathlib.Path,
        overlay: Optional[dict[pathlib.Path, str]] = None,
    ) -> Optional[str]:
        """Delete the doomed page just before update first reads it."""
        if path == doomed and doomed.exists():
            doomed.unlink()
        return real(self, path, overlay)

    # the mid-plan deletion is handled, not crashed on
    monkeypatch.setattr(Wiki, '_current_text', racy)
    wiki.update()

    # the next run degrades to the ordinary broken-link warning
    notices = _capture_notices(wiki)
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Broken link' in err
    assert 'doomed' in err


# ------ byte normalization


@pytest.mark.parametrize('kind', ['page', 'index'])
def test_update_normalizes_crlf_file(
    tmp_path: pathlib.Path,
    kind: str,
) -> None:
    """A CRLF file is noted by lint and rewritten to LF by the next update.

    Universal-newline reads make a CRLF file look permanently clean --
    without a byte-level probe, update would never rewrite it, lint would
    never flag it, and mixed-EOL wikis would drift silently.
    """
    wiki = _make_wiki(tmp_path, folders={'core': ['design']})
    if kind == 'page':
        target = tmp_path / 'core' / 'design.md'
    else:
        target = tmp_path / 'core' / '_index.md'
    rel = str(target.relative_to(tmp_path))
    # convert the converged file to CRLF, as a Windows editor would
    target.write_bytes(target.read_bytes().replace(b'\n', b'\r\n'))

    # lint notes the line endings without raising a hard issue
    notices = _capture_notices(wiki)
    assert wiki.lint() == []
    err = '\n'.join(event.description for event in notices)
    assert f'{rel}: CRLF line endings; update will normalize' in err

    # a dry run reports the file; the real update rewrites it to LF
    assert rel in wiki.update(check=True)
    assert rel in wiki.update()
    assert b'\r' not in target.read_bytes()

    # converged: nothing further to update, and the note is gone
    assert wiki.update() == []
    notices.clear()
    wiki.lint()
    assert 'CRLF' not in '\n'.join(event.description for event in notices)
