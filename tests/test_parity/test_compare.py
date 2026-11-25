"""Test the ``parity.compare`` module.

The comparison of one command on two sides is covered through the
self-check, which doctors each aspect; this module carries the two
moments outside every command, on runs built by hand.
"""

from __future__ import annotations

import pytest

from parity import cache, compare, matrix, side

__all__ = ['test_tree_that_differs_outside_every_command_is_reported']

# one path of a slot, and the manifest content two trees may hold it with
_PATH = 'tree/wiki/page.md'
_HELD = ('file', 0o644, 4, 'a' * 64, None)
_OTHER = ('file', 0o644, 4, 'b' * 64, None)


@pytest.mark.parametrize(
    argnames=('baseline', 'case'),
    argvalues=[(_OTHER, compare.BASELINE), (_HELD, compare.END)],
    ids=['baseline', 'end'],
)
def test_tree_that_differs_outside_every_command_is_reported(
    baseline: tuple,
    case: str,
) -> None:
    """A difference no command made is reported at the moment it shows.

    Two sides run one command that changes nothing, and the second side's
    tree ends with other bytes at one path. A tree that held them before
    the first command differs at the baseline; a tree that started equal
    and changed outside the command does not hold what its commands left.
    """
    command = matrix.Case(name='map', args=('map',))
    stage = matrix.Stage(name='query', cases=(command,))
    first = _run('one', baseline=_HELD, final=_HELD)
    second = _run('two', baseline=baseline, final=_OTHER)
    found = compare.stage(stage, [first, second], folder='tree/wiki/.wiki/cache')
    assert found == [compare.Difference('query', 'two', case, 'tree', _PATH)]


# ------ helpers


def _run(name: str, *, baseline: tuple, final: tuple) -> compare.Run:
    """Build the run of one command that printed and changed nothing."""
    view = cache.View(
        files={},
        gitignore=None,
        counts=None,
        counts_form=None,
        counts_raw=None,
        search_signature=(),
        search_meta=None,
        search_schema={},
        search_files={},
        search_stamps={},
        search_rows={},
    )
    outcome = side.Outcome(stdout=b'', stderr=b'', code=0)
    record = compare.Record(outcome=outcome, touched=(), changes={}, view=view)
    return compare.Run(
        name=name,
        baseline={_PATH: baseline},
        view=view,
        records=(record,),
        final={_PATH: final},
    )
