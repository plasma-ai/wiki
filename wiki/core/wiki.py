"""Implements ``Wiki`` class."""

from __future__ import annotations

import datetime as dt
import difflib
import functools
import http.client
import json
import logging
import os
import pathlib
import re
import shutil
import tempfile
import unicodedata
import urllib.request
import zoneinfo
from typing import Any, Optional

import wiki.util
from wiki.constants import (
    OFFLINE_MODE,
    WIKI_CACHE,
    WIKI_DIR,
    WIKI_INDEX,
    WIKI_SETTINGS,
)
from wiki.typing import Link, PathLike

from . import _obsidian, format
from .event import Event

__all__ = ['Wiki']

# bounds the plugin-asset fetch in _download; ample for the kilobyte-scale
# release assets, small enough that a dead network fails the run fast
_TIMEOUT_SECONDS = 10

# soft ceiling for a folded frontmatter desc: past it lint notes the page,
# since every map row and parent index link reproduces the whole desc
_DESC_NOTE_CHARS = 500

# str.is* predicates a policy may require (applied to the name minus allow chars)
_NAMING_PREDICATES = {
    'ascii': str.isascii,
    'alpha': str.isalpha,
    'alphanum': str.isalnum,
    'identifier': str.isidentifier,
}
# field defaults a preset or settings.json naming block is overlaid onto
_NAMING_DEFAULTS = {
    'validate': [],
    'allow': '',
    'deny': '',
    'pattern': None,
    'min_length': None,
    'max_length': None,
    'leading_digits': True,
    'reserved': [],
}


