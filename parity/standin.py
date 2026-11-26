"""Functions for the stand-in of the registry check.

A stand-in runs a real side and prints other words on some commands, as a
second engine may.

usage: <python> standin.py EDITS COUNT <side argv> <wiki args>

``EDITS`` is a JSON list of pairs: the wiki arguments of one command, and
what the stand-in changes in what the real side answers it. ``COUNT`` is the
length of the real side's argv prefix. Every other command passes through
unchanged.

An edit is an object with up to three keys. ``stdout`` and ``stderr`` each
hold a regular expression and its replacement, applied to that stream;
``exit`` holds the exit code. The fixtures of the registry use a stand-in to
print what another engine may print: a library's text in other words, a
usage error in another layout, a refusal.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Sequence

__all__ = []


def main(argv: Sequence[str]) -> int:
    """Run the real side, edit what it answers, and return the exit code.

    Raises:
        ValueError: If an expression matches nothing in its stream: a
            stand-in that changes nothing would pass for the real side.

    """
    # parse arguments
    edits, count = json.loads(argv[0]), int(argv[1])
    prefix, args = argv[2 : 2 + count], argv[2 + count :]
    # run the real side
    result = subprocess.run(
        [*prefix, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    streams = {'stdout': result.stdout, 'stderr': result.stderr}
    code = result.returncode
    # edit what the listed commands answer
    for target, edit in edits:
        if list(args) != target:
            continue
        for name in streams:
            if name not in edit:
                continue
            pattern, replacement = (text.encode('utf-8') for text in edit[name])
            streams[name], changed = re.subn(pattern, replacement, streams[name])
            if not changed:
                raise ValueError(f'{pattern!r} matches nothing in {name}.')
        code = edit.get('exit', code)
    # relay the streams and the exit code
    sys.stdout.buffer.write(streams['stdout'])
    sys.stderr.buffer.write(streams['stderr'])
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
