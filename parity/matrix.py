"""The command matrix: which commands run on which tree."""

from __future__ import annotations

import dataclasses
import os
import pathlib
import random
import re
from collections.abc import Iterable, Sequence
from typing import Optional

__all__ = [
    'STAGES',
    'Case',
    'Stage',
    'Sample',
    'sample',
    'stages',
]

#: the names of the stages, in the order they run
STAGES = ('pristine', 'query', 'write', 'full', 'cross')

# the exit codes a case allows: a command that must succeed; one that may
# also report (issues, pending changes, no match); a search built to find
# nothing; a command built to fail
_DONE = frozenset({0})
_REPORTS = frozenset({0, 1})
_NOTHING = frozenset({1})
_FAILS = frozenset({2})
# how many of each kind a sample holds
_TOP_SCOPES = 4
_DEEP_SCOPES = 2
_PAGES = 6
_TERMS = 3
# a word no page holds, and the name of a page no wiki holds
_ABSENT = 'zqxjkvparity'
# the folder `new` creates
_NEW = 'paritynew'
_NEW_DESC = 'A folder the parity harness adds.'
_NEW_CONTENT = 'Written by the parity harness.'
_WORD = re.compile(r'[A-Za-z]{5,}')
_INDEX = '_index.md'


@dataclasses.dataclass(frozen=True)
class Case:
    """One wiki command of a stage.

    Attributes:
        name: Label of the case, unique in its stage.
        args: The wiki arguments, without ``--path``.
        inside: Folder below the wiki root to run from, with no ``--path``
            (empty for the root itself). ``None`` runs from a neutral
            folder with ``--path``.
        writes: Whether the command may write outside the cache folder.
        codes: The exit codes the command may end with. A side that ends
            with another one did not run the command the case is built
            for, however well the sides agree.

    """

    name: str
    args: tuple[str, ...]
    inside: Optional[str] = None
    writes: bool = False
    codes: frozenset[int] = _DONE


@dataclasses.dataclass(frozen=True)
class Stage:
    """A run of commands that each side gives one fresh tree.

    Attributes:
        name: Label of the stage.
        cases: The commands, in order.
        base: Name of the stage whose trees this one continues from: each
            side then runs on the tree and caches every side left there.
            ``None`` starts from the snapshot.
        cold: Whether the tree starts with its cache folder removed.

    """

    name: str
    cases: tuple[Case, ...]
    base: Optional[str] = None
    cold: bool = False


@dataclasses.dataclass(frozen=True)
class Sample:
    """The folders, pages and words of one wiki that the matrix names.

    Attributes:
        scopes: Folders for scoped commands, relative to the wiki root.
        pages: Pages for ``read``.
        terms: Words for ``search`` and ``match``.

    """

    scopes: tuple[str, ...]
    pages: tuple[str, ...]
    terms: tuple[str, ...]


def sample(root: pathlib.Path, *, seed: int) -> Sample:
    """Draw the matrix sample of the wiki at ``root``.

    The draw reads names and page text only, and is the same for one seed
    and one tree. Scopes are top-level folders and second-level folders;
    pages are Markdown files in folders that hold an index; terms are
    words of those pages.
    """
    rng = random.Random(seed)
    tops = _folders(root, [''])
    deeps = _folders(root, tops)
    scopes = _draw(rng, tops, _TOP_SCOPES) + _draw(rng, deeps, _DEEP_SCOPES)
    # every page of an indexed folder, in walk order
    candidates = []
    pending = ['']
    while pending:
        folder = pending.pop()
        names = sorted(os.listdir(root / folder))
        pending.extend(reversed(_folders(root, [folder])))
        if _INDEX not in names:
            continue
        for name in names:
            regular = os.path.isfile(root / folder / name)
            page = name.endswith('.md') and (name != _INDEX)
            if page and regular and not name.startswith('-'):
                candidates.append(os.path.join(folder, name))
    pages = _draw(rng, candidates, _PAGES)
    words: set[str] = set()
    for page in pages:
        text = (root / page).read_text(encoding='utf-8', errors='replace')
        words.update(_WORD.findall(text))
    terms = _draw(rng, sorted(words), _TERMS)
    return Sample(scopes=scopes, pages=pages, terms=terms)


