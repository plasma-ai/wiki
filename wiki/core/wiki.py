"""Implements ``Wiki`` class."""

from __future__ import annotations

import datetime as dt
import difflib
import functools
import http.client
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import textwrap
import urllib.request
import zoneinfo
from typing import Optional, Union

from wiki.util.dict import merge

__all__ = ['Wiki']

DEFAULT_WIKI_NAME = 'wiki'
WIKI_DIR = '.wiki'
WIKI_SETTINGS = f'{WIKI_DIR}/settings.json'
WIKI_INDEX = '_index.md'

# region-directive marker grammar; pairing semantics live in _parse_regions
_REGION_DIRECTIVE = re.compile(
    r'<!--\s+(start|end):\s+([a-z0-9]+(?:-[a-z0-9]+)*)'
    r'((?:\s+[a-z0-9]+(?:-[a-z0-9]+)*)*)\s+-->'
)

# NOTE: plugin versions should be periodically updated
_OBSIDIAN_PLUGINS = {
    'obsidian-front-matter-title-plugin': (
        'https://github.com/snezhig/obsidian-front-matter-title'
        '/releases/download/4.1.0/{asset}'
    ),
}
_OBSIDIAN_PLUGIN_ASSETS = ('main.js', 'manifest.json')
_OFFLINE_MODE = 'OFFLINE_MODE'
_TIMEOUT_SECONDS = 10

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
    """

    path_sep: str = '/'
    index_delimiter: str = '***'
    category_order: Optional[list[str]] = None

    def __init__(self: Wiki, path: Union[str, pathlib.Path]) -> None:
        """Initialize the wiki manager.

        Args:
            path: Path to the wiki root directory.

        """
        self._root = pathlib.Path(path).resolve()

    @property
    def _root_name(self: Wiki) -> str:
        """Read the root display name from frontmatter.

        Falls back to the root folder name if the root
        index does not exist yet (e.g. during init).
        """
        root_index = self._root / WIKI_INDEX
        if root_index.exists():
            text = root_index.read_text(encoding='utf-8')
        else:
            return self._root.name
        frontmatter, _, _ = self._parse_index(text)
        if result := self._read_frontmatter_name(frontmatter):
            return result
        return self._root.name

    @functools.cached_property
    def _settings(self: Wiki) -> dict:
        """Per-wiki settings overlay from ``.wiki/settings.json``.

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
            raise ValueError(f'{WIKI_SETTINGS} must be a JSON object')
        return result

    @functools.cached_property
    def _naming(self: Wiki) -> dict:
        """Resolve the effective naming policy from ``settings.json``.

        Overlays the per-wiki ``naming`` block from ``settings.json`` onto the
        field defaults, validates the fields, and folds in the structural
        characters and names that are always denied -- the on-disk grammar
        would otherwise break.
        """
        # overlay the settings.json naming block onto the field defaults
        override = self._settings.get('naming', {})
        if not isinstance(override, dict):
            raise ValueError(f'naming must be a JSON object in {WIKI_SETTINGS}')
        policy = {**_NAMING_DEFAULTS, **override}
        # validate predicate names (settings.json is user input -> fail loudly)
        if not isinstance(policy['validate'], list):
            raise ValueError(
                f'naming.validate must be a list of predicate names in {WIKI_SETTINGS}'
            )
        for predicate in policy['validate']:
            if predicate not in _NAMING_PREDICATES:
                raise ValueError(
                    f'Unknown naming predicate {predicate!r} in {WIKI_SETTINGS}'
                )
        # min_length defaults to 1; an explicit value must be a positive int
        min_length = policy['min_length']
        if min_length is None:
            min_length = 1
        elif not (isinstance(min_length, int) and min_length >= 1):
            raise ValueError(
                f'naming.min_length must be an int >= 1 or null, got'
                f' {min_length!r} in {WIKI_SETTINGS}'
            )
        # max_length is null (no cap) or a positive int
        max_length = policy['max_length']
        if max_length is not None:
            if not (isinstance(max_length, int) and max_length >= 1):
                raise ValueError(
                    f'naming.max_length must be an int >= 1 or null, got'
                    f' {max_length!r} in {WIKI_SETTINGS}'
                )
        # deny/allow are strings of characters; reserved is a list of names
        for leaf in ('deny', 'allow'):
            if not isinstance(policy[leaf], str):
                raise ValueError(
                    f'naming.{leaf} must be a string of characters in {WIKI_SETTINGS}'
                )
        if not isinstance(policy['reserved'], list):
            raise ValueError(
                f'naming.reserved must be a list of strings in {WIKI_SETTINGS}'
            )
        if not isinstance(policy['leading_digits'], bool):
            raise ValueError(
                f'naming.leading_digits must be a boolean in {WIKI_SETTINGS}'
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
        reserved = set(policy['reserved'])
        reserved.add(pathlib.Path(WIKI_INDEX).stem)  # the per-folder index stem
        # compile the optional full-match pattern
        pattern = policy['pattern']
        if pattern is not None:
            if not isinstance(pattern, str):
                raise ValueError(
                    f'naming.pattern must be a string or null, got'
                    f' {pattern!r} in {WIKI_SETTINGS}'
                )
            try:
                pattern = re.compile(pattern)
            except re.error as e:
                raise ValueError(
                    f'naming.pattern in {WIKI_SETTINGS} is not a valid regex: {e}'
                ) from e
        return {
            'validate': policy['validate'],
            'allow': set(policy['allow']),
            'deny': deny,
            'pattern': pattern,
            'min_length': min_length,
            'max_length': max_length,
            'leading_digits': policy['leading_digits'],
            'reserved': reserved,
        }

    @functools.cached_property
    def _timestamp(self: Wiki) -> dict:
        """Resolve the effective timestamp policy from ``settings.json``.

        Validates the per-wiki ``timestamp`` block (``timezone`` / ``format``) so
        a bad value fails loudly with a file+key message rather than leaking a raw
        exception from :meth:`_utc_now` (settings.json is user input).
        """
        # overlay the settings.json timestamp block onto the defaults
        override = self._settings.get('timestamp', {})
        if not isinstance(override, dict):
            raise ValueError(f'timestamp must be a JSON object in {WIKI_SETTINGS}')
        # format is a strftime string; timezone is an IANA name or null (UTC)
        format = override.get('format', '%Y-%m-%dT%H:%M:%SZ')
        if not isinstance(format, str):
            raise ValueError(
                f'timestamp.format must be a string, got {format!r} in {WIKI_SETTINGS}'
            )
        # a blank or multi-line value would corrupt the YAML frontmatter -- reject
        # an empty/whitespace format, strftime's %n/%t newline/tab directives, and
        # literal line breaks, the only ways the rendered value splits or empties
        breakers = ('%n', '%t', '\n', '\r')
        if not format.strip() or any(token in format for token in breakers):
            raise ValueError(
                f'timestamp.format must render a single non-empty line; got'
                f' {format!r} in {WIKI_SETTINGS}'
            )
        timezone = override.get('timezone')
        if timezone is not None and not isinstance(timezone, str):
            raise ValueError(
                f'timestamp.timezone must be a string or null, got'
                f' {timezone!r} in {WIKI_SETTINGS}'
            )
        if timezone:
            try:
                timezone = zoneinfo.ZoneInfo(timezone)
            except (zoneinfo.ZoneInfoNotFoundError, ValueError) as e:
                raise ValueError(
                    f'Unknown timestamp.timezone {timezone!r} in {WIKI_SETTINGS}'
                ) from e
        else:
            timezone = dt.UTC
        return {'format': format, 'zone': timezone}

    def validate_name(self: Wiki, name: str) -> bool:
        """Return ``True`` if ``name`` satisfies the wiki's naming policy.

        The policy is the field defaults overlaid by the per-wiki ``naming`` block
        in ``.wiki/settings.json``; see :attr:`_naming`. The path separator,
        index delimiter, link/markdown grammar characters, and the reserved
        ``_index`` name are always rejected; the ``.wiki`` tool directory needs
        no reservation, since leading-dot names are rejected wholesale.

        Override in subclasses for naming rules a data policy cannot express.

        Args:
            name: Name to validate (page stem or folder name).

        Returns:
            ``True`` if the name is valid.

        """
        # alias naming policy
        policy = self._naming
        # reject empty, over-long, non-printable, and hidden (leading-dot) names
        if len(name) < policy['min_length']:
            return False
        if policy['max_length'] is not None and len(name) > policy['max_length']:
            return False
        if not name.isprintable() or name.startswith('.'):
            return False
        # reject reserved structural names and any denied character
        if name in policy['reserved']:
            return False
        if any(char in policy['deny'] for char in name):
            return False
        # apply the str.is* predicates to the name minus any allowed characters
        probe = ''.join(char for char in name if char not in policy['allow'])
        for predicate in policy['validate']:
            if predicate == 'identifier' and policy['leading_digits']:
                valid = f'_{probe}'.isidentifier()
            else:
                valid = _NAMING_PREDICATES[predicate](probe)
            if not valid:
                return False
        # apply the optional full-match regex
        if policy['pattern'] is not None and not policy['pattern'].fullmatch(name):
            return False
        return True

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
                so the configurable knobs are discoverable.

        """
        # validate OFFLINE_MODE before any filesystem mutation; a bad value must
        # fail fast rather than strand a half-built wiki the re-init guard skips
        _is_offline()
        # ------ TODO: remove back-compat in future versions ------
        self._refuse_legacy_layout()
        # ---------------------------------------------------------
        # resolve the settings seed and prime the _settings cache with it, so
        # the accesses below (validate_name, _utc_now) read this wiki's real
        # policy rather than caching {} from the not-yet-written file
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
        if not self.validate_name(name):
            raise ValueError(f'Invalid wiki name: {name!r}')
        # alias current timestamp
        now = self._utc_now()
        # initialize wiki root
        self._root.mkdir(parents=True, exist_ok=True)
        # seed .wiki/settings.json -- the declared-root marker
        if seed is not None:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(seed, indent=2)
            _write_atomic(settings_path, content + '\n')
        # seed .wiki/obsidian from stock template
        config_dir = self._root / WIKI_DIR / 'obsidian'
        if not config_dir.exists():
            path = pathlib.Path(__file__).parent
            template_dir = path.parent / '_config' / 'obsidian'
            if template_dir.exists():
                config_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(template_dir, config_dir)
        # create root index
        root_index = self._root / WIKI_INDEX
        if not root_index.exists():
            frontmatter = self._build_frontmatter(
                name=name,
                created=now,
                updated=now,
            )
            _write_atomic(
                root_index,
                self._render_index(name, frontmatter, [], ''),
            )
        # update all indexes (reuse this run's timestamp)
        overlay, baseline, _ = self._plan(self._root, now=now)
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
        for notice in self._ensure_settings():
            self._warn(notice)
        # seed a missing .wiki/obsidian from the stock template, so an
        # adopted tree (or one whose .wiki/ was lost) gets the full setup
        config_dir = self._root / WIKI_DIR / 'obsidian'
        if not config_dir.exists():
            path = pathlib.Path(__file__).parent
            template_dir = path.parent / '_config' / 'obsidian'
            if template_dir.exists():
                config_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(template_dir, config_dir)
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
                release_url = _OBSIDIAN_PLUGINS.get(source.name)
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
                        for asset in _OBSIDIAN_PLUGIN_ASSETS:
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
                    else:
                        for tmp, dest in staged:
                            os.replace(tmp, dest)
        # create or merge each top-level json file
        for source in sorted(config_dir.glob('*.json')):
            target = obsidian_dir / source.name
            # create from source when the target is absent
            if not target.exists():
                shutil.copyfile(source, target)
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
            # union merge for arrays
            if isinstance(source_data, list) and isinstance(target_data, list):
                merged = target_data[:]
                for item in source_data:
                    if item not in merged:
                        merged.append(item)
            # deep merge for dicts
            elif isinstance(source_data, dict) and isinstance(target_data, dict):
                merged = merge(target_data, source_data)
            # handle invalid type
            else:
                raise TypeError(
                    f'Cannot merge {type(source_data).__name__} into'
                    f' {type(target_data).__name__}: .obsidian/{source.name}'
                )
            result = json.dumps(merged, indent=2)
            _write_atomic(target, result + '\n')
        return warnings

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
        frontmatter to pages that lack it; a page whose frontmatter
        never closes (no closing ``---``) is left untouched and warned
        about rather than rewritten, and an existing index with no
        (closed) frontmatter -- an emptied or truncated file -- is
        likewise skipped with a notice rather than rebuilt from scratch.

        When broken links are found (targets in the existing index
        that no longer exist on the filesystem), they are preserved
        and a warning is logged per link. Set ``prune=True`` to
        remove them instead. A file whose only drift is CRLF line
        endings is rewritten, normalizing it to LF. Every notice is
        emitted individually -- output modes (the CLI's condensed
        default) are the caller's concern.

        Logs a warning for each new link added with a placeholder
        ``...`` description, for each index-side link description
        overwritten because it diverged from its page's frontmatter
        ``desc`` (the source of truth -- the page is the place to
        edit), and when a deleted ``.wiki/cache/`` is recreated. A
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
        # ------ TODO: remove back-compat in future versions ------
        self._refuse_legacy_layout()
        # ---------------------------------------------------------
        # a dry run reports without mutating, so the marker guarantee
        # applies only to a writing run
        if not check:
            # restore the declared-root marker before reading policy from it
            for notice in self._ensure_settings():
                self._warn(notice)
        # compute corrected content for the scope (single timestamp)
        now = self._utc_now()
        overlay, baseline, notices = self._plan(folder, prune=prune, now=now)
        # report every broken/new link (preserved or added during the run)
        # individually and statelessly; the CLI condenses by default
        for notice in notices:
            self._warn(notice)
        # dry run: report which files would change without writing (a CRLF
        # file reads equal but would be rewritten, so probe its bytes too)
        if check:
            return [
                str(path.relative_to(self._root))
                for path, content in overlay.items()
                if not path.exists()
                or content != path.read_text(encoding='utf-8')
                or self._has_crlf(path)
            ]
        result = self._apply_plan(overlay, baseline, now)
        # refresh the counts cache, announcing a recreated .wiki/cache/ rather
        # than restoring a deleted directory silently
        recreated = not (self._root / WIKI_DIR / 'cache').exists()
        self._load_counts()
        if recreated:
            self._warn(f'Recreated {WIKI_DIR}/cache/ (derived counts cache)')
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
          with its diff suppressed.)
        - **Needs attention:** problems ``update`` cannot fix --
          invalid names, merge conflict markers, unclosed page
          frontmatter, emptied or truncated indexes, likely formatter
          damage (escaped wikilinks, or a thematic break standing
          where ``***`` belongs), missing trailing periods, stale
          links in user content, and broken links (targets that no
          longer exist; ``update`` keeps these without ``--prune``).

        Every line begins with the relevant path; an out-of-date file's
        diff follows its ``Requires update`` header, indented.

        A ``<!-- start: no-lint -->`` ... ``<!-- end: no-lint -->`` region
        suppresses the positional rules (conflict markers, escaped
        wikilinks, stale links) for the lines it wraps; file-level checks
        ignore regions, and a nested or dangling region marker is itself
        a hard issue.

        Placeholder descriptions, empty content sections, and CRLF line
        endings (which the next ``update`` normalizes) are soft notes
        (stderr) and do not count as issues.

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
        # ------ TODO: remove back-compat in future versions ------
        self._refuse_legacy_layout()
        # ---------------------------------------------------------
        # compute what update would write (the source of truth for drift)
        now = self._utc_now()
        overlay, _, _ = self._plan(folder, now=now)
        # walk all directories
        result = []
        folders = self._find_dirs(folder)
        for folder in folders:
            folder_relpath = folder.relative_to(self._root)
            # check folder name
            if folder != self._root and not self.validate_name(folder.name):
                result.append(f'{folder_relpath}/: Invalid folder name')
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
                # CRLF endings read clean through universal newlines; note
                # the pending normalization rather than failing the file
                if self._has_crlf(index_path):
                    self._warn(
                        f'{index_relpath}: CRLF line endings; update will normalize'
                    )
                # region directives: no-lint suppresses the positional rules
                # below, and a malformed pairing is itself a hard issue
                suppressed, region_issues = self._lint_regions(index_path, text)
                result.extend(region_issues)
                # conflict markers take precedence over the generated diff
                markers = _conflict_marker_lines(text)
                if any(n not in suppressed for n in markers):
                    result.append(f'{index_relpath}: Merge conflict markers')
                else:
                    # out of date: show what update would change
                    diff = self._diff(index_path, overlay)
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
                                ' formatter damage (exclude the wiki from'
                                ' markdown formatters; see README)'
                            )
                        else:
                            result.append(
                                f'{index_relpath}: Index missing *** delimiter'
                            )
                    # an escaped wikilink is the other formatter signature
                    escaped = _escaped_wikilink_lines(text)
                    if any(n not in suppressed for n in escaped):
                        result.append(
                            f'{index_relpath}: Escaped wikilinks: likely formatter'
                            ' damage (exclude the wiki from markdown formatters;'
                            ' see README)'
                        )
                    # human-only checks on current content
                    frontmatter, links, user_content = self._parse_index(text)
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
                    # the root display name has no enclosing dir to validate it
                    if folder == self._root:
                        root_name = self._read_frontmatter_name(frontmatter)
                        if root_name and not self.validate_name(root_name):
                            result.append(
                                f'{index_relpath}: Invalid wiki name {root_name!r}'
                            )
                    # broken links (targets gone; update keeps them) + descriptions
                    expected_targets = {
                        target for target, _ in self._build_expected_links(folder)
                    }
                    for target, label, link_desc in links:
                        if label == '..':
                            continue
                        # broken link: target no longer on the filesystem
                        if target not in expected_targets:
                            result.append(
                                f'{index_relpath}: Broken link [[{target}|{label}]]'
                            )
                            continue
                        # the period check applies only to an authored description:
                        # when the child supplies a real desc, update propagates it,
                        # so any drift (period included) is the diff's concern
                        child_text = self._current_text(
                            self._root / (target + '.md'),
                            overlay,
                        )
                        child_desc = None
                        if child_text is not None:
                            child_frontmatter, _ = self._parse_page(child_text)
                            child_desc = self._read_frontmatter_desc(child_frontmatter)
                        if not (child_desc and child_desc != '...'):
                            result.extend(
                                self._lint_link_desc(
                                    index_path,
                                    target,
                                    label,
                                    link_desc,
                                )
                            )
                    # stale links in user content; empty content is a soft note
                    result.extend(self._lint_stale_links(index_path, user_content))
                    if not user_content.strip():
                        self._warn(f'{index_relpath}: Empty content')
            # check pages (always, even when the index is missing)
            for page in self._find_pages(folder):
                page_relpath = page.relative_to(self._root)
                # report an invalid name for every file, including non-markdown
                if not self.validate_name(page.stem):
                    result.append(f'{page_relpath}: Invalid page name')
                # only markdown pages carry frontmatter/content to lint further
                if page.suffix != '.md':
                    continue
                text = self._read_text(page)
                # CRLF endings read clean through universal newlines; note
                # the pending normalization rather than failing the file
                if self._has_crlf(page):
                    self._warn(
                        f'{page_relpath}: CRLF line endings; update will normalize'
                    )
                # region directives: no-lint suppresses the positional rules
                # below, and a malformed pairing is itself a hard issue
                suppressed, region_issues = self._lint_regions(page, text)
                result.extend(region_issues)
                # conflict markers take precedence over the generated diff
                markers = _conflict_marker_lines(text)
                if any(n not in suppressed for n in markers):
                    result.append(f'{page_relpath}: Merge conflict markers')
                    continue
                # out of date: show what update would change
                diff = self._diff(page, overlay)
                if diff:
                    result.append(diff)
                # an escaped wikilink is the signature of formatter damage
                # (update never repairs page prose, so this is a human fix)
                escaped = _escaped_wikilink_lines(text)
                if any(n not in suppressed for n in escaped):
                    result.append(
                        f'{page_relpath}: Escaped wikilinks: likely formatter'
                        ' damage (exclude the wiki from markdown formatters;'
                        ' see README)'
                    )
                # human-only checks on current content
                frontmatter, content = self._parse_page(text)
                # unclosed frontmatter is left untouched by update (see
                # _plan_page), so surface it as a hard issue
                first_line = text.split('\n', 1)[0].lstrip('\ufeff')
                if not frontmatter and first_line.strip() == '---':
                    result.append(
                        f'{page_relpath}: Malformed frontmatter (no closing ---)'
                    )
                if frontmatter:
                    result.extend(self._lint_desc(page, frontmatter))
                result.extend(self._lint_stale_links(page, content))
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
        if start is not None or stop is not None:
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
                search.

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
            # search frontmatter fields
            if fields:
                frontmatter, _ = self._parse_page(text)
                if not frontmatter:
                    continue
                field_lines = self._field_line_ranges(frontmatter, lines, fields)
                for lineno, line in enumerate(lines, 1):
                    if lineno not in field_lines:
                        continue
                    # match against the value only -- the 'key: ' prefix (or
                    # continuation indentation) would defeat value anchors and
                    # match key names; surrounding YAML quotes are stripped so
                    # anchors see the value the wiki writes via _quote, and the
                    # reported line text stays raw
                    match = re.match(r'^(\w+):[^\S\n]*', line)
                    if match:
                        value = _unquote(line[match.end() :])
                    else:
                        value = line.strip()
                    if regex.search(value):
                        result.append((relpath, lineno, line))
            else:
                # search body content: skip only the frontmatter (the region the
                # word count and read slicing also exclude), so the three agree --
                # the H1 heading and an index's link block are body content and
                # are searched, since they are part of what read returns
                frontmatter, _ = self._parse_page(text)
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
                suffix. ``None`` = no truncation.
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
        if name:
            folder = self._resolve_folder(name)
        else:
            folder = self._root
        # counts come from the cache (lazily recomputed on stale entries);
        # skip the walk entirely when words are off
        counts = self._load_counts() if words else {}
        folder_words = self._folder_words(counts)
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
        )
        # a markerless index still maps via the reclaimed parse; warn rather
        # than let the CLI read it silently
        self._warn_markerless_index(folder)
        return '\n'.join(result)

    def _utc_now(self: Wiki) -> str:
        """Return the current timestamp string (UTC, ISO 8601, by default).

        The timezone and strftime format are configurable via ``settings.json``
        (``timestamp.timezone`` / ``timestamp.format``); a non-UTC timezone
        wants a format with ``%z`` rather than the literal trailing ``Z``.
        Override in subclasses for a different time source.

        Returns:
            Timestamp string like ``2026-01-15T12:30:00Z``.

        """
        policy = self._timestamp
        return dt.datetime.now(policy['zone']).strftime(policy['format'])

    def _warn(self: Wiki, message: str) -> None:
        """Emit a warning message.

        Override in subclasses to use a different logging mechanism.

        Args:
            message: Warning message text.

        """
        print(message, file=sys.stderr)

    def _ensure_settings(self: Wiki) -> list[str]:
        """Materialize a missing ``.wiki/settings.json`` as ``{}``.

        The settings file is the declared-root marker: ``init`` writes it
        and every mutating command restores a lost one, so the declaration
        guarantee is enforced rather than assumed. The materialized file
        is an empty object -- all defaults; policy is never invented.

        Returns:
            Notices describing the restoration (empty when present).

        Raises:
            ValueError: If a root ``_config/settings.json`` exists -- the
                legacy config namespace is never read, so its policy must
                migrate before any policy-reading write proceeds.

        """
        # ------ TODO: remove back-compat in future versions ------
        self._refuse_legacy_layout()
        # ---------------------------------------------------------
        path = self._root / WIKI_SETTINGS
        if path.exists():
            return []
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, '{}\n')
        return [f'Restored missing {WIKI_SETTINGS} ({{}} -- all defaults)']

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

        """
        # ------ TODO: remove back-compat in future versions ------
        if (self._root / '_config' / 'settings.json').is_file():
            raise ValueError(
                'Legacy wiki layout. Please perform the following migration:'
                '\n  (1) move `_config/` -> `.wiki/` (delete `.wiki/` first if present)'
                '\n  (2) run `wiki config`'
                '\n  (3) run `wiki update`'
            )
        # ---------------------------------------------------------

    def _index_missing_marker(self: Wiki, folder: pathlib.Path) -> bool:
        """Return ``True`` if ``folder``'s index lost its ``***`` delimiter.

        The delimiter separates generated links from user content; without it
        :meth:`_parse_index` must heuristically reclaim the demoted link block
        (see :meth:`_reclaim_links`). Reports the gap only when the index
        exists and the folder has on-disk children that should be linked, so a
        genuinely empty wiki is not mistaken for a broken one.
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
        _, line_number = self._extract_frontmatter(lines)
        # unclosed frontmatter extracts as none, leaving its own opening
        # '---' first in the walk (past any leading blanks): a truncated
        # index, not a rewritten marker
        opener = next((line for line in lines if line.strip()), '')
        if line_number == 0 and opener.lstrip('\ufeff').strip() == '---':
            return False
        # walk past the H1 and the leading link run (formatter escapes and
        # desc continuations directly under a link tolerated, mirroring
        # _reclaim_links) to the line standing where the delimiter belongs
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
            self._warn(f'{relpath} is missing its *** delimiter; run `wiki update`')

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
        """Enrich frontmatter during a plan.

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

    def _wiki_path(self: Wiki, name: str) -> pathlib.Path:
        """Construct the wiki path for a name."""
        return self._root / name / WIKI_INDEX

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
            raise FileNotFoundError(f'Wiki entry not found: {name!r}')
        path = (self._root / name).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError(f'Path is outside wiki root: {name}')
        if path.is_dir():
            index = path / WIKI_INDEX
            if index.is_file():
                return index
        if path.is_file():
            return path
        # append (not with_suffix, which would replace a dotted name's last
        # segment -- 'app.config' -> 'app.md') so a page whose name contains a
        # dot (e.g. 'app.config', 'v1.2') resolves to '<name>.md'
        result = path.with_name(path.name + '.md')
        if result.is_file():
            return result
        # the literal path missed; a bare leaf ('oncall') for a nested page
        # ('team/eng/oncall') is a common miss -- when exactly one page's stem
        # matches, name its full read key in the error so the user can retry
        suggestion = self._suggest_leaf_match(name)
        if suggestion is not None:
            raise FileNotFoundError(
                f'Wiki entry not found: {name} (did you mean {suggestion}?)'
            )
        raise FileNotFoundError(f'Wiki entry not found: {name}')

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
                if page.suffix == '.md' and page.stem == leaf:
                    matches.append(self._path_to_name(page))
        # only suggest when the match is unambiguous
        if len(matches) == 1:
            return matches[0]
        return None

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
            if resolved.is_relative_to(self._root):
                return resolved
            raise ValueError(f'Path is outside wiki root: {path}')
        raise FileNotFoundError(f'Wiki folder not found: {path}')

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
        # join parts and return
        return self.path_sep.join(parts)

    def _link_target(self: Wiki, path: pathlib.Path) -> str:
        """Convert a path to a wikilink target relative to wiki root."""
        relpath = path.relative_to(self._root)
        return str(relpath.with_suffix(''))

    def _is_excluded_file(self: Wiki, path: pathlib.Path) -> bool:
        """Return ``True`` if file should be excluded from index links.

        Excludes wiki index files (handled separately as the folder
        index) and files with ``.`` prefix.
        """
        return path.name == WIKI_INDEX or path.name.startswith('.')

    def _is_excluded_dir(self: Wiki, path: pathlib.Path) -> bool:
        """Return ``True`` if directory should be excluded from index links.

        Excludes directories with ``.`` prefix (which keeps the ``.wiki``
        tool directory out of the walk by construction) and symlinked
        directories (following a symlink re-walks the same inode, producing
        duplicate/conflicting index writes and risking loops).
        """
        return path.name.startswith('.') or path.is_symlink()

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
                if all_files or page.suffix == '.md':
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
        if overlay is not None and path in overlay:
            return overlay[path]
        if path.exists():
            return self._read_text(path)
        return None

    def _extract_frontmatter(
        self: Wiki,
        lines: list[str],
    ) -> tuple[str, int]:
        """Extract YAML frontmatter from lines.

        Returns ``(frontmatter, line_number)`` where ``line_number``
        is the first line after the closing ``---``. Returns
        ``('', 0)`` if no frontmatter is found.
        """
        # require an opening '---' (tolerating a UTF-8 BOM, which common
        # Windows editors prepend and str.strip does not remove)
        if lines and lines[0].lstrip('\ufeff').strip() == '---':
            line_number = 1
            # only an unindented '---' closes the frontmatter (an indented one is
            # content in a block scalar), so match on rstrip rather than strip
            while line_number < len(lines) and lines[line_number].rstrip() != '---':
                line_number += 1
            # no closing '---' -> malformed/unclosed frontmatter; treat the file as
            # having none so the body is preserved as content rather than silently
            # consumed to EOF (which would let an update discard the whole body)
            if line_number >= len(lines):
                return '', 0
            line_number += 1
            return '\n'.join(lines[:line_number]), line_number
        return '', 0

    def _parse_index(
        self: Wiki,
        text: str,
    ) -> tuple[str, list[tuple[str, str, str]], str]:
        r"""Parse an ``_index.md`` file into components.

        Returns ``(frontmatter, links, user_content)``:

        - ``frontmatter``: raw frontmatter text including ``---`` delimiters
        - ``links``: list of ``(target, label, description)`` tuples
        - ``user_content``: everything after the first delimiter, with any
          prose found above the first link folded in so it is never dropped

        Supports multi-line descriptions: continuation lines (not a link,
        not a delimiter, not blank) are appended to the previous link's
        description.

        A link row carrying the leading-escape formatter damage (``\[\[``
        or ``\[[``) parses as a link, so an escaped block above an intact
        delimiter repairs in place rather than demoting to user content.

        When the delimiter is missing, a leading link run is reclaimed
        (see :meth:`_reclaim_links`) and the rest of the body is the user
        content, so a formatter-mangled index repairs instead of
        duplicating its link block.

        Args:
            text: Raw file content.

        """
        # alias delimiter and lines
        delimiter = self.index_delimiter
        lines = text.split('\n')
        # extract frontmatter
        frontmatter, line_number = self._extract_frontmatter(lines)
        # find the first delimiter after the frontmatter
        marker = None
        for i in range(line_number, len(lines)):
            if lines[i].rstrip() == delimiter:
                marker = i
                break
        # no delimiter: reclaim a demoted link run (a mangled marker), then
        # fold the rest into user content rather than risk dropping prose
        if marker is None:
            body = lines[line_number:]
            while body and not body[0].strip():
                body.pop(0)
            if body and re.match(r'^#\s', body[0]):
                body.pop(0)
            while body and not body[0].strip():
                body.pop(0)
            links, body = self._reclaim_links(body)
            return frontmatter, links, '\n'.join(body)
        # extract user content (everything after the marker)
        user_content = '\n'.join(lines[marker + 1 :])
        # extract links (everything between frontmatter and the marker)
        end = marker
        links = []
        current_link = None
        # prose above the first link is neither a link nor a continuation;
        # capture it as preamble rather than dropping it (the H1 and surrounding
        # blanks drop out, regenerated on render)
        preamble = []
        # blank lines inside a description are held until we know whether a
        # continuation follows (a paragraph break, kept) or the next link /
        # delimiter does (the separator before the next entry, dropped)
        pending_blanks = 0
        for i in range(line_number, end):
            line = lines[i]
            # skip delimiters
            if line.strip() == delimiter:
                if current_link is not None:
                    links.append(current_link)
                    current_link = None
                pending_blanks = 0
                continue
            # try to match a new link: the raw line first (a name may hold a
            # real backslash), then -- only for the leading-escape damage
            # shape (\[\[ or \[[) -- with formatter escapes (\[ \] \_)
            # undone, so an escaped link block above an intact delimiter
            # repairs in place; a healthy desc continuation escapes inside
            # its leading brackets ([\[) and is never promoted to a link
            stripped = line.strip()
            match = re.match(r'^\[\[(.+?)\|(.+?)\]\](?::\s*(.*))?$', stripped)
            if match is None and stripped.startswith('\\['):
                candidate = re.sub(r'\\([\[\]_])', r'\1', stripped)
                match = re.match(r'^\[\[(.+?)\|(.+?)\]\](?::\s*(.*))?$', candidate)
            if match:
                # flush previous link
                if current_link is not None:
                    links.append(current_link)
                pending_blanks = 0
                target = match.group(1)
                label = match.group(2)
                desc = match.group(3) or ''
                current_link = (target, label, desc)
            elif current_link is not None:
                # hold a blank line pending the next line's type
                if not line.strip():
                    pending_blanks += 1
                    continue
                # continuation line: restore held blanks (paragraph breaks)
                target, label, desc = current_link
                desc = desc + '\n' * (pending_blanks + 1) + line.rstrip()
                current_link = (target, label, desc)
                pending_blanks = 0
            else:
                # before the first link: hold for the preamble
                preamble.append(line)
        # flush last link
        if current_link is not None:
            links.append(current_link)
        # strip the regenerated H1 (wherever it sits -- lead prose can precede it)
        # and surrounding blanks, then fold surviving prose into user content;
        # the no-delimiter branch above preserves the body the same way
        for i, line in enumerate(preamble):
            if re.match(r'^#\s', line):
                # drop the H1 and an adjacent blank so removal leaves no gap
                del preamble[i]
                if i < len(preamble) and not preamble[i].strip():
                    del preamble[i]
                elif i > 0 and not preamble[i - 1].strip():
                    del preamble[i - 1]
                break
        while preamble and not preamble[0].strip():
            preamble.pop(0)
        while preamble and not preamble[-1].strip():
            preamble.pop()
        if preamble:
            kept = '\n'.join(preamble)
            user_content = f'{kept}\n\n{user_content}' if user_content else kept
        # return index sections
        return frontmatter, links, user_content

    def _reclaim_links(
        self: Wiki,
        body: list[str],
    ) -> tuple[list[tuple[str, str, str]], list[str]]:
        """Reclaim the leading link run from a markerless index body.

        A formatter that mangles the ``***`` delimiter (rewriting it to a
        ``---`` thematic break, or backslash-escaping the wikilinks) demotes
        the generated link block to user content; re-rendering would then
        emit a fresh block above the stale one, duplicating every link on
        each update. When ``body`` opens with lines that parse as links
        (formatter escapes tolerated), take that run -- plus the thematic
        break standing where the delimiter was -- as the link block and
        return the remainder as user content. A body that opens with prose
        reclaims nothing, so prose is never parsed into invented links.

        Args:
            body: Index body lines (frontmatter, H1, and surrounding
                blanks already stripped).

        Returns:
            Tuple of ``(links, remainder)`` where ``links`` are
            ``(target, label, description)`` tuples and ``remainder`` is
            the surviving user content lines.

        """
        # walk the head of the body, consuming link lines, their directly
        # attached continuations, and the blanks between entries
        links = []
        current_link = None
        consumed = 0
        pending_blanks = 0
        for i, line in enumerate(body):
            stripped = line.strip()
            # hold blanks until the next line decides whether the run goes on
            if not stripped:
                pending_blanks += 1
                continue
            # try the raw line first (a name may hold a real backslash), then
            # -- only for the leading-escape damage shape (\[\[ or \[[) -- with
            # formatter escapes (\[ \] \_) undone; a healthy desc continuation
            # escapes inside its leading brackets ([\[) and is never promoted
            match = re.match(r'^\[\[(.+?)\|(.+?)\]\](?::\s*(.*))?$', stripped)
            if match is None and stripped.startswith('\\['):
                candidate = re.sub(r'\\([\[\]_])', r'\1', stripped)
                match = re.match(r'^\[\[(.+?)\|(.+?)\]\](?::\s*(.*))?$', candidate)
            if match:
                # flush previous link
                if current_link is not None:
                    links.append(current_link)
                target = match.group(1)
                label = match.group(2)
                desc = match.group(3) or ''
                current_link = (target, label, desc)
                pending_blanks = 0
                consumed = i + 1
                continue
            # a thematic break after the run is the mangled delimiter: drop it
            if current_link is not None:
                if re.fullmatch(r'\*{3,}|-{3,}|_{3,}', stripped):
                    consumed = i + 1
                    break
            # a line directly under a link continues its description
            if current_link is not None and not pending_blanks:
                target, label, desc = current_link
                current_link = (target, label, f'{desc}\n{line.rstrip()}')
                consumed = i + 1
                continue
            # prose: the run (and the reclaim) ends here
            break
        # flush last link
        if current_link is not None:
            links.append(current_link)
        # drop the blanks held between the run and the surviving remainder
        remainder = body[consumed:]
        while remainder and not remainder[0].strip():
            remainder.pop(0)
        return links, remainder

    def _parse_page(self: Wiki, text: str) -> tuple[str, str]:
        """Parse a page file into ``(frontmatter, content)``.

        Extracts YAML frontmatter delimited by ``---`` lines.
        If no frontmatter is present, returns ``('', text)``.

        Args:
            text: Raw file content.

        Returns:
            Tuple of ``(frontmatter, content)``. Frontmatter includes
            the ``---`` delimiters. Content is everything after the
            closing ``---``.

        """
        lines = text.split('\n')
        frontmatter, line_number = self._extract_frontmatter(lines)
        if frontmatter:
            content = '\n'.join(lines[line_number:])
            return frontmatter, content
        return '', text

    def _find_heading(self: Wiki, text: str) -> Optional[tuple[int, str]]:
        """Find the first ``# heading`` outside fenced code blocks.

        Returns ``(line_index, title)`` for the heading, or
        ``None`` if there is no top-level heading. The line index
        lets callers rewrite the exact heading line rather than the
        first textual match (which could be inside a code block).
        """
        fence = None
        for index, line in enumerate(text.split('\n')):
            if fence is not None:
                if line.strip() == fence:
                    fence = None
                continue
            match = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
            if match:
                fence = match.group(1)
                continue
            match = re.match(r'^# (.+)$', line)
            if match:
                return index, match.group(1)
        return None

    def _build_frontmatter(
        self: Wiki,
        name: str,
        created: str,
        updated: str,
    ) -> str:
        """Build YAML frontmatter string.

        Args:
            name: Display name for the index.
            created: ISO 8601 timestamp.
            updated: ISO 8601 timestamp.

        Returns:
            Complete frontmatter block including ``---`` delimiters.

        """
        lines = [
            '---',
            f'name: {_quote(name)}',
            'desc: ...',
            'category: null',
            'tags: []',
            'sources: []',
            f'created: {created}',
            f'updated: {updated}',
            '---',
        ]
        return '\n'.join(lines)

    def _read_frontmatter_field(
        self: Wiki,
        frontmatter: str,
        key: str,
    ) -> Optional[str]:
        """Read a scalar frontmatter ``key``, resolving block scalars.

        A plain ``key: value`` returns the stripped value, with one pair of
        matching surrounding YAML quotes stripped (a desc containing ``: ``
        must be quoted to stay valid YAML). A block scalar (``|``/``>`` with
        optional chomping/indentation indicators, e.g. ``|-``, ``>+``, ``|2``)
        resolves to its body: a literal ``|`` keeps line breaks, a folded
        ``>`` joins consecutive non-empty lines with a single space (a blank
        line is a paragraph break). Inline text on the indicator line
        (``key: > one liner.``) is taken as the value when no indented body
        follows. Returns ``None`` if the field is absent; an empty block body
        resolves to an empty string.
        """
        # single-line value
        match = re.search(rf'^{key}:[^\S\n]*(.+)$', frontmatter, re.MULTILINE)
        if match:
            value = match.group(1).strip()
            if not value.startswith(('|', '>')):
                return _unquote(value)
        else:
            return None
        # block scalar: tolerate any header (chomping/indentation indicators
        # |- |+ >- |2 ...) plus trailing inline text, then capture the indented
        # body (blank lines inside the block are kept so a folded break survives)
        match = re.search(
            rf'^{key}:[^\S\n]*([|>])[-+0-9]*[^\S\n]*(.*)\n((?:[ \t]+.*\n|[ \t]*\n)*)',
            frontmatter,
            re.MULTILINE,
        )
        if not match:
            return None
        indicator, inline, body = match.group(1), match.group(2), match.group(3)
        # no indented body: the inline text on the header line is the value
        if not body:
            return inline.strip()
        body = textwrap.dedent(body)
        # folded scalar (>): join non-empty lines with a space, blank line breaks
        if indicator == '>':
            return _fold_lines(body)
        return body.strip()

    def _read_frontmatter_name(self: Wiki, frontmatter: str) -> Optional[str]:
        """Read the ``name`` field from frontmatter text.

        Handles multi-line YAML values (block scalars ``|``, ``>``) the
        same way :meth:`_read_frontmatter_desc` does, so a block-scalar
        name resolves to its body text rather than the ``|``/``>``
        indicator. Returns ``None`` if no name field is found.
        """
        return self._read_frontmatter_field(frontmatter, 'name')

    def _read_frontmatter_desc(self: Wiki, frontmatter: str) -> Optional[str]:
        """Read the ``desc`` field from frontmatter text.

        Handles multi-line YAML values (block scalars ``|``, ``>``, with
        chomping/indentation indicators). Returns ``None`` if no desc
        field is found; an empty block body resolves to an empty string.
        """
        return self._read_frontmatter_field(frontmatter, 'desc')

    def _read_frontmatter_category(self: Wiki, frontmatter: str) -> str:
        """Read the ``category`` field from frontmatter text.

        Returns an empty string if the field is absent, empty, or ``null``.
        """
        value = self._read_frontmatter_field(frontmatter, 'category')
        if value is None or value == 'null':
            return ''
        return value

    def _body_words(self: Wiki, text: str) -> int:
        """Count the body words of a page or index text.

        Counts the body -- everything below the frontmatter, which is the only
        special region -- so the count matches the searchable/sliceable region
        exactly. The H1 heading and an index's auto-generated link block are body
        content, so they are counted (they are part of what ``read`` returns).
        """
        _, body = self._parse_page(text)
        return len(body.split())

    def _load_counts(self: Wiki) -> dict[str, int]:
        """Return body word counts for every markdown file, via the cache.

        Reads ``.wiki/cache/word_counts.json`` under the wiki root, recomputes
        entries whose cached mtime no longer matches the file's, drops entries
        for files that are gone, and rewrites the cache when anything changed.
        A corrupt or unreadable cache is discarded and fully recomputed -- the
        cache can never break a command, the worst case is a full recompute.
        ``update`` calls this after writing, so the cache tracks the tree.

        Returns:
            Dict mapping root-relative paths to body word counts.

        """
        # load the existing cache, tolerating absence or corruption
        cache_path = self._root / WIKI_DIR / 'cache' / 'word_counts.json'
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
                if path.suffix != '.md' or not path.is_file():
                    continue
                relpath = str(path.relative_to(self._root))
                mtime = path.stat().st_mtime
                entry = cached.get(relpath)
                fresh = (
                    isinstance(entry, dict)
                    and entry.get('mtime') == mtime
                    and isinstance(entry.get('words'), int)
                )
                if not fresh:
                    # an undecodable page has no countable body
                    try:
                        words = self._body_words(self._read_text(path))
                    except UnicodeDecodeError:
                        words = 0
                    entry = {'mtime': mtime, 'words': words}
                    dirty = True
                entries[relpath] = entry
                result[relpath] = entry['words']
        # rewrite the cache when entries changed (recomputes, adds, or drops)
        if dirty or set(entries) != set(cached):
            self._write_counts(entries)
        return result

    def _write_counts(self: Wiki, entries: dict) -> None:
        """Write the counts cache, materializing a self-ignoring ``.wiki/cache/``.

        The cache directory carries its own ``.gitignore`` (``*``) so the
        derived state never needs host-repo ignore configuration (the
        settings file beside it stays tracked), and the ``.wiki`` dot
        prefix keeps it out of the wiki walk.
        """
        cache_dir = self._root / WIKI_DIR / 'cache'
        cache_dir.mkdir(parents=True, exist_ok=True)
        gitignore = cache_dir / '.gitignore'
        if not gitignore.exists():
            _write_atomic(gitignore, '*\n')
        content = json.dumps(entries, indent=2, sort_keys=True)
        _write_atomic(cache_dir / 'word_counts.json', content + '\n')

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

    def _field_line_ranges(
        self: Wiki,
        frontmatter: str,
        lines: list[str],
        fields: list[str],
    ) -> set[int]:
        r"""Return 1-based line numbers belonging to named frontmatter fields.

        Walks the frontmatter region of ``lines`` and collects line
        numbers for each field key line and its continuation lines
        (multi-line block scalars).

        Args:
            frontmatter: Parsed frontmatter string (including delimiters).
            lines: Full file lines (from ``text.split('\n')``).
            fields: Field names to match.

        """
        # initialize result
        result = set()
        frontmatter_end = len(frontmatter.split('\n'))
        current_field = None
        for lineno, line in enumerate(lines, 1):
            if lineno >= frontmatter_end:
                break
            # check for field key
            match = re.match(r'^(\w+):', line)
            if match:
                current_field = match.group(1)
                if current_field in fields:
                    result.add(lineno)
                continue
            # continuation line of current field
            if current_field in fields:
                result.add(lineno)
        return result

    def _render_index(
        self: Wiki,
        name: str,
        frontmatter: str,
        links: list[tuple[str, str, str]],
        user_content: str,
    ) -> str:
        """Render a complete ``_index.md`` file.

        All links are in a single section. One delimiter separates
        links from user content (always present).
        """
        # initialize index contents
        parts = [frontmatter, '', f'# {name}', '']
        # render links
        for target, label, desc in links:
            parts.append(self._format_link(target, label, desc))
            parts.append('')
        # delimiter + user content
        parts.append(self.index_delimiter)
        if user_content:
            parts.append(user_content)
        else:
            parts.append('')
        # join parts and return index
        return '\n'.join(parts)

    def _render_page(self: Wiki, frontmatter: str, content: str) -> str:
        """Combine frontmatter and content into a page file.

        Inverse of ``_parse_page``.

        Args:
            frontmatter: YAML frontmatter block including ``---`` delimiters.
            content: Page content after the frontmatter.

        Returns:
            Complete page text.

        """
        if content:
            return frontmatter + '\n' + content
        return frontmatter + '\n'

    def _format_link(self: Wiki, target: str, label: str, description: str) -> str:
        """Format a single link line.

        Parent links (``..``) have no description.
        All other links include a description (at minimum ``...``).
        """
        if label == '..':
            return f'[[{target}|{label}]]'
        desc = description or '...'
        return f'[[{target}|{label}]]: {desc}'

    def _escape_desc(self: Wiki, desc: str) -> str:
        r"""Escape desc lines that would parse as index structure.

        A propagated multi-line desc renders its continuation lines at
        column 0 inside the link block, where a line equal to the ``***``
        delimiter would end the block early (every later link re-added as
        new on the next update, growing the index without bound) and a
        link-shaped line would parse as a phantom entry. A delimiter line
        gets a leading backslash; a link-shaped line gets the backslash
        inside its leading brackets (``[\[``) so the healthy escape never
        carries the ``\[[`` formatter-damage signature lint scans for.
        Markdown renders the text unchanged either way, and the parser
        reads both as ordinary continuations. The escape is stable, so
        re-propagation converges. The first line never needs it -- it sits
        on the link line itself.
        """
        first, *rest = desc.split('\n')
        lines = [first]
        for line in rest:
            stripped = line.strip()
            if stripped == self.index_delimiter:
                line = line.replace(stripped, f'\\{stripped}', 1)
            elif re.match(r'^\[\[.+?\|.+?\]\](?::\s*.*)?$', stripped):
                line = line.replace(stripped, f'[\\{stripped[1:]}', 1)
            lines.append(line)
        return '\n'.join(lines)

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
            frontmatter, body = self._parse_page(content)
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
        Folders get a trailing ``/``.
        """
        # initialize links
        result = []
        # parent link
        if folder != self._root:
            parent = folder.parent
            target = parent / WIKI_INDEX
            target = str(target.relative_to(self._root).with_suffix(''))
            result.append((target, '..'))
        # child directory links
        children = []
        for path in folder.iterdir():
            if path.is_dir() and not self._is_excluded_dir(path):
                children.append(path)
        for child in sorted(children):
            target = child / WIKI_INDEX
            target = str(target.relative_to(self._root).with_suffix(''))
            result.append((target, f'{child.name}/'))
        # page links
        for page in self._find_pages(folder):
            if page.suffix == '.md':
                target = str(page.relative_to(self._root).with_suffix(''))
                result.append((target, page.stem))
            else:
                target = str(page.relative_to(self._root))
                result.append((target, page.name))
        # return links
        return result

    def _invalid_links(
        self: Wiki,
        folder: pathlib.Path,
    ) -> list[tuple[str, str]]:
        """Return ``(target, relpath)`` for folder entries with invalid names.

        A child folder or page whose name/stem fails :meth:`validate_name`
        (e.g. a denied ``|`` or ``#``) is reported so the plan can drop its
        link rather than emit a malformed wikilink. ``target`` matches the
        wikilink target :meth:`_build_expected_links` would have produced;
        ``relpath`` names the offending path for the warning.
        """
        # initialize results
        result = []
        # child directory entries (validated on the folder name)
        for path in sorted(folder.iterdir()):
            if path.is_dir() and not self._is_excluded_dir(path):
                if not self.validate_name(path.name):
                    target = path / WIKI_INDEX
                    target = str(target.relative_to(self._root).with_suffix(''))
                    result.append((target, str(path.relative_to(self._root))))
        # page entries (validated on the stem)
        for page in self._find_pages(folder):
            if not self.validate_name(page.stem):
                if page.suffix == '.md':
                    target = str(page.relative_to(self._root).with_suffix(''))
                else:
                    target = str(page.relative_to(self._root))
                result.append((target, str(page.relative_to(self._root))))
        # return invalid entries
        return result

    def _merge_links(
        self: Wiki,
        existing: list[tuple[str, str, str]],
        expected: list[tuple[str, str]],
        labels: Optional[dict[str, str]] = None,
        prune: bool = False,
    ) -> tuple[
        list[tuple[str, str, str]],
        list[tuple[str, str, str]],
        list[tuple[str, str, str]],
    ]:
        """Merge existing links with expected, refreshing labels and preserving descs.

        ``expected`` provides ``(target, base_label)`` pairs from the filesystem.
        ``labels`` maps a target to its categorized label
        (e.g. ``{'path/_index': '[store] name/'}``), supplied every update by
        ``_read_child_labels``.

        Each expected link's label is recomputed from current state: the categorized
        label when ``labels`` has the target, otherwise the base label. Descriptions
        are preserved from existing links; new links get ``...``. Parent links
        (``..``) never get a description.

        When ``prune`` is ``False`` (default), broken links (existing targets
        no longer on the filesystem) are preserved in the merged result.
        When ``True``, they are excluded.

        Returns:
            Tuple of ``(merged, broken, new)`` where each is a list
            of ``(target, label, description)`` tuples.

        """
        # lookup existing by target
        existing_by_target = {target: (label, desc) for target, label, desc in existing}
        expected_targets = {target for target, _ in expected}
        # identify broken links (in existing but not in expected)
        broken = []
        for target, label, desc in existing:
            if target not in expected_targets and label != '..':
                broken.append((target, label, desc))
        # build merged list from expected
        result = []
        new = []
        for target, base_label in expected:
            # authoritative label: categorized if available, else base
            if labels and target in labels:
                label = labels[target]
            else:
                label = base_label
            if target in existing_by_target:
                # preserve description, refresh label from current state
                _, desc = existing_by_target[target]
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
        links: list[tuple[str, str, str]],
    ) -> list[tuple[str, str, str]]:
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
                frontmatter, _ = self._parse_page(text)
                category = self._read_frontmatter_category(frontmatter)
            else:
                category = ''
            if category:
                target = self._link_target(child_index)
                result[target] = f'[{category}] {child.name}/'
        # read page categories from markdown page frontmatter
        for page in self._find_pages(folder):
            if page.suffix != '.md':
                continue
            text = self._current_text(page, overlay)
            if text is not None:
                frontmatter, _ = self._parse_page(text)
                category = self._read_frontmatter_category(frontmatter)
            else:
                category = ''
            if category:
                target = self._link_target(page)
                result[target] = f'[{category}] {page.stem}'
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
        list[str],
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
            are the broken/new-link, desc-overwrite, and
            malformed-frontmatter warnings collected across all indexes
            and pages.

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
            # writer can detect (and skip) a concurrent edit to the file
            index_path = folder / WIKI_INDEX
            baseline[index_path] = self._current_text(index_path)
            content, index_notices = self._plan_index(
                folder=folder,
                now=now,
                prune=prune,
                overlay=overlay,
            )
            overlay[index_path] = content
            notices.extend(index_notices)
        # plan pages
        for folder in folders:
            for page in self._find_pages(folder):
                if page.suffix == '.md':
                    baseline[page] = self._current_text(page)
                    content, page_notices = self._plan_page(page, now)
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
            if current is not None and content == current and not self._has_crlf(path):
                continue
            # skip a file that changed since the plan read it: writing would
            # revert the concurrent edit; the next run converges
            if current != baseline[path]:
                relpath = path.relative_to(self._root)
                self._warn(
                    f'Skipping {relpath}: changed during update; re-run `wiki update`'
                )
                continue
            # stamp updated: now that a write is happening
            content = re.sub(
                r'^updated:.*$',
                f'updated: {now}',
                content,
                count=1,
                flags=re.MULTILINE,
            )
            _write_atomic(path, content)
            result.append(str(path.relative_to(self._root)))
        return result

    def _plan_index(
        self: Wiki,
        folder: pathlib.Path,
        now: str,
        *,
        prune: bool = False,
        overlay: dict[pathlib.Path, str],
    ) -> tuple[str, list[str]]:
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
            prune: Remove broken links instead of preserving them.
            overlay: Staged ``{path: content}`` from earlier passes.

        Returns:
            Tuple of ``(content, notices)`` where ``notices`` are the
            broken/new-link and desc-overwrite warnings (emitted by the
            writer, not here).

        """
        # alias index path
        path = folder / WIKI_INDEX
        # determine name
        name = self._path_to_name(folder)
        # parse existing (staged content wins over disk)
        text = self._current_text(path, overlay)
        if text is not None:
            frontmatter, existing, user_content = self._parse_index(text)
            if frontmatter:
                # update name from folder path (add it if the field is missing, so
                # an index with frontmatter but no name: does not stay un-named)
                if re.search(r'^name:', frontmatter, re.MULTILINE):
                    # callable repl so a backslash-digit in the
                    # name is not read as a group reference
                    frontmatter = re.sub(
                        r'^name:.*$',
                        lambda _: f'name: {_quote(name)}',
                        frontmatter,
                        count=1,
                        flags=re.MULTILINE,
                    )
                else:
                    pos = frontmatter.rfind('---')
                    frontmatter = frontmatter[:pos] + f'name: {_quote(name)}\n---'
                # add desc field if missing (after name line); restore the
                # placeholder on a present-but-blank key
                if re.search(r'^desc:[^\S\n]*$', frontmatter, re.MULTILINE):
                    frontmatter = re.sub(
                        r'^desc:[^\S\n]*$',
                        'desc: ...',
                        frontmatter,
                        count=1,
                        flags=re.MULTILINE,
                    )
                elif not re.search(r'^desc:', frontmatter, re.MULTILINE):
                    frontmatter = re.sub(
                        r'^(name:.*\n)',
                        r'\1desc: ...\n',
                        frontmatter,
                        count=1,
                        flags=re.MULTILINE,
                    )
                # add created/updated if missing; stamp a present-but-blank key
                # in place so a duplicate is never appended
                if re.search(r'^created:[^\S\n]*$', frontmatter, re.MULTILINE):
                    frontmatter = re.sub(
                        r'^created:[^\S\n]*$',
                        f'created: {now}',
                        frontmatter,
                        count=1,
                        flags=re.MULTILINE,
                    )
                elif not re.search(r'^created:', frontmatter, re.MULTILINE):
                    match = re.search(r'^updated:', frontmatter, re.MULTILINE)
                    pos = match.start() if match else frontmatter.rfind('---')
                    frontmatter = (
                        frontmatter[:pos] + f'created: {now}\n' + frontmatter[pos:]
                    )
                if re.search(r'^updated:[^\S\n]*$', frontmatter, re.MULTILINE):
                    frontmatter = re.sub(
                        r'^updated:[^\S\n]*$',
                        f'updated: {now}',
                        frontmatter,
                        count=1,
                        flags=re.MULTILINE,
                    )
                elif not re.search(r'^updated:', frontmatter, re.MULTILINE):
                    pos = frontmatter.rfind('---')
                    frontmatter = (
                        frontmatter[:pos] + f'updated: {now}\n' + frontmatter[pos:]
                    )
            else:
                # an existing index with no (closed) frontmatter is an emptied or
                # truncated file: rebuilding it fresh would permanently discard its
                # authored content, so keep it as-is and name the recovery paths
                relpath = path.relative_to(self._root)
                return text, [
                    f'Empty or truncated index (no frontmatter) in {relpath};'
                    ' restore it from git or delete it to rebuild'
                ]
        else:
            frontmatter = self._build_frontmatter(
                name=name,
                created=now,
                updated=now,
            )
            existing = []
            user_content = ''
        # enrich frontmatter (hook for subclass tag enrichment)
        frontmatter = self._enrich_frontmatter(path, frontmatter)
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
            notices.append(f'New index: {relpath} (fill in its desc)')
        invalid = self._invalid_links(folder)
        for _target, skipped in invalid:
            notices.append(f'Skipping {skipped}: invalid name')
        invalid_targets = {target for target, _ in invalid}
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
        # collect broken/new link notices (emitted by the writer, not here)
        for target, label, _desc in broken:
            if prune:
                notices.append(f'Pruned link: [[{target}|{label}]] from {relpath}')
            else:
                notices.append(f'Broken link: [[{target}|{label}]] in {relpath}')
        for target, label, _desc in new:
            notices.append(f'New link: [[{target}|{label}]] in {relpath}')
        # propagate child desc frontmatter to link descriptions
        propagated = []
        for target, label, link_desc in links:
            if label == '..':
                propagated.append((target, label, link_desc))
                continue
            # resolve child path from target (staged content wins over disk)
            child_path = self._root / (target + '.md')
            child_text = self._current_text(child_path, overlay)
            if child_text is None:
                propagated.append((target, label, link_desc))
                continue
            child_frontmatter, _ = self._parse_page(child_text)
            child_desc = self._read_frontmatter_desc(child_frontmatter)
            if child_desc and child_desc != '...':
                # child desc is source of truth; rstrip each line first
                # (_parse_index never preserves trailing spaces, so an
                # unnormalized desc re-triggers the overwrite notice on every
                # converged run), then escape lines that would parse as index
                # structure once rendered in the link block
                child_desc = '\n'.join(
                    line.rstrip() for line in child_desc.strip().split('\n')
                )
                child_desc = self._escape_desc(child_desc)
                # line breaks are formatter-owned: a link desc differing only
                # in wrapping is already converged, so the index's own breaks
                # survive and only a content change ports the frontmatter
                # desc (with its breaks) back onto the row
                if link_desc.replace('\n', ' ') == child_desc.replace('\n', ' '):
                    propagated.append((target, label, link_desc))
                    continue
                # announce a genuine overwrite (a hand-edit would otherwise vanish
                # silently); first-time propagation onto the placeholder stays quiet
                if link_desc not in ('', '...'):
                    notices.append(
                        f'Overwrote desc: [[{target}|{label}]] in {relpath}'
                        f' (the page frontmatter desc wins; edit it in {target}.md)'
                    )
                propagated.append((target, label, child_desc))
            else:
                propagated.append((target, label, link_desc))
        links = propagated
        # render the corrected index
        content = self._render_index(name, frontmatter, links, user_content)
        return content, notices

    def _plan_page(
        self: Wiki,
        path: pathlib.Path,
        now: str,
    ) -> tuple[str, list[str]]:
        """Compute the corrected content for a page file.

        Pure with respect to the filesystem (see :meth:`_plan_index`).
        The page's ``name`` and H1 heading are set to the path-joined
        name (e.g. ``core/design``); an authored title is intentionally
        overwritten so names stay consistent with the tree structure.
        Missing or blank frontmatter fields (``desc``/``created``/
        ``updated``) are filled in. A page whose frontmatter
        never closes is left untouched and reported instead: prepending
        a fresh block would demote the authored fields to body text. The
        returned content carries the file's *original* ``updated:``
        value (a page has no cross-file reads, so it is computed once
        from its own content).

        Args:
            path: Page file to compute.
            now: Timestamp for seeding missing fields / fresh frontmatter.

        Returns:
            Tuple of ``(content, notices)`` where ``notices`` name a
            malformed frontmatter (emitted by the writer, not here).

        """
        # read page
        text = self._read_text(path)
        frontmatter, content = self._parse_page(text)
        # unclosed frontmatter parses as none at all: keep the file as-is and
        # report it rather than demote the authored fields to body text
        first_line = text.split('\n', 1)[0].lstrip('\ufeff')
        if not frontmatter and first_line.strip() == '---':
            relpath = path.relative_to(self._root)
            return text, [f'Malformed frontmatter (no closing ---) in {relpath}']
        # update or create frontmatter
        if frontmatter:
            # update name from file path (add it if the field is missing, so
            # a page with frontmatter but no name: does not stay un-named)
            page_name = self._path_to_name(path)
            if re.search(r'^name:', frontmatter, re.MULTILINE):
                # callable repl so a backslash-digit in the
                # name is not read as a group reference
                frontmatter = re.sub(
                    r'^name:.*$',
                    lambda _: f'name: {_quote(page_name)}',
                    frontmatter,
                    count=1,
                    flags=re.MULTILINE,
                )
            else:
                pos = frontmatter.rfind('---')
                frontmatter = frontmatter[:pos] + f'name: {_quote(page_name)}\n---'
            # update H1 heading to match name (rewrite the exact heading line,
            # not a '# ...' that may appear inside a fenced code block)
            heading = self._find_heading(content)
            if heading:
                heading_index, _ = heading
                content_lines = content.split('\n')
                content_lines[heading_index] = f'# {page_name}'
                content = '\n'.join(content_lines)
            # add desc field if missing (preserve existing); restore the
            # placeholder on a present-but-blank key
            if re.search(r'^desc:[^\S\n]*$', frontmatter, re.MULTILINE):
                frontmatter = re.sub(
                    r'^desc:[^\S\n]*$',
                    'desc: ...',
                    frontmatter,
                    count=1,
                    flags=re.MULTILINE,
                )
            elif not re.search(r'^desc:', frontmatter, re.MULTILINE):
                pos = frontmatter.rfind('---')
                frontmatter = frontmatter[:pos] + 'desc: ...\n---'
            # add created/updated if missing; stamp a present-but-blank key in
            # place so a duplicate is never appended
            if re.search(r'^created:[^\S\n]*$', frontmatter, re.MULTILINE):
                frontmatter = re.sub(
                    r'^created:[^\S\n]*$',
                    f'created: {now}',
                    frontmatter,
                    count=1,
                    flags=re.MULTILINE,
                )
            elif not re.search(r'^created:', frontmatter, re.MULTILINE):
                match = re.search(r'^updated:', frontmatter, re.MULTILINE)
                pos = match.start() if match else frontmatter.rfind('---')
                frontmatter = (
                    frontmatter[:pos] + f'created: {now}\n' + frontmatter[pos:]
                )
            if re.search(r'^updated:[^\S\n]*$', frontmatter, re.MULTILINE):
                frontmatter = re.sub(
                    r'^updated:[^\S\n]*$',
                    f'updated: {now}',
                    frontmatter,
                    count=1,
                    flags=re.MULTILINE,
                )
            elif not re.search(r'^updated:', frontmatter, re.MULTILINE):
                pos = frontmatter.rfind('---')
                frontmatter = (
                    frontmatter[:pos] + f'updated: {now}\n' + frontmatter[pos:]
                )
        else:
            # use the path-joined name (not the bare stem) and rewrite the H1 to
            # match, so a fresh page converges in one pass instead of two
            page_name = self._path_to_name(path)
            frontmatter = self._build_frontmatter(
                name=page_name,
                created=now,
                updated=now,
            )
            content = '\n' + text
            heading = self._find_heading(content)
            if heading:
                heading_index, _ = heading
                content_lines = content.split('\n')
                content_lines[heading_index] = f'# {page_name}'
                content = '\n'.join(content_lines)
        # render the corrected page
        result = self._render_page(frontmatter, content)
        return result, []

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
        _lines: Optional[list[str]] = None,
    ) -> list[str]:
        """Recursively build map lines for a wiki folder.

        Reads the folder's ``_index.md``, iterates non-parent
        links, and appends formatted lines. Recurses into
        child folders respecting ``depth``.
        """
        # default lines to empty (use the passed buffer even when it is empty)
        result = _lines if _lines is not None else []
        # map presentation knobs (settings.json map.*)
        map_config = self._settings.get('map', {})
        indent_unit = map_config.get('indent', '  ')
        ellipsis = map_config.get('ellipsis', '...')
        # read and parse the folder's index
        index_path = folder / WIKI_INDEX
        if index_path.is_file():
            text = index_path.read_text(encoding='utf-8')
            _, links, _ = self._parse_index(text)
        elif current_depth == 0:
            # top-level target has no index: mark it unindexed
            name = self._path_to_name(folder)
            result.append(f'{indent}{name}/ (unindexed)')
            return result
        else:
            # unindexed child reached during recursion: nothing to add
            return result
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
            # markdown-ness probes the actual <name>.md file, since a
            # '.'-in-target test misfires on a dotted stem like my.notes
            child_folder = (self._root / target).parent
            if is_folder:
                child_path = child_folder / WIKI_INDEX
                is_markdown = False
            elif (self._root / (target + '.md')).is_file():
                child_path = self._root / (target + '.md')
                is_markdown = True
            else:
                child_path = self._root / target
                is_markdown = False
            # a link resolving outside this folder (or to a missing file) is a
            # preserved broken link: annotate it, never recurse into another subtree
            if is_folder:
                broken = child_folder.parent != folder or not child_folder.is_dir()
            else:
                broken = child_path.parent != folder or not child_path.exists()
            # apply markdown filter (pages only)
            if not is_folder and markdown is not None and markdown != is_markdown:
                continue
            # detect unindexed folder (folder present, no _index.md)
            unindexed = is_folder and not broken and not child_path.is_file()
            # recurse into a child folder first so a category filter can prune
            # folders whose subtree contributes nothing
            child_lines = []
            if is_folder and not unindexed and not broken:
                if depth is None or current_depth < depth:
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
                        _lines=child_lines,
                    )
            # skip a non-matching folder with no matching descendants; a
            # depth cutoff leaves child_lines empty for depth reasons, not
            # content, so probe the subtree before pruning -- a match beyond
            # the cutoff still shows this folder (children stay hidden)
            if is_folder and not matches_category and not child_lines:
                probe = []
                if not unindexed and not broken:
                    if depth is not None and current_depth >= depth:
                        probe = self._map_folder(
                            folder=child_folder,
                            desc=False,
                            category=category,
                            markdown=markdown,
                            words=False,
                        )
                if not probe:
                    continue
            # read word counts from the cache; always render a count (0 for a
            # non-markdown file); unindexed/broken entries show their marker
            word_label = None
            if words and not unindexed and not broken:
                count = counts.get(str(child_path.relative_to(self._root)), 0)
                if is_folder:
                    tree_key = str(child_folder.relative_to(self._root))
                    tree = folder_words.get(tree_key, 0)
                    word_label = f'{_format_words(count)}/{_format_words(tree)}'
                else:
                    word_label = _format_words(count)
            # format description (folded to one line)
            desc_text = ''
            if desc and description:
                desc_text = description.replace('\n', ' ').strip()
                if desc_text == '...':
                    desc_text = ''
                if desc_text and desc_limit is not None:
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
    ) -> Optional[str]:
        """Return ``path``'s drift as a header line plus an indented diff.

        The corrected content comes from the plan ``overlay``; the
        comparison is byte-exact, so the result is non-empty exactly
        when ``update`` would rewrite the file. The first line is
        ``{path}: Requires update``; the diff body is indented so it
        reads distinctly from the one-line "needs attention" messages.
        Returns ``None``
        when the file is unchanged, has no plan entry, or does not exist
        on disk (a missing index is reported separately).

        Args:
            path: File to diff.
            overlay: Corrected ``{path: content}`` from :meth:`_plan`.

        Returns:
            The path header plus indented diff, or ``None`` if there is
            nothing to report.

        """
        corrected = overlay.get(path)
        if corrected is None or not path.exists():
            return None
        current = path.read_text(encoding='utf-8')
        if current == corrected:
            return None
        relpath = str(path.relative_to(self._root))
        # drop difflib's '---'/'+++' header lines; the path header replaces them
        diff = difflib.unified_diff(
            current.splitlines(),
            corrected.splitlines(),
            lineterm='',
        )
        body = list(diff)[2:]
        # a byte difference with no line-level delta is a line-ending change;
        # still report it so the flag never desyncs from update's byte compare
        if not body:
            return f'{relpath}: Requires update (line endings differ)'
        # drop trailing blank context lines so the diff ends on a real change
        while body and not body[-1].strip():
            body.pop()
        # indent the body; render blank context lines as truly empty (no trailing ws)
        indented = '\n'.join('    ' + line if line.strip() else '' for line in body)
        return f'{relpath}: Requires update\n{indented}'

    def _lint_regions(
        self: Wiki,
        path: pathlib.Path,
        text: str,
    ) -> tuple[set[int], list[str]]:
        """Resolve ``text``'s ``no-lint`` suppression set and region issues.

        Returns the 1-based line numbers inside well-formed ``no-lint``
        regions -- the lines the positional rules skip -- plus a
        formatted hard issue per nesting/dangling violation. A malformed
        region suppresses nothing.
        """
        regions, errors = _parse_regions(text)
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
        """Check desc is present and ends in a period."""
        # alias relative path
        result = []
        relpath = path.relative_to(self._root)
        desc = self._read_frontmatter_desc(frontmatter)
        # a placeholder desc is a soft, "not yet authored" state (init seeds it),
        # so note it without failing lint; a real desc must end in a period
        if desc == '...':
            self._warn(f'{relpath}: Needs desc')
        elif desc and not desc.strip().endswith('.'):
            result.append(f'{relpath}: Missing period in desc')
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
        # alias relative path
        result = []
        relpath = path.relative_to(self._root)
        # check link description ends in period
        joined = _join_lines(link_desc)
        if joined and joined != '...' and not joined.endswith('.'):
            result.append(f'{relpath}: Missing period in [[{target}|{label}]]')
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
            return str(resolved.relative_to(self._root))
        return None

    def _lint_stale_links(
        self: Wiki,
        path: pathlib.Path,
        content: str,
    ) -> list[str]:
        """Check wikilinks in content resolve to existing files.

        Lines inside a well-formed ``no-lint`` region are exempt; the
        region is parsed from the scanned content itself, so the region
        must wrap the link lines.
        """
        # alias relative path
        result = []
        relpath = path.relative_to(self._root)
        # strip fenced code blocks and inline code spans before scanning
        stripped = _mask_code(content)
        # no-lint regions suppress the stale-link check line by line
        suppressed = _no_lint_lines(content)
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
            if not (self._root / (page_target + '.md')).exists():
                if not (self._root / page_target).exists():
                    # a folder-relative link (e.g. [[../overview]]) is stale because
                    # wiki targets are root-relative; when it resolves to a real page
                    # from this file's folder, name the canonical form as the fix
                    canonical = self._canonical_link_target(path, page_target)
                    if canonical is not None and canonical != page_target:
                        result.append(
                            f'{relpath}: Stale link [[{target}]] '
                            f'(use [[{canonical}{anchor}]])'
                        )
                    else:
                        result.append(f'{relpath}: Stale link [[{target}]]')
        return result


