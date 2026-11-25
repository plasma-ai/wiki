"""The v1 launcher: the Python engine's command line as a side of the harness.

usage: <a v1 venv's python> -P launcher.py <wiki args>

Runs the command line of the ``wiki`` package the interpreter imports, with
``argv[0]`` set to ``wiki`` as the console script has it. When ``WIKI_TRACE``
names a file, the launcher first appends the side's identity record to it:
one line of JSON with the engine's name, its version, and its build id, which
is the commit of the checkout the package is imported from. The variable is
the launcher's own switch and does not reach the command.

Run with ``-P`` so the launcher's folder stays off ``sys.path``: the
interpreter then imports the package its venv installs and nothing of the
tree the launcher lives in.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

__all__ = [
    'ENGINE',
    'TRACE',
    'main',
]

#: the engine name in the identity record of a v1 side
ENGINE = 'python'
#: the variable that names the file a side appends its identity record to
TRACE = 'WIKI_TRACE'


def main() -> None:
    """Record the side's identity, then run the wiki command line."""
    import wiki
    from wiki.cli.main import cli

    trace = os.environ.pop(TRACE, None)
    if trace:
        record = {
            'engine': ENGINE,
            'version': wiki.__version__,
            'build': _head(pathlib.Path(wiki.__file__).parent.parent),
        }
        with open(trace, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(record) + '\n')
    sys.argv = ['wiki', *sys.argv[1:]]
    cli()


# ------ helper functions


def _head(checkout: pathlib.Path) -> str:
    """Return what ``HEAD`` of the checkout at ``checkout`` holds.

    A detached checkout holds its commit, which is the build id. Any other
    answer -- a branch reference, or nothing for a package outside a
    checkout -- is no commit, so the harness refuses the side.
    """
    marker = checkout / '.git'
    # a linked worktree keeps its HEAD in the folder its .git file names
    if marker.is_file():
        text = marker.read_text(encoding='utf-8')
        marker = checkout / text.removeprefix('gitdir:').strip()
    head = marker / 'HEAD'
    if not head.is_file():
        return ''
    return head.read_text(encoding='utf-8').strip()


if __name__ == '__main__':
    main()
