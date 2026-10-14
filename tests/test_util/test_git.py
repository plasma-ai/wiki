"""Test the ``wiki.util.git`` module.

The helper's own contract is which caller environment reaches git: the
variables that only restrict git pass, while those that redirect it are
dropped. The fence probes and merge-driver wiring it backs are covered
end-to-end by the gitignore-fence tests in ``test_core.test_exclude``
and ``test_core.test_lint`` against real repositories.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import time
from typing import Optional

import pytest

from wiki.util.git import _git

__all__ = ['test_optional_lock_setting_reaches_git']


@pytest.mark.skipif(shutil.which('git') is None, reason='requires git')
@pytest.mark.parametrize(
    argnames='optional_locks',
    argvalues=[None, '0'],
    ids=['default', 'off'],
)
def test_optional_lock_setting_reaches_git(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    optional_locks: Optional[str],
) -> None:
    """A caller's ``GIT_OPTIONAL_LOCKS=0`` keeps the helper's reads off the index.

    A plain status rewrites a stale index under ``index.lock``, failing a
    concurrent ``git add`` or commit in the same repository; a caller
    that turns optional locks off for its session must have the setting
    reach the wiki's git calls too.
    """
    # a staged page whose index stat data has gone stale
    page = tmp_path / 'page.md'
    page.write_text('# Page\n', encoding='utf-8')
    for args in (['init', '-q'], ['add', 'page.md']):
        subprocess.run(['git', '-C', f'{tmp_path}', *args], check=True)
    stale = time.time() - 3600
    os.utime(page, (stale, stale))
    index = tmp_path / '.git' / 'index'
    before = index.read_bytes()

    # the read leaves the index as it was exactly when optional locks are off
    if optional_locks is None:
        monkeypatch.delenv('GIT_OPTIONAL_LOCKS', raising=False)
    else:
        monkeypatch.setenv('GIT_OPTIONAL_LOCKS', optional_locks)
    _git(['status', '--porcelain'], cwd=tmp_path)
    assert (index.read_bytes() == before) is (optional_locks == '0')
