"""Functions for generating the fixture families.

A fixture family is a small checkout, written from nothing, whose wiki holds
one kind of input the corpora lack: a malformed frontmatter block, a name
that is not ASCII, a symlink, control bytes. Each family is a converged
wiki, as the Python engine writes one, with the family's own files added
the way a person would add them: raw, with no row in any index. So the
read-only commands report on input an engine did not write, and ``update``
has to take it in.

A family is a corpus of the matrix with stages of its own, whose commands
name the family's pages. The generator gives the same bytes and modification
times on every host, and the same modes under any umask; a symlink has the
mode its platform gives one. A repository family has the same commit wherever
git stores the names as they are written: on macOS git composes a decomposed
name, so the commit of the names family is another there.
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import posixpath
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from typing import Optional, Union

from .matrix import _FAILS, Case, Stage, _case, _named
from .runner import WIKI_SETTINGS, Corpus

__all__ = []

#: what a family may need of the file system it is written to: names that
#: differ only in case, and names that are no valid UTF-8
NEEDS = ('case', 'bytes')

# the wiki root of every family, below its checkout
_ROOT = 'wiki'
# the instant every path of a family was last modified at, and its commit
# was made at, in seconds since the epoch: 2026-01-02T03:04:05Z
_WRITTEN = 1_767_323_045
# that instant as the default timestamp policy renders it
_STAMP = '2026-01-02T03:04:05Z'
# the umask every path of a family is made under, whatever the caller's
_UMASK = 0o022
# the identity and the dates of a family's one commit
_COMMIT = {
    'GIT_AUTHOR_NAME': 'parity',
    'GIT_AUTHOR_EMAIL': 'parity@example.invalid',
    'GIT_AUTHOR_DATE': f'{_WRITTEN} +0000',
    'GIT_COMMITTER_NAME': 'parity',
    'GIT_COMMITTER_EMAIL': 'parity@example.invalid',
    'GIT_COMMITTER_DATE': f'{_WRITTEN} +0000',
}
# the bytes of the control family: a color sequence, a title sequence that
# its bell ends, a unit separator and a delete
_COLOR = '\x1b[31m'
_TITLE = '\x1b]0;parity\x07'
_UNIT = '\x1f'
_DELETE = '\x7f'
# the variables of the second run of the control family's commands: each
# one changes what some terminal library writes to a pipe
_FORCED = {'NO_COLOR': '1', 'CLICOLOR_FORCE': '1', 'TERM': 'dumb'}
# how many files the plain family holds in its excluded folder: their names
# together are larger than any pipe buffer in use, so an engine that hands
# them to a git that has already ended must survive the closed pipe
_BULK = 5_001
# how deep the block of the `deep` page nests its collections: one past what
# the Python engine reads
_DEPTH = 101
# two bytes that are no valid UTF-8 in a name
_E9 = b'\xe9'
_FF = b'\xff'

# what the block of each page of the frontmatter family holds, by the page's
# name: every way a block can fail to be a mapping of fields, as the reader
# of the Python engine tells them apart, and then the well-formed spellings
# that only its line readers and writers meet
_BLOCKS = {
    'empty': '',
    'sequence': '- one\n- two',
    'scalar': 'only a sentence',
    'nonscalar-key': '[one, two]: three\ndesc: A key that is a sequence.',
    'duplicate-key': 'desc: The first desc.\ndesc: The second desc.',
    'alias-key': 'desc: &text An anchored desc.\n*text : again',
    'nested-duplicate': 'desc: A nested mapping.\nmeta:\n  key: one\n  key: two',
    'deep': f'desc: Nested too deep.\nmeta: {"[" * _DEPTH}{"]" * _DEPTH}',
    'mapping-value': 'desc: A colon: inside a plain value.',
    'missing-space': 'title:Joined\ndesc: A key with no space.',
    'bad-token': 'desc: @starts with a reserved character.',
    'tab': 'desc: A tab line follows.\n\t',
    'open-flow': 'desc: An open flow sequence.\nsources: [',
    'no-key': ': a value with no key',
    'duplicate-anchor': 'desc: &one An anchor used twice.\ntitle: &one Twice',
    'undefined-alias': 'desc: *nowhere',
    'control': 'desc: A control\x01 character.',
    'bad-escape': 'desc: "An escape past the last code point \\U00110000."',
    'surrogate': 'desc: "A lone surrogate \\uD800."',
    'late-bom': 'desc: A byte order mark opens the next line.\n\ufefftitle: Marked',
    'second-document': 'desc: One document.\n--- !!map\ntitle: Another',
    'open-quote': 'desc: "A quote that never closes.',
    'folded': 'desc: >\n  A folded desc\n  on two lines.',
    'literal': 'desc: |\n  A literal desc.\ntags: [one, two]',
    'quoted-key': '"desc": A quoted key.\n\'title\': Quoted',
    'comments': (
        '# a comment above\ndesc: A commented block.  # a tail comment\n'
        '# a comment below\n...'
    ),
    'null-title': 'title: null\ndesc: A title reset by hand.',
    'tagged': 'desc: !!str A tagged desc.\ntitle: &anchor Anchored',
}


@dataclasses.dataclass(frozen=True)
class Link:
    """A symlink of a family's tree.

    Attributes:
        target: The text the link holds.

    """

    target: str


# what a family's tree holds at each path below its checkout: the bytes of a
# file, or a symlink
_Tree = Mapping[bytes, Union[bytes, Link]]


@dataclasses.dataclass(frozen=True)
class Family:
    """One fixture family.

    Attributes:
        name: Name of the family, and the label of its corpus.
        tree: Returns what the checkout holds, by path.
        stages: The family's own stages, which run after the matrix's.
        alone: Whether the family's stages run without the matrix's. The
            matrix is built for a wiki that ``update`` takes, and holds a
            refusal there against the side.
        repository: Whether the checkout is a git repository with the tree
            committed. A family without one is a plain folder.
        needs: What the family needs of the file system, of :data:`NEEDS`.
        entry: Id of the registry entry under which a second engine refuses
            the tree. Such a family is no differential fixture: it shows
            what v1 does there, and :func:`generate` writes it only when it
            is named.

    """

    name: str
    tree: Callable[[], _Tree]
    stages: tuple[Stage, ...] = ()
    alone: bool = False
    repository: bool = True
    needs: frozenset[str] = frozenset()
    entry: str = ''


def generate(
    folder: pathlib.Path,
    *,
    names: Optional[Sequence[str]] = None,
) -> list[Corpus]:
    """Write fixture families below the new folder ``folder``.

    Each family becomes the checkout ``<folder>/<name>``.

    Args:
        folder: New folder for the checkouts.
        names: Names of the families to write. ``None`` writes every
            differential family the file system at ``folder`` can hold.

    Returns:
        One corpus per family written, with the family's stages.

    Raises:
        ValueError: If :func:`validate` refuses ``folder``, if no family
            has one of the names, if a name is given twice, or if the file
            system cannot hold a named family.

    """
    validate(folder)
    known = {family.name: family for family in families()}
    given = list(names or ())
    unknown = [name for name in given if name not in known]
    if unknown:
        raise ValueError(
            f'No fixture family is named {unknown!r}; the families are'
            f' {sorted(known)!r}.'
        )
    twice = sorted({name for name in given if given.count(name) > 1})
    if twice:
        raise ValueError(f'Fixture families are named twice: {twice!r}.')
    # ask the file system before anything is made: the nearest folder that
    # exists lies on the one the new folder will lie on
    held = next(parent for parent in folder.resolve().parents if parent.is_dir())
    lacks = lacking(held)
    if names is None:
        chosen = [
            family
            for family in known.values()
            if (not family.entry) and not (family.needs & lacks)
        ]
    else:
        chosen = [known[name] for name in names]
        for family in chosen:
            missing = sorted(family.needs & lacks)
            if missing:
                raise ValueError(
                    f'Fixture family {family.name!r} needs a file system that'
                    f' keeps {missing!r}, and the one at {held} does not.'
                )
    folder.mkdir(parents=True)
    result = []
    for family in chosen:
        checkout = folder / family.name
        _write(family, checkout)
        corpus = Corpus(
            label=family.name,
            checkout=checkout,
            root=_ROOT,
            stages=family.stages,
            alone=family.alone,
        )
        result.append(corpus)
    return result


def validate(folder: pathlib.Path) -> None:
    """Refuse a folder that :func:`generate` cannot write to; make nothing.

    Raises:
        ValueError: If a folder above ``folder`` declares a wiki (every
            family would be refused as nested in it), if ``folder`` lies
            inside a repository (a family with none of its own would be
            read as part of it), or if ``folder`` exists.

    """
    for parent in folder.resolve().parents:
        if (parent / WIKI_SETTINGS).is_file():
            raise ValueError(
                f'Fixture folder {folder} lies below {parent}, which holds'
                f' {WIKI_SETTINGS}; choose one outside it.'
            )
        if os.path.lexists(parent / '.git'):
            raise ValueError(
                f'Fixture folder {folder} lies inside the repository {parent};'
                f' choose one outside it.'
            )
    if os.path.lexists(folder):
        raise ValueError(f'Fixture folder {folder} exists; the harness makes it new.')


def lacking(folder: pathlib.Path) -> frozenset[str]:
    """Return what the file system at ``folder`` lacks, of :data:`NEEDS`.

    A file system that folds case holds one file for two names that differ
    in case only, and one that stores names as text refuses a name that is
    no valid UTF-8.
    """
    result = set()
    with tempfile.TemporaryDirectory(dir=folder) as probe:
        base = os.fsencode(probe)
        with open(os.path.join(base, b'case'), 'wb'):
            pass
        if os.path.lexists(os.path.join(base, b'CASE')):
            result.add('case')
        try:
            with open(os.path.join(base, _FF), 'wb'):
                pass
        except OSError:
            result.add('bytes')
    return frozenset(result)


def families() -> tuple[Family, ...]:
    """Return the fixture families: the differential ones, then the refused."""
    return (
        Family(
            name='frontmatter',
            tree=_frontmatter,
            stages=_stages(
                [f'blocks/{name}.md' for name in ('empty', 'unclosed', 'crlf', 'deep')],
                ['blockword'],
            ),
        ),
        Family(
            name='names',
            tree=_names,
            stages=_stages(
                [
                    'names/caf\u00e9.md',
                    'names/cafe\u0301 decomposed.md',
                    'names/links/to-page.md',
                    'names/sidecar/X.md',
                    'names/pointers.md',
                ],
                ['nameword'],
            ),
        ),
        Family(
            name='nested',
            tree=_nested,
            stages=(Stage(name='fixture', cases=_named(_nested_cases())),),
            alone=True,
        ),
        Family(
            name='stamps-zone',
            tree=lambda: _stamps(
                {
                    'timestamp': {
                        'timezone': 'Asia/Kolkata',
                        'format': '%d/%m/%Y %H:%M:%S %z',
                    }
                },
                '02/01/2026 08:34:05 +0530',
            ),
            stages=_stages(['stamps/kept.md', 'stamps/default.md'], ['stampword']),
        ),
        Family(
            name='stamps-literal',
            tree=lambda: _stamps(
                {'timestamp': {'format': '%Y%m%d at %H.%M.%S (100%%)'}},
                '20260102 at 03.04.05 (100%)',
            ),
            stages=_stages(['stamps/kept.md', 'stamps/default.md'], ['stampword']),
        ),
        Family(
            name='control',
            tree=_control,
            stages=_stages(
                [
                    f'control/{_COLOR}red{_TITLE}{_UNIT}{_DELETE}.md',
                    'control/escaped.md',
                    'control/rows/_index.md',
                ],
                ['byteword'],
                forced=True,
            ),
        ),
        Family(
            name='plain',
            tree=_plain,
            stages=_stages(['plain/page.md'], ['plainword']),
            repository=False,
        ),
        Family(
            name='linux',
            tree=_linux,
            stages=_stages(
                ['linux/case/Page.md', 'linux/case/page.md', 'linux/pointers.md'],
                ['caseword'],
            ),
            needs=frozenset(NEEDS),
        ),
        Family(
            name='refused-page',
            tree=lambda: _refused('page'),
            needs=frozenset({'bytes'}),
            entry='D4',
        ),
        Family(
            name='refused-folder',
            tree=lambda: _refused('folder'),
            needs=frozenset({'bytes'}),
            entry='D4',
        ),
        Family(
            name='refused-pattern',
            tree=lambda: _refused('pattern'),
            needs=frozenset({'bytes'}),
            entry='D4',
        ),
        Family(
            name='refused-file',
            tree=lambda: _refused('file'),
            needs=frozenset({'bytes'}),
            entry='D4',
        ),
    )


# ------ helper functions


def _write(family: Family, checkout: pathlib.Path) -> None:
    """Write the tree of ``family`` to the new folder ``checkout``.

    Raises:
        RuntimeError: If git cannot commit the tree.

    """
    base = os.fsencode(checkout)
    tree = family.tree()
    # make every path under one umask, so its mode is not the caller's choice
    previous = os.umask(_UMASK)
    try:
        os.mkdir(base)
        for relpath, content in tree.items():
            path = os.path.join(base, relpath)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if isinstance(content, Link):
                os.symlink(os.fsencode(content.target), path)
            else:
                with open(path, 'wb') as handle:
                    handle.write(content)
    finally:
        os.umask(previous)
    # give every path one modification time, the folders after what they hold
    # NOTE: a walk lists a symlink to a folder among the folders
    for folder, folders, names in os.walk(base, topdown=False):
        for name in (*folders, *names):
            path = os.path.join(folder, name)
            os.utime(path, (_WRITTEN, _WRITTEN), follow_symlinks=False)
        os.utime(folder, (_WRITTEN, _WRITTEN))
    if family.repository:
        _commit(checkout, f'fixture family {family.name}')


def _commit(checkout: pathlib.Path, message: str) -> None:
    """Make the folder ``checkout`` a repository with one commit of all it holds.

    The commit is made under a fixed identity and date, and under no config
    but the repository's own.

    Raises:
        RuntimeError: If git cannot commit the tree.

    """
    env = {**os.environ, **_COMMIT}
    env['GIT_CONFIG_GLOBAL'] = os.devnull
    env['GIT_CONFIG_SYSTEM'] = os.devnull
    commands = (
        ['init', '--quiet', '--initial-branch', 'main'],
        ['add', '--all'],
        ['commit', '--quiet', '--message', message],
    )
    for command in commands:
        result = subprocess.run(
            ['git', '-C', f'{checkout}', *command],
            env=env,
            capture_output=True,
            text=True,
            errors='replace',
        )
        if result.returncode != 0:
            error = result.stderr.strip()
            raise RuntimeError(
                f'git {command[0]} failed (exit {result.returncode}): {error!r}'
            )


def _path(*parts: Union[str, bytes]) -> bytes:
    """Return a path below the wiki root, as a path below the checkout."""
    encoded = [
        part.encode('utf-8') if isinstance(part, str) else part for part in parts
    ]
    return b'/'.join([_ROOT.encode('ascii'), *encoded])


def _page(
    name: str,
    desc: str,
    body: str,
    *,
    tags: str = '',
    stamp: str = _STAMP,
) -> bytes:
    """Return a page as the Python engine leaves one it manages."""
    lines = ['---', f'name: {name}', f'desc: {desc}']
    if tags:
        lines.append(f'tags: {tags}')
    lines.extend([f'created: {stamp}', f'updated: {stamp}', '---', '', f'# {name}'])
    text = '\n'.join(lines) + f'\n\n{body}\n'
    return text.encode('utf-8')


def _index(
    name: str,
    desc: str,
    rows: Sequence[str],
    *,
    stamp: str = _STAMP,
) -> bytes:
    """Return an index as the Python engine leaves one it manages.

    The index of every folder but the root opens with the row of its
    parent's index.
    """
    lines = [
        '---',
        f'name: {name}',
        f'desc: {desc}',
        'tags: []',
        'sources: []',
        f'created: {stamp}',
        f'updated: {stamp}',
        '---',
        '',
        f'# {name}',
        '',
    ]
    if name != _ROOT:
        parent = posixpath.join(posixpath.dirname(name), '_index')
        lines.extend([f'[[{parent}|..]]', ''])
    for row in rows:
        lines.extend([row, ''])
    lines.extend(['***', '', f'Notes kept by hand about {name}.'])
    text = '\n'.join(lines) + '\n'
    return text.encode('utf-8')


def _raw(block: str, body: str) -> bytes:
    """Return a page a person wrote: a frontmatter block and a body."""
    return f'---\n{block}\n---\n\n{body}\n'.encode()


def _base(
    extra: str,
    desc: str,
    *,
    settings: Optional[Mapping[str, object]] = None,
    stamp: str = _STAMP,
) -> dict[bytes, Union[bytes, Link]]:
    """Return the converged wiki every family starts from.

    It holds three folders: ``core`` and ``guides``, whose pages the
    indexes list, and the folder ``extra``, which has an index, a row in
    the root index and one page. A family puts its own files there, at any
    depth. Every folder of the first two levels is one a command can
    be scoped to, which the matrix sample relies on.
    """
    text = json.dumps(settings or {}, indent=2) + '\n'
    rows = {
        'core': '[[core/_index|core/]]: The widget subsystem.',
        extra: f'[[{extra}/_index|{extra}/]]: {desc}',
        'guides': '[[guides/_index|guides/]]: Guides for the widget.',
    }
    return {
        _path(WIKI_SETTINGS): text.encode('utf-8'),
        _path('_index.md'): _index(
            _ROOT,
            'A wiki of the parity fixtures.',
            [rows[folder] for folder in sorted(rows)],
            stamp=stamp,
        ),
        _path('core/_index.md'): _index(
            'core',
            'The widget subsystem.',
            ['[[core/design|design]]: The design of the widget subsystem.'],
            stamp=stamp,
        ),
        _path('core/design.md'): _page(
            'core/design',
            'The design of the widget subsystem.',
            'The widget subsystem handles rendering and layout.',
            stamp=stamp,
        ),
        _path('guides/_index.md'): _index(
            'guides',
            'Guides for the widget.',
            [
                '[[guides/deep/_index|deep/]]: Notes on render order.',
                '[[guides/setup|setup]]: Installing the widget.',
            ],
            stamp=stamp,
        ),
        _path('guides/setup.md'): _page(
            'guides/setup',
            'Installing the widget.',
            'Run the installer to set up the widget before rendering.',
            tags='[install, widget]',
            stamp=stamp,
        ),
        _path('guides/deep/_index.md'): _index(
            'guides/deep',
            'Notes on render order.',
            ['[[guides/deep/notes|notes]]: Notes on the render order.'],
            stamp=stamp,
        ),
        _path('guides/deep/notes.md'): _page(
            'guides/deep/notes',
            'Notes on the render order.',
            'Widgets render after the installer finishes its layout pass.',
            stamp=stamp,
        ),
        _path(f'{extra}/_index.md'): _index(
            extra,
            desc,
            [f'[[{extra}/sound|sound]]: A sound page beside the odd ones.'],
            stamp=stamp,
        ),
        _path(f'{extra}/sound.md'): _page(
            f'{extra}/sound',
            'A sound page beside the odd ones.',
            'A sound page that the indexes already list.',
            stamp=stamp,
        ),
    }


def _stages(
    pages: Sequence[str],
    words: Sequence[str],
    *,
    forced: bool = False,
) -> tuple[Stage, ...]:
    """Return a family's own stages.

    One stage runs on the tree as the family wrote it: what ``lint`` and
    ``update --check`` report, each of ``pages`` as it stands, then
    ``update``, and on the tree it leaves the reports, the map, the pages
    and a match and a search for each of ``words``. ``forced`` adds the
    same stage under the variables that change what a terminal library
    writes to a pipe.
    """
    reads = [_case(['read', page]) for page in pages]
    cases = [
        _case(['lint', '--json']),
        _case(['update', '--check', '--full']),
        *reads,
        _case(['update', '--full'], writes=True),
        _case(['update', '--check']),
        _case(['lint', '--json']),
        _case(['lint', '--full']),
        _case(['map']),
        *reads,
    ]
    for word in words:
        cases.append(_case(['match', word, '--lines']))
        cases.append(_case(['search', word, '--json']))
    result = [Stage(name='fixture', cases=_named(cases))]
    if forced:
        result.append(Stage(name='fixture forced', cases=_named(cases), env=_FORCED))
    return tuple(result)


def _nested_cases() -> list[Case]:
    """Return the commands of the nested family.

    The Python engine reports a wiki declared inside the wiki, serves its
    pages, and refuses to update across it or to run from inside it.
    """
    inner = 'nested/area/inner'
    return [
        _case(['lint', '--json']),
        _case(['lint', '--full']),
        _case(['update', '--check'], codes=_FAILS),
        _case(['update'], codes=_FAILS),
        _case(['map']),
        _case(['read', 'nested/outer.md']),
        _case(['read', f'{inner}/page.md']),
        _case(['read', f'{inner}/_index.md']),
        _case(['match', 'Nestword', '--lines']),
        _case(['search', 'Nestword', '--json']),
        _case(['lint'], inside=inner, codes=_FAILS),
        _case(['map'], inside='nested/area/bare', codes=_FAILS),
    ]


def _frontmatter() -> _Tree:
    """Return the frontmatter family: every class of malformed block.

    Beside the pages of :data:`_BLOCKS` it holds the files whose trouble is
    the fences or the bytes around the block, and three indexes: one with
    a block no reader takes, one with no bytes, and one with no delimiter.
    """
    tree = _base('blocks', 'Pages whose blocks are malformed.')
    for name, block in _BLOCKS.items():
        body = f'# {name}\n\nA blockword page whose block is {name}.'
        tree[_path(f'blocks/{name}.md')] = _raw(block, body)
    text = 'A blockword page'
    files = {
        'unclosed': f'---\ndesc: A block that never closes.\n\n# unclosed\n\n{text}.\n',
        'bom': f'\ufeff---\ndesc: A byte order mark opens the file.\n---\n\n{text}.\n',
        'indented-fence': f'  ---  \ndesc: An indented fence.\n---   \n\n{text}.\n',
        'crlf': f'---\r\ndesc: Lines that end in CR LF.\r\n---\r\n\r\n{text}.\r\n',
        'bare': f'# bare\n\n{text} with no block.\n',
        'rule': f'---\n\n{text} under a rule.\n\n---\n\nAnd below a second rule.\n',
    }
    for name, content in files.items():
        tree[_path(f'blocks/{name}.md')] = content.encode('utf-8')
    tree[_path('blocks/broken/_index.md')] = _raw(
        'name: blocks/broken\ndesc: An index: with a bad block.',
        '# blocks/broken\n\n[[blocks/_index|..]]\n\n***',
    )
    tree[_path('blocks/broken/page.md')] = _raw('desc: A page.', f'{text}.')
    tree[_path('blocks/truncated/_index.md')] = b''
    tree[_path('blocks/truncated/page.md')] = _raw('desc: A page.', f'{text}.')
    tree[_path('blocks/unmarked/_index.md')] = _raw(
        'name: blocks/unmarked\ndesc: An index with no delimiter.',
        '# blocks/unmarked\n\n[[blocks/_index|..]]\n\nNotes with no delimiter above.',
    )
    tree[_path('blocks/unmarked/page.md')] = _raw('desc: A page.', f'{text}.')
    return tree


def _names() -> _Tree:
    """Return the names family: names, symlinks, siblings and link case.

    It holds names that are not ASCII, in both normal forms; names the
    naming policy rejects; symlinks to a page, to a folder, to nothing and
    out of the wiki; a page ``X.md`` beside a file, a symlink and a
    gitignored file named ``X``, and ``readme.md`` beside ``Readme``; and
    links that spell a page in another case than its file has. Whether a
    sibling or such a link is found turns on the file system, so both
    engines must ask it.
    """
    tree = _base('names', 'Names, symlinks and siblings.')
    names = (
        'caf\u00e9',
        'cafe\u0301 decomposed',
        'with space',
        'emoji \U0001f642',
        'UPPER',
        '\u00df',
        '\u65e5\u672c\u8a9e/\u30da\u30fc\u30b8',
        'pipe|bar',
        'open[bracket',
        'hash#mark',
        '.hidden',
        'twin',
        'twin/inner',
    )
    for name in names:
        body = f'# A nameword page\n\nA nameword page at {name}.'
        tree[_path(f'names/{name}.md')] = _raw('desc: A named page.', body)
    # the symlinks: to a page, to a folder, to nothing, and out of the wiki
    tree[_path('names/links/real.md')] = _raw('desc: A page beside links.', 'Real.')
    tree[_path('names/links/to-page.md')] = Link('../caf\u00e9.md')
    tree[_path('names/links/to-folder')] = Link('../\u65e5\u672c\u8a9e')
    tree[_path('names/links/dangling.md')] = Link('missing.md')
    tree[_path('names/links/outside.md')] = Link('../../../outside.txt')
    tree[b'outside.txt'] = b'A file outside the wiki.\n'
    # the siblings: a page beside a file of its name without the suffix
    for name in ('X', 'Y', 'Z', 'readme'):
        body = f'A nameword page beside a sibling of {name}.'
        tree[_path(f'names/sidecar/{name}.md')] = _raw('desc: A sidecar page.', body)
    tree[_path('names/sidecar/X')] = Link('target.txt')
    tree[_path('names/sidecar/target.txt')] = b'The target of a symlink.\n'
    tree[_path('names/sidecar/Y')] = b'A gitignored sibling.\n'
    tree[_path('names/sidecar/Z')] = b'A plain sibling.\n'
    tree[_path('names/sidecar/Readme')] = b'A sibling in another case.\n'
    tree[b'.gitignore'] = f'/{_ROOT}/names/sidecar/Y\n'.encode()
    # the links: each page spelled as its file is, and in another case
    links = (
        'guides/setup',
        'Guides/Setup',
        'GUIDES/SETUP|shouted',
        'guides/Setup#heading',
        'guides',
        'names/caf\u00e9',
        'names/Caf\u00c9',
        'names/sidecar/readme',
        'names/sidecar/Readme',
        'names/sidecar/X',
        'missing/page',
    )
    lines = '\n'.join(f'- [[{link}]]' for link in links)
    body = f'# pointers\n\nA nameword page of links.\n\n{lines}'
    tree[_path('names/pointers.md')] = _raw('desc: Links in two cases.', body)
    return tree


def _nested() -> _Tree:
    """Return the nested family: wikis declared inside the wiki.

    One inner wiki has a root index and a page; the other declares itself
    and holds a page with no index.
    """
    tree = _base('nested', 'Wikis inside the wiki.')
    tree[_path('nested/outer.md')] = _raw(
        'desc: A page of the outer wiki.', 'Nestword.'
    )
    inner = 'nested/area/inner'
    tree[_path(f'{inner}/{WIKI_SETTINGS}')] = b'{}\n'
    tree[_path(f'{inner}/_index.md')] = _index('inner', 'An inner wiki.', [])
    tree[_path(f'{inner}/page.md')] = _raw(
        'desc: A page of the inner wiki.', 'Nestword.'
    )
    bare = 'nested/area/bare'
    tree[_path(f'{bare}/{WIKI_SETTINGS}')] = b'{}\n'
    tree[_path(f'{bare}/page.md')] = _raw('desc: A page of a bare wiki.', 'Nestword.')
    tree[_path('nested/area/page.md')] = _raw('desc: A page between.', 'Nestword.')
    return tree


def _stamps(settings: Mapping[str, object], stamp: str) -> _Tree:
    """Return a stamps family: a timestamp policy of the wiki's own.

    ``stamp`` is the family's instant as the policy renders it. The pages
    hold a stamp in that form, in the default form, in no form, on one
    field only and in quotes, and one page has no block to stamp.
    """
    tree = _base(
        'stamps', 'Stamps in a format of our own.', settings=settings, stamp=stamp
    )
    blocks = {
        'kept': f'desc: Stamps as the policy writes them.\ncreated: {stamp}\nupdated: {stamp}',
        'default': f'desc: Stamps in the default form.\ncreated: {_STAMP}\nupdated: {_STAMP}',
        'garbage': 'desc: Stamps in no form.\ncreated: yesterday\nupdated: 12',
        'partial': f'desc: One stamp only.\ncreated: {stamp}',
        'quoted': f'desc: Stamps in quotes.\ncreated: "{stamp}"\nupdated: \'{stamp}\'',
    }
    for name, block in blocks.items():
        tree[_path(f'stamps/{name}.md')] = _raw(block, 'A stampword page.')
    tree[_path('stamps/bare.md')] = b'# bare\n\nA stampword page with no block.\n'
    return tree


def _control() -> _Tree:
    """Return the control family: control bytes in names, fields and rows.

    One page holds the bytes in its file name and its body, and one in its
    desc, which the index row and the map repeat. An index holds unit
    separators around and inside its rows: Python counts that byte as
    whitespace, which decides how a row is split and trimmed.
    """
    tree = _base('control', 'Control bytes.')
    held = f'{_COLOR}red{_TITLE}{_UNIT}{_DELETE}'
    body = f'# bytes\n\nA byteword page that holds {held} in its body.'
    tree[_path(f'control/{held}.md')] = _raw('desc: A page named in bytes.', body)
    block = 'desc: "Holds \\e[31mred\\e]0;parity\\a\\x1F\\x7F in its desc."'
    tree[_path('control/escaped.md')] = _raw(block, 'A byteword page.')
    rows = (
        f'[[control/rows/after|after]]:{_UNIT}A desc behind a unit separator.',
        f'{_UNIT}[[control/rows/before|before]]: A row behind a unit separator.',
        f'[[control/rows/inside|inside]]: A desc with a{_UNIT}unit separator inside.',
        f'[[control/rows/trailing|trailing]]: A desc before a unit separator.{_UNIT}',
    )
    tree[_path('control/rows/_index.md')] = _index(
        'control/rows',
        'Rows that hold unit separators.',
        rows,
    )
    for name in ('after', 'before', 'inside', 'trailing'):
        page = _page(f'control/rows/{name}', '...', 'A byteword page.')
        tree[_path(f'control/rows/{name}.md')] = page
    return tree


def _plain() -> _Tree:
    """Return the plain family: a wiki outside any repository.

    An excluded folder holds :data:`_BULK` files. An engine lists them
    all for git, and git ends at once outside a repository, before it has
    read the list.
    """
    settings = {'exclude': {'patterns': ['bulk/']}}
    tree = _base('plain', 'A wiki outside any repository.', settings=settings)
    tree[_path('plain/page.md')] = _raw('desc: A page.', 'A plainword page.')
    for number in range(_BULK):
        tree[_path(f'plain/area/bulk/file-{number:04d}.txt')] = b''
    return tree


def _linux() -> _Tree:
    """Return the linux family: names only some file systems hold.

    Two pages and two folders differ in case only. Three names are no
    valid UTF-8, each where no engine reads it as a name: inside a folder
    an exclude pattern names, inside a gitignored folder, and as a
    gitignored file in a managed folder.
    """
    settings = {'exclude': {'patterns': ['downloads/']}}
    tree = _base('linux', 'Names of a case-sensitive file system.', settings=settings)
    for name in ('case/Page', 'case/page', 'case/Twin/inner', 'case/twin/inner'):
        body = f'A caseword page at {name}.'
        tree[_path(f'linux/{name}.md')] = _raw('desc: One of two cases.', body)
    links = '\n'.join(f'- [[linux/case/{name}]]' for name in ('Page', 'page', 'PAGE'))
    tree[_path('linux/pointers.md')] = _raw('desc: Links in three cases.', links)
    tree[_path('linux/area/page.md')] = _raw('desc: A managed page.', 'Caseword.')
    tree[_path('linux/area/downloads', b'b' + _E9 + b'.bin')] = b'excluded\n'
    tree[_path('linux/area/downloads', b'p' + _E9 + b'.md')] = b'# excluded\n'
    tree[_path('linux/area/build', b'o' + _E9 + b'.o')] = b'ignored\n'
    tree[_path('linux/area', b'run' + _E9 + b'.log')] = b'ignored\n'
    tree[b'.gitignore'] = b'build/\n*.log\n'
    return tree


def _refused(placement: str) -> _Tree:
    """Return a tree with one name that is no valid UTF-8 where v1 reads it.

    ``placement`` is ``page`` (a page of a managed folder), ``folder`` (a
    managed folder), ``pattern`` (a page that only an exclude pattern
    keeps out) or ``file`` (a file of a managed folder that is no page).
    """
    settings = {'exclude': {'patterns': ['raw-*']}} if placement == 'pattern' else {}
    tree = _base('refused', 'A name v1 reads as text.', settings=settings)
    names = {
        'page': b'caf' + _E9 + b'.md',
        'folder': b'dir' + _FF + b'/inner.md',
        'pattern': b'raw-' + _E9 + b'.md',
        'file': b'data' + _E9 + b'.bin',
    }
    tree[_path('refused', names[placement])] = b'# odd\n\nA refuseword page.\n'
    return tree
