"""The command matrix: which commands run on which tree."""

from __future__ import annotations

import dataclasses
import os
import pathlib
import random
import re
from collections.abc import Iterable, Sequence
from typing import Any, Optional

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
_TERMS = 2
# how much of a page the draw reads: its frontmatter and the head of its body
_HEAD = 65536
# a word no page holds, and the name of a page no wiki holds
_ABSENT = 'zqxjkvparity'
# the folder `new` creates
_NEW = 'paritynew'
_NEW_DESC = 'A folder the parity harness adds.'
_NEW_CONTENT = 'Written by the parity harness.'
# a whole ASCII word: the search index folds accents, so part of a longer
# word would match no token
_WORD = re.compile(r'\b[A-Za-z]{5,}\b')
# one token of a tags field
_TAG = re.compile(r'[^\W_]+')
# a frontmatter line that opens a field
_FIELD = re.compile(r'([A-Za-z_][\w-]*):(.*)')
_FENCE = '---'
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
        hits: Whether the command is built to find something. Exit code 1
            on every run then means the case compared only "no match".

    """

    name: str
    args: tuple[str, ...]
    inside: Optional[str] = None
    writes: bool = False
    codes: frozenset[int] = _DONE
    hits: bool = False


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

    Each word is drawn from the text the command it goes to reads, so that
    the command finds something. A field is empty where the wiki holds no
    such word.

    Attributes:
        scopes: Folders for scoped commands, relative to the wiki root.
        pages: Pages for ``read``.
        terms: Words of one page's body, for ``search`` and ``match``.
        scoped: A body word of a page below the first scope.
        desc: A word of one page's ``desc`` field.
        label: A word of one page's ``name`` or ``tags`` field.
        tagged: A body word and a tag of one page that has tags.

    """

    scopes: tuple[str, ...]
    pages: tuple[str, ...]
    terms: tuple[str, ...]
    scoped: Optional[str] = None
    desc: Optional[str] = None
    label: Optional[str] = None
    tagged: tuple[str, ...] = ()


