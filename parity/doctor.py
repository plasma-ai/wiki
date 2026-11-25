"""A doctored side: it runs a real side and changes one thing in one command.

usage: <python> doctor.py KIND TARGET PATH COUNT <side argv> <wiki args>

``KIND`` is one of :data:`KINDS`, ``TARGET`` the wiki arguments of the one
command to doctor (a JSON list), ``PATH`` the file the kind acts on, and
``COUNT`` the length of the real side's argv prefix. Every other command
passes through unchanged.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from collections.abc import Sequence

__all__ = [
    'KINDS',
    'COUNTS_KEY',
    'INDEX_PATH',
    'main',
]

#: what a doctored side changes: one stdout byte, one stderr byte, the exit
#: code, one byte of a file, one file mode, one extra written file, one file
#: written again with the same bytes, one word count, or one search row
KINDS = (
    'stdout',
    'stderr',
    'exit',
    'file',
    'mode',
    'extra',
    'rewrite',
    'counts',
    'index',
)
#: the ``word_counts.json`` entry the ``counts`` kind changes
COUNTS_KEY = '_index.md'
#: the path of the rows the ``index`` kind adds to ``search.db``
INDEX_PATH = 'parity_doctored.md'


def main(argv: Sequence[str]) -> int:
    """Run the real side, doctor the targeted command, and return its exit code."""
    kind, target, path, count = argv[0], json.loads(argv[1]), argv[2], int(argv[3])
    prefix, args = argv[4 : 4 + count], argv[4 + count :]
    result = subprocess.run(
        [*prefix, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    stdout, stderr, code = result.stdout, result.stderr, result.returncode
    if list(args) == target:
        if kind == 'stdout':
            stdout = _flip(stdout)
        elif kind == 'stderr':
            stderr = _flip(stderr)
        elif kind == 'exit':
            code ^= 1
        elif kind == 'file':
            content = pathlib.Path(path).read_bytes()
            _write_quietly(path, _flip(content))
        elif kind == 'mode':
            os.chmod(path, os.stat(path).st_mode ^ 0o100)
        elif kind == 'extra':
            pathlib.Path(path).write_bytes(b'doctored\n')
        elif kind == 'rewrite':
            # the same bytes and mode under a new inode
            spare = f'{path}.doctored'
            pathlib.Path(spare).write_bytes(pathlib.Path(path).read_bytes())
            os.chmod(spare, os.stat(path).st_mode)
            os.replace(spare, path)
        elif kind == 'counts':
            data = json.loads(pathlib.Path(path).read_bytes())
            data[COUNTS_KEY]['words'] += 1
            text = json.dumps(data, indent=2, sort_keys=True) + '\n'
            _write_quietly(path, text.encode('utf-8'))
        elif kind == 'index':
            _add_search_rows(path)
    sys.stdout.buffer.write(stdout)
    sys.stderr.buffer.write(stderr)
    return code


# ------ helper functions


def _flip(data: bytes) -> bytes:
    """Return ``data`` with its last byte changed, or one byte for none."""
    if not data:
        return b'\x01'
    return data[:-1] + bytes([data[-1] ^ 1])


def _write_quietly(path: str, content: bytes) -> None:
    """Write ``content`` over the file in place and put its old times back."""
    status = os.stat(path)
    with open(path, 'r+b') as handle:
        handle.write(content)
        handle.truncate()
    os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns))


def _add_search_rows(path: str) -> None:
    """Add one row to each table of the search index and put its old times back."""
    status = os.stat(path)
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute(
                'INSERT INTO files(path, mtime_ns, size) VALUES (?, 0, 0)',
                (INDEX_PATH,),
            )
            connection.execute(
                'INSERT INTO notes_fts(body, path, folder) VALUES (?, ?, ?)',
                ('doctored', INDEX_PATH, '.'),
            )
    finally:
        connection.close()
    os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
