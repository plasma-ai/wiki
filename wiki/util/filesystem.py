"""Functions for saving and loading on the file system.

Can be accessed via alias ``wiki.util.fs``.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

__all__ = []


def write_atomic(path: pathlib.Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file + rename).

    A plain ``write_text`` truncates before writing, exposing an empty or
    partial file to concurrent readers and leaving a torn file if the
    process dies mid-write. Staging to a temp file in the same directory
    and ``os.replace``-ing it into place makes every read all-or-nothing.
    The dot-prefixed temp name keeps a leftover from a crash out of the
    wiki walk.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.')
    tmp = pathlib.Path(tmp)
    try:
        # newline='\n' writes LF verbatim on every platform, so a rewrite
        # normalizes CRLF and never reintroduces it
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        # mkstemp creates the temp 0600 and os.replace carries that mode
        # onto the target: preserve the existing mode, or honor the umask
        # for a fresh file
        try:
            os.chmod(tmp, path.stat().st_mode & 0o777)
        except FileNotFoundError:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, path)
    except BaseException:
        # discard the partial temp file, leaving the target untouched
        tmp.unlink(missing_ok=True)
        raise