def sample(root: pathlib.Path, *, seed: int) -> Sample:
    """Draw the matrix sample of the wiki at ``root``.

    The draw reads names and page text only, and is the same for one seed
    and one tree. Scopes are top-level folders and second-level folders;
    pages are Markdown files in folders that hold an index. The words come
    from those pages that carry a ``name`` field, read in a drawn order
    until each kind is found: a wiki with no tags at all is read to its
    last page.
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
            path = root / folder / name
            regular = os.path.isfile(path) and not os.path.islink(path)
            page = name.endswith('.md') and (name != _INDEX)
            if page and regular and not name.startswith('-'):
                candidates.append(os.path.join(folder, name))
    pages = _draw(rng, candidates, _PAGES)
    # the kinds of word still to find; a scoped one only where a page can hold it
    inner = set()
    if scopes:
        below = os.path.join(scopes[0], '')
        inner = {page for page in candidates if page.startswith(below)}
    missing = {'terms', 'desc', 'label', 'tagged'}
    if inner:
        missing.add('scoped')
    drawn: dict[str, Any] = {}
    for page in rng.sample(candidates, len(candidates)):
        if not missing:
            break
        fields, body = _head(root / page)
        # the wiki names the pages it manages; search may not hold another
        if 'name' not in fields:
            continue
        words = _words(body)
        tags = _TAG.findall(fields.get('tags', ''))
        descs = _words(fields.get('desc', ''))
        labels = _words(f'{fields.get("name", "")}\n{fields.get("tags", "")}')
        if ('terms' in missing) and words:
            drawn['terms'] = _draw(rng, words, _TERMS)
        if ('scoped' in missing) and words and (page in inner):
            drawn['scoped'] = rng.choice(words)
        if ('desc' in missing) and descs:
            drawn['desc'] = rng.choice(descs)
        if ('label' in missing) and labels:
            drawn['label'] = rng.choice(labels)
        if ('tagged' in missing) and words and tags:
            drawn['tagged'] = (rng.choice(words), rng.choice(tags))
        missing -= set(drawn)
    return Sample(scopes=scopes, pages=pages, terms=drawn.pop('terms', ()), **drawn)


def stages(sample: Sample) -> tuple[Stage, ...]:
    """Return the stages of the matrix for one wiki.

    Read-only commands run on the snapshot (``pristine``, ``query``).
    Mutating commands run on a tree with no caches (``write``) and on the
    snapshot (``full``). ``cross`` then runs each side on the tree and
    caches each side left after ``write``. Where the sample lacks a kind
    of word, its command takes a body word and may find nothing.
    """
    term = sample.terms[0] if sample.terms else _ABSENT
    other = sample.terms[-1] if sample.terms else _ABSENT
    (word, tag) = sample.tagged or (term, other)
    scope = sample.scopes[0] if sample.scopes else None
    # read-only commands, from a neutral folder and from inside the wiki
    pristine = [
        _case(['lint']),
        _case(['lint', '--json']),
        _case(['lint', '--full']),
        _case(['lint', '--count']),
        _case(['update', '--check']),
        _case(['update', '--check', '--full']),
        _case(['update', '--check', '--count']),
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
        _case(['search', word, '--tag', tag]),
        _case(['search', _ABSENT], codes=_NOTHING),
        _case(['search', '"', '--raw'], codes=_FAILS),
        _case(['match', term]),
        _case(['match', term, '--lines']),
        _case(['match', term, '--lineno']),
        _case(['match', term.upper(), '--ignore-case', '--lines']),
        _case(['match', term, '--all']),
        _case(['match', sample.desc or term, '--field', 'desc']),
        _case(['match', sample.label or term, '--field', 'name,tags', '--lines']),
        _case(['match', _ABSENT], codes=_NOTHING),
        _case(['match', '('], codes=_FAILS),
        _case(['read', _INDEX]),
        _case(['read', f'{_ABSENT}.md'], codes=_FAILS),
    ]
    if scope is not None:
        query.append(_case(['map', scope]))
        query.append(_case(['search', sample.scoped or term, scope, '--json']))
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


def _head(path: pathlib.Path) -> tuple[dict[str, str], str]:
    """Return the frontmatter fields and the body of the head of a page.

    The fields are read by line: a line that opens with a key starts a
    field, and every line up to the next key belongs to it. This is enough
    to draw words from; what a field holds is each side's to decide.
    """
    with open(path, 'rb') as handle:
        raw = handle.read(_HEAD)
    text = raw.decode('utf-8', errors='replace')
    # a full head may end inside a word: drop its last line
    if len(raw) == _HEAD:
        text = text[: text.rfind('\n') + 1]
    lines = text.splitlines()
    if (not lines) or (lines[0] != _FENCE) or (_FENCE not in lines[1:]):
        return {}, text
    end = lines.index(_FENCE, 1)
    fields: dict[str, str] = {}
    key = None
    for line in lines[1:end]:
        opened = _FIELD.match(line)
        if opened is not None:
            key = opened[1]
            fields[key] = opened[2]
        elif key is not None:
            fields[key] += f'\n{line}'
    return fields, '\n'.join(lines[end + 1 :])


def _words(text: str) -> list[str]:
    """Return the distinct words of ``text``, sorted."""
    return sorted(set(_WORD.findall(text)))


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
    Such a ``search`` or ``match`` is built to find something.
    """
    name = ' '.join(args)
    if inside is not None:
        name += f' [in {inside or "."}]'
    hits = False
    if codes is None:
        hits = args[0] in ('search', 'match')
        reports = hits or (args[0] == 'lint') or ('--check' in args)
        codes = _REPORTS if reports else _DONE
    return Case(
        name=name,
        args=tuple(args),
        inside=inside,
        writes=writes,
        codes=codes,
        hits=hits,
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