class Wiki:
    """Base class for structured wikis.

    Provides ``init``, ``update_config``, ``read``, ``search``,
    ``update``, ``lint``, and ``map`` operations for a folder-based
    wiki with ``_index.md`` files.

    Instances are one-shot: policy (settings, naming, timestamps) and
    the root display name are cached per instance, so hosted embedders
    construct a fresh ``Wiki`` per operation rather than holding one
    across mutations of the wiki.
    """

    path_sep: str = '/'
    index_delimiter: str = '***'
    category_order: Optional[list[str]] = None

    def __init__(self: Wiki, path: PathLike) -> None:
        """Initialize the wiki manager.

        Args:
            path: Path to the wiki root directory.

        """
        self._root = pathlib.Path(path).expanduser().resolve()

    @functools.cached_property
    def _root_name(self: Wiki) -> str:
        """Return the root display name from frontmatter.

        Falls back to the root folder name if the root index does not
        exist yet (e.g. during init). Caching is safe because every
        flow reads this only after the root index holds its final name:
        init writes the root index before its first ``_path_to_name``
        call, and an update run's plan reads disk before apply rewrites
        it -- were a read to land before the root index exists, the
        cache would silently pin the folder-name fallback.
        """
        root_index = self._root / WIKI_INDEX
        if root_index.exists():
            text = self._read_text(root_index)
        else:
            return self._root.name
        frontmatter, _, _ = format.parse_index(text, delimiter=self.index_delimiter)
        if result := format.read_frontmatter_name(frontmatter):
            return result
        return self._root.name

    @functools.cached_property
    def _settings(self: Wiki) -> dict:
        """Return the per-wiki settings overlay from ``.wiki/settings.json``.

        Returns an empty dict when the file is absent. Raises ``ValueError``
        on malformed JSON or a non-object top level, since the file is
        user-editable input that should fail loudly rather than be ignored.
        """
        path = self._root / WIKI_SETTINGS
        if not path.exists():
            return {}
        try:
            result = json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as e:
            raise ValueError(f'Malformed JSON in {WIKI_SETTINGS}: {e}') from e
        if not isinstance(result, dict):
            raise ValueError(f'{WIKI_SETTINGS} must be a JSON object.')
        return result

    @functools.cached_property
    def _naming_policy(self: Wiki) -> dict:
        """Return the effective naming policy from ``settings.json``.

        Overlays the per-wiki ``naming`` block from ``settings.json`` onto the
        field defaults, validates the fields, and folds in the structural
        characters and names that are always denied -- the on-disk grammar
        would otherwise break.
        """
        # overlay the settings.json naming block onto the field defaults
        override = self._settings.get('naming', {})
        if not isinstance(override, dict):
            raise ValueError(
                f'The naming block must be a JSON object in {WIKI_SETTINGS}.'
            )
        policy = {**_NAMING_DEFAULTS, **override}
        # validate predicate names (settings.json is user input -> fail loudly)
        validate = policy['validate']
        if not isinstance(validate, list):
            raise ValueError(
                f'naming.validate must be a list of predicate names, got'
                f' {validate!r} in {WIKI_SETTINGS}.'
            )
        for predicate in validate:
            if predicate not in _NAMING_PREDICATES:
                options = ', '.join(_NAMING_PREDICATES)
                raise ValueError(
                    f'Unknown naming predicate {predicate!r} (must be one of'
                    f' {options}) in {WIKI_SETTINGS}.'
                )
        # min_length defaults to 1; an explicit value must be a positive int
        # (bool subclasses int, so check the exact type)
        min_length = policy['min_length']
        if min_length is None:
            min_length = 1
        elif (type(min_length) is not int) or (min_length < 1):
            raise ValueError(
                f'naming.min_length must be an int >= 1 or null, got'
                f' {min_length!r} in {WIKI_SETTINGS}.'
            )
        # max_length is null (no cap) or a positive int
        max_length = policy['max_length']
        if max_length is not None:
            if (type(max_length) is not int) or (max_length < 1):
                raise ValueError(
                    f'naming.max_length must be an int >= 1 or null, got'
                    f' {max_length!r} in {WIKI_SETTINGS}.'
                )
        # deny/allow are strings of characters; reserved is a list of names
        for leaf in ('deny', 'allow'):
            if not isinstance(policy[leaf], str):
                raise ValueError(
                    f'naming.{leaf} must be a string of characters, got'
                    f' {policy[leaf]!r} in {WIKI_SETTINGS}.'
                )
        reserved = policy['reserved']
        if not isinstance(reserved, list):
            raise ValueError(
                f'naming.reserved must be a list of strings, got'
                f' {reserved!r} in {WIKI_SETTINGS}.'
            )
        leading_digits = policy['leading_digits']
        if not isinstance(leading_digits, bool):
            raise ValueError(
                f'naming.leading_digits must be a boolean, got'
                f' {leading_digits!r} in {WIKI_SETTINGS}.'
            )
        # always deny the path separator, index delimiter, and link/markdown grammar
        deny = set(policy['deny'])
        deny.add(self.path_sep)  # would split a name into folders
        deny.update(self.index_delimiter)  # the *** generated/user-content delimiter
        deny.add('\\')  # escape character + Windows path separator
        deny.update('[]|')  # wikilink [[target|label]] + category [cat] name
        deny.add('#')  # markdown heading marker / link anchor
        # always reserve the per-folder index stem; the .wiki tool directory
        # needs no entry -- leading-dot names are rejected wholesale
        reserved = set(reserved)
        reserved.add(pathlib.Path(WIKI_INDEX).stem)  # the per-folder index stem
        # compile the optional full-match pattern
        pattern = policy['pattern']
        if pattern is not None:
            if not isinstance(pattern, str):
                raise ValueError(
                    f'naming.pattern must be a string or null, got'
                    f' {pattern!r} in {WIKI_SETTINGS}.'
                )
            try:
                pattern = re.compile(pattern)
            except re.error as e:
                raise ValueError(
                    f'naming.pattern in {WIKI_SETTINGS} is not a valid regex: {e}'
                ) from e
        return {
            'validate': validate,
            'allow': set(policy['allow']),
            'deny': deny,
            'pattern': pattern,
            'min_length': min_length,
            'max_length': max_length,
            'leading_digits': leading_digits,
            'reserved': reserved,
        }

    @functools.cached_property
    def _timestamp_policy(self: Wiki) -> dict:
        """Return the effective timestamp policy from ``settings.json``.

        Validates the per-wiki ``timestamp`` block (``timezone`` / ``format``) so
        a bad value fails loudly with a file+key message rather than leaking a raw
        exception from :meth:`_utc_now` (settings.json is user input).
        """
        # overlay the settings.json timestamp block onto the defaults
        override = self._settings.get('timestamp', {})
        if not isinstance(override, dict):
            raise ValueError(
                f'The timestamp block must be a JSON object in {WIKI_SETTINGS}.'
            )
        # format is a strftime string; timezone is an IANA name or null (UTC)
        # -- the default format's literal 'Z' asserts UTC, so a configured
        # zone swaps it for %z to keep the rendered offset honest, while an
        # authored format always passes through untouched (author's choice)
        if 'format' in override:
            format = override['format']
        elif override.get('timezone'):
            format = '%Y-%m-%dT%H:%M:%S%z'
        else:
            format = '%Y-%m-%dT%H:%M:%SZ'
        if not isinstance(format, str):
            raise ValueError(
                f'timestamp.format must be a string, got {format!r} in {WIKI_SETTINGS}.'
            )
        # a blank or multi-line value would corrupt the YAML frontmatter -- reject
        # an empty/whitespace format, strftime's %n/%t newline/tab directives, and
        # literal line breaks, the only ways the rendered value splits or empties
        breakers = ('%n', '%t', '\n', '\r')
        if not format.strip() or any(token in format for token in breakers):
            raise ValueError(
                f'timestamp.format must render a single non-empty line, got'
                f' {format!r} in {WIKI_SETTINGS}.'
            )
        timezone = override.get('timezone')
        if (timezone is not None) and not isinstance(timezone, str):
            raise ValueError(
                f'timestamp.timezone must be a string or null, got'
                f' {timezone!r} in {WIKI_SETTINGS}.'
            )
        if timezone:
            try:
                timezone = zoneinfo.ZoneInfo(timezone)
            except (zoneinfo.ZoneInfoNotFoundError, ValueError) as e:
                raise ValueError(
                    f'Unknown timestamp.timezone {timezone!r} in {WIKI_SETTINGS}.'
                ) from e
        else:
            timezone = dt.UTC
        return {'format': format, 'zone': timezone}

    @functools.cached_property
    def _map_policy(self: Wiki) -> dict:
        """Return the effective map presentation policy from ``settings.json``.

        Validates the per-wiki ``map`` block (``desc_limit`` / ``indent`` /
        ``ellipsis``) so a bad value fails loudly with a file+key message
        rather than leaking a raw exception from deep inside the map render
        (settings.json is user input).
        """
        # overlay the settings.json map block onto the defaults
        override = self._settings.get('map', {})
        if not isinstance(override, dict):
            raise ValueError(f'The map block must be a JSON object in {WIKI_SETTINGS}.')
        # desc_limit bounds each rendered description; -1 (or null/unset) disables it
        desc_limit = override.get('desc_limit')
        if desc_limit is None:
            desc_limit = -1
        if (type(desc_limit) is not int) or (desc_limit < -1):
            raise ValueError(
                f'map.desc_limit must be an int >= -1 or null, got'
                f' {desc_limit!r} in {WIKI_SETTINGS}.'
            )
        # indent is the per-level unit; ellipsis marks a truncated desc
        indent = override.get('indent', '  ')
        if not isinstance(indent, str):
            raise ValueError(
                f'map.indent must be a string, got {indent!r} in {WIKI_SETTINGS}.'
            )
        ellipsis = override.get('ellipsis', '...')
        if not isinstance(ellipsis, str):
            raise ValueError(
                f'map.ellipsis must be a string, got {ellipsis!r} in {WIKI_SETTINGS}.'
            )
        return {'desc_limit': desc_limit, 'indent': indent, 'ellipsis': ellipsis}

    @functools.cached_property
    def _titles_required(self: Wiki) -> bool:
        """Return the effective ``titles.required`` flag from ``settings.json``.

        When true, every index and page must carry an authored ``title:``:
        update seeds a ``title: null`` placeholder on files missing the
        field (instead of removing null titles as the transient unset
        idiom), and lint fails each placeholder until a value is authored.
        """
        # overlay the settings.json titles block onto the default
        override = self._settings.get('titles', {})
        if not isinstance(override, dict):
            raise ValueError(
                f'The titles block must be a JSON object in {WIKI_SETTINGS}.'
            )
        required = override.get('required', False)
        if not isinstance(required, bool):
            raise ValueError(
                f'titles.required must be a boolean, got'
                f' {required!r} in {WIKI_SETTINGS}.'
            )
        return required

    def _name_violation(self: Wiki, name: str) -> Optional[str]:
        """Return the naming rule ``name`` breaks, or ``None`` if it is valid.

        Names the failing rule for a caller's error message, so a rejection
        says which policy the name tripped rather than a bare "invalid". The
        policy fields are :attr:`_naming_policy`; :meth:`validate_name` is the
        boolean gate over this. Override in subclasses for naming rules a data
        policy cannot express -- every internal name check delegates here, so
        this is the effective extension point.
        """
        # alias naming policy
        policy = self._naming_policy
        # reject empty, over-long, non-printable, and hidden (leading-dot) names
        min_length = policy['min_length']
        max_length = policy['max_length']
        if len(name) < min_length:
            return f'shorter than {min_length} character(s)'
        if (max_length is not None) and (len(name) > max_length):
            return f'longer than {max_length} character(s)'
        if not name.isprintable() or name.startswith('.'):
            return 'non-printable or a leading-dot name'
        # reject reserved structural names and any denied character
        if name in policy['reserved']:
            return 'a reserved name'
        if any(char in policy['deny'] for char in name):
            return 'contains a denied character'
        # apply the str.is* predicates to the name minus any allowed characters
        probe = ''.join(char for char in name if char not in policy['allow'])
        for predicate in policy['validate']:
            if (predicate == 'identifier') and policy['leading_digits']:
                valid = f'_{probe}'.isidentifier()
            else:
                valid = _NAMING_PREDICATES[predicate](probe)
            if not valid:
                return f"fails the '{predicate}' rule"
        # apply the optional full-match regex
        if (policy['pattern'] is not None) and not policy['pattern'].fullmatch(name):
            return 'does not match the required pattern'
        return None

    def validate_name(self: Wiki, name: str) -> bool:
        """Return ``True`` if ``name`` satisfies the wiki's naming policy.

        The policy is the field defaults overlaid by the per-wiki ``naming`` block
        in ``.wiki/settings.json``; see :attr:`_naming_policy`. The path separator,
        index delimiter, link/markdown grammar characters, and the reserved
        ``_index`` name are always rejected; the ``.wiki`` tool directory needs
        no reservation, since leading-dot names are rejected wholesale.

        Args:
            name: Name to validate (page stem or folder name).

        Returns:
            ``True`` if the name is valid.

        """
        return self._name_violation(name) is None

    def init(
        self: Wiki,
        name: Optional[str] = None,
        *,
        settings: Optional[dict] = None,
    ) -> None:
        """Initialize wiki with root ``_index.md``.

        Creates the wiki root directory and scaffolds the
        root index. Updates all index files after scaffolding.

        Args:
            name: Display name for the root ``_index.md``.
                Defaults to the wiki root folder name.
            settings: Initial ``.wiki/settings.json`` contents.
                When omitted, the default naming policy is seeded
                so the configurable knobs are discoverable. Ignored
                when the file already exists -- re-init never
                overwrites a wiki's settings.

        """
        # validate OFFLINE_MODE before any filesystem mutation; a bad value must
        # fail fast rather than strand a half-built wiki the re-init guard skips
        _is_offline()
        # TODO: remove back-compat in future version
        self._refuse_legacy_layout()
        # resolve the settings seed and prime the _settings cache with it, so
        # the accesses below (_name_violation, _utc_now) read this wiki's real
        # policy rather than caching {} from the not-yet-written file
        # NOTE: the priming assignment shadows the _settings cached_property --
        #   the instance attribute takes precedence over the descriptor, so
        #   the seed becomes the cached value
        settings_path = self._root / WIKI_SETTINGS
        if settings_path.exists():
            seed = None
        else:
            seed = settings if settings is not None else {'naming': _NAMING_DEFAULTS}
            self._settings = seed
        # validate the seeded policies BEFORE any filesystem write: a rejected
        # seed must fail cleanly rather than strand a wiki whose settings.json
        # every later command (and re-init, which never overwrites it) rejects
        name = name or self._root.name
        violation = self._name_violation(name)
        if violation is not None:
            raise ValueError(f'Invalid wiki name {name!r}: {violation}')
        # the titles and map policies are read lazily (first inside the plan
        # and the map render), so touch them here to reject a bad seed early
        self._titles_required  # noqa: B018
        self._map_policy  # noqa: B018
        # alias current timestamp
        now = self._utc_now()
        # initialize wiki root
        self._root.mkdir(parents=True, exist_ok=True)
        # seed .wiki/settings.json -- the declared-root marker
        if seed is not None:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(seed, indent=2)
            wiki.util.fs.write_atomic(settings_path, content + '\n')
        # seed .wiki/obsidian from stock template
        _obsidian.seed_template(self._root)
        # create root index
        root_index = self._root / WIKI_INDEX
        if not root_index.exists():
            frontmatter = format.build_frontmatter(
                name=name,
                created=now,
                updated=now,
            )
            text = format.render_index(
                heading=name,
                frontmatter=frontmatter,
                links=[],
                user_content='',
                delimiter=self.index_delimiter,
            )
            wiki.util.fs.write_atomic(root_index, text)
        # update all indexes (reuse this run's timestamp); a re-init sweeps
        # an existing tree like update, so it refuses over merge conflict
        # markers rather than bake one conflict side into the rewrite
        overlay, baseline, _ = self._plan(self._root, now=now)
        self._refuse_conflicted(baseline)
        self._apply_plan(overlay, baseline, now)
        # materialize the self-ignoring counts cache
        self._load_counts()

    def update_config(self: Wiki) -> list[str]:
        """Install ``.wiki/obsidian/`` into ``.obsidian/``.

        Copies each bundled plugin's settings under
        ``.wiki/obsidian/plugins/`` into ``.obsidian/plugins/`` and
        downloads pinned plugin code from its upstream release. Each
        top-level ``.json`` file (like ``community-plugins.json``) is
        created from source when absent, else merged: arrays are
        union-merged and dicts deep-merged with source winning. Other
        installed plugins are left untouched. A missing
        ``.wiki/obsidian/`` is seeded from the stock template first, so
        an adopted tree gets the full setup. Also guarantees the
        declared-root marker: a missing ``.wiki/settings.json`` is
        restored as ``{}`` with a notice.

        Returns:
            List of warning messages (e.g. when a plugin download
            fails because there is no network connection).

        """
        # validate OFFLINE_MODE before any filesystem mutation
        offline = _is_offline()
        # initialize warnings
        warnings = []
        # restore the declared-root marker before touching anything else;
        # a restoration is informational, so it rides the notice channel
        # rather than the returned warnings (which mean setup is unfinished)
        for event in self._ensure_settings():
            self._dispatch_notice(event)
        # seed a missing .wiki/obsidian from the stock template, so an
        # adopted tree (or one whose .wiki/ was lost) gets the full setup
        _obsidian.seed_template(self._root)
        config_dir = self._root / WIKI_DIR / 'obsidian'
        # prepare the .obsidian/ vault directory
        obsidian_dir = self._root / '.obsidian'
        obsidian_dir.mkdir(exist_ok=True)
        # install plugin settings, then download pinned plugin code
        plugins_dir = config_dir / 'plugins'
        if plugins_dir.is_dir():
            for source in sorted(plugins_dir.iterdir()):
                if not source.is_dir():
                    continue
                # copy curated settings into the vault
                target = obsidian_dir / 'plugins' / source.name
                shutil.copytree(source, target, dirs_exist_ok=True)
                # download pinned plugin code from its release, unless offline
                release_url = _obsidian._OBSIDIAN_PLUGINS.get(source.name)
                if release_url and offline:
                    warnings.append(
                        f'Skipped {source.name} download (OFFLINE_MODE).'
                        ' Re-run `wiki config` online to finish setup.'
                    )
                elif release_url:
                    # fetch all assets to temp paths first, then move them into
                    # place only after every fetch succeeds, so a mid-fetch
                    # failure never leaves a skewed main.js/manifest.json pair
                    staged = []
                    try:
                        for asset in _obsidian._OBSIDIAN_PLUGIN_ASSETS:
                            url = release_url.format(asset=asset)
                            fd, tmp = tempfile.mkstemp(dir=target, suffix=asset)
                            os.close(fd)
                            tmp = pathlib.Path(tmp)
                            staged.append((tmp, target / asset))
                            self._download(url, tmp)
                    except (OSError, http.client.HTTPException) as e:
                        # discard the partial download, leaving existing files
                        for tmp, _ in staged:
                            tmp.unlink(missing_ok=True)
                        warnings.append(
                            f'Could not download {source.name} ({e}).'
                            ' Re-run `wiki config` to finish setup.'
                        )
                    except BaseException:
                        # a Ctrl-C (or any other interrupt) mid-fetch must not
                        # strand random-named temps in the plugin directory
                        for tmp, _ in staged:
                            tmp.unlink(missing_ok=True)
                        raise
                    else:
                        for tmp, dest in staged:
                            os.replace(tmp, dest)
        # create or merge each top-level json file
        for source in sorted(config_dir.glob('*.json')):
            target = obsidian_dir / source.name
            # create from source when the target is absent (atomically, so a
            # crash mid-create never leaves a torn file the merge rejects)
            if not target.exists():
                wiki.util.fs.write_atomic(target, source.read_text(encoding='utf-8'))
                continue
            # load source and target for merge; the target is user-editable, so
            # name the file on bad JSON instead of a bare, undiagnosable error
            source_data = json.loads(source.read_text(encoding='utf-8'))
            try:
                target_data = json.loads(target.read_text(encoding='utf-8'))
            except json.JSONDecodeError as e:
                raise ValueError(
                    f'Malformed JSON in .obsidian/{source.name}: {e}'
                ) from e
            # merge per the install policy (arrays union, dicts deep)
            merged = _obsidian.merge_settings(
                target_data=target_data,
                source_data=source_data,
                name=source.name,
            )
            result = json.dumps(merged, indent=2)
            wiki.util.fs.write_atomic(target, result + '\n')
        return warnings

    def _refuse_enclosing_wiki(self: Wiki, folder: pathlib.Path) -> None:
        """Refuse a scope strictly inside a nested declared wiki.

        The plan derives every ``name:`` against ``self._root``, so a scope
        below a foreign ``.wiki/settings.json`` would rewrite the nested
        wiki's paths against the outer root; the descendant scan in
        :meth:`update`/:meth:`lint` catches an enclosed marker, but the
        scope's own ancestors are outside that walk, so check them here.
        """
        # an unscoped run has no ancestors inside the wiki to check; walking
        # folder.parents would never hit the break and probe above the root
        if folder == self._root:
            return
        for ancestor in folder.parents:
            if ancestor == self._root:
                break
            if (ancestor / WIKI_SETTINGS).is_file():
                raise ValueError(
                    f'Path is inside the wiki at: {ancestor};'
                    f' nested wikis are not supported.'
                )

    def update(
        self: Wiki,
        name: Optional[str] = None,
        *,
        prune: bool = False,
        check: bool = False,
    ) -> list[str]:
        """Update wiki files.

        Walks the filesystem, refreshing category labels from child
        frontmatter and preserving existing descriptions. Adds
        frontmatter to pages that lack it (seeding ``title:`` from an
        authored H1, so adoption preserves the heading, while a page
        with no H1 gains the path-joined heading in its body, never a
        seeded title), announcing each adoption; a page whose
        frontmatter never closes (no closing ``---``) is left untouched
        and warned about rather than rewritten, and an existing index
        with no (closed) frontmatter -- an emptied or truncated file --
        is likewise skipped with a notice rather than rebuilt from
        scratch.

        An authored ``title:`` (on an index or a page) wins the file's
        H1 and is kept directly under ``name:``; a blank or lowercase
        ``null`` value unsets it -- the line is removed. Under
        ``titles.required`` (``.wiki/settings.json``) a missing title is
        instead seeded as a ``title: null`` placeholder, kept for lint
        to fail until a value is authored.

        When broken links are found (targets in the existing index
        that no longer exist on the filesystem), they are preserved
        and a warning is logged per link. Set ``prune=True`` to
        remove them instead. A link whose target still exists on disk
        as a symlinked file is warned about as a symlink skip instead
        of a broken link -- symlinked files are excluded from the walk. A file whose only drift is CRLF line
        endings is rewritten, normalizing it to LF. Every notice is
        emitted individually -- output modes (the CLI's condensed
        default) are the caller's concern.

        Emits a notice for each new link added with a placeholder
        ``...`` description and when a deleted ``.wiki/cache/`` is
        recreated, and a warning for each index-side link description
        overwritten because it diverged from its page's frontmatter
        ``desc`` (the source of truth -- the page is the place to
        edit). A
        missing ``.wiki/settings.json`` -- the declared-root marker --
        is restored as ``{}`` (all defaults; policy is never invented)
        with a notice; a dry run leaves it untouched, neither restoring
        nor reporting it (the CLI's resolver names it instead).

        Args:
            name: Restrict scope to named subtree (relative
                path). ``None`` means the entire wiki.
            prune: Remove broken links instead of preserving them.
            check: Report the files that would change without writing
                them (a dry run).

        Returns:
            List of relative paths of updated files (or, when ``check``
            is set, the files that *would* be updated).

        Raises:
            ValueError: If the scope encloses a nested declared wiki (a
                directory carrying its own ``.wiki/settings.json``) --
                sweeping across it would absorb that wiki, so the write
                and the dry run refuse alike, naming the nested root.
            ValueError: If a scope file carries merge conflict markers
                outside a well-formed ``no-lint`` region -- the plan
                would read the markers as authored content and bake
                them into the rewrite, so the write and the dry run
                refuse alike, naming every marked file.

        Note:
            A scoped update (``name`` set) does not refresh the parent
            index label for a category change at the scope root, since
            the parent folder is outside the scope.

        """
        # resolve the scope first, so a bad entry fails before the write
        # sweeps below mutate anything
        if name:
            folder = self._resolve_folder(name)
        else:
            folder = self._root
        # a scope inside a nested declared wiki would sweep it against the
        # outer root -- refuse before any mutation (the descendant scan below
        # covers an enclosed marker; this covers an enclosing one)
        self._refuse_enclosing_wiki(folder)
        # TODO: remove back-compat in future version
        self._refuse_legacy_layout()
        # refuse to sweep across a nested declared wiki: absorbing it would
        # rewrite its name: paths against the wrong root, and a dry run
        # would preview that same absorption, so both refuse alike
        for nested in self._find_dirs(folder):
            if (nested != self._root) and (nested / WIKI_SETTINGS).is_file():
                raise _encloses_wiki_error(nested)
        # a dry run reports without mutating, so the marker guarantee
        # applies only to a writing run
        if not check:
            # restore the declared-root marker before reading policy from it
            for event in self._ensure_settings():
                self._dispatch_notice(event)
        # compute corrected content for the scope (single timestamp)
        now = self._utc_now()
        overlay, baseline, notices = self._plan(folder, prune=prune, now=now)
        # refuse to write over merge conflict markers: the write and the
        # dry run refuse alike, naming every marked file
        self._refuse_conflicted(baseline)
        # report every broken/new link (preserved or added during the run)
        # individually and statelessly; the CLI condenses by default
        for event in notices:
            self._dispatch_notice(event)
        # dry run: report which files would change without writing (a CRLF
        # file reads equal but would be rewritten, so probe its bytes too)
        if check:
            return [
                str(path.relative_to(self._root))
                for path, content in overlay.items()
                if not path.exists()
                or content != self._read_text(path)
                or self._has_crlf(path)
            ]
        result = self._apply_plan(overlay, baseline, now)
        # refresh the counts cache, announcing a recreated .wiki/cache/ rather
        # than restoring a deleted directory silently
        recreated = not (self._root / WIKI_CACHE).exists()
        self._load_counts()
        if recreated:
            self.on_cache_restore(path=WIKI_CACHE)
        return result

    def lint(
        self: Wiki,
        name: Optional[str] = None,
    ) -> list[str]:
        """Check wiki health.

        Reports two kinds of issue:

        - **Out of date:** any file ``update`` would rewrite, shown as
          a unified diff of the change it would make -- a byte
          comparison against the plan (:meth:`_plan`), so lint flags
          exactly what ``update`` changes. (A file with merge conflict
          markers is the one exception: it is reported only as such,
          with its diff suppressed.) A bare page (no frontmatter) is
          additionally named as one, so the pending adoption is
          legible without reading its diff.
        - **Needs attention:** problems ``update`` cannot fix --
          invalid names, merge conflict markers (``update`` refuses
          the sweep until they are resolved), unclosed page
          frontmatter, emptied or truncated indexes, nested wiki roots
          (``update`` refuses to sweep across a directory declaring
          its own ``.wiki/settings.json``), likely formatter
          damage (escaped wikilinks, or a thematic break standing
          where ``***`` belongs), hand-wrap mangles (a hyphen dangle,
          or a list marker mid-sentence or directly under a
          paragraph), missing trailing periods, unparseable
          ``created:``/``updated:`` stamps (the fields are
          tool-owned, so a parseable value is never judged), broken
          index links (targets that no longer exist; ``update`` keeps
          these without ``--prune``, and a target still on disk as a
          symlinked file is named as such -- symlinked files are not
          indexed), and -- under ``titles.required``
          -- a missing or unfilled ``title:``.

        Every line begins with the relevant path; an out-of-date file's
        diff follows its ``Requires update`` header, indented.

        A ``<!-- start: no-lint -->`` ... ``<!-- end: no-lint -->`` region
        suppresses the positional rules (conflict markers, escaped
        wikilinks, wrap mangles, stale links) for the lines it wraps;
        file-level checks ignore regions, and a nested or dangling
        region marker is itself a hard issue.

        Placeholder and oversized descriptions, empty content sections,
        stale links in user content (index bodies and pages -- the
        generated link block's broken-link check is the hard surface),
        and CRLF line endings (which the next ``update`` normalizes)
        are soft notes (stderr) and do not count as issues.

        Args:
            name: Restrict scope to named subtree (relative
                path). ``None`` means the entire wiki.

        Returns:
            List of issue descriptions.

        """
        # resolve folder
        if name:
            folder = self._resolve_folder(name)
        else:
            folder = self._root
        # a scope inside a nested declared wiki would preview the wrong plan
        # (names against the outer root) -- refuse before planning, as update
        # does (the enclosed-marker case is reported per-folder below)
        self._refuse_enclosing_wiki(folder)
        # TODO: remove back-compat in future version
        self._refuse_legacy_layout()
        # compute what update would write (the source of truth for drift)
        now = self._utc_now()
        overlay, _, _ = self._plan(folder, now=now)
        # walk all directories
        result = []
        folders = self._find_dirs(folder)
        for folder in folders:
            folder_relpath = folder.relative_to(self._root)
            # a nested declared root marks a foreign wiki: update refuses to
            # sweep across it, so name the marker as the root cause
            if (folder != self._root) and (folder / WIKI_SETTINGS).is_file():
                result.append(
                    f'{folder_relpath}/: Nested wiki root (declared by'
                    f' {WIKI_SETTINGS}); update refuses to sweep across it'
                )
            # check folder name
            if folder != self._root:
                violation = self._name_violation(folder.name)
                if violation is not None:
                    result.append(
                        f'{folder_relpath}/: Invalid folder name: {violation}'
                    )
            # flag a folder that shadows a same-named page: read <name> returns the
            # folder index, hiding <name>.md (resolution is directory-first)
            for child in sorted(folder.iterdir()):
                if child.is_dir() and not self._is_excluded_dir(child):
                    page = child.with_name(child.name + '.md')
                    if page.is_file():
                        page_relpath = page.relative_to(self._root)
                        result.append(
                            f'{page_relpath}: Shadowed by folder {child.name}/'
                        )
            # check the index
            index_path = folder / WIKI_INDEX
            if not index_path.exists():
                # update would create it; pages below are still linted
                result.append(f'{folder_relpath}/: Missing index')
            else:
                index_relpath = index_path.relative_to(self._root)
                text = self._read_text(index_path)
                # mask code once per file; the region parse and the
                # escaped-wikilink scan below share it
                masked = wiki.util.markdown.mask_code(text)
                # CRLF endings read clean through universal newlines; note
                # the pending normalization rather than failing the file
                if self._has_crlf(index_path):
                    self.on_crlf_notice(path=str(index_relpath))
                # region directives: no-lint suppresses the positional rules
                # below, and a malformed pairing is itself a hard issue
                suppressed, region_issues = self._lint_regions(index_path, masked)
                result.extend(region_issues)
                # conflict markers take precedence over the generated diff
                markers = _conflict_marker_lines(text)
                if any(n not in suppressed for n in markers):
                    result.append(f'{index_relpath}: Merge conflict markers')
                else:
                    # out of date: show what update would change
                    diff = self._diff(index_path, overlay, current=text)
                    if diff:
                        result.append(diff)
                    # a missing *** delimiter collapses the link block into user
                    # content; the diff already flags the rewrite -- name the marker
                    # (and, for a thematic break in its place, the likely formatter)
                    if self._index_missing_marker(folder):
                        if self._index_mangled_marker(folder):
                            result.append(
                                f'{index_relpath}: Index missing *** delimiter'
                                ' with a thematic break in its place: likely'
                                ' formatter damage (keep generic markdown'
                                ' formatters off the wiki; see README)'
                            )
                        else:
                            result.append(
                                f'{index_relpath}: Index missing *** delimiter'
                            )
                    # an escaped wikilink is the other formatter signature
                    escaped = format.escaped_wikilink_lines(masked)
                    if any(n not in suppressed for n in escaped):
                        result.append(
                            f'{index_relpath}: Escaped wikilinks: likely formatter'
                            ' damage (keep generic markdown formatters off the'
                            ' wiki; see README)'
                        )
                    # human-only checks on current content
                    frontmatter, links, user_content = format.parse_index(
                        text,
                        delimiter=self.index_delimiter,
                    )
                    # an index with no closed frontmatter is emptied or
                    # truncated; update keeps it as-is (see _plan_index), so
                    # surface the recovery paths as a hard issue
                    if not frontmatter:
                        result.append(
                            f'{index_relpath}: Empty or truncated index (no'
                            ' frontmatter); restore it from git or delete it'
                            ' to rebuild'
                        )
                    result.extend(self._lint_desc(index_path, frontmatter))
                    result.extend(self._lint_title(index_path, frontmatter))
                    result.extend(self._lint_timestamps(index_path, frontmatter))
                    # the root display name has no enclosing dir to validate it
                    if folder == self._root:
                        root_name = format.read_frontmatter_name(frontmatter)
                        violation = None
                        if root_name:
                            violation = self._name_violation(root_name)
                        if violation is not None:
                            result.append(
                                f'{index_relpath}: Invalid wiki name'
                                f' {root_name!r}: {violation}'
                            )
                    # broken links (targets gone; update keeps them) + descriptions
                    # -- matched by normalized identity, as _merge_links matches
                    expected_targets = {
                        unicodedata.normalize('NFC', target)
                        for target, _ in self._build_expected_links(folder)
                    }
                    for target, label, link_desc in links:
                        if label == '..':
                            continue
                        # broken link: target no longer on the filesystem (a
                        # target still on disk as a symlinked file is not
                        # missing, so name the exclusion as the cause)
                        if unicodedata.normalize('NFC', target) not in expected_targets:
                            if self._is_symlink_skipped(target):
                                result.append(
                                    f'{index_relpath}: Link [[{target}|{label}]]'
                                    ' targets a symlink; symlinked files are'
                                    ' not indexed'
                                )
                            else:
                                result.append(
                                    f'{index_relpath}: Broken link [[{target}|{label}]]'
                                )
                            continue
                        # the period check applies only to an authored description:
                        # when the child supplies a real desc, update propagates it,
                        # so any drift (period included) is the diff's concern --
                        # resolved like update (_target_page), so a raw file's row
                        # stays authored despite a same-named sidecar page
                        child_page = self._target_page(target)
                        child_text = None
                        if child_page is not None:
                            child_text = self._current_text(
                                path=child_page,
                                overlay=overlay,
                            )
                        child_desc = None
                        if child_text is not None:
                            child_frontmatter, _ = format.parse_page(child_text)
                            child_desc = format.read_frontmatter_desc(child_frontmatter)
                        if not (child_desc and (child_desc != '...')):
                            result.extend(
                                self._lint_link_desc(
                                    path=index_path,
                                    target=target,
                                    label=label,
                                    link_desc=link_desc,
                                )
                            )
                    # hand-wrap artifacts in the link rows, user content,
                    # and raw desc lines are hard issues
                    result.extend(
                        self._lint_wrap_mangles(
                            path=index_path,
                            text=text,
                            masked=masked,
                            suppressed=suppressed,
                            frontmatter=frontmatter,
                        )
                    )
                    # stale links in user content and empty content are soft notes
                    self._lint_stale_links(index_path, user_content)
                    if not user_content.strip():
                        self.on_content_empty(path=str(index_relpath))
            # check pages (always, even when the index is missing)
            for page in self._find_pages(folder):
                page_relpath = page.relative_to(self._root)
                # report an invalid name for every file, including non-markdown
                # (a non-markdown file -- or a page whose stripped name a
                # sibling file claims -- links by its full name, suffix
                # included, so validate what the wikilink would carry)
                if (page.suffix == '.md') and not page.with_suffix('').is_file():
                    violation = self._name_violation(page.stem)
                else:
                    violation = self._name_violation(page.name)
                if violation is not None:
                    result.append(f'{page_relpath}: Invalid page name: {violation}')
                # only markdown pages carry frontmatter/content to lint further
                if page.suffix != '.md':
                    continue
                text = self._read_text(page)
                # mask code once per file; the region parse and the
                # escaped-wikilink scan below share it
                masked = wiki.util.markdown.mask_code(text)
                # CRLF endings read clean through universal newlines; note
                # the pending normalization rather than failing the file
                if self._has_crlf(page):
                    self.on_crlf_notice(path=str(page_relpath))
                # region directives: no-lint suppresses the positional rules
                # below, and a malformed pairing is itself a hard issue
                suppressed, region_issues = self._lint_regions(page, masked)
                result.extend(region_issues)
                # conflict markers take precedence over the generated diff
                markers = _conflict_marker_lines(text)
                if any(n not in suppressed for n in markers):
                    result.append(f'{page_relpath}: Merge conflict markers')
                    continue
                # out of date: show what update would change
                diff = self._diff(page, overlay, current=text)
                if diff:
                    result.append(diff)
                # an escaped wikilink is the signature of formatter damage
                # (update never repairs page prose, so this is a human fix)
                escaped = format.escaped_wikilink_lines(masked)
                if any(n not in suppressed for n in escaped):
                    result.append(
                        f'{page_relpath}: Escaped wikilinks: likely formatter'
                        ' damage (keep generic markdown formatters off the'
                        ' wiki; see README)'
                    )
                # human-only checks on current content
                frontmatter, content = format.parse_page(text)
                # unclosed frontmatter is left untouched by update (see
                # _plan_page), so surface it as a hard issue; any other
                # frontmatterless page is bare -- name the pending adoption
                first_line = text.split('\n', 1)[0].lstrip('\ufeff')
                if not frontmatter and (first_line.strip() == '---'):
                    result.append(
                        f'{page_relpath}: Malformed frontmatter (no closing ---)'
                    )
                elif not frontmatter:
                    result.append(
                        f'{page_relpath}: Bare page (no frontmatter);'
                        ' update will adopt it'
                    )
                if frontmatter:
                    result.extend(self._lint_desc(page, frontmatter))
                    result.extend(self._lint_title(page, frontmatter))
                    result.extend(self._lint_timestamps(page, frontmatter))
                # hand-wrap artifacts in the content and raw desc lines
                # are hard issues
                result.extend(
                    self._lint_wrap_mangles(page, text, masked, suppressed, frontmatter)
                )
                # stale links in page content are soft notes
                self._lint_stale_links(page, content)
        return result

    def read(
        self: Wiki,
        name: str,
        *,
        start: Optional[int] = None,
        stop: Optional[int] = None,
        on: str = 'lines',
    ) -> str:
        """Return content for a named wiki entry.

        Resolves relative paths: tries as directory (``_index.md``), then as
        file, then with ``.md`` extension. When ``start``/``stop`` are given,
        slices the content section by ``on`` (``lines``/``words``/``chars``);
        for markdown files the frontmatter is always preserved.

        Args:
            name: Relative path (e.g. ``core/design``).
            start: Start index (0-indexed) for the slice, in units of ``on``.
            stop: Stop index (exclusive) for the slice.
            on: Slice unit -- ``lines``, ``words``, or ``chars``.

        Returns:
            File content string.

        Raises:
            FileNotFoundError: If the resolved path does not exist.

        """
        # resolve path
        path = self._resolve_path(name)
        # read file content
        content = self._read_text(path)
        # slice content on lines/words/chars
        if (start is not None) or (stop is not None):
            content = self._slice(content, path, start, stop, on)
        return content

    def search(
        self: Wiki,
        pattern: str,
        *,
        name: Optional[str] = None,
        field: Optional[str] = None,
        ignore_case: bool = False,
        all_files: bool = False,
    ) -> list[tuple[str, int, str]]:
        """Search wiki content for a regex pattern.

        By default searches body content of wiki-tracked
        markdown files. Use ``field`` to search specific
        frontmatter fields instead. Use ``all_files`` to
        include non-markdown files.

        Args:
            pattern: Regex pattern to match.
            name: Restrict scope to named subtree (relative
                path). ``None`` means the entire wiki.
            field: Comma-separated frontmatter field names to
                search (e.g. ``'tags'``, ``'desc,name'``). When
                ``None``, searches body content only. Patterns match
                each field's value (block-scalar continuation lines
                included), never the ``key:`` prefix.
            ignore_case: Use case-insensitive matching.
            all_files: Include non-markdown files in the
                search. Non-markdown files are searched whole
                (frontmatter is a markdown concept), so ``field``
                mode never matches them.

        Returns:
            List of ``(relative_path, line_number, line_text)``
            tuples. Line numbers are 1-based.

        """
        # compile pattern
        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(pattern, flags)
        # resolve folder scope
        if name:
            folder = self._resolve_folder(name)
        else:
            folder = self._root
        # enumerate files
        files = self._search_files(folder, all_files=all_files)
        # parse target fields ('' is an explicit empty field set, not "search body")
        if field is not None:
            fields = [item.strip() for item in field.split(',')]
        else:
            fields = []
        # search each file
        result = []
        for path in files:
            # skip binary / non-utf-8 files (e.g. an image under --all) rather
            # than aborting the whole search on one undecodable file
            try:
                text = path.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                continue
            relpath = str(path.relative_to(self._root))
            lines = text.split('\n')
            # frontmatter is a markdown concept -- read slices non-md files
            # whole, so a non-md file's leading '---' pair is body content
            # here too, not a frontmatter block to lift off
            if path.suffix == '.md':
                frontmatter, _ = format.parse_page(text)
            else:
                frontmatter = ''
            # search frontmatter fields
            if fields:
                if not frontmatter:
                    continue
                field_lines = format.field_line_ranges(frontmatter, lines, fields)
                for lineno, line in enumerate(lines, 1):
                    if lineno not in field_lines:
                        continue
                    # match against the value only -- the 'key: ' prefix (or
                    # continuation indentation) would defeat value anchors and
                    # match key names; surrounding YAML quotes are stripped so
                    # anchors see the value the wiki writes via format.quote,
                    # and the reported line text stays raw
                    value = format.field_value(line)
                    if regex.search(value):
                        result.append((relpath, lineno, line))
            else:
                # search body content: skip only the frontmatter (the region the
                # word count and read slicing also exclude), so the three agree --
                # the H1 heading and an index's link block are body content and
                # are searched, since they are part of what read returns
                if frontmatter:
                    body_start = len(frontmatter.split('\n'))
                else:
                    body_start = 0
                for lineno, line in enumerate(lines, 1):
                    if lineno <= body_start:
                        continue
                    if regex.search(line):
                        result.append((relpath, lineno, line))
        return result

    def map(
        self: Wiki,
        name: Optional[str] = None,
        *,
        depth: Optional[int] = None,
        desc: bool = True,
        desc_limit: Optional[int] = None,
        category: Optional[str | list[str]] = None,
        markdown: Optional[bool] = None,
        words: bool = True,
    ) -> str:
        """Return a compact tree overview of the wiki.

        Walks the ``_index.md`` tree, producing an indented
        list of folders, pages, and files with descriptions
        from their parent index.

        Args:
            name: Restrict scope to named subtree (relative
                path). ``None`` means the entire wiki.
            depth: Maximum tree depth. ``None`` means no limit.
                ``0`` means top-level entries only.
            desc: Show descriptions from parent index.
            desc_limit: Maximum characters per description.
                Longer descriptions are truncated with ``...``
                suffix. ``None`` resolves ``map.desc_limit`` from
                ``settings.json`` (untruncated when unset); ``-1``
                disables truncation.
            category: Filter by category at root level.
                ``None`` means no filter (show all entries).
                A list of category names shows only entries
                matching those categories. An empty list shows
                only uncategorized entries.
            markdown: Filter by file type. ``None`` means show all.
                ``True`` means only folders and ``.md`` pages.
                ``False`` means only folders and non-``.md`` files.
            words: Show word counts (from the ``.wiki/cache/word_counts.json``
                cache, lazily recomputed on stale entries).

        Returns:
            Indented tree string.

        """
        if isinstance(category, str):
            category = [category]
        # resolve the desc limit (argument > map.desc_limit setting,
        # untruncated if neither is set); -1 explicitly disables truncation
        if desc_limit is None:
            desc_limit = self._map_policy['desc_limit']
        if desc_limit == -1:
            desc_limit = None
        if name:
            folder = self._resolve_folder(name)
        else:
            folder = self._root
        # counts come from the cache (lazily recomputed on stale entries);
        # skip the walk entirely when words are off
        counts = self._load_counts() if words else {}
        folder_words = self._folder_words(counts)
        # a category filter prunes folders by their subtree contents;
        # precompute the match set in one post-order pass over the same
        # link traversal instead of re-probing subtrees while rendering
        matches: set[pathlib.Path] = set()
        if category is not None:
            self._category_matches(
                folder=folder,
                category=category,
                markdown=markdown,
                matches=matches,
            )
        result = self._map_folder(
            folder=folder,
            indent='',
            current_depth=0,
            depth=depth,
            desc=desc,
            desc_limit=desc_limit,
            category=category,
            markdown=markdown,
            words=words,
            counts=counts,
            folder_words=folder_words,
            matches=matches,
        )
        # a markerless index still maps via the reclaimed parse; warn rather
        # than let the CLI read it silently
        self._warn_markerless_index(folder)
        return '\n'.join(result)

    def _utc_now(self: Wiki) -> str:
        """Return the current timestamp string (UTC, ISO 8601, by default).

        The timezone and strftime format are configurable via ``settings.json``
        (``timestamp.timezone`` / ``timestamp.format``); when a timezone is
        configured without an authored format, the default format's literal
        trailing ``Z`` becomes ``%z``, so the stamp carries the zone's real
        offset. Override in subclasses for a different time source.

        Returns:
            Timestamp string like ``2026-01-15T12:30:00Z``.

        """
        policy = self._timestamp_policy
        return dt.datetime.now(policy['zone']).strftime(policy['format'])

    @functools.cached_property
    def logger(self: Wiki) -> logging.Logger:
        """Return stdlib logger named by the fully qualified class."""
        return logging.getLogger(
            f'{self.__class__.__module__}.{self.__class__.__qualname__}'
        )

    def log(self: Wiki, message: str, level: Optional[int] = None) -> None:
        """Log a message (default ``logging.INFO``); handlers are the host's."""
        return self.logger.log(level or logging.INFO, message)

    def on_index_create(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.INFO,
        event: Optional[IndexCreateEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a created-index notice event.

        Constructs an ``IndexCreateEvent`` from ``message`` and the
        payload kwargs unless a pre-built plan-phase ``event`` is passed
        through, then delegates to ``on_notice``. Override in subclasses
        to intercept this notice kind alone; override ``on_notice`` to
        intercept every notice.
        """
        if event is None:
            event = IndexCreateEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_page_adopt(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.INFO,
        event: Optional[PageAdoptEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle an adopted-page notice event.

        Constructs a ``PageAdoptEvent`` from ``message`` and the
        payload kwargs unless a pre-built plan-phase ``event`` is passed
        through, then delegates to ``on_notice``. Override in subclasses
        to intercept this notice kind alone; override ``on_notice`` to
        intercept every notice.
        """
        if event is None:
            event = PageAdoptEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_link_add(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.INFO,
        event: Optional[LinkAddEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a new-link notice event.

        Constructs a ``LinkAddEvent`` from ``message`` and the payload
        kwargs unless a pre-built plan-phase ``event`` is passed
        through, then delegates to ``on_notice``. Override in subclasses
        to intercept this notice kind alone; override ``on_notice`` to
        intercept every notice.
        """
        if event is None:
            event = LinkAddEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_link_break(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.WARNING,
        event: Optional[LinkBreakEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a broken-link notice event.

        Constructs a ``LinkBreakEvent`` from ``message`` and the payload
        kwargs unless a pre-built plan-phase ``event`` is passed
        through, then delegates to ``on_notice``. Override in subclasses
        to intercept this notice kind alone; override ``on_notice`` to
        intercept every notice.
        """
        if event is None:
            event = LinkBreakEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_link_prune(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.INFO,
        event: Optional[LinkPruneEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a pruned-link notice event.

        Constructs a ``LinkPruneEvent`` from ``message`` and the payload
        kwargs unless a pre-built plan-phase ``event`` is passed
        through, then delegates to ``on_notice``. Override in subclasses
        to intercept this notice kind alone; override ``on_notice`` to
        intercept every notice.
        """
        if event is None:
            event = LinkPruneEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_desc_overwrite(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.WARNING,
        event: Optional[DescOverwriteEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle an overwritten-desc notice event.

        Constructs a ``DescOverwriteEvent`` from ``message`` and the
        payload kwargs unless a pre-built plan-phase ``event`` is passed
        through, then delegates to ``on_notice``. Override in subclasses
        to intercept this notice kind alone; override ``on_notice`` to
        intercept every notice.
        """
        if event is None:
            event = DescOverwriteEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_name_skip(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.WARNING,
        event: Optional[NameSkipEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle an invalid-name skip notice event.

        Constructs a ``NameSkipEvent`` from ``message`` and the payload
        kwargs unless a pre-built plan-phase ``event`` is passed
        through, then delegates to ``on_notice``. Override in subclasses
        to intercept this notice kind alone; override ``on_notice`` to
        intercept every notice.
        """
        if event is None:
            event = NameSkipEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_symlink_skip(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.WARNING,
        event: Optional[SymlinkSkipEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a symlinked-target skip notice event.

        Constructs a ``SymlinkSkipEvent`` from ``message`` and the
        payload kwargs unless a pre-built plan-phase ``event`` is passed
        through, then delegates to ``on_notice``. Override in subclasses
        to intercept this notice kind alone; override ``on_notice`` to
        intercept every notice.
        """
        if event is None:
            event = SymlinkSkipEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_write_skip(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.WARNING,
        event: Optional[WriteSkipEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a concurrent-edit skip notice event.

        Constructs a ``WriteSkipEvent`` from ``message`` and the payload
        kwargs (the live-site path) unless a pre-built ``event`` is
        passed through, then delegates to ``on_notice``. Override in
        subclasses to intercept this notice kind alone; override
        ``on_notice`` to intercept every notice.
        """
        if event is None:
            event = WriteSkipEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_frontmatter_malformed(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.WARNING,
        event: Optional[FrontmatterMalformedEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a malformed-frontmatter notice event.

        Constructs a ``FrontmatterMalformedEvent`` from ``message`` and
        the payload kwargs unless a pre-built plan-phase ``event`` is
        passed through, then delegates to ``on_notice``. Override in
        subclasses to intercept this notice kind alone; override
        ``on_notice`` to intercept every notice.
        """
        if event is None:
            event = FrontmatterMalformedEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_index_truncated(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.WARNING,
        event: Optional[IndexTruncatedEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a truncated-index notice event.

        Constructs an ``IndexTruncatedEvent`` from ``message`` and the
        payload kwargs unless a pre-built plan-phase ``event`` is passed
        through, then delegates to ``on_notice``. Override in subclasses
        to intercept this notice kind alone; override ``on_notice`` to
        intercept every notice.
        """
        if event is None:
            event = IndexTruncatedEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_index_markerless(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.WARNING,
        event: Optional[IndexMarkerlessEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a markerless-index notice event.

        Constructs an ``IndexMarkerlessEvent`` from ``message`` and the
        payload kwargs (the live-site path) unless a pre-built ``event``
        is passed through, then delegates to ``on_notice``. Override in
        subclasses to intercept this notice kind alone; override
        ``on_notice`` to intercept every notice.
        """
        if event is None:
            event = IndexMarkerlessEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_settings_restore(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.INFO,
        event: Optional[SettingsRestoreEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a restored-settings notice event.

        Constructs a ``SettingsRestoreEvent`` from ``message`` and the
        payload kwargs unless a pre-built ``event`` is passed through,
        then delegates to ``on_notice``. Override in subclasses to
        intercept this notice kind alone; override ``on_notice`` to
        intercept every notice.
        """
        if event is None:
            event = SettingsRestoreEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_cache_restore(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.INFO,
        event: Optional[CacheRestoreEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a recreated-cache notice event.

        Constructs a ``CacheRestoreEvent`` from ``message`` and the
        payload kwargs (the live-site path) unless a pre-built ``event``
        is passed through, then delegates to ``on_notice``. Override in
        subclasses to intercept this notice kind alone; override
        ``on_notice`` to intercept every notice.
        """
        if event is None:
            event = CacheRestoreEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_desc_missing(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.INFO,
        event: Optional[DescMissingEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a missing-desc notice event.

        Constructs a ``DescMissingEvent`` from ``message`` and the
        payload kwargs (the live-site path) unless a pre-built ``event``
        is passed through, then delegates to ``on_notice``. Override in
        subclasses to intercept this notice kind alone; override
        ``on_notice`` to intercept every notice.
        """
        if event is None:
            event = DescMissingEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_desc_long(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.INFO,
        event: Optional[DescLongEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a long-desc notice event.

        Constructs a ``DescLongEvent`` from ``message`` and the
        payload kwargs (the live-site path and folded desc length)
        unless a pre-built ``event`` is passed through, then delegates
        to ``on_notice``. Override in subclasses to intercept this
        notice kind alone; override ``on_notice`` to intercept every
        notice.
        """
        if event is None:
            event = DescLongEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_content_empty(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.INFO,
        event: Optional[ContentEmptyEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle an empty-content notice event.

        Constructs a ``ContentEmptyEvent`` from ``message`` and the
        payload kwargs (the live-site path) unless a pre-built ``event``
        is passed through, then delegates to ``on_notice``. Override in
        subclasses to intercept this notice kind alone; override
        ``on_notice`` to intercept every notice.
        """
        if event is None:
            event = ContentEmptyEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_crlf_notice(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.INFO,
        event: Optional[CrlfNoticeEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a CRLF-line-endings notice event.

        Constructs a ``CrlfNoticeEvent`` from ``message`` and the
        payload kwargs (the live-site path) unless a pre-built ``event``
        is passed through, then delegates to ``on_notice``. Override in
        subclasses to intercept this notice kind alone; override
        ``on_notice`` to intercept every notice.
        """
        if event is None:
            event = CrlfNoticeEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_link_stale(
        self: Wiki,
        message: Optional[str] = None,
        *,
        logging_level: int = logging.INFO,
        event: Optional[LinkStaleEvent] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a stale-link note event.

        Constructs a ``LinkStaleEvent`` from ``message`` and the
        payload kwargs (the live-site path) unless a pre-built ``event``
        is passed through, then delegates to ``on_notice``. Override in
        subclasses to intercept this notice kind alone; override
        ``on_notice`` to intercept every notice.
        """
        if event is None:
            event = LinkStaleEvent(message, **kwargs)
        return self.on_notice(event, logging_level=logging_level)

    def on_notice(
        self: Wiki,
        event: Event,
        *,
        logging_level: Optional[int] = None,
        **kwargs: Any,
    ) -> Event:
        """Handle a wiki notice event.

        The single diagnostics seam (all engine narration, damage
        reports, and soft notes flow through here): logs
        ``event.description`` at the event's severity and returns the
        event. Override in subclasses to route notices to a different
        logging system; the CLI swaps this hook per instance to capture
        and condense.
        """
        self.log(event.description, logging_level or event.logging_level)
        return event

    def _dispatch_notice(self: Wiki, event: Event) -> Event:
        """Dispatch a pre-built notice event through its per-kind hook."""
        return getattr(self, _NOTICE_HOOKS[type(event)])(event=event)

    def _ensure_settings(self: Wiki) -> list[Event]:
        """Materialize a missing ``.wiki/settings.json`` as ``{}``.

        The settings file is the declared-root marker: ``init`` writes it
        and every mutating command restores a lost one, so the declaration
        guarantee is enforced rather than assumed. The materialized file
        is an empty object -- all defaults; policy is never invented.

        Returns:
            Notice events describing the restoration (empty when present).

        Raises:
            ValueError: If a root ``_config/settings.json`` exists -- the
                legacy config namespace is never read, so its policy must
                migrate before any policy-reading write proceeds.

        """
        # TODO: remove back-compat in future version
        self._refuse_legacy_layout()
        path = self._root / WIKI_SETTINGS
        if path.exists():
            return []
        path.parent.mkdir(parents=True, exist_ok=True)
        wiki.util.fs.write_atomic(path, '{}\n')
        return [SettingsRestoreEvent(path=WIKI_SETTINGS)]

    def _refuse_legacy_layout(self: Wiki) -> None:
        """Refuse to plan a sweep on a legacy-layout wiki.

        A root ``_config/settings.json`` marks the legacy config
        namespace, which is never read: until its policy migrates to
        ``.wiki/``, a write sweep would run on defaults and index
        ``_config/`` as content -- and a dry run (``update --check``,
        ``lint``) would preview that same sweep, so every sweep-planning
        path refuses alike. Read paths (``read``, ``search``, ``map``)
        stay tolerant.

        Raises:
            ValueError: If a root ``_config/settings.json`` exists.

        Todo:
            Remove back-compat in future versions -- this guard and
            the marked legacy blocks retire together.

        """
        # TODO: remove back-compat in future version
        if (self._root / '_config' / 'settings.json').is_file():
            raise ValueError(
                'Legacy wiki layout. Please perform the following migration:'
                '\n  (1) move `_config/` -> `.wiki/` (delete `.wiki/` first if present)'
                '\n  (2) run `wiki config`'
                '\n  (3) run `wiki update`'
            )

    def _index_missing_marker(self: Wiki, folder: pathlib.Path) -> bool:
        """Return ``True`` if ``folder``'s index lost its ``***`` delimiter.

        The delimiter separates generated links from user content; without it
        :func:`format.parse_index` must heuristically reclaim the demoted link
        block (see :func:`format.reclaim_link_run`). Reports the gap only when
        the index exists and the folder has on-disk children that should be
        linked, so a genuinely empty wiki is not mistaken for a broken one.
        """
        # the index must exist to be missing its marker
        index_path = folder / WIKI_INDEX
        if not index_path.is_file():
            return False
        # only a folder with on-disk children (pages or child folders) would
        # lose links to the gap; the parent '..' link is not a child, so check
        # the filesystem directly rather than _build_expected_links
        has_pages = bool(self._find_pages(folder))
        has_dirs = any(
            child.is_dir() and not self._is_excluded_dir(child)
            for child in folder.iterdir()
        )
        if not (has_pages or has_dirs):
            return False
        # the marker is a line equal to the delimiter
        text = self._read_text(index_path)
        lines = [line.rstrip() for line in text.split('\n')]
        return self.index_delimiter not in lines

    def _index_mangled_marker(self: Wiki, folder: pathlib.Path) -> bool:
        """Return ``True`` if a thematic break stands where ``***`` belongs.

        Only meaningful when the index is missing its delimiter (see
        :meth:`_index_missing_marker`): a markdown formatter rewrites the
        ``***`` delimiter to a ``---``/``___`` thematic break, so a break
        standing directly after the leading link run (or the H1, when the
        link block is empty) is the signature of formatter damage rather
        than a hand-deleted marker. A break deeper in body prose is
        ordinary content and never matches.
        """
        text = self._read_text(folder / WIKI_INDEX)
        lines = text.split('\n')
        _, line_number = format.extract_frontmatter(lines)
        # unclosed frontmatter extracts as none, leaving its own opening
        # '---' first in the walk (past any leading blanks): a truncated
        # index, not a rewritten marker
        opener = next((line for line in lines if line.strip()), '')
        if (line_number == 0) and (opener.lstrip('\ufeff').strip() == '---'):
            return False
        # walk past the H1 and the leading link run (formatter escapes and
        # desc continuations directly under a link tolerated, mirroring
        # format.reclaim_link_run) to the line standing where the delimiter
        # belongs; the walk is a deliberately coarser damage classifier than
        # the grammar's reclaim -- blanks and the H1 are gaps it keeps
        # walking past, and the escape-undo is unconditional where the
        # reclaim gates on the \[ damage shape -- so it must not be rebuilt
        # on format.match_link_row (unifying them flips lint's damage
        # classification on formatter-damaged inputs)
        in_run = False
        gap = True
        for line in lines[line_number:]:
            stripped = line.strip()
            if not stripped or re.match(r'^#\s', stripped):
                gap = True
                continue
            candidate = re.sub(r'\\([\[\]_])', r'\1', stripped)
            if re.match(r'^\[\[.+?\|.+?\]\](?::\s*.*)?$', candidate):
                in_run = True
                gap = False
                continue
            if re.fullmatch(r'\*{3,}|-{3,}|_{3,}', stripped):
                return True
            # a line directly under a link is a desc continuation
            if in_run and not gap:
                continue
            return False
        return False

    def _warn_markerless_index(self: Wiki, folder: pathlib.Path) -> None:
        """Warn when ``folder``'s index is missing its ``***`` delimiter.

        Used by :meth:`map` so a damaged index is reported (with its fix:
        run ``wiki update``) rather than mapped silently from the reclaimed
        parse.
        """
        if self._index_missing_marker(folder):
            relpath = (folder / WIKI_INDEX).relative_to(self._root)
            self.on_index_markerless(path=str(relpath))

    def _download(self: Wiki, url: str, target: pathlib.Path) -> None:
        """Download ``url`` to ``target``.

        Override in subclasses to use a different fetch mechanism.

        Args:
            url: Source URL to download.
            target: Destination file path.

        Raises:
            OSError: If the connection fails (e.g. no network).
            http.client.HTTPException: If the response is malformed
                (e.g. the connection drops mid-download).

        """
        # fetch the asset (pinned https url; scheme is safe)
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            target.write_bytes(response.read())

    def _enrich_frontmatter(
        self: Wiki,
        path: pathlib.Path,
        frontmatter: str,
    ) -> str:
        """Hook for enriching frontmatter during a plan.

        Called from :meth:`_plan_index` after frontmatter is constructed
        or parsed. Override in subclasses to modify frontmatter
        (e.g. enrich tags from external sources).

        Because the plan backs both ``update`` and ``lint``, an override
        must be idempotent and side-effect-free: it runs on every
        ``lint`` as well, where lint reports drift only when the enriched
        result differs from disk.

        Args:
            path: Path to the index file.
            frontmatter: Current frontmatter string.

        Returns:
            Possibly modified frontmatter string.

        """
        return frontmatter

    def _resolve_path(self: Wiki, name: str) -> pathlib.Path:
        """Resolve a wiki name to a file path.

        Resolution order:

        1. If the path is a directory, return its ``_index.md``.
        2. If the path is an existing file, return it directly.
        3. Try appending ``.md`` extension.

        Args:
            name: Relative path (e.g. ``core/design``).

        Raises:
            FileNotFoundError: If the resolved path does not exist.
            ValueError: If the resolved path is outside the wiki root.

        """
        if not name.strip():
            raise self._entry_not_found(name)
        path = (self._root / name).resolve()
        if not path.is_relative_to(self._root):
            raise self._outside_root(name)
        if path.is_dir():
            index = path / WIKI_INDEX
            if index.is_file():
                return index
        if path.is_file():
            return path
        # append (not with_suffix, which would replace a dotted name's last
        # segment -- 'app.config' -> 'app.md') so a page whose name contains a
        # dot (e.g. 'app.config', 'v1.2') resolves to '<name>.md'; on the root
        # itself with_name escapes to '<root parent>/<root name>.md', so the
        # fallback must stay contained before it is probed
        result = path.with_name(path.name + '.md')
        if result.is_relative_to(self._root) and result.is_file():
            return result
        # the literal path missed; a bare leaf ('oncall') for a nested page
        # ('team/eng/oncall') is a common miss -- when exactly one page's stem
        # matches, name its full read key in the error so the user can retry
        suggestion = self._suggest_leaf_match(name)
        raise self._entry_not_found(name, suggestion)

    def _suggest_leaf_match(self: Wiki, name: str) -> Optional[str]:
        """Suggest a nested page whose leaf matches a failed ``read`` name.

        Walks the tree for markdown pages whose stem equals the final
        component of ``name`` (e.g. ``oncall`` -> ``team/eng/oncall``).
        Returns the unique match's read key (the path-joined name) when
        exactly one page matches, else ``None`` (no hint for an ambiguous
        or absent leaf).
        """
        # match on the final component of the requested name
        leaf = pathlib.Path(name).name
        matches = []
        for folder in self._find_dirs(self._root):
            for page in self._find_pages(folder):
                if (page.suffix == '.md') and (page.stem == leaf):
                    matches.append(self._path_to_name(page))
        # only suggest when the match is unambiguous
        if len(matches) == 1:
            return matches[0]
        return None

    def _entry_not_found(
        self: Wiki,
        name: str,
        suggestion: Optional[str] = None,
    ) -> FileNotFoundError:
        """Build the entry not-found error, naming any suggested read key."""
        message = f'Wiki entry not found: {name!r}'
        if suggestion is not None:
            message += f' (did you mean {suggestion}?)'
        return FileNotFoundError(message)

    def _outside_root(self: Wiki, name: str) -> ValueError:
        """Build the outside-wiki-root error for a caller-supplied path."""
        return ValueError(f'Path is outside wiki root: {name!r}')

    def _resolve_folder(self: Wiki, path: str) -> pathlib.Path:
        """Resolve a wiki name to a folder path.

        Args:
            path: Relative path (e.g. ``core/store``).

        Raises:
            FileNotFoundError: If the resolved folder does not exist.
            ValueError: If the resolved path is outside the wiki root.

        """
        resolved = pathlib.Path(path)
        if not resolved.is_absolute():
            resolved = self._root / resolved
        resolved = resolved.resolve()
        if resolved.is_dir():
            if not resolved.is_relative_to(self._root):
                raise self._outside_root(path)
            # reject a scope that is (or nests under) an excluded dot
            # directory (a symlinked scope already resolved to its real
            # target above): the walk skips those by construction, so
            # scaffolding indexes into .wiki/.git/.obsidian would leave
            # junk no later walk can see or repair
            for ancestor in (resolved, *resolved.parents):
                if ancestor == self._root:
                    break
                if self._is_excluded_dir(ancestor):
                    raise ValueError(f'Path is inside an excluded directory: {path!r}')
            return resolved
        raise FileNotFoundError(f'Wiki folder not found: {path!r}')

    def _path_to_name(self: Wiki, path: pathlib.Path) -> str:
        """Convert a wiki path (folder or page) to a display name.

        Uses slashes for structural folders (e.g. ``core/store``).
        Returns the root name for the root folder.
        """
        # return root wiki name
        if path == self._root:
            return self._root_name
        # strip .md suffix for pages
        relpath = path.relative_to(self._root)
        if path.is_file():
            relpath = relpath.with_suffix('')
        parts = relpath.parts
        # join parts, composed to NFC: the name is display identity, so a
        # decomposed on-disk form (an NFD-producing filesystem) must not
        # churn a name: field a sibling platform wrote composed
        return unicodedata.normalize('NFC', self.path_sep.join(parts))

    def _link_target(self: Wiki, path: pathlib.Path) -> str:
        """Convert a path to a wikilink target relative to wiki root.

        Targets always join with ``/`` (``as_posix``), the wikilink
        grammar's separator, never the platform's.
        """
        relpath = path.relative_to(self._root)
        return relpath.with_suffix('').as_posix()

    def _target_page(self: Wiki, target: str) -> Optional[pathlib.Path]:
        """Return the markdown page a link ``target`` names, or ``None``.

        Mirrors read resolution: the literal on-disk file wins, so an
        explicit ``.md`` target is the page itself and a raw file's
        target never dereferences to a same-named sidecar page (e.g.
        ``Makefile`` beside ``Makefile.md`` -- the page's desc and word
        count belong on the page's own row); otherwise the target
        appends ``.md``, the form the wikilink grammar strips.
        """
        literal = self._root / target
        if literal.is_file():
            if literal.suffix == '.md':
                return literal
            return None
        page = self._root / (target + '.md')
        if page.is_file():
            return page
        return None

    def _is_excluded_file(self: Wiki, path: pathlib.Path) -> bool:
        """Return ``True`` if file should be excluded from index links.

        Excludes wiki index files (handled separately as the folder
        index), files with ``.`` prefix, and symlinked files (reading and
        rewriting a page through a symlink would copy the symlink target's
        content -- possibly from outside the wiki root -- into a tracked
        file, the file-level analogue of the ``_is_excluded_dir`` guard).
        """
        return path.name == WIKI_INDEX or path.name.startswith('.') or path.is_symlink()

    def _is_excluded_dir(self: Wiki, path: pathlib.Path) -> bool:
        """Return ``True`` if directory should be excluded from index links.

        Excludes directories with ``.`` prefix (which keeps the ``.wiki``
        tool directory out of the walk by construction) and symlinked
        directories (following a symlink re-walks the same inode, producing
        duplicate/conflicting index writes and risking loops).
        """
        return path.name.startswith('.') or path.is_symlink()

    def _is_symlink_skipped(self: Wiki, target: str) -> bool:
        """Return ``True`` if link ``target`` names a symlinked file on disk.

        A broken index link whose target still exists as a symlinked file
        is not missing -- :meth:`_is_excluded_file` drops symlinks from
        the walk -- so callers can name the exclusion as the cause instead
        of a generic broken-link report. Probes the target's byte form and
        the ``.md`` form the wikilink grammar strips.
        """
        for name in (target, target + '.md'):
            # a preserved target may carry '..' or an absolute path, so
            # contain the probe to the root lexically (never resolved --
            # the symlink itself may point outside the root)
            joined = os.path.normpath(self._root / name)
            probe = pathlib.Path(joined)
            if not probe.is_relative_to(self._root):
                continue
            if probe.is_symlink():
                return True
        return False

    def _find_dirs(self: Wiki, root: pathlib.Path) -> list[pathlib.Path]:
        """Return all non-excluded directories under ``root``, depth-first."""
        result = [root]
        for child in sorted(root.iterdir()):
            if child.is_dir() and not self._is_excluded_dir(child):
                result.extend(self._find_dirs(child))
        return result

    def _find_pages(self: Wiki, folder: pathlib.Path) -> list[pathlib.Path]:
        """Return non-excluded files in ``folder``."""
        result = []
        for path in sorted(folder.iterdir()):
            if path.is_file() and not self._is_excluded_file(path):
                result.append(path)
        return result

    def _search_files(
        self: Wiki,
        folder: pathlib.Path,
        *,
        all_files: bool = False,
    ) -> list[pathlib.Path]:
        """Enumerate wiki files for search.

        Returns wiki-tracked files: ``_index.md`` for each
        directory plus markdown pages. When ``all_files`` is
        ``True``, non-markdown pages are included as well.

        Args:
            folder: Root folder to search under.
            all_files: Include non-markdown files.

        """
        result = []
        for directory in self._find_dirs(folder):
            index = directory / WIKI_INDEX
            if index.is_file():
                result.append(index)
            for page in self._find_pages(directory):
                if all_files or (page.suffix == '.md'):
                    result.append(page)
        return result

    def _read_text(self: Wiki, path: pathlib.Path) -> str:
        """Read ``path`` as UTF-8, naming the file on undecodable bytes.

        A bare ``UnicodeDecodeError`` carries only a byte offset --
        unactionable on a tree of thousands of files -- so the re-raise
        appends the offending file's relative path to the reason.
        """
        try:
            return path.read_text(encoding='utf-8')
        except UnicodeDecodeError as e:
            relpath = path.relative_to(self._root)
            raise UnicodeDecodeError(
                e.encoding,
                e.object,
                e.start,
                e.end,
                f'{e.reason} (in {relpath})',
            ) from e

    def _has_crlf(self: Wiki, path: pathlib.Path) -> bool:
        r"""Return ``True`` if ``path``'s bytes carry CR line endings.

        Universal-newline reads translate ``\r\n`` (and a lone ``\r``) to
        ``\n``, so a CRLF file reads -- and plans -- as identical to its
        normalized form; this byte probe is what makes the drift visible
        to update and lint.
        """
        return b'\r' in path.read_bytes()

    def _current_text(
        self: Wiki,
        path: pathlib.Path,
        overlay: Optional[dict[pathlib.Path, str]] = None,
    ) -> Optional[str]:
        """Read a file's content, preferring staged plan content.

        During a plan (:meth:`_plan`), earlier passes stage corrected
        content in ``overlay`` without writing to disk. Routing every
        cross-file content read through this resolver makes a cascade
        (a child's category or desc flowing into a parent link label)
        visible without touching disk. When ``overlay`` is ``None``
        (the default, used outside a plan), reads come from disk.

        Args:
            path: File to read.
            overlay: Staged ``{path: content}`` from earlier passes.

        Returns:
            Staged content if present, the on-disk text if the file
            exists, otherwise ``None``.

        """
        if (overlay is not None) and (path in overlay):
            return overlay[path]
        if path.exists():
            return self._read_text(path)
        return None

    def _load_counts(self: Wiki) -> dict[str, int]:
        """Return body word counts for every markdown file, via the cache.

        Reads ``.wiki/cache/word_counts.json`` under the wiki root, recomputes
        entries whose cached mtime/size no longer match the file's, drops
        entries for files that are gone, and rewrites the cache when anything
        changed. A corrupt or unreadable cache is discarded and fully
        recomputed, and a failed cache write is swallowed -- the cache can
        never break a command, the worst case is a full recompute.
        ``update`` calls this after writing, so the cache tracks the tree.

        Returns:
            Dict mapping root-relative paths to body word counts.

        """
        # load the existing cache, tolerating absence or corruption
        cache_path = self._root / WIKI_CACHE / 'word_counts.json'
        try:
            cached = json.loads(cache_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            cached = {}
        if not isinstance(cached, dict):
            cached = {}
        # walk the wiki, reusing fresh entries and recomputing stale ones
        result = {}
        entries = {}
        dirty = False
        for folder in self._find_dirs(self._root):
            paths = [folder / WIKI_INDEX, *self._find_pages(folder)]
            for path in paths:
                if (path.suffix != '.md') or not path.is_file():
                    continue
                # keys are composed to NFC, the form index-derived lookups use
                relative = str(path.relative_to(self._root))
                relpath = unicodedata.normalize('NFC', relative)
                # freshness keys on mtime and size: size catches a rewrite
                # landing within the filesystem's mtime granularity, and bool
                # is excluded because it passes an isinstance int check
                stat = path.stat()
                entry = cached.get(relpath)
                fresh = isinstance(entry, dict)
                if fresh:
                    count = entry.get('words')
                    same_mtime = entry.get('mtime') == stat.st_mtime
                    same_size = entry.get('size') == stat.st_size
                    counted = isinstance(count, int) and not isinstance(count, bool)
                    fresh = same_mtime and same_size and counted
                if not fresh:
                    # an undecodable page has no countable body
                    try:
                        words = format.body_words(self._read_text(path))
                    except UnicodeDecodeError:
                        words = 0
                    entry = {
                        'mtime': stat.st_mtime,
                        'size': stat.st_size,
                        'words': words,
                    }
                    dirty = True
                entries[relpath] = entry
                result[relpath] = entry['words']
        # rewrite the cache when entries changed (recomputes, adds, or drops);
        # the cache is a pure optimization, so a failed flush (a read-only
        # tree, a full disk) degrades to a recompute on the next run rather
        # than failing the command
        if dirty or (set(entries) != set(cached)):
            try:
                self._write_counts(entries)
            except OSError:
                pass
        return result

    def _write_counts(self: Wiki, entries: dict) -> None:
        """Write the counts cache, materializing a self-ignoring ``.wiki/cache/``.

        The cache directory carries its own ``.gitignore`` (``*``) so the
        derived state never needs host-repo ignore configuration (the
        settings file beside it stays tracked), and the ``.wiki`` dot
        prefix keeps it out of the wiki walk.
        """
        cache_dir = self._root / WIKI_CACHE
        cache_dir.mkdir(parents=True, exist_ok=True)
        gitignore = cache_dir / '.gitignore'
        if not gitignore.exists():
            wiki.util.fs.write_atomic(gitignore, '*\n')
        content = json.dumps(entries, indent=2, sort_keys=True)
        wiki.util.fs.write_atomic(cache_dir / 'word_counts.json', content + '\n')

    def _folder_words(self: Wiki, counts: dict[str, int]) -> dict[str, int]:
        """Sum per-file counts into per-folder tree totals.

        Every file's count contributes to each of its ancestor folders, so
        a folder's total covers its whole subtree (its own index included).
        The root folder is keyed by the empty string.

        Args:
            counts: Root-relative path -> body word count (from
                :meth:`_load_counts`).

        """
        result = {}
        for relpath, words in counts.items():
            parent = pathlib.PurePath(relpath).parent
            while True:
                key = '' if str(parent) == '.' else str(parent)
                result[key] = result.get(key, 0) + words
                if not key:
                    break
                parent = parent.parent
        return result

    def _slice(
        self: Wiki,
        content: str,
        path: pathlib.Path,
        start: Optional[int],
        stop: Optional[int],
        on: str = 'lines',
    ) -> str:
        """Slice content ``on`` ``lines``, ``words``, or ``chars``.

        For markdown the frontmatter is lifted off and re-added around the slice
        so it stays valid; the H1 is ordinary body content and is sliced with the
        rest. Non-markdown files are sliced whole. A ``words`` slice keeps the
        original spacing between the first and last selected words rather than
        collapsing runs of whitespace.

        Args:
            content: Full file content.
            path: File path (used to detect markdown).
            start: Start index (0-indexed, inclusive), in units of ``on``.
            stop: Stop index (exclusive).
            on: Slice unit -- ``lines``, ``words``, or ``chars``.

        Returns:
            Sliced content string.

        """
        # lift frontmatter off markdown so the slice stays valid markdown
        if path.suffix == '.md':
            frontmatter, body = format.parse_page(content)
        else:
            frontmatter = ''
            body = content
        # drop structural leading/trailing blank lines so the indices align
        body = body.strip()
        # slice the body
        if on == 'lines':
            lines = body.splitlines()
            result = '\n'.join(lines[start:stop])
        elif on == 'words':
            # words: keep the original span from the first to the last word
            spans = [match.span() for match in re.finditer(r'\S+', body)]
            chosen = spans[start:stop]
            if chosen:
                begin, end = chosen[0][0], chosen[-1][1]
                result = body[begin:end]
            else:
                result = ''
        elif on == 'chars':
            result = body[start:stop]
        else:
            raise ValueError(
                f"Invalid value {on!r} (must be 'lines', 'words', or 'chars')."
            )
        # recombine frontmatter and the sliced body as valid markdown
        parts = [part for part in (frontmatter, result) if part]
        return '\n\n'.join(parts) + '\n'

    def _build_expected_links(
        self: Wiki,
        folder: pathlib.Path,
    ) -> list[tuple[str, str]]:
        """Build expected ``(target, base_label)`` pairs from filesystem.

        Labels are base names only (no category prefix).
        Folders get a trailing ``/``. Targets join with ``/``
        (``as_posix``), the wikilink grammar's separator, never the
        platform's.
        """
        # initialize links
        result = []
        # parent link
        if folder != self._root:
            parent = folder.parent
            target = parent / WIKI_INDEX
            target = target.relative_to(self._root).with_suffix('').as_posix()
            result.append((target, '..'))
        # child directory links
        children = []
        for path in folder.iterdir():
            if path.is_dir() and not self._is_excluded_dir(path):
                children.append(path)
        for child in sorted(children):
            target = child / WIKI_INDEX
            target = target.relative_to(self._root).with_suffix('').as_posix()
            result.append((target, f'{child.name}/'))
        # page links; a page whose stripped name a sibling file claims (e.g.
        # Makefile.md beside Makefile) links by its full name, suffix included,
        # like a non-markdown file -- stripping would collide both entries on
        # one target, duplicating rows and shadowing the page on read
        for page in self._find_pages(folder):
            if (page.suffix == '.md') and not page.with_suffix('').is_file():
                target = page.relative_to(self._root).with_suffix('').as_posix()
                result.append((target, page.stem))
            else:
                target = page.relative_to(self._root).as_posix()
                result.append((target, page.name))
        # return links, labels composed to NFC: labels are display identity
        # (like name:), so a decomposed on-disk form must not churn a label a
        # sibling platform wrote composed; targets keep the on-disk byte form
        # they resolve by
        return [
            (target, unicodedata.normalize('NFC', label)) for target, label in result
        ]

    def _invalid_links(
        self: Wiki,
        folder: pathlib.Path,
    ) -> list[tuple[str, str, str]]:
        """Return ``(target, relpath, reason)`` for entries with invalid names.

        A child folder or page whose name/stem fails :meth:`validate_name`
        (e.g. a denied ``|`` or ``#``) is reported so the plan can drop its
        link rather than emit a malformed wikilink. ``target`` matches the
        wikilink target :meth:`_build_expected_links` would have produced;
        ``relpath`` names the offending path and ``reason`` the broken rule
        for the warning.
        """
        # initialize results
        result = []
        # child directory entries (validated on the folder name)
        for path in sorted(folder.iterdir()):
            if path.is_dir() and not self._is_excluded_dir(path):
                violation = self._name_violation(path.name)
                if violation is not None:
                    target = path / WIKI_INDEX
                    target = target.relative_to(self._root).with_suffix('').as_posix()
                    relpath = str(path.relative_to(self._root))
                    result.append((target, relpath, violation))
        # page entries (validated on the stem for pages; a non-markdown file
        # -- or a page linking by its full name because a sibling file claims
        # the stripped one -- links suffix included, so validate what the
        # wikilink would carry)
        for page in self._find_pages(folder):
            if (page.suffix == '.md') and not page.with_suffix('').is_file():
                violation = self._name_violation(page.stem)
            else:
                violation = self._name_violation(page.name)
            if violation is not None:
                if (page.suffix == '.md') and not page.with_suffix('').is_file():
                    target = page.relative_to(self._root).with_suffix('').as_posix()
                else:
                    target = page.relative_to(self._root).as_posix()
                result.append((target, str(page.relative_to(self._root)), violation))
        # return invalid entries
        return result

    def _merge_links(
        self: Wiki,
        existing: list[Link],
        expected: list[tuple[str, str]],
        *,
        labels: Optional[dict[str, str]] = None,
        prune: bool = False,
    ) -> tuple[list[Link], list[Link], list[Link]]:
        """Merge existing links with expected, refreshing labels and preserving descs.

        ``expected`` provides ``(target, base_label)`` pairs from the filesystem.
        ``labels`` maps a target to its categorized label
        (e.g. ``{'path/_index': '[store] name/'}``), supplied every update by
        ``_read_child_labels``.

        Each expected link's label is recomputed from current state: the categorized
        label when ``labels`` has the target, otherwise the base label. Descriptions
        are preserved from existing links; new links get ``...``. Parent links
        (``..``) never get a description. Targets are matched on their
        NFC-composed form, and a matched link keeps its existing target
        string, so a decomposed filesystem (HFS+, NFD-producing mounts)
        never breaks -- or churns -- the composed rows a sibling
        platform wrote.

        When ``prune`` is ``False`` (default), broken links (existing targets
        no longer on the filesystem) are preserved in the merged result.
        When ``True``, they are excluded.

        Returns:
            Tuple of ``(merged, broken, new)`` where each is a list
            of ``(target, label, description)`` tuples.

        """
        # lookup existing by NFC-composed target (matched rows keep their own
        # target form, so the index never churns between the two forms)
        existing_by_target = {
            unicodedata.normalize('NFC', target): (target, label, desc)
            for target, label, desc in existing
        }
        expected_targets = {
            unicodedata.normalize('NFC', target) for target, _ in expected
        }
        # identify broken links (in existing but not in expected)
        broken = []
        for target, label, desc in existing:
            normalized = unicodedata.normalize('NFC', target)
            if (normalized not in expected_targets) and (label != '..'):
                broken.append((target, label, desc))
        # build merged list from expected
        result = []
        new = []
        for target, base_label in expected:
            # authoritative label: categorized if available, else base
            if labels and (target in labels):
                label = labels[target]
            else:
                label = base_label
            normalized = unicodedata.normalize('NFC', target)
            if normalized in existing_by_target:
                # preserve target form and description,
                # refresh label from current state
                target, _, desc = existing_by_target[normalized]
                result.append((target, label, desc))
            else:
                # new entry: seed description
                desc = '' if base_label == '..' else '...'
                result.append((target, label, desc))
                if base_label != '..':
                    new.append((target, label, desc))
        # preserve broken links when not pruning
        if not prune:
            result.extend(broken)
        # return merged links, broken links, and new links
        return result, broken, new

    def _sort_links(
        self: Wiki,
        links: list[Link],
    ) -> list[Link]:
        """Sort links: parent first, then categorized, then uncategorized.

        Within categorized entries, sorts by ``(category_order index,
        base_name)``. Categories in ``category_order`` come first in
        that order; unlisted categories sort alphabetically after.
        Uncategorized entries sort alphabetically by label, always last.
        """
        # initialize links
        parent = []
        categorized = []
        uncategorized = []
        for link in links:
            _target, label, _desc = link
            if label == '..':
                parent.append(link)
                continue
            category, base_name = self._parse_category(label)
            if category:
                categorized.append((link, category, base_name))
            else:
                uncategorized.append(link)
        # sort categorized by (order_index, base_name)
        order = self.category_order or []

        def category_key(item: tuple) -> tuple:
            _, category, base_name = item
            if category in order:
                return (0, order.index(category), base_name)
            return (1, category, base_name)

        categorized.sort(key=category_key)
        # sort uncategorized by label
        uncategorized.sort(key=lambda link: link[1])
        return parent + [link for link, _, _ in categorized] + uncategorized

    def _parse_category(self: Wiki, label: str) -> tuple[str, str]:
        """Split a label into ``(category, base_name)``.

        If the label has a category prefix (e.g. ``[store] db/``),
        returns ``('store', 'db/')``. Otherwise returns ``('', label)``.
        A category is user data, not a name, so it may hold any
        characters except ``]`` (``to-do``, ``v1.2``, ``my cat``).
        """
        match = re.match(r'^\[([^\]]+)\] (.+)$', label)
        if match:
            return match.group(1), match.group(2)
        return '', label

    def _read_child_labels(
        self: Wiki,
        folder: pathlib.Path,
        overlay: Optional[dict[pathlib.Path, str]] = None,
    ) -> dict[str, str]:
        """Read categorized labels from child frontmatter.

        Reads the ``category`` field from each child folder's
        ``_index.md`` and each child ``.md`` page, producing a
        ``[category]`` prefix for the parent's link label. Cross-file
        reads route through :meth:`_current_text`, so staged plan
        content (``overlay``) is honored during a plan.

        Args:
            folder: Parent folder to scan.
            overlay: Staged ``{path: content}`` from earlier passes.

        Returns:
            Dict mapping wikilink targets to categorized labels.

        """
        # collect child directories
        children = []
        for path in folder.iterdir():
            if path.is_dir() and not self._is_excluded_dir(path):
                children.append(path)
        # read folder categories from child indexes
        result = {}
        for child in sorted(children):
            child_index = child / WIKI_INDEX
            text = self._current_text(child_index, overlay)
            if text is not None:
                frontmatter, _ = format.parse_page(text)
                category = format.read_frontmatter_category(frontmatter)
            else:
                category = ''
            if category:
                # compose the label to NFC, like _build_expected_links
                target = self._link_target(child_index)
                label = f'[{category}] {child.name}/'
                result[target] = unicodedata.normalize('NFC', label)
        # read page categories from markdown page frontmatter
        for page in self._find_pages(folder):
            if page.suffix != '.md':
                continue
            text = self._current_text(page, overlay)
            if text is not None:
                frontmatter, _ = format.parse_page(text)
                category = format.read_frontmatter_category(frontmatter)
            else:
                category = ''
            if category:
                # compose the label to NFC, like _build_expected_links (which
                # keeps the suffix when a sibling file claims the stripped name)
                if page.with_suffix('').is_file():
                    target = page.relative_to(self._root).as_posix()
                    label = f'[{category}] {page.name}'
                else:
                    target = self._link_target(page)
                    label = f'[{category}] {page.stem}'
                result[target] = unicodedata.normalize('NFC', label)
        return result

    def _plan(
        self: Wiki,
        folder: pathlib.Path,
        *,
        prune: bool = False,
        now: str,
    ) -> tuple[
        dict[pathlib.Path, str],
        dict[pathlib.Path, Optional[str]],
        list[Event],
    ]:
        """Compute corrected content for every file under ``folder``.

        Runs the two update passes (indexes bottom-up, then pages) into
        an in-memory overlay without writing to disk. The overlay maps
        each file path to its corrected content
        carrying the file's *original* ``updated:`` value, so a caller
        can compare against disk to decide whether to write
        (:meth:`_apply_plan`) or to report drift (:meth:`lint`).

        Args:
            folder: Root folder to plan under.
            prune: Remove broken links instead of preserving them.
            now: Single timestamp threaded through every pass (seeds
                missing fields; :meth:`_apply_plan` reuses it to
                re-stamp ``updated:``).

        Returns:
            Tuple of ``(overlay, baseline, notices)`` where ``baseline``
            maps each planned file to the on-disk text it was planned
            from (``None`` when it did not exist) -- the writer compares
            against it to detect a concurrent edit -- and ``notices``
            are the broken/new-link, desc-overwrite,
            malformed-frontmatter, and adoption notice events collected
            across all indexes and pages.

        """
        # alias directories
        folders = self._find_dirs(folder)
        overlay: dict[pathlib.Path, str] = {}
        baseline: dict[pathlib.Path, Optional[str]] = {}
        notices = []
        # plan indexes (bottom-up so child categories
        # exist before parents read them)
        for folder in reversed(folders):
            # snapshot the on-disk text before planning from it, so the
            # writer can detect (and skip) a concurrent edit to the file;
            # the plan reads the same snapshot, so plan input and the
            # concurrent-edit compare agree by construction
            index_path = folder / WIKI_INDEX
            baseline[index_path] = self._current_text(index_path)
            content, index_notices = self._plan_index(
                folder=folder,
                now=now,
                text=baseline[index_path],
                prune=prune,
                overlay=overlay,
            )
            overlay[index_path] = content
            notices.extend(index_notices)
        # plan pages
        for folder in folders:
            for page in self._find_pages(folder):
                if page.suffix == '.md':
                    text = self._read_text(page)
                    baseline[page] = text
                    content, page_notices = self._plan_page(page, now, text=text)
                    overlay[page] = content
                    notices.extend(page_notices)
        # return overlay, baseline, and notices
        return overlay, baseline, notices

    def _apply_plan(
        self: Wiki,
        overlay: dict[pathlib.Path, str],
        baseline: dict[pathlib.Path, Optional[str]],
        now: str,
    ) -> list[str]:
        """Write corrected content to disk where it differs.

        Thin writer over :meth:`_plan`'s overlay: for each file whose
        corrected content differs from disk, re-stamps ``updated: now``
        and writes. Files already correct are skipped, so a
        timestamp-only difference never triggers a write (the overlay
        carries the original ``updated:``). A file whose disk text no
        longer matches its plan-time ``baseline`` snapshot is skipped
        with a warning: writing the staged content would silently revert
        the concurrent edit, whereas the next run converges.

        Args:
            overlay: Corrected ``{path: content}`` from :meth:`_plan`.
            baseline: Plan-time ``{path: text}`` from :meth:`_plan`.
            now: Timestamp for the ``updated:`` re-stamp (the same
                value threaded through the plan).

        Returns:
            List of relative paths of written files.

        """
        result = []
        for path, content in overlay.items():
            # skip files already correct (ignores updated:-only churn); the
            # byte probe forces the rewrite that normalizes a CRLF file to LF
            current = self._current_text(path)
            unchanged = (current is not None) and (content == current)
            if unchanged and not self._has_crlf(path):
                continue
            # skip a file that changed since the plan read it: writing would
            # revert the concurrent edit; the next run converges
            if current != baseline[path]:
                relpath = path.relative_to(self._root)
                self.on_write_skip(path=str(relpath))
                continue
            # re-stamp updated: only on a real content change; a write forced
            # solely to normalize CRLF->LF (content == disk) must not re-stamp
            # -- on a verbatim passthrough of an unclosed-frontmatter page the
            # re.sub would rewrite an authored body line the parse left as body
            if (current is None) or (content != current):
                content = re.sub(
                    pattern=r'^updated:.*$',
                    # a callable repl, so a backslash in a user timestamp.format
                    # is emitted verbatim, not parsed as a group reference
                    repl=lambda _: f'updated: {now}',
                    string=content,
                    count=1,
                    flags=re.MULTILINE,
                )
            wiki.util.fs.write_atomic(path, content)
            result.append(str(path.relative_to(self._root)))
        return result

    def _refuse_conflicted(
        self: Wiki,
        baseline: dict[pathlib.Path, Optional[str]],
    ) -> None:
        """Refuse a sweep whose baseline carries merge conflict markers.

        The plan reads the markers as authored content and would bake
        them into the rewrite -- silently dropping one conflict side --
        so every sweep over an existing tree refuses before writing,
        naming every marked file.
        """
        conflicted = []
        for path, text in baseline.items():
            if text is None:
                continue
            markers = _conflict_marker_lines(text)
            if not markers:
                continue
            # a well-formed no-lint region sanctions marker-shaped lines
            # (e.g. a git tutorial), exactly as lint suppresses them
            masked = wiki.util.markdown.mask_code(text)
            suppressed, _ = self._lint_regions(path, masked)
            if any(n not in suppressed for n in markers):
                conflicted.append(str(path.relative_to(self._root)))
        if conflicted:
            names = ', '.join(sorted(conflicted))
            raise ValueError(
                f'Merge conflict markers in: {names}; resolve them and rerun.'
            )

    def _plan_index(
        self: Wiki,
        folder: pathlib.Path,
        now: str,
        *,
        text: Optional[str],
        prune: bool = False,
        overlay: dict[pathlib.Path, str],
    ) -> tuple[str, list[Event]]:
        """Compute the corrected ``_index.md`` content for a folder.

        Pure with respect to the filesystem: reads inputs (cross-file
        reads via :meth:`_current_text`, so staged plan content is
        visible) but writes nothing. The returned content carries the
        file's *original* ``updated:`` value -- the re-stamp is the
        writer's job (:meth:`_apply_plan`) -- so ``content`` differing
        from disk is exactly "update would rewrite this file" with
        timestamp-only churn excluded.

        Args:
            folder: Folder whose index to compute.
            now: Timestamp for seeding *missing or blank* ``created:``/
                ``updated:`` fields and fresh frontmatter (never to
                re-stamp an existing ``updated:`` value).
            text: The index's plan-time on-disk text (``None`` when the
                file does not exist) -- the caller's baseline snapshot,
                so plan input and the writer's concurrent-edit compare
                are the same read.
            prune: Remove broken links instead of preserving them.
            overlay: Staged ``{path: content}`` from earlier passes.

        Returns:
            Tuple of ``(content, notices)`` where ``notices`` are the
            created-index, broken/new/pruned-link, desc-overwrite,
            name-skip, symlink-skip, and truncated-index notice events
            (emitted by the writer, not here).

        """
        # alias index path
        path = folder / WIKI_INDEX
        # determine name
        name = self._path_to_name(folder)
        # parse existing (the caller's plan-time snapshot)
        if text is not None:
            frontmatter, existing, user_content = format.parse_index(
                text,
                delimiter=self.index_delimiter,
            )
            if frontmatter:
                # refresh the name from the folder path, fill the missing
                # or blank desc/created/updated keys, drop an unset title
                # or category, and enforce the canonical field order
                frontmatter = format.repair_frontmatter(
                    frontmatter,
                    name=name,
                    now=now,
                    title=True,
                    category=True,
                    order=True,
                )
            else:
                # an existing index with no (closed) frontmatter is an emptied or
                # truncated file: rebuilding it fresh would permanently discard its
                # authored content, so keep it as-is and name the recovery paths
                relpath = path.relative_to(self._root)
                return text, [IndexTruncatedEvent(path=str(relpath))]
        else:
            frontmatter = format.build_frontmatter(
                name=name,
                created=now,
                updated=now,
            )
            existing = []
            user_content = ''
        # enrich frontmatter (hook for subclass tag enrichment)
        frontmatter = self._enrich_frontmatter(path, frontmatter)
        # required-titles mode: seed the null placeholder lint holds open
        # until a title is authored
        if self._titles_required:
            frontmatter = format.seed_frontmatter_title(frontmatter)
        # build expected links from filesystem
        expected = self._build_expected_links(folder)
        # drop links for filesystem entries whose stem/name fails the naming
        # policy (a denied char like '|' yields a malformed [[a|b|a|b]] link that
        # grows the index every run); skip them and warn once, like search skips
        # an undecodable file rather than aborting the whole run
        relpath = path.relative_to(self._root)
        notices = []
        # announce an auto-created index so its placeholder desc gets filled
        if text is None:
            notices.append(IndexCreateEvent(path=str(relpath)))
        invalid = self._invalid_links(folder)
        for _target, skipped, reason in invalid:
            notices.append(NameSkipEvent(path=skipped, reason=reason))
        invalid_targets = {target for target, _, _ in invalid}
        expected = [(t, label) for t, label in expected if t not in invalid_targets]
        # enrich new entries from child frontmatter
        labels = self._read_child_labels(folder, overlay)
        # merge and sort
        links, broken, new = self._merge_links(
            existing=existing,
            expected=expected,
            labels=labels,
            prune=prune,
        )
        links = self._sort_links(links)
        # collect broken/new link notices (emitted by the writer, not here); a
        # target still on disk as a symlinked file is not missing, so name the
        # exclusion as the cause instead of the generic broken link (alongside
        # the prune notice, which still names the removal)
        for target, label, _desc in broken:
            symlinked = self._is_symlink_skipped(target)
            if symlinked:
                notices.append(
                    SymlinkSkipEvent(path=str(relpath), target=target, label=label)
                )
            if prune:
                notices.append(
                    LinkPruneEvent(path=str(relpath), target=target, label=label)
                )
            elif not symlinked:
                notices.append(
                    LinkBreakEvent(path=str(relpath), target=target, label=label)
                )
        for target, label, _desc in new:
            notices.append(LinkAddEvent(path=str(relpath), target=target, label=label))
        # propagate child desc frontmatter to link descriptions
        propagated = []
        for target, label, link_desc in links:
            if label == '..':
                propagated.append((target, label, link_desc))
                continue
            # resolve child path from target (staged content wins over disk);
            # a row with no markdown page behind it -- a raw file, even one
            # with a same-named sidecar page -- carries no desc to propagate
            # NOTE: a broken/preserved target may carry '..' or an absolute path,
            #   so contain the resolved form to the root before dereferencing --
            #   an out-of-root read would copy a foreign file's desc into the
            #   generated link block; the unresolved path stays the overlay key
            #   (self._root is already resolved, so the two agree for in-root
            #   targets), only the containment test resolves
            child_path = self._target_page(target)
            if child_path is None:
                propagated.append((target, label, link_desc))
                continue
            if not child_path.resolve().is_relative_to(self._root):
                propagated.append((target, label, link_desc))
                continue
            child_text = self._current_text(child_path, overlay)
            if child_text is None:
                propagated.append((target, label, link_desc))
                continue
            child_frontmatter, _ = format.parse_page(child_text)
            child_desc = format.read_frontmatter_desc(child_frontmatter)
            if child_desc and (child_desc != '...'):
                # child desc is source of truth; rstrip each line first
                # (format.parse_index never preserves trailing spaces, so an
                # unnormalized desc re-triggers the overwrite notice on every
                # converged run), then escape lines that would parse as index
                # structure once rendered in the link block
                child_desc = '\n'.join(
                    line.rstrip() for line in child_desc.strip().split('\n')
                )
                child_desc = format.escape_desc(
                    child_desc,
                    delimiter=self.index_delimiter,
                )
                # fold whitespace runs before comparing
                # NOTE: line breaks and blank lines are formatter-owned: a link
                #   desc differing only in wrapping -- or in a blank line the
                #   formatter inserts before a block (list/heading/fence) -- is
                #   already converged; the index's own breaks survive; only a
                #   content change ports the frontmatter desc (with its breaks)
                #   back onto the row
                link_folded = ' '.join(link_desc.split())
                child_folded = ' '.join(child_desc.split())
                if link_folded == child_folded:
                    propagated.append((target, label, link_desc))
                    continue
                # announce a genuine overwrite (a hand-edit would otherwise vanish
                # silently); first-time propagation onto the placeholder stays quiet
                if link_desc not in ('', '...'):
                    notices.append(
                        DescOverwriteEvent(
                            path=str(relpath),
                            target=target,
                            label=label,
                        )
                    )
                propagated.append((target, label, child_desc))
            else:
                propagated.append((target, label, link_desc))
        links = propagated
        # resolve the H1: an authored title wins over the path-derived name
        title = format.read_frontmatter_title(frontmatter)
        # render the corrected index
        content = format.render_index(
            heading=title or name,
            frontmatter=frontmatter,
            links=links,
            user_content=user_content,
            delimiter=self.index_delimiter,
        )
        return content, notices

    def _plan_page(
        self: Wiki,
        path: pathlib.Path,
        now: str,
        *,
        text: str,
    ) -> tuple[str, list[Event]]:
        """Compute the corrected content for a page file.

        Pure with respect to the filesystem (see :meth:`_plan_index`).
        The page's ``name`` is set to the path-joined name (e.g.
        ``core/design``) and the H1 heading follows it, so names stay
        consistent with the tree structure; an authored ``title:``
        (indexes and pages alike) wins the H1 instead, and adopting a
        page with no frontmatter seeds ``title:`` from its authored
        heading, so adoption preserves it, while a page with no H1
        gains the path-joined heading in its body, never a seeded
        title. Missing or blank frontmatter fields
        (``desc``/``created``/``updated``) are filled in. A page
        whose frontmatter never closes is left untouched and reported
        instead: prepending a fresh block would demote the authored
        fields to body text. The returned content carries the file's
        *original* ``updated:`` value (a page has no cross-file reads,
        so it is computed once from its own content).

        Args:
            path: Page file to compute.
            now: Timestamp for seeding missing fields / fresh frontmatter.
            text: The page's plan-time on-disk text -- the caller's
                baseline snapshot, so plan input and the writer's
                concurrent-edit compare are the same read.

        Returns:
            Tuple of ``(content, notices)`` where ``notices`` name a
            malformed frontmatter or an adoption (emitted by the
            writer, not here).

        """
        # parse page (the caller's plan-time snapshot)
        frontmatter, content = format.parse_page(text)
        # unclosed frontmatter parses as none at all: keep the file as-is and
        # report it rather than demote the authored fields to body text
        first_line = text.split('\n', 1)[0].lstrip('\ufeff')
        if not frontmatter and (first_line.strip() == '---'):
            relpath = path.relative_to(self._root)
            return text, [FrontmatterMalformedEvent(path=str(relpath))]
        # update or create frontmatter
        notices: list[Event] = []
        if frontmatter:
            # refresh the name from the file path, fill the missing or
            # blank desc/created/updated keys, drop an unset title or
            # category, and enforce the canonical field order
            page_name = self._path_to_name(path)
            frontmatter = format.repair_frontmatter(
                frontmatter,
                name=page_name,
                now=now,
                title=True,
                category=True,
                order=True,
            )
        else:
            # use the path-joined name (not the bare stem), so a fresh page
            # converges in one pass instead of two
            page_name = self._path_to_name(path)
            frontmatter = format.build_frontmatter(
                name=page_name,
                created=now,
                updated=now,
            )
            # drop a UTF-8 BOM: it hides the authored H1 from the seeding
            # below and would land mid-file under the fresh frontmatter
            content = '\n' + text.lstrip('\ufeff')
            # adoption seeds title: from the authored H1, so the heading
            # the author wrote survives the name rewrite below
            adopted_title = None
            heading = wiki.util.markdown.find_heading(content)
            if heading and (authored := heading[1].strip()):
                frontmatter = format.seed_frontmatter_title(frontmatter, authored)
                adopted_title = authored
            elif heading is None:
                # a page with no H1 at all gains the path-joined one in
                # its body; the invented heading is not authored, so it
                # never seeds title:
                content = f'\n# {page_name}\n' + content
            # adoption rewrites the file wholesale, so announce the act
            relpath = path.relative_to(self._root)
            notices.append(PageAdoptEvent(path=str(relpath), title=adopted_title))
        # required-titles mode: seed the null placeholder lint holds open
        # until a title is authored
        if self._titles_required:
            frontmatter = format.seed_frontmatter_title(frontmatter)
        # rewrite the H1: an authored title wins over the path-joined name
        title = format.read_frontmatter_title(frontmatter)
        content = format.replace_heading(content, title or page_name)
        # render the corrected page
        result = format.render_page(frontmatter, content)
        return result, notices

    def _category_matches(
        self: Wiki,
        folder: pathlib.Path,
        *,
        category: list[str],
        markdown: Optional[bool],
        matches: set[pathlib.Path],
    ) -> bool:
        """Collect folders whose subtree renders under a category filter.

        A post-order pass over the same link traversal
        :meth:`_map_folder` renders from -- index link targets, with
        identical broken/unindexed gating, so a subtree reachable only
        through broken links or unindexed folders stays invisible here
        too. Fills ``matches`` bottom-up with every folder whose subtree
        contributes at least one line under ``category``/``markdown``
        and returns whether ``folder``'s did.
        """
        # an unindexed folder renders no children, so it never matches
        index_path = folder / WIKI_INDEX
        if not index_path.is_file():
            return False
        text = self._read_text(index_path)
        _, links, _ = format.parse_index(text, delimiter=self.index_delimiter)
        # broken-ness is the renderer's normalized-target identity (see
        # _map_folder), so the two traversals agree link for link
        expected_targets = {
            unicodedata.normalize('NFC', target)
            for target, _ in self._build_expected_links(folder)
        }
        found = False
        for target, label, _ in links:
            # skip parent link
            if label == '..':
                continue
            # the renderer's category test: an empty filter means uncategorized
            entry_category, base_name = self._parse_category(label)
            is_folder = base_name.endswith('/')
            if category:
                matches_category = entry_category in category
            else:
                matches_category = not entry_category
            # a matching page renders (broken or not) unless the markdown
            # filter drops it
            if not is_folder:
                if not matches_category:
                    continue
                is_markdown = self._target_page(target) is not None
                if (markdown is None) or (markdown == is_markdown):
                    found = True
                continue
            # a matching folder renders its own line; a healthy child is
            # recursed either way so deeper folders join the match set
            child_folder = (self._root / target).parent
            broken = unicodedata.normalize('NFC', target) not in expected_targets
            if matches_category:
                found = True
            if not broken:
                child_matches = self._category_matches(
                    folder=child_folder,
                    category=category,
                    markdown=markdown,
                    matches=matches,
                )
                if child_matches:
                    found = True
        if found:
            matches.add(folder)
        return found

    def _map_folder(
        self: Wiki,
        folder: pathlib.Path,
        *,
        indent: str = '',
        current_depth: int = 0,
        depth: Optional[int] = None,
        desc: bool = True,
        desc_limit: Optional[int] = None,
        category: Optional[list[str]] = None,
        markdown: Optional[bool] = None,
        words: bool = True,
        counts: Optional[dict[str, int]] = None,
        folder_words: Optional[dict[str, int]] = None,
        matches: Optional[set[pathlib.Path]] = None,
        _lines: Optional[list[str]] = None,
    ) -> list[str]:
        """Recursively build map lines for a wiki folder.

        Reads the folder's ``_index.md``, iterates non-parent
        links, and appends formatted lines. Recurses into
        child folders respecting ``depth``. ``matches`` is the
        precomputed category match set (:meth:`_category_matches`),
        required whenever ``category`` is set.
        """
        # default lines to empty (use the passed buffer even when it is empty)
        result = _lines if _lines is not None else []
        # map presentation knobs (settings.json map.*)
        policy = self._map_policy
        indent_unit = policy['indent']
        ellipsis = policy['ellipsis']
        # read and parse the folder's index
        index_path = folder / WIKI_INDEX
        if index_path.is_file():
            text = self._read_text(index_path)
            _, links, _ = format.parse_index(text, delimiter=self.index_delimiter)
        elif current_depth == 0:
            # top-level target has no index: mark it unindexed
            name = self._path_to_name(folder)
            result.append(f'{indent}{name}/ (unindexed)')
            return result
        else:
            # unindexed child reached during recursion: nothing to add
            return result
        # broken-ness is string identity against the same expected targets
        # update and lint compare -- a bare filesystem probe would pass a
        # case-only (or normalization-only) mismatch on an insensitive volume,
        # rendering a stale row live here while lint calls it broken
        expected_targets = {
            unicodedata.normalize('NFC', target)
            for target, _ in self._build_expected_links(folder)
        }
        # iterate non-parent links
        for target, label, description in links:
            # skip parent link
            if label == '..':
                continue
            # parse category
            entry_category, base_name = self._parse_category(label)
            # check if folder or page
            is_folder = base_name.endswith('/')
            # category filter: a page must match its category; a folder is always
            # recursed and shown only if it or a descendant matches (below)
            matches_category = True
            if category is not None:
                if category:
                    matches_category = entry_category in category
                else:
                    matches_category = not entry_category
            if not is_folder and not matches_category:
                continue
            # resolve the child path from the link target, never the display
            # label (a preserved broken link keeps a live sibling's label);
            # markdown-ness resolves like read (_target_page), since a
            # '.'-in-target test misfires on a dotted stem like my.notes and
            # a raw file's row must not render a same-named sidecar's count
            child_folder = (self._root / target).parent
            child_page = self._target_page(target)
            if is_folder:
                child_path = child_folder / WIKI_INDEX
                is_markdown = False
            elif child_page is not None:
                child_path = child_page
                is_markdown = True
            else:
                child_path = self._root / target
                is_markdown = False
            # a link resolving outside this folder (or to a missing file) is a
            # preserved broken link: annotate it, never recurse into another subtree
            broken = unicodedata.normalize('NFC', target) not in expected_targets
            # apply markdown filter (pages only)
            if not is_folder and (markdown is not None) and (markdown != is_markdown):
                continue
            # detect unindexed folder (folder present, no _index.md)
            unindexed = is_folder and not broken and not child_path.is_file()
            # recurse into a child folder first so a category filter can prune
            # folders whose subtree contributes nothing
            child_lines = []
            if is_folder and not unindexed and not broken:
                if (depth is None) or (current_depth < depth):
                    # warn on a damaged child index before it maps silently
                    # from the reclaimed parse (map() warns the scope root)
                    self._warn_markerless_index(child_folder)
                    self._map_folder(
                        folder=child_folder,
                        indent=indent + indent_unit,
                        current_depth=current_depth + 1,
                        depth=depth,
                        desc=desc,
                        desc_limit=desc_limit,
                        category=category,
                        markdown=markdown,
                        words=words,
                        counts=counts,
                        folder_words=folder_words,
                        matches=matches,
                        _lines=child_lines,
                    )
            # skip a non-matching folder with no matching descendants; a depth
            # cutoff leaves child_lines empty for depth reasons, not content,
            # so consult the precomputed match set -- a match beyond the
            # cutoff still shows this folder (children stay hidden)
            if is_folder and not matches_category and not child_lines:
                if unindexed or broken or (child_folder not in matches):
                    continue
            # read word counts from the cache; always render a count (0 for a
            # non-markdown file); unindexed/broken entries show their marker
            word_label = None
            if words and not unindexed and not broken:
                # count keys are NFC-composed, matching the cache's
                relative = str(child_path.relative_to(self._root))
                count_key = unicodedata.normalize('NFC', relative)
                count = counts.get(count_key, 0)
                if is_folder:
                    relative = str(child_folder.relative_to(self._root))
                    tree_key = unicodedata.normalize('NFC', relative)
                    tree = folder_words.get(tree_key, 0)
                    page_label = wiki.util.str.format_words(count)
                    tree_label = wiki.util.str.format_words(tree)
                    word_label = f'{page_label}/{tree_label}'
                else:
                    word_label = wiki.util.str.format_words(count)
            # format description (folded to one line)
            desc_text = ''
            if desc and description:
                desc_text = ' '.join(description.split())
                if desc_text == '...':
                    desc_text = ''
                if desc_text and (desc_limit is not None):
                    if len(desc_text) > desc_limit:
                        # for limits too small for content + ellipsis, show raw
                        # chars so a real desc isn't collapsed to the bare '...'
                        if desc_limit >= len(ellipsis) + 1:
                            cutoff = desc_limit - len(ellipsis)
                            desc_text = desc_text[:cutoff] + ellipsis
                        else:
                            desc_text = desc_text[:desc_limit]
            # format line
            if entry_category:
                prefix = f'[{entry_category}] {base_name}'
            else:
                prefix = base_name
            if unindexed:
                prefix += ' (unindexed)'
            elif broken:
                prefix += ' (broken)'
            elif word_label is not None:
                prefix += f' ({word_label})'
            # append detail
            if desc_text:
                result.append(f'{indent}{prefix}: {desc_text}')
            else:
                result.append(f'{indent}{prefix}')
            # append the child folder's already-rendered lines after its entry
            result.extend(child_lines)
        return result

    def _diff(
        self: Wiki,
        path: pathlib.Path,
        overlay: dict[pathlib.Path, str],
        *,
        current: str,
    ) -> Optional[str]:
        """Return ``path``'s drift as a header line plus an indented diff.

        The corrected content comes from the plan ``overlay``; the
        comparison is byte-exact, so the result is non-empty exactly
        when ``update`` would rewrite the file. The first line is
        ``{path}: Requires update``; the diff body is indented so it
        reads distinctly from the one-line "needs attention" messages.
        Returns ``None`` when the file is unchanged or has no plan
        entry (a missing index is reported separately).

        Args:
            path: File to diff.
            overlay: Corrected ``{path: content}`` from :meth:`_plan`.
            current: The file's current text (lint reads each file once
                and shares the read).

        Returns:
            The path header plus indented diff, or ``None`` if there is
            nothing to report.

        """
        corrected = overlay.get(path)
        if (corrected is None) or (current == corrected):
            return None
        relpath = str(path.relative_to(self._root))
        # drop difflib's '---'/'+++' header lines; the path header replaces them
        diff = difflib.unified_diff(
            current.splitlines(),
            corrected.splitlines(),
            lineterm='',
        )
        body = list(diff)[2:]
        # a byte difference with no line-level delta is a final-newline change
        # (splitlines cannot see one; CRLF drift reads clean through universal
        # newlines and never reaches here); still report it so the flag never
        # desyncs from update's byte compare
        if not body:
            return f'{relpath}: Requires update (final newline differs)'
        # drop trailing blank context lines so the diff ends on a real change
        while body and not body[-1].strip():
            body.pop()
        # indent the body; render blank context lines as truly empty (no trailing ws)
        indented = '\n'.join('    ' + line if line.strip() else '' for line in body)
        return f'{relpath}: Requires update\n{indented}'

    def _lint_regions(
        self: Wiki,
        path: pathlib.Path,
        masked: str,
    ) -> tuple[set[int], list[str]]:
        """Resolve ``masked``'s ``no-lint`` suppression set and region issues.

        ``masked`` is the file's code-masked text (lint masks each file
        once and shares the mask). Returns the 1-based line numbers
        inside well-formed ``no-lint`` regions -- the lines the
        positional rules skip -- plus a formatted hard issue per
        nesting/dangling violation. A malformed region suppresses
        nothing.
        """
        regions, errors = format.parse_regions(masked)
        suppressed = set()
        for start, end in regions.get('no-lint', []):
            suppressed.update(range(start, end + 1))
        relpath = path.relative_to(self._root)
        result = [f'{relpath}: {error}' for error in errors]
        return suppressed, result

    def _lint_desc(
        self: Wiki,
        path: pathlib.Path,
        frontmatter: str,
    ) -> list[str]:
        """Check desc is present, concise, and ends in a period."""
        # initialize issues
        result = []
        # alias relative path
        relpath = path.relative_to(self._root)
        desc = format.read_frontmatter_desc(frontmatter)
        # a placeholder desc is a soft, "not yet authored" state (init seeds it),
        # so note it without failing lint; a real desc must end in a period
        if desc == '...':
            self.on_desc_missing(path=str(relpath))
        elif desc and not desc.strip().endswith('.'):
            result.append(f'{relpath}: Missing period in desc')
        # desc length is author judgment, not structure, so an oversized desc
        # draws a soft note rather than an issue
        if desc:
            folded = format.join_lines(desc)
            if len(folded) > _DESC_NOTE_CHARS:
                self.on_desc_long(path=str(relpath), length=len(folded))
        return result

    def _lint_title(
        self: Wiki,
        path: pathlib.Path,
        frontmatter: str,
    ) -> list[str]:
        """Check the authored title ``titles.required`` demands.

        Under ``titles.required`` every index and page must carry an
        authored ``title:``; update seeds a ``title: null`` placeholder
        on files missing the field, and the file stays a hard issue
        until a value is authored. Without the setting, titles are
        optional and never checked. A truncated index (no frontmatter)
        is reported as such, not as a missing title.
        """
        # initialize issues
        result = []
        # alias relative path
        relpath = path.relative_to(self._root)
        # only an authored value satisfies the requirement: the field is
        # absent (update seeds it), blank, or the null placeholder
        if self._titles_required and frontmatter:
            if not format.read_frontmatter_title(frontmatter):
                result.append(f'{relpath}: Missing title (author a value)')
        return result

    def _lint_timestamps(
        self: Wiki,
        path: pathlib.Path,
        frontmatter: str,
    ) -> list[str]:
        """Check ``created:``/``updated:`` parse under the timestamp policy.

        The stamps are tool-owned -- seeded when a file gains
        frontmatter, ``updated:`` rewritten on every actual write -- so
        lint never judges a parseable value against a clock: a hand
        edit goes undetected unless it breaks ``timestamp.format``,
        which is a hard issue. A missing or blank field is the repair
        path's business (update stamps it in place), so only a present,
        non-blank value is parsed.
        """
        # initialize issues
        result = []
        # alias relative path
        relpath = path.relative_to(self._root)
        # parse each present stamp against the configured strftime format
        policy = self._timestamp_policy
        for field in ('created', 'updated'):
            value = format.read_frontmatter_field(frontmatter, field)
            if not value:
                continue
            try:
                dt.datetime.strptime(value, policy['format'])
            except ValueError:
                timestamp_format = policy['format']
                result.append(
                    f'{relpath}: Unparseable {field}: stamp {value!r}'
                    f' (expected timestamp format {timestamp_format!r}; a changed'
                    ' timestamp.format needs existing stamps rewritten by hand)'
                )
        return result

    def _lint_link_desc(
        self: Wiki,
        path: pathlib.Path,
        target: str,
        label: str,
        link_desc: str,
    ) -> list[str]:
        """Check a link description ends in a period.

        A description out of sync with the child's ``desc`` is rewritten
        by ``update`` and surfaced by the generated diff; only the
        trailing period -- which ``update`` never adds -- is checked here.
        """
        # initialize issues
        result = []
        # alias relative path
        relpath = path.relative_to(self._root)
        # check link description ends in period
        joined = format.join_lines(link_desc)
        if joined and (joined != '...') and not joined.endswith('.'):
            result.append(f'{relpath}: Missing period in [[{target}|{label}]]')
        return result

    def _lint_wrap_mangles(
        self: Wiki,
        path: pathlib.Path,
        text: str,
        masked: str,
        suppressed: set[int],
        frontmatter: str,
    ) -> list[str]:
        """Flag hand-wrap artifacts that read back as mangled text.

        Two line-level signatures: a hyphen dangle (a break inside a
        hyphenated word -- every folded read rejoins the pair with a
        space) and a list marker opening a line mid-sentence or
        directly under a paragraph line (rendered as a phantom list
        item). The scan covers the content region -- minus ``no-lint``
        suppression -- plus the raw ``desc:`` field lines, whose wrap
        artifacts the folded field reads structurally cannot see; the
        rest of the frontmatter never wraps, so it stays out of scope.
        """
        # scope: content lines minus suppression, plus the raw desc lines
        lines = masked.split('\n')
        in_scope = format.field_line_ranges(frontmatter, lines, ['desc'])
        frontmatter_end = len(frontmatter.split('\n')) if frontmatter else 0
        in_scope.update(
            lineno
            for lineno in range(frontmatter_end + 1, len(lines) + 1)
            if lineno not in suppressed
        )
        # initialize issues
        result = []
        # alias relative path
        relpath = path.relative_to(self._root)
        # a finding needs its pair line in scope too: the dangle reads one
        # line ahead, the marker one line back
        for lineno in format.hyphen_dangle_lines(masked):
            if (lineno in in_scope) and (lineno + 1 in in_scope):
                result.append(
                    f'{relpath}: Hyphen dangle (line {lineno}): the line break'
                    ' splits a hyphenated word; rejoin the wrapped line'
                )
        for lineno in format.wrapped_marker_lines(masked, text):
            if (lineno in in_scope) and (lineno - 1 in in_scope):
                result.append(
                    f'{relpath}: Wrapped list marker (line {lineno}): the line'
                    ' reads as a list item; rejoin the wrapped line or open a'
                    ' real list after a blank line'
                )
        return result

    def _canonical_link_target(
        self: Wiki,
        path: pathlib.Path,
        target: str,
    ) -> Optional[str]:
        """Return the root-relative form of a folder-relative wikilink target.

        Resolves ``target`` (e.g. ``../overview``) from ``path``'s folder and
        expresses it relative to the wiki root, the form wikilinks use. Returns
        the canonical target when it resolves to a page or folder inside the root,
        else ``None`` (an unresolvable target has no fix to suggest).
        """
        # resolve the folder-relative target without touching the filesystem
        joined = os.path.normpath(path.parent / target)
        resolved = pathlib.Path(joined)
        if not resolved.is_relative_to(self._root):
            return None
        # only suggest a target that actually exists (as a page or folder)
        if resolved.with_name(resolved.name + '.md').exists() or resolved.is_dir():
            return resolved.relative_to(self._root).as_posix()
        return None

    def _lint_stale_links(
        self: Wiki,
        path: pathlib.Path,
        content: str,
    ) -> None:
        """Note wikilinks in content that resolve to no existing file.

        A stale link in user content is a soft note (``on_link_stale``),
        not an issue: prose references pages that come and go, and the
        generated index link block's broken-link check is the hard
        surface. Lines inside a well-formed ``no-lint`` region are
        exempt; the region is parsed from the scanned content itself,
        so the region must wrap the link lines.
        """
        # alias relative path
        relpath = path.relative_to(self._root)
        # strip fenced code blocks and inline code spans before scanning;
        # the region parse below shares this content-local mask
        stripped = wiki.util.markdown.mask_code(content)
        # no-lint regions suppress the stale-link note line by line
        suppressed = format.no_lint_lines(stripped)
        for match in re.finditer(r'\[\[([^\]|]+)', stripped):
            # the masked scan preserves line structure, so the match
            # offset maps straight to its source line
            lineno = stripped.count('\n', 0, match.start()) + 1
            if lineno in suppressed:
                continue
            # strip trailing backslash (escaped pipe in markdown tables)
            target = match.group(1).rstrip('\\')
            # drop an anchor suffix (#heading / #^block) for the existence check:
            # '#' is a denied name character, so the suffix always addresses
            # within the page (a bare [[#anchor]] is same-page, never stale)
            page_target = target.partition('#')[0]
            anchor = target[len(page_target) :]
            if not page_target:
                continue
            # targets are root-relative by grammar, so a join that escapes the
            # root ('..' segments) is stale even when a file exists above it
            joined = pathlib.Path(os.path.normpath(self._root / page_target))
            if joined.is_relative_to(self._root):
                if (self._root / (page_target + '.md')).exists():
                    continue
                if (self._root / page_target).exists():
                    continue
            # a folder-relative link (e.g. [[../overview]]) is stale because
            # wiki targets are root-relative; when it resolves to a real page
            # from this file's folder, name the canonical form as the fix
            canonical = self._canonical_link_target(path, page_target)
            if (canonical is not None) and (canonical != page_target):
                self.on_link_stale(
                    path=str(relpath),
                    target=target,
                    canonical=canonical + anchor,
                )
            else:
                self.on_link_stale(path=str(relpath), target=target)


# ------ events


class IndexCreateEvent(Event):
    """Emitted when update creates a missing index."""

    path: str

    @property
    def description(self: IndexCreateEvent) -> str:
        """Return the created-index notice line."""
        return f'New index: {self.path} (fill in its desc)'


class PageAdoptEvent(Event):
    """Emitted when update adopts a bare page by adding frontmatter."""

    path: str
    title: Optional[str] = None

    @property
    def description(self: PageAdoptEvent) -> str:
        """Return the adopted-page notice line."""
        result = f'Adopted bare page: {self.path} (frontmatter added'
        if self.title:
            result += '; title: seeded from its H1'
        return result + ')'


class LinkAddEvent(Event):
    """Emitted when update adds a new index link."""

    path: str
    target: str
    label: str

    @property
    def description(self: LinkAddEvent) -> str:
        """Return the new-link notice line."""
        return f'New link: [[{self.target}|{self.label}]] in {self.path}'


class LinkBreakEvent(Event):
    """Emitted when update preserves a broken index link."""

    path: str
    target: str
    label: str

    @property
    def description(self: LinkBreakEvent) -> str:
        """Return the broken-link notice line."""
        return f'Broken link: [[{self.target}|{self.label}]] in {self.path}'


class LinkPruneEvent(Event):
    """Emitted when update prunes a broken index link."""

    path: str
    target: str
    label: str

    @property
    def description(self: LinkPruneEvent) -> str:
        """Return the pruned-link notice line."""
        return f'Pruned link: [[{self.target}|{self.label}]] from {self.path}'


class DescOverwriteEvent(Event):
    """Emitted when update overwrites a diverged index-side link desc."""

    path: str
    target: str
    label: str

    @property
    def description(self: DescOverwriteEvent) -> str:
        """Return the overwritten-desc notice line."""
        return (
            f'Overwrote desc: [[{self.target}|{self.label}]] in {self.path}'
            f' (the page frontmatter desc wins; edit it in {self.target}.md)'
        )


class NameSkipEvent(Event):
    """Emitted when update skips an entry whose name breaks the policy."""

    path: str
    reason: str

    @property
    def description(self: NameSkipEvent) -> str:
        """Return the invalid-name skip notice line."""
        return f'Skipping {self.path}: invalid name ({self.reason})'


class SymlinkSkipEvent(Event):
    """Emitted when an index link targets a symlinked file the walk skips."""

    path: str
    target: str
    label: str

    @property
    def description(self: SymlinkSkipEvent) -> str:
        """Return the symlinked-target skip notice line."""
        return (
            f'Link targets a symlink: [[{self.target}|{self.label}]] in'
            f' {self.path} (symlinked files are not indexed)'
        )


class WriteSkipEvent(Event):
    """Emitted when apply skips a file edited concurrently with the plan."""

    path: str

    @property
    def description(self: WriteSkipEvent) -> str:
        """Return the concurrent-edit skip notice line."""
        return f'Skipping {self.path}: changed during update; re-run `wiki update`'


class FrontmatterMalformedEvent(Event):
    """Emitted when a page's frontmatter never closes."""

    path: str

    @property
    def description(self: FrontmatterMalformedEvent) -> str:
        """Return the malformed-frontmatter notice line."""
        return f'Malformed frontmatter (no closing ---) in {self.path}'


class IndexTruncatedEvent(Event):
    """Emitted when an emptied or truncated index is kept as-is."""

    path: str

    @property
    def description(self: IndexTruncatedEvent) -> str:
        """Return the truncated-index notice line."""
        return (
            f'Empty or truncated index (no frontmatter) in {self.path};'
            ' restore it from git or delete it to rebuild'
        )


class IndexMarkerlessEvent(Event):
    """Emitted when map reads an index missing its ``***`` delimiter."""

    path: str

    @property
    def description(self: IndexMarkerlessEvent) -> str:
        """Return the markerless-index notice line."""
        return f'{self.path} is missing its *** delimiter; run `wiki update`'


class SettingsRestoreEvent(Event):
    """Emitted when a missing ``.wiki/settings.json`` is restored."""

    path: str

    @property
    def description(self: SettingsRestoreEvent) -> str:
        """Return the restored-settings notice line."""
        return f'Restored missing {self.path} ({{}} -- all defaults)'


class CacheRestoreEvent(Event):
    """Emitted when a deleted ``.wiki/cache/`` is recreated."""

    path: str

    @property
    def description(self: CacheRestoreEvent) -> str:
        """Return the recreated-cache notice line."""
        return f'Recreated {self.path}/ (derived counts cache)'


class DescMissingEvent(Event):
    """Emitted when lint notes a placeholder desc."""

    path: str

    @property
    def description(self: DescMissingEvent) -> str:
        """Return the missing-desc note line."""
        return f'{self.path}: Needs desc'


class DescLongEvent(Event):
    """Emitted when lint notes an oversized desc."""

    path: str
    length: int

    @property
    def description(self: DescLongEvent) -> str:
        """Return the long-desc note line."""
        return (
            f'{self.path}: Desc is {self.length} chars;'
            f' keep descs under {_DESC_NOTE_CHARS}'
        )


class ContentEmptyEvent(Event):
    """Emitted when lint notes an empty user-content section."""

    path: str

    @property
    def description(self: ContentEmptyEvent) -> str:
        """Return the empty-content note line."""
        return f'{self.path}: Empty content'


class CrlfNoticeEvent(Event):
    """Emitted when lint notes CRLF line endings pending normalization."""

    path: str

    @property
    def description(self: CrlfNoticeEvent) -> str:
        """Return the CRLF note line."""
        return f'{self.path}: CRLF line endings; update will normalize'


class LinkStaleEvent(Event):
    """Emitted when lint notes a stale wikilink in user content."""

    path: str
    target: str
    canonical: Optional[str] = None

    @property
    def description(self: LinkStaleEvent) -> str:
        """Return the stale-link note line."""
        result = f'{self.path}: Stale link [[{self.target}]]'
        if self.canonical:
            result += f' (use [[{self.canonical}]])'
        return result


# plan-phase dispatch: event class -> per-kind hook name (kept in lockstep
# with the events above; the CLI's _UPDATE_CATEGORIES keys on the same classes)
_NOTICE_HOOKS = {
    IndexCreateEvent: 'on_index_create',
    PageAdoptEvent: 'on_page_adopt',
    LinkAddEvent: 'on_link_add',
    LinkBreakEvent: 'on_link_break',
    LinkPruneEvent: 'on_link_prune',
    DescOverwriteEvent: 'on_desc_overwrite',
    NameSkipEvent: 'on_name_skip',
    SymlinkSkipEvent: 'on_symlink_skip',
    WriteSkipEvent: 'on_write_skip',
    FrontmatterMalformedEvent: 'on_frontmatter_malformed',
    IndexTruncatedEvent: 'on_index_truncated',
    IndexMarkerlessEvent: 'on_index_markerless',
    SettingsRestoreEvent: 'on_settings_restore',
    CacheRestoreEvent: 'on_cache_restore',
    DescMissingEvent: 'on_desc_missing',
    DescLongEvent: 'on_desc_long',
    ContentEmptyEvent: 'on_content_empty',
    CrlfNoticeEvent: 'on_crlf_notice',
    LinkStaleEvent: 'on_link_stale',
}


# ------ helper functions


def _conflict_marker_lines(text: str) -> list[int]:
    """Return 1-based line numbers of git merge conflict markers.

    Scans the RAW text, deliberately bypassing the code masking every
    other rule uses: a real merge conflict can land entirely inside a
    fenced block, where a masked scan goes blind, and git's marker shape
    -- a line starting with exactly seven ``<``/``>`` then a space or end
    of line -- is never legitimate rendered content. The rare page that
    must show marker lines (e.g. a git tutorial) wraps them in a
    ``no-lint`` region instead.
    """
    result = []
    for lineno, line in enumerate(text.split('\n'), 1):
        if re.match(r'^(<{7}|>{7})( |$)', line):
            result.append(lineno)
    return result


def _is_offline() -> bool:
    """Return ``True`` if ``OFFLINE_MODE`` is set to ``true``.

    Raises:
        ValueError: If ``OFFLINE_MODE`` is set to anything other than
            ``true`` or ``false`` (case-insensitive).

    """
    value = os.environ.get(OFFLINE_MODE, '').strip().lower()
    if value == 'true':
        return True
    if value not in ('false', ''):
        raise ValueError(f'{OFFLINE_MODE} must be "true" or "false", got {value!r}.')
    return False


def _encloses_wiki_error(nested: pathlib.Path) -> ValueError:
    """Build the path-encloses-a-wiki error, naming the nested root."""
    return ValueError(
        f'Path encloses the wiki at: {nested}; run the command from that declared root.'
    )
