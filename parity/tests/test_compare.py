"""Test the ``parity.compare`` module.

The comparison of one command on two sides is covered through the
self-check, which doctors each aspect; this module carries what no
doctored check reaches, on runs built by hand: the two moments outside
every command, when the caches and the bytes of the counts are compared,
and the runs after the second.
"""

from __future__ import annotations

from typing import Optional

import pytest

from parity import cache, compare, matrix, side

__all__ = [
    'test_tree_that_differs_outside_every_command_is_reported',
    'test_caches_that_differ_before_the_first_command_are_reported',
    'test_counts_bytes_are_compared_until_a_side_writes_a_page',
    'test_every_later_run_is_compared_with_the_first',
    'test_read_only_command_may_make_the_folder_of_its_cache_folder',
]

# the cache folder of the wiki, as a manifest path
_FOLDER = 'tree/wiki/.wiki/cache'
# one page of a slot and its counts cache
_PATH = 'tree/wiki/page.md'
_COUNTS = f'{_FOLDER}/word_counts.json'
# the folder the cache folder goes in, and the manifest content of a folder
_HOLDER = 'tree/wiki/.wiki'
_FOLDER_MADE = ('dir', 0o755, 0, None, None)
# the manifest content two trees may hold the page with
_HELD = ('file', 0o644, 4, 'a' * 64, None)
_OTHER = ('file', 0o644, 4, 'b' * 64, None)
# the digests of two counts files that hold the same entries in the same form
_DIGEST = 'c' * 64
_OTHER_DIGEST = 'd' * 64


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
    first = _run('one')
    second = _run('two', baseline=baseline, final=_OTHER)
    found = compare.stage(stage, [first, second], folder=_FOLDER)
    assert found == [compare.Difference('query', 'two', case, 'tree', _PATH)]


def test_caches_that_differ_before_the_first_command_are_reported() -> None:
    """Caches that differ on the trees two sides start from are a difference.

    Two sides run one command that touches nothing, so no command gives
    the caches a second look: the one comparison is the one before the
    first command, and it names the ``.gitignore`` whose bytes differ.
    """
    command = matrix.Case(name='map', args=('map',))
    stage = matrix.Stage(name='query', cases=(command,))
    first = _run('one', view=_view(gitignore=b'*\n'))
    second = _run('two', view=_view(gitignore=b'*\n# changed\n'))
    found = compare.stage(stage, [first, second], folder=_FOLDER)
    expected = compare.Difference(
        stage='query',
        run='two',
        case=compare.BASELINE,
        aspect='cache',
        detail='.gitignore',
    )
    assert found == [expected]


@pytest.mark.parametrize(
    argnames=('base', 'touched', 'cases'),
    argvalues=[
        (None, (), [compare.BASELINE]),
        ('write', (), []),
        (None, (_COUNTS,), [compare.BASELINE, 'update']),
        (None, (_PATH,), [compare.BASELINE]),
    ],
    ids=['snapshot', 'continued', 'cache-write', 'page-write'],
)
def test_counts_bytes_are_compared_until_a_side_writes_a_page(
    base: Optional[str],
    touched: tuple[str, ...],
    cases: list[str],
) -> None:
    """The bytes of the counts are compared only while they must be equal.

    Two sides hold the same counts entries in the same form, in files
    whose bytes differ. On a snapshot the bytes differ before the first
    command, and again after a command that touched only the cache
    folder. They are not compared on a tree another stage wrote, nor
    after a command that touched a page: each side stamped its pages at
    its own moment, so the entries agree while the bytes may not.
    """
    command = matrix.Case(name='update', args=('update',), writes=True)
    stage = matrix.Stage(name='query', cases=(command,), base=base)
    first = _run('one', view=_view(counts_raw=_DIGEST), touched=touched)
    second = _run('two', view=_view(counts_raw=_OTHER_DIGEST), touched=touched)
    found = compare.stage(stage, [first, second], folder=_FOLDER)
    expected = [
        compare.Difference('query', 'two', case, 'cache', 'word_counts.json: bytes')
        for case in cases
    ]
    assert found == expected


def test_every_later_run_is_compared_with_the_first() -> None:
    """A third run that differs from the first is reported under its name.

    A stage that continues from another has one run per pair of sides,
    and a difference may show only where one engine reads the caches the
    other wrote: the second run agrees with the first, and the third
    starts from other bytes at one path.
    """
    command = matrix.Case(name='map', args=('map',))
    stage = matrix.Stage(name='cross', cases=(command,), base='write')
    runs = [
        _run('one on one'),
        _run('one on two'),
        _run('two on one', baseline=_OTHER, final=_OTHER),
    ]
    found = compare.stage(stage, runs, folder=_FOLDER)
    expected = compare.Difference(
        stage='cross',
        run='two on one',
        case=compare.BASELINE,
        aspect='tree',
        detail=_PATH,
    )
    assert found == [expected]


@pytest.mark.parametrize(
    argnames=('held', 'made', 'wrote'),
    argvalues=[
        (False, _FOLDER_MADE, []),
        (True, _FOLDER_MADE, [_HOLDER]),
        (False, _HELD, [_HOLDER]),
    ],
    ids=['made', 'held', 'file'],
)
def test_read_only_command_may_make_the_folder_of_its_cache_folder(
    held: bool,
    made: tuple,
    wrote: list[str],
) -> None:
    """A read-only command on a wiki with no ``.wiki`` folder may make one.

    Its cache folder has to go somewhere, so the folder above it is the
    one path outside the caches such a command may touch, and only to
    make it as a folder. A command that touches a ``.wiki`` folder the
    tree already held, or puts a file there, wrote outside the caches.
    """
    command = matrix.Case(name='map', args=('map',))
    stage = matrix.Stage(name='query', cases=(command,))
    baseline = {_PATH: _HELD, **({_HOLDER: _FOLDER_MADE} if held else {})}
    record = compare.Record(
        outcome=side.Outcome(stdout=b'', stderr=b'', code=0),
        touched=(_HOLDER, _FOLDER, _COUNTS),
        changes={_HOLDER: made, _FOLDER: _FOLDER_MADE, _COUNTS: _HELD},
        view=_view(),
    )
    final = {**baseline, **record.changes}
    run = compare.Run('one', baseline, _view(), (record,), final)
    found = compare.stage(stage, [run], folder=_FOLDER)
    assert found == [
        compare.Difference('query', 'one', 'map', 'wrote', relpath) for relpath in wrote
    ]


# ------ helpers


def _view(
    *,
    gitignore: Optional[bytes] = None,
    counts_raw: Optional[str] = None,
) -> cache.View:
    """Build the view of caches that hold nothing but what the arguments name."""
    return cache.View(
        files={},
        gitignore=gitignore,
        counts=None,
        counts_form=None,
        counts_raw=counts_raw,
        search_signature=(),
        search_meta=None,
        search_schema={},
        search_files={},
        search_stamps={},
        search_rows={},
    )


def _run(
    name: str,
    *,
    baseline: tuple = _HELD,
    final: tuple = _HELD,
    view: Optional[cache.View] = None,
    touched: tuple[str, ...] = (),
) -> compare.Run:
    """Build the run of one command that printed nothing and changed no content.

    The caches hold ``view`` before and after the command, which touched
    the paths of ``touched`` and left their content as it was.
    """
    if view is None:
        view = _view()
    outcome = side.Outcome(stdout=b'', stderr=b'', code=0)
    record = compare.Record(outcome=outcome, touched=touched, changes={}, view=view)
    return compare.Run(
        name=name,
        baseline={_PATH: baseline},
        view=view,
        records=(record,),
        final={_PATH: final},
    )
