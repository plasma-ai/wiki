"""Test the ``parity.perturb`` module.

The runs of a perturbation are covered through the command line; this module
carries what a perturbation owes a run: the same tree every time for one
seed, every operator on every tree, no write out of its checkout, the kinds
its changes name, the edits v1 restamps and the ones it leaves, a page its
tag finds, the two commands v1 refuses on a folder under a denied name, the
three trees its stages run on, and the small trees and the seeds of a loop.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import sys
import zoneinfo

import pytest

from parity import fixtures, kinds, manifest, matrix, perturb, replay, runner, side

from .conftest import NOW, _git, _needs_git

__all__ = [
    'test_one_seed_gives_one_perturbation',
    'test_operators_draw_only_what_the_indexes_list',
    'test_index_that_is_a_symlink_is_never_written',
    'test_write_that_leads_out_of_the_checkout_is_refused',
    'test_perturbed_trees_show_the_kinds_their_changes_name',
    'test_operator_alone_shows_every_kind_it_names',
    'test_edit_at_a_time_its_stamp_covers_is_not_restamped',
    'test_time_a_stamp_covers_outlasts_later_writes',
    'test_page_stamped_since_its_commit_names_no_restamp',
    'test_search_by_the_tag_that_finds_nothing_is_a_finding',
    'test_matrix_expects_what_v1_does_with_a_denied_name',
    'test_stages_of_a_perturbation_run_on_its_three_trees',
    'test_kind_list_that_disagrees_with_the_operators_is_refused',
    'test_kind_that_no_tree_shows_is_a_finding',
    'test_loop_runs_on_the_example_and_the_families_update_takes',
    'test_wiki_of_a_corpus_is_copied_into_a_repository_of_its_own',
    'test_seed_list_that_breaks_a_rule_is_refused',
]

# a side that prints whether the wiki it is pointed at has a root index, and
# whether the file its variable names holds a conflict marker
_PROBE = """
import os, sys
root = sys.argv[sys.argv.index('--path') + 1]
indexed = os.path.isfile(os.path.join(root, '_index.md'))
with open(os.path.join(root, os.environ['MARKED']), encoding='utf-8') as handle:
    marked = '<<<<<<< ' in handle.read()