# ------ helper functions


def _join_lines(text: str) -> str:
    """Join multi-line text into a single line."""
    return ' '.join(line.strip() for line in text.strip().split('\n'))


def _fold_lines(text: str) -> str:
    """Fold a YAML folded-scalar body (``>``) into paragraphs.

    Consecutive non-empty lines join with a single space; a blank line is
    a paragraph break (preserved as a newline). Mirrors the YAML
    folded-scalar rule.
    """
    # group consecutive non-empty lines into paragraphs
    paragraphs = []
    current = []
    for line in text.split('\n'):
        if line.strip():
            current.append(line.strip())
        elif current:
            paragraphs.append(' '.join(current))
            current = []
    if current:
        paragraphs.append(' '.join(current))
    return '\n'.join(paragraphs)


def _quote(value: str) -> str:
    """YAML-quote a scalar when writing it plain would break the mapping.

    A value containing ``': '`` (or ending with ``:``) reads as a nested
    mapping in YAML, so it is written single-quoted with embedded single
    quotes doubled; any other value passes through unquoted. Inverse of
    :func:`_unquote` for the values the wiki writes.
    """
    if ': ' in value or value.endswith(':'):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return value


def _unquote(value: str) -> str:
    """Strip one pair of matching surrounding YAML quotes from a scalar.

    A quoted scalar (``"..."`` / ``'...'``) resolves to its body, with the
    YAML escapes undone -- doubled single quotes in a single-quoted value,
    backslash-escaped quotes/backslashes in a double-quoted one. An
    unquoted value is returned unchanged.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        body = value[1:-1]
        if value[0] == '"':
            return body.replace('\\"', '"').replace('\\\\', '\\')
        return body.replace("''", "'")
    return value


def _format_words(n: int) -> str:
    """Format a word count with ``k``/``m``/``b``/``t`` suffix."""
    tiers = {
        'k': 1_000,
        'm': 1_000_000,
        'b': 1_000_000_000,
        't': 1_000_000_000_000,
    }
    # walk high -> low so the largest applicable suffix wins
    ordered = list(reversed(tiers.items()))
    for i, (suffix, threshold) in enumerate(ordered):
        if n >= threshold:
            scaled = n / threshold
            # promote into the next tier when rounding would overflow this one
            if round(scaled, 1) >= 1_000:
                if i > 0:
                    upper_suffix, upper_threshold = ordered[i - 1]
                    return f'{n / upper_threshold:.1f}{upper_suffix}'
                # top tier has nowhere to promote: clamp below the roll-over
                return f'{(n * 10 // threshold) / 10:.1f}{suffix}'
            return f'{scaled:.1f}{suffix}'
    return str(n)


def _mask_code(text: str) -> str:
    """Blank fenced code blocks and inline code spans in text.

    Fenced blocks (backtick or tilde fences) blank whole lines via a fence
    state machine; inline spans are removed per CommonMark's backtick-run
    rule -- a span opens with a run of backticks and closes at the next
    run of the same length, and may wrap across a newline but never a
    blank line. The line structure is preserved (masked regions become
    empty lines), so positional checks can attribute their findings to
    source lines. Lint checks scan the masked text so code samples never
    trip them.
    """
    # blank fenced code blocks (line count preserved)
    lines = []
    fence = None
    for line in text.split('\n'):
        if fence is not None:
            lines.append('')
            if line.strip() == fence:
                fence = None
            continue
        match = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if match:
            fence = match.group(1)
            lines.append('')
            continue
        lines.append(line)
    # blank inline code spans (equal-length backtick runs, newline-tolerant;
    # a span's interior newlines survive so line numbers stay aligned)
    return re.sub(
        r'(?<!`)(`+)(?!`)(?:[^`\n]|\n(?![ \t]*\n))+?\1(?!`)',
        lambda match: '\n' * match.group(0).count('\n'),
        '\n'.join(lines),
    )


def _parse_regions(text: str) -> tuple[dict[str, list[tuple[int, int]]], list[str]]:
    """Parse region-directive comments into per-directive line ranges.

    One grammar covers all comment-bracketed regions:
    ``<!-- start: <directive> [args] -->`` ... ``<!-- end: <directive> -->``,
    each marker alone on its line, with bare kebab-word directives and
    args. Every directive pairs as an independent bracket stream, so
    regions of different directives interleave freely while
    same-directive nesting and dangling markers are structural errors.
    Code is masked first, so a fenced marker is a sample, not a
    directive. ``no-lint`` is the sole directive with shipped semantics;
    unknown well-formed pairs are inert.

    Returns:
        Tuple of ``(regions, errors)`` where ``regions`` maps each
        directive to its well-formed ``(start, end)`` line ranges
        (1-based, inclusive; a pair poisoned by a nested start is
        malformed and never recorded) and ``errors`` describe
        nesting/dangling violations, each naming its marker and line.

    """
    # collect marker events per directive from the masked text
    regions: dict[str, list[tuple[int, int]]] = {}
    errors = []
    open_starts: dict[str, Optional[int]] = {}
    poisoned: set[str] = set()
    for lineno, line in enumerate(_mask_code(text).split('\n'), 1):
        match = _REGION_DIRECTIVE.fullmatch(line.strip())
        if not match:
            continue
        kind, directive = match.group(1), match.group(2)
        # a nested start poisons the open region (a malformed pair must
        # suppress nothing); an end without an open start dangles
        if kind == 'start':
            if open_starts.get(directive) is not None:
                errors.append(f"Nested '<!-- start: {directive} -->' (line {lineno})")
                poisoned.add(directive)
            else:
                open_starts[directive] = lineno
        elif open_starts.get(directive) is None:
            errors.append(f"Dangling '<!-- end: {directive} -->' (line {lineno})")
        else:
            if directive in poisoned:
                poisoned.discard(directive)
            else:
                regions.setdefault(directive, []).append(
                    (open_starts[directive], lineno)
                )
            open_starts[directive] = None
    # a start still open at EOF dangles
    for directive, start in open_starts.items():
        if start is not None:
            errors.append(f"Dangling '<!-- start: {directive} -->' (line {start})")
    return regions, errors


def _no_lint_lines(text: str) -> set[int]:
    """Return 1-based line numbers inside well-formed ``no-lint`` regions."""
    regions, _ = _parse_regions(text)
    result = set()
    for start, end in regions.get('no-lint', []):
        result.update(range(start, end + 1))
    return result


def _escaped_wikilink_lines(text: str) -> list[int]:
    r"""Return 1-based line numbers carrying formatter-escaped wikilinks.

    Markdown formatters backslash-escape ``[[...]]`` link brackets
    (``\[\[`` or ``\[[``); the sequence never appears in healthy
    generated content, so it is the signature lint uses to name likely
    formatter damage. Code is masked first, so a sample documenting the
    escape never trips it.
    """
    result = []
    for lineno, line in enumerate(_mask_code(text).split('\n'), 1):
        if re.search(r'\\\[\\?\[', line):
            result.append(lineno)
    return result


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


def _write_atomic(path: pathlib.Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically (temp file + rename).

    A plain ``write_text`` truncates before writing, exposing an empty or
    partial file to concurrent readers and leaving a torn file if the
    process dies mid-write. Staging to a temp file in the same directory
    and ``os.replace``-ing it into place makes every read all-or-nothing.
    The dot-prefixed temp name keeps a leftover from a crash out of the
    wiki walk.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.')
    tmp = pathlib.Path(tmp)
    try:
        # newline='\n' writes LF verbatim on every platform, so a rewrite
        # normalizes CRLF and never reintroduces it
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(content)
        # mkstemp creates the temp 0600 and os.replace carries that mode
        # onto the target: preserve the existing mode, or honor the umask
        # for a fresh file
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        else:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, path)
    except OSError:
        # discard the partial temp file, leaving the target untouched
        tmp.unlink(missing_ok=True)
        raise


def _is_offline() -> bool:
    """Return ``True`` if ``OFFLINE_MODE`` is set to ``true``.

    Raises:
        ValueError: If ``OFFLINE_MODE`` is set to anything other than
            ``true`` or ``false`` (case-insensitive).

    """
    value = os.environ.get(_OFFLINE_MODE, '').strip().lower()
    if value == 'true':
        return True
    if value not in ('false', ''):
        raise ValueError(f'{_OFFLINE_MODE} must be "true" or "false", got: {value!r}')
    return False
