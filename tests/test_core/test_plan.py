"""Behavioral tests for the plan/apply engine behind ``Wiki.update``.

The overlay/baseline split observed through the verb: ``updated:``
re-stamped only on real writes, dry-run reporting, the
concurrent-edit baseline skip, atomic writes under real reader
threads, the walked tree changing mid-plan (deletion, replacement,
permission loss), and the CRLF byte probe.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import threading
from typing import Any, Optional

import pytest

from wiki.core.wiki import Wiki

from ._helpers import _capture_notices, _make_wiki, _needs_unprivileged, page_index

__all__ = [
    'test_noop_update_leaves_updated_alone',
    'test_update_check_reports_without_writing',
    'test_new_file_created_equals_updated',
    'test_update_preserves_concurrent_edit',
    'test_update_writes_are_atomic_for_concurrent_readers',
    'test_update_preserves_file_mode',
    'test_update_survives_page_deleted_mid_plan',
    'test_update_survives_page_deleted_mid_page_pass',
    'test_update_survives_page_deleted_mid_read',
    'test_update_survives_folder_deleted_mid_walk',
    'test_update_survives_folder_deleted_mid_index_read',
    'test_update_survives_folder_replaced_after_walk',
    'test_update_survives_page_replaced_after_walk',
    'test_update_survives_root_replaced_after_walk',
    'test_update_fails_on_folder_unreadable_before_plan',
    'test_update_counts_skip_folder_unreadable_before_refresh',
    'test_update_survives_folder_deleted_mid_write',
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
    broken-link prune the next run reports.
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

    # the next run prunes the vanished page's row with the ordinary notice
    notices = _capture_notices(wiki)
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Pruned link' in err
    assert 'doomed' in err


def test_update_survives_page_deleted_mid_page_pass(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page deleted after the page pass walks it never crashes update.

    The page pass reads each walked file directly, so a page vanishing
    between its folder's enumeration and its read (a concurrent node's
    delete) must plan as absent from the walk rather than raise
    ``FileNotFoundError``.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['alpha', 'doomed']})
    doomed = tmp_path / 'notes' / 'doomed.md'
    real = Wiki._plan_page

    def racy(
        self: Wiki,
        path: pathlib.Path,
        now: str,
        **kwargs: Any,
    ) -> tuple:
        """Delete the doomed page while its already-walked sibling plans."""
        if (path != doomed) and doomed.exists():
            doomed.unlink()
        return real(self, path, now, **kwargs)

    # the mid-pass deletion is handled, not crashed on, and the vanished
    # page's row is pruned with the ordinary notice -- in this run, since the
    # indexes plan after the pages, and never again
    monkeypatch.setattr(Wiki, '_plan_page', racy)
    notices = _capture_notices(wiki)
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Pruned link' in err
    assert 'doomed' in err
    assert Wiki(tmp_path).update() == []


def test_update_survives_page_deleted_mid_read(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page deleted inside the read itself never crashes update.

    Cross-file reads resolve absence at the read rather than through a
    separate existence probe, so a delete landing between any probe and
    the read (a concurrent node's) still answers as an absent file
    rather than raising ``FileNotFoundError``.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['doomed', 'readme']})
    doomed = tmp_path / 'notes' / 'doomed.md'
    real = Wiki._read_text

    def racy(self: Wiki, path: pathlib.Path) -> str:
        """Delete the doomed page just as its read begins."""
        if (path == doomed) and doomed.exists():
            doomed.unlink()
        return real(self, path)

    # the mid-read deletion is handled, not crashed on, and the vanished
    # page's row is pruned with the ordinary notice in this run
    monkeypatch.setattr(Wiki, '_read_text', racy)
    notices = _capture_notices(wiki)
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Pruned link' in err
    assert 'doomed' in err
    assert Wiki(tmp_path).update() == []


def test_update_survives_folder_deleted_mid_walk(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A folder deleted between listing and descent never crashes update.

    The walk lists a folder from its parent and then descends into it,
    so a folder vanishing inside that window (a concurrent delete) must
    walk as absent -- its subtree with it -- with the same run pruning
    the stale parent row.
    """
    wiki = _make_wiki(tmp_path, folders={'doomed': ['gone'], 'notes': ['alpha']})
    doomed = tmp_path / 'doomed'
    real = Wiki._is_excluded_dir

    def racy(self: Wiki, path: pathlib.Path) -> bool:
        """Delete the doomed folder just after the walk lists it."""
        result = real(self, path)
        if (path == doomed) and doomed.exists():
            shutil.rmtree(doomed)
        return result

    # the mid-walk deletion is handled, not crashed on, and the same run
    # prunes the vanished folder's row with the ordinary notice
    monkeypatch.setattr(Wiki, '_is_excluded_dir', racy)
    notices = _capture_notices(wiki)
    wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Pruned link' in err
    assert 'doomed' in err

    # the tree has converged: nothing left pending
    assert wiki.update(check=True) == []