sys.stdout.write(f'{indexed} {marked}')
"""
# a home that is a test of v1, as the kind list names one
_HELD = 'tests/test_core/test_plan.py::test_update_preserves_concurrent_edit'
# the form of a stamp under the default timestamp policy
_DEFAULT = '%Y-%m-%dT%H:%M:%SZ'
# timestamp policies of a wiki's own: an offset in every stamp, and a stamp
# that names a day, which is read in the policy's zone
_POLICIES = {
    'default': {},
    'zone': {'timezone': 'Asia/Kolkata', 'format': '%d/%m/%Y %H:%M:%S %z'},
    'day': {'timezone': 'America/New_York', 'format': '%Y-%m-%d'},
}


@_needs_git
def test_one_seed_gives_one_perturbation(tmp_path: pathlib.Path) -> None:
    """Two perturbations of one tree under one seed leave the same tree.

    Every file has the same bytes, mode and modification time, and every
    symlink the same target, and what the perturbation reports is the
    same. Another seed leaves another tree. Every operator changes a tree
    with a repository at least once, and on a tree with none the one that
    needs a repository changes nothing. No change names a kind its
    operator does not.
    """
    stands = tmp_path / 'stands'
    held = []
    done = []
    for name, seed in (('one', 5), ('two', 5), ('other', 6)):
        (control, plain), _ = perturb.trees(tmp_path / name, names=['control', 'plain'])
        for corpus in (control, plain):
            done.append(
                perturb.apply(
                    corpus.checkout,
                    root=corpus.root,
                    seed=seed,
                    stands=stands,
                )
            )
        entries = manifest.scan(control.checkout, skip=frozenset({'.git'}))
        held.append(
            {
                relpath: (*entry.content, entry.mtime_ns)
                for relpath, entry in entries.items()
                if entry.kind != 'dir'
            }
        )
    assert held[0] == held[1]
    assert held[0] != held[2]
    assert done[0] == done[2]
    assert done[0] != done[4]
    # every operator applies, but the one a tree without a repository cannot take
    control, plain, *_ = done
    changing = [operator.name for operator in perturb.operators() if operator.change]
    assert control.repository
    assert [name for name in changing if not control.applied[name]] == []
    assert not plain.repository
    assert [name for name in changing if not plain.applied[name]] == ['gitignored-row']
    named = perturb.targets()
    for family in kinds.FAMILIES:
        assert set(control.expected[perturb.EDITED][family]) <= named[family]
    assert 'gitignore_skip' in control.expected[perturb.EDITED]['notices']
    assert 'gitignore_skip' not in plain.expected[perturb.EDITED]['notices']
    assert 'restamp' not in plain.expected[perturb.EDITED]['notices']


@_needs_git
def test_operators_draw_only_what_the_indexes_list(tmp_path: pathlib.Path) -> None:
    """A file with no row in the index of its folder is never changed.

    A wiki gives a page it excludes no row, and a change made there would
    show nothing. Such a page has the engine's form here, and a second one
    holds a byte that is no valid UTF-8: under no seed does an operator
    change either, add to their folder's rows for them, or end on the byte.
    """
    (corpus,), _ = perturb.trees(tmp_path / 'trees', names=['control'])
    root = corpus.checkout / corpus.root
    unlisted = {
        'unlisted.md': fixtures._page('core/unlisted', 'A desc.', 'A body.'),
        'raw.md': b'---\nname: core/raw\ndesc: A desc.\n---\n\n\xff\n',
    }
    for name, content in unlisted.items():
        (root / 'core' / name).write_bytes(content)
    for seed in range(12):
        checkout = tmp_path / f'{seed}'
        shutil.copytree(corpus.checkout, checkout, symlinks=True)
        done = perturb.apply(checkout, root=corpus.root, seed=seed, stands=root)
        changed = {
            pathlib.PurePosixPath(relpath).name
            for paths in done.applied.values()
            for relpath in paths
        }
        # a renamed folder takes the two files along, as they are
        for name, content in unlisted.items():
            (path,) = (checkout / corpus.root).rglob(name)
            assert path.read_bytes() == content
            assert name not in changed


@_needs_git
def test_index_that_is_a_symlink_is_never_written(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """A folder whose index is a symlink is left alone under every seed.

    A clone keeps the target of a symlink as it is written, so a write to
    such an index would change the file the link names, in the checkout the
    clone was taken from. The folder has a row in the index above it and a
    page of its own; no operator changes the index, the file it points at
    or the page.
    """
    outside = tmp_path / 'outside.md'
    row = '[[linked/page|page]]: A page.'
    outside.write_bytes(fixtures._index('linked', 'A linked index.', [row]))
    held = (outside.read_bytes(), outside.stat().st_mtime_ns)
    source = tmp_path / 'source'
    shutil.copytree(corpus.checkout, source, symlinks=True)
    root = source / corpus.root
    (root / 'linked').mkdir()
    (root / 'linked' / '_index.md').symlink_to(outside)
    page = fixtures._page('linked/page', 'A page.', 'A body.')
    (root / 'linked' / 'page.md').write_bytes(page)
    lines = (root / '_index.md').read_text(encoding='utf-8').split('\n')
    at = lines.index('***')
    lines[at:at] = ['[[linked/_index|linked]]: A linked index.', '']
    (root / '_index.md').write_text('\n'.join(lines), encoding='utf-8')
    for seed in range(12):
        checkout = tmp_path / f'{seed}'
        shutil.copytree(source, checkout, symlinks=True)
        done = perturb.apply(checkout, root=corpus.root, seed=seed, stands=root)
        changed = {relpath for paths in done.applied.values() for relpath in paths}
        assert not {relpath for relpath in changed if relpath.startswith('linked')}
        assert (outside.read_bytes(), outside.stat().st_mtime_ns) == held
        assert (checkout / corpus.root / 'linked' / 'page.md').read_bytes() == page


@_needs_git
@pytest.mark.parametrize(
    argnames='linked',
    argvalues=['root', 'settings', 'settings folder', 'ignore file'],
)
def test_write_that_leads_out_of_the_checkout_is_refused(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    linked: str,
) -> None:
    """A perturbation writes no file through a link that leaves its checkout.

    The wiki root, the settings file, the folder that holds it and the
    ignore file at the top of the checkout are each a symlink to a place
    outside it here. The perturbation is refused, and what lies outside
    stays as it was: a rename of the root's folders, a pattern in the
    settings and a pattern in the ignore file would each have landed there.
    """
    checkout = tmp_path / 'checkout'
    shutil.copytree(corpus.checkout, checkout, symlinks=True)
    relpath = {
        'root': corpus.root,
        'settings': f'{corpus.root}/{runner.WIKI_SETTINGS}',
        'settings folder': f'{corpus.root}/.wiki',
        'ignore file': '.gitignore',
    }[linked]
    inside = checkout / relpath
    outside = tmp_path / 'outside'
    if os.path.lexists(inside):
        inside.rename(outside)
    else:
        outside.write_bytes(b'*.log\n')
    inside.symlink_to(outside)
    held = manifest.scan(tmp_path, skip=frozenset({'checkout'}))
    with pytest.raises(ValueError, match='leads out of'):
        perturb.apply(checkout, root=corpus.root, seed=0, stands=checkout / corpus.root)
    assert manifest.scan(tmp_path, skip=frozenset({'checkout'})) == held


@_needs_git
def test_perturbed_trees_show_the_kinds_their_changes_name(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
) -> None:
    """v1 meets every kind a change names, on the tree the change is made on.

    The tap runs on the three trees of a perturbation: the tree the
    operators leave, the tree with no index and the tree with conflict
    markers, on which alone ``update`` refuses. Every kind the operators
    name shows on its tree, so the perturbation has no finding. A named
    kind that a tree does not show is one.
    """
    plan, done = perturb.prepare(corpus, work=tmp_path / 'work', seed=5, loop=True)
    findings, shown = perturb.check(plan, done, engine, now=NOW)
    assert findings == []
    assert list(shown) == [perturb.EDITED, 'unindexed', 'conflicted']
    named = perturb.targets()
    met = {
        family: {kind for tree in shown.values() for kind in tree[family]}
        for family in kinds.FAMILIES
    }
    for family in kinds.FAMILIES:
        assert named[family] <= met[family]
    assert 'missing_index' in shown['unindexed']['issues']
    assert 'conflict_markers' in shown['conflicted']['issues']
    refused = {name: bool(tree['refused']) for name, tree in shown.items()}
    assert refused == {perturb.EDITED: False, 'unindexed': False, 'conflicted': True}
    # a kind no command of the tree meets is reported
    expected = {
        **done.expected,
        'unindexed': {'notices': ('write_skip',), 'issues': ()},
    }
    doctored = dataclasses.replace(done, expected=expected)
    findings, _ = perturb.check(plan, doctored, engine, now=NOW)
    assert findings == [
        replay.Finding(
            kind='kind',
            subject='example seed 5: notice write_skip',
            detail='the tree unindexed does not show it',
        )
    ]


@_needs_git
@pytest.mark.parametrize(
    argnames='name',
    argvalues=[operator.name for operator in perturb.operators() if operator.change],
)
def test_operator_alone_shows_every_kind_it_names(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    """Each operator shows v1 the kinds it names with no other operator's help.

    Several operators name one kind, and on a tree that all of them changed
    one that does not cause the kind would pass on another's change. Here
    one operator applies, on a converged wiki that shows none of the kinds
    by itself: its change names every kind the operator names, and the tap
    meets each of them. The operator that tags a page applies too, since a
    stage searches by its tag; it names no kind.
    """
    alone = tuple(
        operator
        for operator in perturb.operators()
        if (operator.name in (name, 'add-tags')) or operator.stage
    )
    monkeypatch.setattr(perturb, 'operators', lambda: alone)
    plan, done = perturb.prepare(corpus, work=tmp_path / 'work', seed=5, loop=True)
    (operator,) = [operator for operator in alone if operator.name == name]
    expected = done.expected[perturb.EDITED]
    assert set(expected['notices']) == set(operator.notices)
    assert set(expected['issues']) == set(operator.issues)
    shown = runner.tap(plan, engine, now=NOW)
    assert set(operator.notices) <= set(shown['notices'])
    assert set(operator.issues) <= set(shown['issues'])


@_needs_git
@pytest.mark.parametrize(argnames='policy', argvalues=list(_POLICIES))
def test_edit_at_a_time_its_stamp_covers_is_not_restamped(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
    policy: str,
) -> None:
    """v1 restamps an edit made far from the page's stamp, and no edit the stamp covers.

    v1 reads a page that git holds with its stamp as edited since, unless
    the page was last written in the period its stamp names or in the
    minute after: that write is the one that stamped it. The perturbation
    leaves pages on both sides of the rule, under the wiki's own form and
    zone of a stamp. ``update`` restamps each page it left far from its
    stamp and none it left at a time the stamp covers, and a page listed as
    covered that ``update`` restamps is a finding.
    """
    checkout = _stamped(tmp_path / 'checkout', corpus, _POLICIES[policy])
    stamped = dataclasses.replace(corpus, checkout=checkout)
    plan, done = perturb.prepare(stamped, work=tmp_path / 'work', seed=5, loop=True)
    findings, shown = perturb.check(plan, done, engine, now=NOW)
    assert findings == []
    edited = set(done.applied['dirty-edit'])
    restamped = set(shown[perturb.EDITED]['restamped'])
    assert done.kept
    assert set(done.kept) < edited
    assert not (restamped & set(done.kept))
    assert restamped & edited
    assert 'restamp' in done.expected[perturb.EDITED]['notices']
    # a page that is restamped while the perturbation lists it as covered
    (far, *_) = sorted(restamped & edited)
    doctored = dataclasses.replace(done, kept=(far,))
    findings, _ = perturb.check(plan, doctored, engine, now=NOW)
    assert [(finding.kind, finding.subject) for finding in findings] == [
        ('stamp', f'example seed 5: {far}'),
        ('stamp', f'example seed 5: {far}'),
    ]


@_needs_git
def test_time_a_stamp_covers_outlasts_later_writes(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """A page left at a time its stamp covers stays there whatever is added to it.

    The operators that add a link or a marker to a body draw among all
    pages, and a later write at one of the two fixed times would turn the
    edit into one made since the stamp. Under every seed here each covered
    page ends within the minute its stamp opens, and under some seed a
    later operator wrote to one.
    """
    root = corpus.checkout / corpus.root
    again = 0
    for seed in range(12):
        checkout = tmp_path / f'{seed}'
        shutil.copytree(corpus.checkout, checkout, symlinks=True)
        done = perturb.apply(checkout, root=corpus.root, seed=seed, stands=root)
        for page in done.kept:
            path = checkout / corpus.root / page
            (line,) = [
                line
                for line in path.read_text(encoding='utf-8').split('\n')
                if line.startswith('updated: ')
            ]
            stamp = dt.datetime.strptime(line.removeprefix('updated: '), _DEFAULT)
            since = path.stat().st_mtime - stamp.replace(tzinfo=dt.UTC).timestamp()
            assert 0 <= since < 60
            again += sum(paths.count(page) for paths in done.applied.values()) > 1
    assert again


@_needs_git
def test_page_stamped_since_its_commit_names_no_restamp(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
) -> None:
    """An edit names a restamp only on a page git holds with the stamp it has.

    v1 restamps a page that differs from the committed one while its stamp
    is still that one's. Here every page of the checkout was stamped again
    and not committed, as a checkout in use may hold one. No change names
    a restamp, v1 makes none, and the perturbation has no finding.
    """
    checkout = tmp_path / 'checkout'
    shutil.copytree(corpus.checkout, checkout, symlinks=True)
    for page in (checkout / corpus.root).rglob('*.md'):
        text = page.read_text(encoding='utf-8')
        stamped = text.replace('\nupdated: 2026', '\nupdated: 2027')
        assert stamped != text
        page.write_text(stamped, encoding='utf-8')
    stamped = dataclasses.replace(corpus, checkout=checkout)
    plan, done = perturb.prepare(stamped, work=tmp_path / 'work', seed=5, loop=True)
    assert 'restamp' not in done.expected[perturb.EDITED]['notices']
    findings, shown = perturb.check(plan, done, engine, now=NOW)
    assert findings == []
    assert 'restamp' not in shown[perturb.EDITED]['notices']


@_needs_git
def test_search_by_the_tag_that_finds_nothing_is_a_finding(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
) -> None:
    """A run in which no side finds a page by the perturbation's tag has a finding.

    The operator that tags pages names no kind, so nothing else would
    notice that it had stopped working. Each mode searches by the tag: a
    loop in its short stage, and a corpus in the matrix, whose own sample
    could have drawn a tag the wiki held before. The search finds a page of
    the perturbed wiki, and it is a finding where the tagged page does not
    hold the word the search names.
    """
    loop, done = perturb.prepare(corpus, work=tmp_path / 'loop', seed=5, loop=True)
    word, tag = done.tagged
    assert tag == perturb.TAG
    whole, _ = perturb.prepare(corpus, work=tmp_path / 'corpus', seed=5, loop=False)
    (query,) = [stage for stage in whole.stages if stage.name == 'query']
    assert ('search', word, '--tag', tag) in [case.args for case in query.cases]
    result = runner.run(loop, [engine], now=NOW, only=[perturb.EDITED])
    assert perturb.unfound(done, result.report) == []
    doctored = dataclasses.replace(done, tagged=('absentword', tag))
    stages = perturb.stages(doctored, root=corpus.root, loop=True)
    result = runner.run(
        dataclasses.replace(loop, stages=stages),
        [engine],
        now=NOW,
        only=[perturb.EDITED],
    )
    assert perturb.unfound(doctored, result.report) == [
        replay.Finding(
            kind='tag',
            subject=f'example seed 5: edited: search absentword --tag {tag}',
            detail='no side finds a page',
        )
    ]


@_needs_git
def test_matrix_expects_what_v1_does_with_a_denied_name(
    tmp_path: pathlib.Path,
    engine: side.Side,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The matrix on a perturbed wiki expects what v1 does with a denied name.

    v1 reads a page whose name holds a denied character like any other. A
    folder under such a name comes with a page and no index: until
    ``update`` runs, ``read`` finds no entry there, and ``new`` takes no
    folder below such a name at any time. Both exit 2, and the ``new``
    writes nothing, while every other scoped command runs. Here the two
    operators that make such names apply to the example wiki, which has no
    folder of its own, so the sample draws the folder first and every page
    for ``read``. The matrix names the folder as refused and searches below
    it for the word its page holds: v1 ends every case as the case is built
    to end, and each search finds something.
    """
    denied = ('invalid-page', 'invalid-folder')
    alone = tuple(
        operator
        for operator in perturb.operators()
        if (operator.name in (*denied, 'add-tags')) or operator.stage
    )
    monkeypatch.setattr(perturb, 'operators', lambda: alone)
    (example,), _ = perturb.trees(tmp_path / 'trees', names=[perturb.EXAMPLE])
    plan, done = perturb.prepare(example, work=tmp_path / 'work', seed=5, loop=False)
    scope, *_ = plan.sample.scopes
    assert done.denied
    assert set(plan.sample.refused) == set(plan.sample.scopes) == set(done.denied)
    assert plan.sample.scoped == perturb.WORD
    pages = done.applied['invalid-page']
    assert set(pages) <= set(plan.sample.pages)
    result = runner.run(plan, [engine], now=NOW, only=['query', 'write'])
    assert result.differences == ()
    assert [stage['no_hit'] for stage in result.report['stages']] == [[], []]
    # v1 refuses the two commands on the folder, and reads each page
    exits = {
        case['name']: case['runs'][engine.name]['exit']
        for stage in result.report['stages']
        for case in stage['cases']
    }
    (new,) = [name for name in exits if name.startswith(f'new {scope}/')]
    assert (exits[f'read {scope}'], exits[new]) == (2, 2)
    assert [exits[f'read {page}'] for page in pages] == [0] * len(pages)


