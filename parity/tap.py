"""The notice tap: the kinds one tree shows v1, as one JSON object.

usage: <a v1 venv's python> -P tap.py ROOT

Runs ``lint`` and then ``update`` on the wiki at ``ROOT`` inside the
interpreter, the way the command line runs them, and prints the kinds they
met: ``issues`` holds the kind of each issue ``lint`` returned, and
``notices`` the kind of each notice either call emitted, both sorted. A
command line prints the text of a notice and not its kind, so the kinds are
read where the engine still holds them. ``restamped`` holds the path of each
file ``update`` restamped as edited since its stamp, relative to the root.
``refused`` is the text of the error ``update`` raised, or null.

``update`` writes, so the tap runs on a tree of its own that nothing is
compared on. Run with ``-P`` so the script's folder stays off ``sys.path``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

__all__ = [
    'main',
]


def main() -> None:
    """Print the kinds ``lint`` and ``update`` meet on the wiki the command names."""
    from wiki.cli.utils import resolve_wiki

    _, root = sys.argv
    notices = []
    restamped = []

    def _capture(event: Any, **kwargs: Any) -> Any:
        """Keep the kind of one notice, as the ``lint --json`` document spells it."""
        kind = event.name.removesuffix('_EVENT').lower()
        notices.append(kind)
        if kind == 'restamp':
            restamped.append(event.path)
        return event

    wiki = resolve_wiki(root)
    wiki.on_notice = _capture
    issues = [issue.kind for issue in wiki.lint()]
    refused = None
    try:
        wiki.update()
    except ValueError as e:
        refused = f'{e}'
    kinds = {
        'issues': sorted(set(issues)),
        'notices': sorted(set(notices)),
        'restamped': sorted(restamped),
        'refused': refused,
    }
    print(json.dumps(kinds))


if __name__ == '__main__':
    main()