def test_update_survives_folder_deleted_mid_index_read(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A folder deleted as the plan reads its index never crashes update.

    The index pass snapshots a walked folder's index and then lists the
    folder for its links, so a folder vanishing inside that window (a
    concurrent delete) must plan with no children left to link -- the
    fresh index it plans dropped at the write -- with the same run
    pruning the stale parent row.
    """
    wiki = _make_wiki(tmp_path, folders={'doomed': ['gone'], 'notes': ['alpha']})
    doomed = tmp_path / 'doomed'
    index = doomed / '_index.md'
    real = Wiki._current_text

    def racy(
        self: Wiki,
        path: pathlib.Path,
        overlay: Optional[dict[pathlib.Path, str]] = None,
    ) -> Optional[str]:
        """Delete the doomed folder just as the plan reads its index."""
        if (path == index) and doomed.exists():
            shutil.rmtree(doomed)
        return real(self, path, overlay)

    # the mid-read deletion is handled, not crashed on: the vanished
    # folder is neither recreated nor reported written, and the same run
    # prunes its row from the parent with the ordinary notice
    monkeypatch.setattr(Wiki, '_current_text', racy)
    notices = _capture_notices(wiki)
    written = wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Pruned link' in err
    assert 'doomed' in err
    assert not doomed.exists()
    assert all('doomed' not in path for path in written)

    # the tree has converged: nothing left pending
    assert wiki.update(check=True) == []


@pytest.mark.parametrize('replacement', ['file', 'symlink'])
@pytest.mark.parametrize(
    argnames='folders',
    argvalues=[
        {'doomed': ['gone'], 'notes': ['alpha']},
        {
            'doomed': ['gone'],
            'doomed/sub': ['deep'],
            'notes': ['alpha'],
            'notes/sub': ['beta'],
        },
    ],
    ids=['flat', 'nested'],
)
def test_update_survives_folder_replaced_after_walk(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
    folders: dict[str, list[str]],
) -> None:
    """A walked folder replaced before the plan reads it is never planned through.

    Update walks the scope once ahead of its plan, so a folder a concurrent
    writer swaps for a file or a symlink in that window is re-judged at
    plan time: it plans as absent, its walked subtree with it, nothing is
    written or counted through the replacement, and the same run prunes
    the stale parent row.
    """
    wiki = _make_wiki(tmp_path, folders=folders)
    doomed = tmp_path / 'doomed'
    notes = tmp_path / 'notes'
    before = {path: path.read_bytes() for path in notes.rglob('*.md')}
    real = Wiki._utc_now

    def racy(self: Wiki) -> str:
        """Swap the doomed folder as the clock is read ahead of the plan."""
        if doomed.is_dir() and not doomed.is_symlink():
            shutil.rmtree(doomed)
            if replacement == 'file':
                doomed.write_text('raw\n', encoding='utf-8')
            else:
                doomed.symlink_to(notes)
        return real(self)

    # the swap is handled, not planned through: the parent row is pruned,
    # the replacement's target is untouched, and the counts refresh skips
    # the swapped folder rather than counting through the replacement
    monkeypatch.setattr(Wiki, '_utc_now', racy)
    notices = _capture_notices(wiki)
    written = wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Pruned link' in err
    assert all('doomed' not in path for path in written)
    assert {path: path.read_bytes() for path in notes.rglob('*.md')} == before
    counts = json.loads(
        (tmp_path / '.wiki' / 'cache' / 'word_counts.json').read_text(encoding='utf-8')
    )
    surviving = {path.relative_to(tmp_path).as_posix() for path in before}
    assert set(counts) == {'_index.md'} | surviving

    # the tree has converged: nothing left pending
    assert wiki.update(check=True) == []


@pytest.mark.parametrize('replacement', ['symlink', 'directory'])
def test_update_survives_page_replaced_after_walk(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    """A walked page replaced before the plan reads it is never planned through.

    Update walks the scope once ahead of its plan, so a page a concurrent
    writer swaps for a symlink or a directory in that window is re-judged
    before its read: it plans as absent, nothing is written or counted
    through the replacement (a symlink's out-of-root target never lands
    in a tracked page), and the same run prunes the stale parent row.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    wiki = _make_wiki(root, folders={'notes': ['alpha', 'beta']})
    page = root / 'notes' / 'alpha.md'
    secret = tmp_path / 'secret.md'
    secret.write_text('# secret\n\nTOP SECRET\n', encoding='utf-8')
    real = Wiki._utc_now

    def racy(self: Wiki) -> str:
        """Swap the page as the clock is read ahead of the plan."""
        if page.is_file() and not page.is_symlink():
            page.unlink()
            if replacement == 'symlink':
                page.symlink_to(secret)
            else:
                page.mkdir()
                (page / 'late.md').write_text('# late\n\nLate.\n', encoding='utf-8')
        return real(self)

    # the swap is handled, not planned through: the parent row is pruned, no
    # path under the page is written or counted, and the replacement stands
    monkeypatch.setattr(Wiki, '_utc_now', racy)
    notices = _capture_notices(wiki)
    written = wiki.update()
    err = '\n'.join(event.description for event in notices)
    assert 'Pruned link: [[notes/alpha|alpha]]' in err
    assert all('alpha' not in path for path in written)
    counts = json.loads(
        (root / '.wiki' / 'cache' / 'word_counts.json').read_text(encoding='utf-8')
    )
    assert sorted(counts) == ['_index.md', 'notes/_index.md', 'notes/beta.md']
    if replacement == 'symlink':
        assert page.is_symlink()
        assert 'Link targets a symlink' in err
        assert secret.read_text(encoding='utf-8') == '# secret\n\nTOP SECRET\n'
    else:
        assert (page / 'late.md').read_text(encoding='utf-8') == '# late\n\nLate.\n'

    # the tree converges: a symlink leaves nothing pending, and the folder
    # that replaced the page is planned by the next run
    if replacement == 'directory':
        assert 'notes/alpha.md/_index.md' in wiki.update()
    assert wiki.update(check=True) == []


@pytest.mark.parametrize(
    argnames=('replacement', 'linked_cache'),
    argvalues=[('file', False), ('symlink', False), ('symlink', True)],
    ids=['file', 'symlink-bare', 'symlink-cached'],
)
def test_update_survives_root_replaced_after_walk(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
    linked_cache: bool,
) -> None:
    """A root replaced before the plan reads it is never planned or counted through.

    Update walks the root once ahead of its plan, so a root a concurrent
    writer swaps for a file or a symlink to another wiki in that window
    plans as absent: nothing is written or counted through the
    replacement, so the other wiki's bare page stands, a counts cache it
    is missing is neither written through the link nor announced as
    recreated, and one it holds is not emptied through the link.
    """
    root = tmp_path / 'wiki'
    root.mkdir()
    wiki = _make_wiki(root, folders={'notes': ['alpha']})
    other = tmp_path / 'other'
    other.mkdir()
    _make_wiki(other, folders={'notes': ['alpha']})
    (other / 'notes' / 'alpha.md').write_text('# alpha\n\nraw\n', encoding='utf-8')
    # the recreate notice would fire through the link on a missing cache;
    # the counts refresh would empty a held one through the link
    if not linked_cache:
        shutil.rmtree(other / '.wiki' / 'cache')
    before = {path: path.read_bytes() for path in other.rglob('*') if path.is_file()}
    real = Wiki._utc_now

    def racy(self: Wiki) -> str:
        """Swap the root as the clock is read ahead of the plan."""
        if root.is_dir() and not root.is_symlink():
            shutil.rmtree(root)
            if replacement == 'file':
                root.write_text('raw\n', encoding='utf-8')
            else:
                root.symlink_to(other)
        return real(self)

    # the swap is handled, not planned through: nothing is
    # written, the other wiki is untouched (no cache lands
    # through the link), and no cache is announced as recreated
    monkeypatch.setattr(Wiki, '_utc_now', racy)
    notices = _capture_notices(wiki)
    assert wiki.update() == []
    err = '\n'.join(event.description for event in notices)
    assert 'Recreated' not in err
    after = {path: path.read_bytes() for path in other.rglob('*') if path.is_file()}
    assert after == before
    if replacement == 'file':
        assert root.read_text(encoding='utf-8') == 'raw\n'

    # the symlink root converges: a second run still plans nothing through the
    # link and the other wiki stays untouched; a file root fails at its listing
    if replacement == 'symlink':
        assert wiki.update() == []
        after = {path: path.read_bytes() for path in other.rglob('*') if path.is_file()}
        assert after == before


@_needs_unprivileged
@pytest.mark.parametrize(
    argnames='folders',
    argvalues=[
        {'core': ['design'], 'core/sub': ['deep'], 'other': ['alpha']},
        {'core': ['design'], 'core/sub': ['deep'], 'core/sub/leaf': ['tip']},
    ],
    ids=['leaf', 'nested'],
)
def test_update_fails_on_folder_unreadable_before_plan(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    folders: dict[str, list[str]],
) -> None:
    """A walked folder made unreadable past the nested-root scan fails the run at its index read.

    Update walks the scope once ahead of its plan, so a folder a concurrent
    writer strips of every permission past the nested-root scan is
    re-judged at plan time: its pages plan as absent, the index pass fails
    the run naming the folder's index, and nothing is written -- the same
    outcome on every interpreter, whichever way pathlib answers an
    unreadable parent.
    """
    wiki = _make_wiki(tmp_path, folders=folders)
    sub = tmp_path / 'core' / 'sub'
    before = {path: path.read_bytes() for path in tmp_path.rglob('*.md')}
    real = Wiki._utc_now

    def racy(self: Wiki) -> str:
        """Strip the folder's permissions as the clock is read ahead of the plan."""
        os.chmod(sub, 0o000)
        return real(self)

    # the run fails on the folder's index, and nothing is written
    monkeypatch.setattr(Wiki, '_utc_now', racy)
    try:
        with pytest.raises(PermissionError, match=r'core/sub/_index\.md'):
            wiki.update()
    finally:
        os.chmod(sub, 0o700)
    assert {path: path.read_bytes() for path in tmp_path.rglob('*.md')} == before

    # the tree has converged: nothing left pending
    monkeypatch.setattr(Wiki, '_utc_now', real)
    assert wiki.update(check=True) == []


@_needs_unprivileged
@pytest.mark.parametrize(
    argnames='folders',
    argvalues=[
        {'core': ['design'], 'core/sub': ['deep']},
        {'core': ['design'], 'core/sub': ['deep'], 'core/sub/leaf': ['tip']},
    ],
    ids=['leaf', 'nested'],
)
def test_update_counts_skip_folder_unreadable_before_refresh(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    folders: dict[str, list[str]],
) -> None:
    """A walked folder made unreadable before the counts refresh drops out of the cache.

    A writing update refreshes the counts cache from its own walk, so a
    folder a concurrent writer strips of every permission after the writes
    is re-judged there: the run succeeds, the folder's entries drop out of
    the cache like deleted files, and the next run counts them again -- the
    same outcome on every interpreter, whichever way pathlib answers an
    unreadable parent.
    """
    wiki = _make_wiki(tmp_path, folders=folders)
    sub = tmp_path / 'core' / 'sub'
    cache = tmp_path / '.wiki' / 'cache' / 'word_counts.json'
    converged = json.loads(cache.read_text(encoding='utf-8'))
    real = Wiki._apply_plan

    def racy(
        self: Wiki,
        overlay: dict[pathlib.Path, str],
        baseline: dict[pathlib.Path, Optional[str]],
        now: str,
    ) -> list[str]:
        """Strip the folder's permissions once the writes are applied."""
        result = real(self, overlay, baseline, now)
        os.chmod(sub, 0o000)
        return result

    # the run succeeds and the cache skips the folder
    monkeypatch.setattr(Wiki, '_apply_plan', racy)
    try:
        assert wiki.update() == []
        counts = json.loads(cache.read_text(encoding='utf-8'))
    finally:
        os.chmod(sub, 0o700)
    assert set(counts) == {key for key in converged if not key.startswith('core/sub/')}

    # the next run counts the folder again
    monkeypatch.setattr(Wiki, '_apply_plan', real)
    assert wiki.update() == []
    counts = json.loads(cache.read_text(encoding='utf-8'))
    assert counts == converged


def test_update_survives_folder_deleted_mid_write(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A folder deleted between the plan and the write never crashes update.

    A fresh folder's planned index has nowhere to land once the folder
    vanishes (a concurrent delete), so the write is dropped -- never
    recreating the folder or reporting it written -- and the next run
    converges.
    """
    wiki = _make_wiki(tmp_path, folders={'notes': ['alpha']})
    doomed = tmp_path / 'doomed'
    doomed.mkdir()
    real = Wiki._refuse_conflicted

    def racy(
        self: Wiki,
        baseline: dict[pathlib.Path, Optional[str]],
    ) -> None:
        """Delete the doomed folder after the plan staged its fresh index."""
        if doomed.exists():
            shutil.rmtree(doomed)
        return real(self, baseline)

    # the mid-write deletion is handled, not crashed on, and the dropped
    # write neither recreates the folder nor reports it written
    monkeypatch.setattr(Wiki, '_refuse_conflicted', racy)
    written = wiki.update()
    assert not doomed.exists()
    assert all('doomed' not in path for path in written)

    # the next run prunes the stale parent row and converges
    wiki.update()
    assert wiki.update(check=True) == []


# ------ byte normalization


@page_index
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