@_needs_git
def test_stages_of_a_perturbation_run_on_its_three_trees(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """Each stage of a perturbation gives every side the tree it is named for.

    In a loop the short stage runs on the tree the operators leave, the
    next on that tree with every index deleted, and the last on that tree
    with conflict markers in the files the perturbation chose. Outside a
    loop the stages of the matrix stand in for the short one.
    """
    plan, done = perturb.prepare(corpus, work=tmp_path / 'loop', seed=5, loop=True)
    assert [stage.name for stage in plan.stages] == [
        perturb.EDITED,
        'unindexed',
        'conflicted',
    ]
    # a side that prints what its tree holds shows what each stage ran on
    marked, _ = done.conflicts
    probe = side.Side(
        name='probe',
        argv=(sys.executable, '-c', _PROBE),
        env={'MARKED': marked},
        notice=False,
    )
    result = runner.run(plan, [probe], now=NOW)
    printed = {
        stage['name']: {
            case['runs']['probe']['stdout']['sha256'] for case in stage['cases']
        }
        for stage in result.report['stages']
    }
    assert printed == {
        perturb.EDITED: {hashlib.sha256(b'True False').hexdigest()},
        'unindexed': {hashlib.sha256(b'False False').hexdigest()},
        'conflicted': {hashlib.sha256(b'True True').hexdigest()},
    }
    plan, _ = perturb.prepare(corpus, work=tmp_path / 'corpus', seed=5, loop=False)
    assert [stage.name for stage in plan.stages] == [
        *matrix.STAGES,
        'unindexed',
        'conflicted',
    ]


@pytest.mark.parametrize(
    argnames=('kind', 'home', 'message'),
    argvalues=[
        ('write_skip', kinds.PERTURBATION, 'no operator names it'),
        ('link_add', _HELD, 'another home'),
    ],
    ids=['unnamed', 'homed twice'],
)
def test_kind_list_that_disagrees_with_the_operators_is_refused(
    kind: str,
    home: str,
    message: str,
) -> None:
    """The kinds of the perturbation are those its operators name, and no other.

    A kind with the perturbation for a home that no operator names would
    have a home nothing answers for, and a kind an operator names whose
    home is a test would have two. The committed list keeps the rule.
    """
    listed = kinds.load()
    perturb.validate(listed)
    broken = {**listed, 'notices': {**listed['notices'], kind: home}}
    with pytest.raises(ValueError, match=message):
        perturb.validate(broken)


def test_kind_that_no_tree_shows_is_a_finding() -> None:
    """A whole run holds the kind list to the kinds its trees showed.

    A kind whose home is the perturbation and that no tree of the run
    shows is a finding; a kind whose home is a test is the replay's to
    answer for.
    """
    listed = {
        'notices': {'link_add': kinds.PERTURBATION, 'write_skip': _HELD},
        'issues': {'bare_page': kinds.PERTURBATION},
    }
    one = {'notices': ['link_add'], 'issues': [], 'refused': None}
    other = {'notices': [], 'issues': ['bare_page'], 'refused': None}
    assert perturb.unshown(listed, [one, other]) == []
    assert perturb.unshown(listed, [one]) == [
        replay.Finding('kind', 'issue bare_page', 'no tree of the run shows it')
    ]


@_needs_git
def test_loop_runs_on_the_example_and_the_families_update_takes(
    tmp_path: pathlib.Path,
) -> None:
    """The small trees are the example wiki and the families ``update`` takes.

    The example is a committed checkout of the files git tracks in the
    repository's example. A family that runs alone and a tree a second
    engine refuses are no trees of the loop, and a family the file system
    cannot hold is left out and named. No tree brings a stage of its own.
    A name that is no tree, a name given twice and every tree on a file
    system that cannot hold one are refused before anything is written.
    """
    lacks = fixtures.lacking(tmp_path)
    families = [
        family for family in fixtures.families() if not (family.entry or family.alone)
    ]
    corpora, left = perturb.trees(tmp_path / 'trees')
    written = [family.name for family in families if not (family.needs & lacks)]
    assert [corpus.label for corpus in corpora] == [perturb.EXAMPLE, *written]
    assert left == [family.name for family in families if family.needs & lacks]
    assert 'nested' not in written
    assert all(not corpus.stages for corpus in corpora)
    example, *_ = corpora
    tracked = _git(example.checkout, 'ls-files').splitlines()
    assert f'{example.root}/.wiki/settings.json' in tracked
    assert f'{example.root}/_index.md' in tracked
    assert _git(example.checkout, 'status', '--porcelain') == ''
    refusals = {
        'No tree of the loop is named': ['nested'],
        'named twice': [perturb.EXAMPLE, perturb.EXAMPLE],
    }
    for message, names in refusals.items():
        with pytest.raises(ValueError, match=message):
            perturb.trees(tmp_path / 'new', names=names)
        assert not (tmp_path / 'new').exists()
    if lacks:
        with pytest.raises(ValueError, match='needs a file system'):
            perturb.trees(tmp_path / 'new', every=True)
        assert not (tmp_path / 'new').exists()


@_needs_git
def test_wiki_of_a_corpus_is_copied_into_a_repository_of_its_own(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """A loop takes the wiki of a corpus without the checkout around it.

    The copy holds the wiki at the path it has in its checkout, committed
    as it stands, and nothing else of the checkout. Its objects are packed
    before a clone is taken: git would otherwise pack a large commit in the
    background and take files away under the clone. The checkout is read
    and not written. A corpus whose wiki root is no folder is refused.
    """
    before = manifest.scan(corpus.checkout)
    carved = perturb.carve(corpus, tmp_path / 'trees')
    assert carved.root == corpus.root
    assert carved.checkout == tmp_path / 'trees' / corpus.label
    assert _git(carved.checkout, 'status', '--porcelain') == ''
    assert _git(carved.checkout, 'ls-files') == _git(corpus.checkout, 'ls-files')
    assert _git(carved.checkout, 'rev-list', '--count', 'HEAD') == '1'
    assert _git(carved.checkout, 'count-objects') == '0 objects, 0 kilobytes'
    after = manifest.scan(corpus.checkout)
    assert {relpath: entry.content for relpath, entry in after.items()} == {
        relpath: entry.content for relpath, entry in before.items()
    }
    absent = dataclasses.replace(corpus, label='absent', root='nowhere')
    with pytest.raises(ValueError, match='is not a folder'):
        perturb.carve(absent, tmp_path / 'trees')


@pytest.mark.parametrize(
    argnames=('text', 'message'),
    argvalues=[
        ('seeds = [1]\nfresh = 2\n', 'unknown key'),
        ('seeds = 3\n', 'seeds must be a list'),
        ('seeds = []\n', 'seeds must be a list'),
        ('seeds = [1, -2]\n', 'seeds must be a list'),
        ('seeds = [1, 2.0]\n', 'seeds must be a list'),
        ('seeds = [1, true]\n', 'seeds must be a list'),
        ('seeds = [4, 4]\n', 'seeds must be a list'),
    ],
    ids=['key', 'shape', 'empty', 'negative', 'fraction', 'flag', 'twice'],
)
def test_seed_list_that_breaks_a_rule_is_refused(
    tmp_path: pathlib.Path,
    text: str,
    message: str,
) -> None:
    """The committed seeds are whole numbers from zero up, each once.

    A list that keeps the rule comes back in its order, and so does the
    committed one, which every pull request runs.
    """
    path = tmp_path / 'seeds.toml'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError, match=message):
        perturb.load_seeds(path)
    path.write_text('seeds = [7, 0, 3]\n', encoding='utf-8')
    assert perturb.load_seeds(path) == (7, 0, 3)
    assert len(perturb.load_seeds()) == 20


# ------ helpers


def _stamped(
    checkout: pathlib.Path,
    corpus: runner.Corpus,
    policy: dict[str, str],
) -> pathlib.Path:
    """Copy the checkout of ``corpus`` under a timestamp policy, and return the copy.

    Every stamp is written again in the form and the zone ``policy``
    names, the settings name the policy, and the copy is committed, so git
    holds every page with the stamp it has.
    """
    shutil.copytree(corpus.checkout, checkout, symlinks=True)
    if not policy:
        return checkout
    root = checkout / corpus.root
    zone = zoneinfo.ZoneInfo(policy['timezone'])
    for path in root.rglob('*.md'):
        lines = path.read_text(encoding='utf-8').split('\n')
        for number, line in enumerate(lines):
            key, _, value = line.partition(': ')
            if key in ('created', 'updated'):
                instant = dt.datetime.strptime(value, _DEFAULT).replace(tzinfo=dt.UTC)
                stamp = instant.astimezone(zone).strftime(policy['format'])
                lines[number] = f'{key}: {stamp}'
        path.write_text('\n'.join(lines), encoding='utf-8')
    settings = json.loads((root / runner.WIKI_SETTINGS).read_text(encoding='utf-8'))
    text = json.dumps({**settings, 'timestamp': policy}, indent=2) + '\n'
    (root / runner.WIKI_SETTINGS).write_text(text, encoding='utf-8')
    _git(checkout, 'commit', '-q', '-a', '-m', 'stamps')
    return checkout