def stages(sample: Sample) -> tuple[Stage, ...]:
    """Return the stages of the matrix for one wiki.

    Read-only commands run on the snapshot (``pristine``, ``query``).
    Mutating commands run on a tree with no caches (``write``) and on the
    snapshot (``full``). ``cross`` then runs each side on the tree and
    caches each side left after ``write``.
    """
    term = sample.terms[0] if sample.terms else _ABSENT
    other = sample.terms[-1] if sample.terms else _ABSENT
    scope = sample.scopes[0] if sample.scopes else None
    # read-only commands, from a neutral folder and from inside the wiki
    pristine = [
        _case(['lint']),
        _case(['lint', '--json']),
        _case(['lint', '--full']),
        _case(['lint', '--count']),
        _case(['update', '--check']),
        _case(['update', '--check', '--full']),
    ]
    for folder in sample.scopes:
        pristine.append(_case(['lint', folder]))
        pristine.append(_case(['update', '--check', folder]))
    pristine.append(_case(['lint'], inside=''))
    pristine.append(_case(['update', '--check'], inside=''))
    if scope is not None:
        pristine.append(_case(['lint'], inside=scope))
    # views, searches, matches and reads, on the caches the snapshot holds
    query = [
        _case(['map']),
        _case(['map', '--depth', '0']),
        _case(['map', '--depth', '1']),
        _case(['map', '--no-desc']),
        _case(['map', '--desc-limit', '20']),
        _case(['map', '--no-words']),
        _case(['map', '--stat']),
        _case(['map', '--markdown']),
        _case(['map', '--no-markdown']),
        _case(['map', '--category', '']),
        _case(['search', term]),
        _case(['search', term, '--json']),
        _case(['search', f'{term} {other}', '--json']),
        _case(['search', term, '--limit', '3']),
        _case(['search', term[:3], '--prefix', '--json']),
        _case(['search', f'{term} OR {other}', '--raw', '--json']),
        _case(['search', term, '--tag', other]),
        _case(['search', _ABSENT], codes=_NOTHING),
        _case(['search', '"', '--raw'], codes=_FAILS),
        _case(['match', term]),
        _case(['match', term, '--lines']),
        _case(['match', term, '--lineno']),
        _case(['match', term.upper(), '--ignore-case', '--lines']),
        _case(['match', term, '--all']),
        _case(['match', term, '--field', 'desc']),
        _case(['match', term, '--field', 'name,tags', '--lines']),
        _case(['match', _ABSENT], codes=_NOTHING),
        _case(['match', '('], codes=_FAILS),
        _case(['read', _INDEX]),
        _case(['read', f'{_ABSENT}.md'], codes=_FAILS),
    ]
    if scope is not None:
        query.append(_case(['map', scope]))
        query.append(_case(['search', term, scope, '--json']))
        query.append(_case(['match', r'^#+ \S', scope, '--lineno']))
        query.append(_case(['read', scope]))
    for page in sample.pages:
        query.append(_case(['read', page]))
    for page in sample.pages[:1]:
        query.append(_case(['read', page, '--lines', '0:5']))
        query.append(_case(['read', page, '--lines', '2:']))
        query.append(_case(['read', page, '--words', '3:40']))
        query.append(_case(['read', page, '--chars', '10:200']))
    # cold caches, then the writes
    new = ['--desc', _NEW_DESC, '--content', _NEW_CONTENT]
    write = [
        _case(['search', term, '--json']),
        _case(['map']),
        _case(['update'], writes=True),
        _case(['update', '--check']),
        _case(['new', _NEW, *new], writes=True),
    ]
    if scope is not None:
        write.append(_case(['new', f'{scope}/{_NEW}', *new], writes=True))
    write.extend(_case(args) for args in (['update', '--check'], ['lint'], ['map']))
    write.append(_case(['search', term]))
    full = [
        _case(['update', '--check', '--full']),
        _case(['update', '--full'], writes=True),
    ]
    cross = [
        _case(['map']),
        _case(['search', term, '--json']),
        _case(['lint']),
        _case(['update', '--check']),
        _case(['update'], writes=True),
    ]
    return (
        Stage(name='pristine', cases=_named(pristine)),
        Stage(name='query', cases=_named(query)),
        Stage(name='write', cases=_named(write), cold=True),
        Stage(name='full', cases=_named(full)),
        Stage(name='cross', cases=_named(cross), base='write'),
    )


# ------ helper functions


def _folders(root: pathlib.Path, parents: Iterable[str]) -> list[str]:
    """Return the plain folders directly below ``parents``, skipping dot names."""
    result = []
    for parent in parents:
        for name in sorted(os.listdir(root / parent)):
            path = root / parent / name
            if name.startswith(('.', '-')) or os.path.islink(path):
                continue
            if os.path.isdir(path):
                result.append(os.path.join(parent, name))
    return result


def _draw(rng: random.Random, population: Sequence[str], count: int) -> tuple[str, ...]:
    """Return ``count`` members of ``population``, or all of a smaller one."""
    if len(population) <= count:
        return tuple(population)
    return tuple(rng.sample(population, count))


def _case(
    args: Sequence[str],
    *,
    inside: Optional[str] = None,
    writes: bool = False,
    codes: Optional[frozenset[int]] = None,
) -> Case:
    """Build a case named after its arguments and where it runs.

    Unless ``codes`` names them, the case allows the exit codes of a
    command that works: ``lint``, ``update --check``, ``search`` and
    ``match`` report through exit code 1, and every other command exits 0.
    """
    name = ' '.join(args)
    if inside is not None:
        name += f' [in {inside or "."}]'
    if codes is None:
        reports = (args[0] in ('lint', 'search', 'match')) or ('--check' in args)
        codes = _REPORTS if reports else _DONE
    return Case(
        name=name,
        args=tuple(args),
        inside=inside,
        writes=writes,
        codes=codes,
    )


def _named(cases: Iterable[Case]) -> tuple[Case, ...]:
    """Number the repeats of a case, so every name in a stage is unique."""
    result = []
    seen: dict[str, int] = {}
    for case in cases:
        seen[case.name] = seen.get(case.name, 0) + 1
        if seen[case.name] > 1:
            case = dataclasses.replace(case, name=f'{case.name} #{seen[case.name]}')
        result.append(case)
    return tuple(result)
