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
WIKI_CONFIG = '_config'
WIKI_INDEX = '_index.md'

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
        """Per-wiki settings overlay from ``_config/settings.json``.

        Returns an empty dict when the file is absent. Raises ``ValueError``
        on malformed JSON or a non-object top level, since the file is
        user-editable input that should fail loudly rather than be ignored.
        """
        path = self._root / WIKI_CONFIG / 'settings.json'
        if not path.exists():
            return {}
        try:
            result = json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as e:
            raise ValueError(
                f'Malformed JSON in {WIKI_CONFIG}/settings.json: {e}'
            ) from e
        if not isinstance(result, dict):
            raise ValueError(f'{WIKI_CONFIG}/settings.json must be a JSON object')
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
            raise ValueError('naming must be a JSON object')
        policy = {**_NAMING_DEFAULTS, **override}
        # validate predicate names (settings.json is user input -> fail loudly)
        if not isinstance(policy['validate'], list):
            raise ValueError('naming.validate must be a list of predicate names')
        for predicate in policy['validate']:
            if predicate not in _NAMING_PREDICATES:
                raise ValueError(f'Unknown naming predicate: {predicate!r}')
        # min_length defaults to 1; an explicit value must be a positive int
        min_length = policy['min_length']
        if min_length is None:
            min_length = 1
        elif not (isinstance(min_length, int) and min_length >= 1):
            raise ValueError(
                f'naming.min_length must be an int >= 1 or null, got {min_length!r}'
            )
        # max_length is null (no cap) or a positive int
        max_length = policy['max_length']
        if max_length is not None:
            if not (isinstance(max_length, int) and max_length >= 1):
                raise ValueError(
                    f'naming.max_length must be an int >= 1 or null, got {max_length!r}'
                )
        # deny/allow are strings of characters; reserved is a list of names
        for leaf in ('deny', 'allow'):
            if not isinstance(policy[leaf], str):
                raise ValueError(f'naming.{leaf} must be a string of characters')
        if not isinstance(policy['reserved'], list):
            raise ValueError('naming.reserved must be a list of strings')
        if not isinstance(policy['leading_digits'], bool):
            raise ValueError('naming.leading_digits must be a boolean')
        # always deny the path separator, index delimiter, and link/markdown grammar
        deny = set(policy['deny'])
        deny.add(self.path_sep)  # would split a name into folders
        deny.update(self.index_delimiter)  # the *** generated/user-content delimiter
        deny.add('\\')  # escape character + Windows path separator
        deny.update('[]|')  # wikilink [[target|label]] + category [cat] name
        deny.add('#')  # markdown heading marker / link anchor
        # always reserve the per-folder index stem; the config directory's stem is
        # reserved only at the root (in validate_name) -- the one place it exists
        reserved = set(policy['reserved'])
        reserved.add(pathlib.Path(WIKI_INDEX).stem)  # the per-folder index stem
        # compile the optional full-match pattern
        pattern = policy['pattern']
        if pattern is not None:
            if not isinstance(pattern, str):
                raise ValueError(
                    f'naming.pattern must be a string or null, got {pattern!r}'
                )
            try:
                pattern = re.compile(pattern)
            except re.error as e:
                raise ValueError(f'naming.pattern is not a valid regex: {e}') from e
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
            raise ValueError('timestamp must be a JSON object')
        # format is a strftime string; timezone is an IANA name or null (UTC)
        format = override.get('format', '%Y-%m-%dT%H:%M:%SZ')
        if not isinstance(format, str):
            raise ValueError(f'timestamp.format must be a string, got {format!r}')
        # a blank or multi-line value would corrupt the YAML frontmatter -- reject
        # an empty/whitespace format, strftime's %n/%t newline/tab directives, and
        # literal line breaks, the only ways the rendered value splits or empties
        breakers = ('%n', '%t', '\n', '\r')
        if not format.strip() or any(token in format for token in breakers):
            raise ValueError(
                f'timestamp.format must render a single non-empty line; got {format!r}'
            )
        timezone = override.get('timezone')
        if timezone is not None and not isinstance(timezone, str):
            raise ValueError(
                f'timestamp.timezone must be a string or null, got {timezone!r}'
            )
        if timezone:
            try:
                timezone = zoneinfo.ZoneInfo(timezone)
            except (zoneinfo.ZoneInfoNotFoundError, ValueError) as e:
                raise ValueError(
                    f'Unknown timestamp.timezone {timezone!r} in'
                    f' {WIKI_CONFIG}/settings.json'
                ) from e
        else:
            timezone = dt.UTC
        return {'format': format, 'zone': timezone}

    def validate_name(self: Wiki, name: str, *, root: bool = False) -> bool:
        """Return ``True`` if ``name`` satisfies the wiki's naming policy.

        The policy is the field defaults overlaid by the per-wiki ``naming`` block
        in ``_config/settings.json``; see :attr:`_naming`. The path separator,
        index delimiter, link/markdown grammar characters, and the reserved
        ``_index`` name are always rejected; ``_config`` -- the config directory's
        stem -- collides only at the root, so it is rejected there alone.

        Override in subclasses for naming rules a data policy cannot express.

        Args:
            name: Name to validate (page stem or folder name).
            root: Whether ``name`` sits at the wiki root, where ``_config``
                additionally collides with the ``_config/`` config directory.

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
        # reject reserved structural names and any denied character; the config
        # directory's stem collides only at the root, so reject it there alone
        if name in policy['reserved']:
            return False
        if root and name == WIKI_CONFIG:
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
            settings: Initial ``_config/settings.json`` contents.
                When omitted, the default naming policy is seeded
                so the configurable knobs are discoverable.

        """
        # validate OFFLINE_MODE before any filesystem mutation; a bad value must
        # fail fast rather than strand a half-built wiki the re-init guard skips
        _is_offline()
        # initialize wiki root
        self._root.mkdir(parents=True, exist_ok=True)
        # seed _config/settings.json FIRST -- the caller's settings, or the
        # default naming policy so the configurable knobs are discoverable. It
        # must precede every _settings access below (validate_name, _utc_now)
        # so the cached property reads this wiki's real policy rather than
        # caching {} from the not-yet-written file
        settings_path = self._root / WIKI_CONFIG / 'settings.json'
        if not settings_path.exists():
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            seed = settings if settings is not None else {'naming': _NAMING_DEFAULTS}
            content = json.dumps(seed, indent=2)
            settings_path.write_text(content + '\n', encoding='utf-8')
        # resolve wiki name against the now-seeded policy
        name = name or self._root.name
        if not self.validate_name(name, root=True):
            raise ValueError(f'Invalid wiki name: {name!r}')
        # alias current timestamp
        now = self._utc_now()
        # seed _config/obsidian from stock template
        config_dir = self._root / WIKI_CONFIG / 'obsidian'
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
                index=True,
            )
            root_index.write_text(
                self._render_index(name, frontmatter, [], ''),
                encoding='utf-8',
            )
        # update all indexes (reuse this run's timestamp)
        overlay, _ = self._plan(self._root, now=now)
        self._apply_plan(overlay, now)

    def update_config(self: Wiki) -> list[str]:
        """Install ``_config/obsidian/`` into ``.obsidian/``.

        Copies each bundled plugin's settings under
        ``_config/obsidian/plugins/`` into ``.obsidian/plugins/`` and
        downloads pinned plugin code from its upstream release. Each
        top-level ``.json`` file (like ``community-plugins.json``) is
        created from source when absent, else merged: arrays are
        union-merged and dicts deep-merged with source winning. Other
        installed plugins are left untouched.

        Returns:
            List of warning messages (e.g. when a plugin download
            fails because there is no network connection).

        """
        # validate OFFLINE_MODE before any filesystem mutation
        offline = _is_offline()
        # prepare _config/obsidian/ and .obsidian/ directories
        config_dir = self._root / WIKI_CONFIG / 'obsidian'
        if config_dir.exists():
            obsidian_dir = self._root / '.obsidian'
            obsidian_dir.mkdir(exist_ok=True)
        else:
            relpath = config_dir.relative_to(self._root)
            raise FileNotFoundError(f'Directory not found: {relpath}')
        # install plugin settings, then download pinned plugin code
        warnings = []
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
            target.write_text(result + '\n', encoding='utf-8')
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
        frontmatter to pages that lack it.

        When broken links are found (targets in the existing index
        that no longer exist on the filesystem), they are preserved
        and a warning is logged. Set ``prune=True`` to remove them
        instead.

        Logs a warning for each new link added with a placeholder
        ``...`` description.

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
        if name:
            folder = self._resolve_folder(name)
        else:
            folder = self._root
        # compute corrected content for the scope (single timestamp)
        now = self._utc_now()
        overlay, notices = self._plan(folder, prune=prune, now=now)
        # report broken/new links (preserved or added during the run)
        for notice in notices:
            self._warn(notice)
        # dry run: report which files would change without writing
        if check:
            return [
                str(path.relative_to(self._root))
                for path, content in overlay.items()
                if not path.exists() or content != path.read_text(encoding='utf-8')
            ]
        return self._apply_plan(overlay, now)

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
          invalid names, merge conflict markers, missing trailing
          periods, stale links in user content, and broken links
          (targets that no longer exist; ``update`` keeps these
          without ``--prune``).

        Every line begins with the relevant path; an out-of-date file's
        diff follows its ``Requires update`` header, indented.

        Placeholder descriptions and empty content sections are soft
        notes (stderr) and do not count as issues.

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
        # compute what update would write (the source of truth for drift)
        now = self._utc_now()
        overlay, _ = self._plan(folder, now=now)
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
                text = index_path.read_text(encoding='utf-8')
                # conflict markers take precedence over the generated diff
                if _has_conflict_markers(text):
                    result.append(f'{index_relpath}: Merge conflict markers')
                else:
                    # out of date: show what update would change
                    diff = self._diff(index_path, overlay)
                    if diff:
                        result.append(diff)
                    # a missing *** delimiter collapses the link block into user
                    # content (the diff already flags the rewrite; name the marker
                    # specifically so the cause is obvious)
                    if self._index_missing_marker(folder):
                        result.append(f'{index_relpath}: Index missing *** delimiter')
                    # human-only checks on current content
                    frontmatter, links, user_content = self._parse_index(text)
                    result.extend(self._lint_desc(index_path, frontmatter))
                    # the root display name has no enclosing dir to validate it
                    if folder == self._root:
                        root_name = self._read_frontmatter_name(frontmatter)
                        if root_name and not self.validate_name(root_name, root=True):
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
                if not self.validate_name(page.stem, root=(folder == self._root)):
                    result.append(f'{page_relpath}: Invalid page name')
                # only markdown pages carry frontmatter/content to lint further
                if page.suffix != '.md':
                    continue
                text = page.read_text(encoding='utf-8')
                # conflict markers take precedence over the generated diff
                if _has_conflict_markers(text):
                    result.append(f'{page_relpath}: Merge conflict markers')
                    continue
                # out of date: show what update would change
                diff = self._diff(page, overlay)
                if diff:
                    result.append(diff)
                # human-only checks on current content
                frontmatter, content = self._parse_page(text)
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
        content = path.read_text(encoding='utf-8')
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
                ``None``, searches body content only.
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
                    if lineno in field_lines and regex.search(line):
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
            words: Show word counts from frontmatter.

        Returns:
            Indented tree string.

        """
        if isinstance(category, str):
            category = [category]
        if name:
            folder = self._resolve_folder(name)
        else:
            folder = self._root
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
        )
        # an empty map with an index that parses to 0 links yet has on-disk
        # children means the index lost its *** marker (its links became user
        # content); warn rather than letting the CLI read it as an empty wiki
        if not result:
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

    def _index_missing_marker(self: Wiki, folder: pathlib.Path) -> bool:
        """Return ``True`` if ``folder``'s index lost its ``***`` delimiter.

        The delimiter separates generated links from user content; without it
        :meth:`_parse_index` folds the link block into user content and yields
        zero links. Reports the gap only when the index exists and the folder
        has on-disk children that should be linked, so a genuinely empty wiki
        is not mistaken for a broken one.
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
        text = index_path.read_text(encoding='utf-8')
        lines = [line.rstrip() for line in text.split('\n')]
        return self.index_delimiter not in lines

    def _warn_markerless_index(self: Wiki, folder: pathlib.Path) -> None:
        """Warn when ``folder``'s index is missing its ``***`` delimiter.

        Used by :meth:`map` so an index whose links collapsed into user content
        is reported as broken rather than read as an empty wiki.
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

        Excludes directories with ``.`` prefix, the ``_config``
        infrastructure directory, and symlinked directories
        (following a symlink re-walks the same inode, producing
        duplicate/conflicting index writes and risking loops).
        """
        return (
            path.name.startswith('.') or path.name == WIKI_CONFIG or path.is_symlink()
        )

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

    def _current_text(
        self: Wiki,
        path: pathlib.Path,
        overlay: Optional[dict[pathlib.Path, str]] = None,
    ) -> Optional[str]:
        """Read a file's content, preferring staged plan content.

        During a plan (:meth:`_plan`), earlier passes stage corrected
        content in ``overlay`` without writing to disk. Routing every
        cross-file content read through this resolver makes a cascade
        (a child's refreshed ``page_words`` flowing into a parent's
        ``tree_words``, or a child's category into a parent link label)
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
            return path.read_text(encoding='utf-8')
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
        # require an opening '---'
        if lines and lines[0].strip() == '---':
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
        """Parse an ``_index.md`` file into components.

        Returns ``(frontmatter, links, user_content)``:

        - ``frontmatter``: raw frontmatter text including ``---`` delimiters
        - ``links``: list of ``(target, label, description)`` tuples
        - ``user_content``: everything after the first delimiter, with any
          prose found above the first link folded in so it is never dropped

        Supports multi-line descriptions: continuation lines (not a link,
        not a delimiter, not blank) are appended to the previous link's
        description.

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
        # no delimiter: do not risk parsing prose as links and dropping it
        if marker is None:
            body = lines[line_number:]
            while body and not body[0].strip():
                body.pop(0)
            if body and re.match(r'^#\s', body[0]):
                body.pop(0)
            while body and not body[0].strip():
                body.pop(0)
            return frontmatter, [], '\n'.join(body)
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
            # try to match a new link
            match = re.match(r'^\[\[(.+?)\|(.+?)\]\](?::\s*(.*))?$', line.strip())
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
        index: bool = False,
    ) -> str:
        """Build YAML frontmatter string.

        Args:
            name: Display name for the index.
            created: ISO 8601 timestamp.
            updated: ISO 8601 timestamp.
            index: Whether this is an index file (includes ``tree_words``).

        Returns:
            Complete frontmatter block including ``---`` delimiters.

        """
        lines = [
            '---',
            f'name: {name}',
            'desc: ...',
            'category: null',
            'tags: []',
            'sources: []',
            f'created: {created}',
            f'updated: {updated}',
            'page_words:',
        ]
        if index:
            lines.append('tree_words:')
        lines.append('---')
        return '\n'.join(lines)

    def _read_frontmatter_field(
        self: Wiki,
        frontmatter: str,
        key: str,
    ) -> Optional[str]:
        """Read a scalar frontmatter ``key``, resolving block scalars.

        A plain ``key: value`` returns the stripped value. A block scalar
        (``|``/``>`` with optional chomping/indentation indicators, e.g.
        ``|-``, ``>+``, ``|2``) resolves to its body: a literal ``|`` keeps
        line breaks, a folded ``>`` joins consecutive non-empty lines with a
        single space (a blank line is a paragraph break). Inline text on the
        indicator line (``key: > one liner.``) is taken as the value when no
        indented body follows. Returns ``None`` if the field is absent; an
        empty block body resolves to an empty string.

        Shared by the typed readers below (name, desc, category, word counts).
        """
        # single-line value
        match = re.search(rf'^{key}:[^\S\n]*(.+)$', frontmatter, re.MULTILINE)
        if match:
            value = match.group(1).strip()
            if not value.startswith(('|', '>')):
                return value
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

    def _read_frontmatter_page_words(
        self: Wiki,
        path: pathlib.Path,
        overlay: Optional[dict[pathlib.Path, str]] = None,
    ) -> Optional[int]:
        """Read the ``page_words`` field from a file's frontmatter.

        Reads staged plan content when ``overlay`` is given (see
        :meth:`_current_text`), else disk. Returns ``None`` if the file is
        absent or has no integer ``page_words`` field.
        """
        text = self._current_text(path, overlay)
        if text is None:
            return None
        value = self._read_frontmatter_field(text, 'page_words')
        return int(value) if value and value.isdigit() else None

    def _read_frontmatter_tree_words(
        self: Wiki,
        path: pathlib.Path,
        overlay: Optional[dict[pathlib.Path, str]] = None,
    ) -> Optional[int]:
        """Read the ``tree_words`` field from a file's frontmatter.

        Reads staged plan content when ``overlay`` is given (see
        :meth:`_current_text`), else disk. Returns ``None`` if the file is
        absent or has no integer ``tree_words`` field.
        """
        text = self._current_text(path, overlay)
        if text is None:
            return None
        value = self._read_frontmatter_field(text, 'tree_words')
        return int(value) if value and value.isdigit() else None

    def _set_page_words(self: Wiki, text: str) -> str:
        """Compute and write ``page_words`` into rendered text.

        Counts the body -- everything below the frontmatter, which is the only
        special region -- so the count matches the searchable/sliceable region
        exactly. The H1 heading and an index's auto-generated link block are body
        content, so they are counted (they are part of what ``read`` returns).
        Single-pass (the ``page_words`` field lives in the frontmatter, so it
        never enters the counted body).

        Args:
            text: Rendered page or index text.

        Returns:
            Text with ``page_words`` set to the body word count.

        """
        _, body = self._parse_page(text)
        words = len(body.split())
        return re.sub(
            r'^page_words:.*$',
            f'page_words: {words}',
            text,
            count=1,
            flags=re.MULTILINE,
        )

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
            if not self.validate_name(page.stem, root=(folder == self._root)):
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
        """
        match = re.match(r'^\[(\w+)\] (.+)$', label)
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
            frontmatter, _ = self._parse_page(text)
            category = self._read_frontmatter_category(frontmatter)
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
    ) -> tuple[dict[pathlib.Path, str], list[str]]:
        """Compute corrected content for every file under ``folder``.

        Runs the three update passes (indexes bottom-up, pages, then
        ``tree_words``) into an in-memory overlay without writing to
        disk. The overlay maps each file path to its corrected content
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
            Tuple of ``(overlay, notices)`` where ``notices`` are the
            broken/new-link warnings collected across all indexes.

        """
        # alias directories
        folders = self._find_dirs(folder)
        overlay: dict[pathlib.Path, str] = {}
        notices = []
        # plan indexes (bottom-up so child categories
        # exist before parents read them)
        for folder in reversed(folders):
            content, index_notices = self._plan_index(
                folder=folder,
                now=now,
                prune=prune,
                overlay=overlay,
            )
            overlay[folder / WIKI_INDEX] = content
            notices.extend(index_notices)
        # plan pages
        for folder in folders:
            for page in self._find_pages(folder):
                if page.suffix == '.md':
                    overlay[page] = self._plan_page(page, now)
        # compute tree_words bottom-up
        self._plan_tree_words(folders, overlay)
        # return overlay and notices
        return overlay, notices

    def _apply_plan(
        self: Wiki,
        overlay: dict[pathlib.Path, str],
        now: str,
    ) -> list[str]:
        """Write corrected content to disk where it differs.

        Thin writer over :meth:`_plan`'s overlay: for each file whose
        corrected content differs from disk, re-stamps ``updated: now``
        and writes. Files already correct are skipped, so a
        timestamp-only difference never triggers a write (the overlay
        carries the original ``updated:``).

        Args:
            overlay: Corrected ``{path: content}`` from :meth:`_plan`.
            now: Timestamp for the ``updated:`` re-stamp (the same
                value threaded through the plan).

        Returns:
            List of relative paths of written files.

        """
        result = []
        for path, content in overlay.items():
            # skip files already correct (ignores updated:-only churn)
            if path.exists() and content == path.read_text(encoding='utf-8'):
                continue
            # stamp updated: now that a write is happening
            content = re.sub(
                r'^updated:.*$',
                f'updated: {now}',
                content,
                count=1,
                flags=re.MULTILINE,
            )
            path.write_text(content, encoding='utf-8')
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
            now: Timestamp for seeding *missing* ``created:``/
                ``updated:`` fields and fresh frontmatter (never to
                re-stamp an existing ``updated:``).
            prune: Remove broken links instead of preserving them.
            overlay: Staged ``{path: content}`` from earlier passes.

        Returns:
            Tuple of ``(content, notices)`` where ``notices`` are the
            broken/new-link warnings (emitted by the writer, not here).

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
                # update name from folder path (callable repl so a backslash-digit
                # in the name is not read as a group reference)
                frontmatter = re.sub(
                    r'^name:.*$',
                    lambda _: f'name: {name}',
                    frontmatter,
                    count=1,
                    flags=re.MULTILINE,
                )
                # add desc field if missing (after name line)
                if not re.search(r'^desc:', frontmatter, re.MULTILINE):
                    frontmatter = re.sub(
                        r'^(name:.*\n)',
                        r'\1desc: ...\n',
                        frontmatter,
                        count=1,
                        flags=re.MULTILINE,
                    )
                # add page_words field if missing (updated after render)
                if not re.search(r'^page_words:', frontmatter, re.MULTILINE):
                    pos = frontmatter.rfind('---')
                    frontmatter = frontmatter[:pos] + 'page_words:\n---'
                # add created/updated timestamps if missing
                if not re.search(r'^created:', frontmatter, re.MULTILINE):
                    pos = frontmatter.rfind('---')
                    frontmatter = frontmatter[:pos] + f'created: {now}\n---'
                if not re.search(r'^updated:', frontmatter, re.MULTILINE):
                    pos = frontmatter.rfind('---')
                    frontmatter = frontmatter[:pos] + f'updated: {now}\n---'
            else:
                frontmatter = self._build_frontmatter(
                    name=name,
                    created=now,
                    updated=now,
                    index=True,
                )
        else:
            frontmatter = self._build_frontmatter(
                name=name,
                created=now,
                updated=now,
                index=True,
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
                # child desc is source of truth
                propagated.append((target, label, child_desc.strip()))
            else:
                propagated.append((target, label, link_desc))
        links = propagated
        # render and compute word count
        content = self._render_index(name, frontmatter, links, user_content)
        content = self._set_page_words(content)
        return content, notices

    def _plan_page(
        self: Wiki,
        path: pathlib.Path,
        now: str,
    ) -> str:
        """Compute the corrected content for a page file.

        Pure with respect to the filesystem (see :meth:`_plan_index`).
        The page's ``name`` and H1 heading are set to the path-joined
        name (e.g. ``core/design``); an authored title is intentionally
        overwritten so names stay consistent with the tree structure.
        Missing frontmatter fields (``desc``/``page_words``/``created``/
        ``updated``) are filled in. The returned content carries the
        file's *original* ``updated:`` value (a page has no cross-file
        reads, so it is computed once from its own content).

        Args:
            path: Page file to compute.
            now: Timestamp for seeding missing fields / fresh frontmatter.

        Returns:
            Corrected page content.

        """
        # read page
        text = path.read_text(encoding='utf-8')
        frontmatter, content = self._parse_page(text)
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
                    lambda _: f'name: {page_name}',
                    frontmatter,
                    count=1,
                    flags=re.MULTILINE,
                )
            else:
                pos = frontmatter.rfind('---')
                frontmatter = frontmatter[:pos] + f'name: {page_name}\n---'
            # update H1 heading to match name (rewrite the exact heading line,
            # not a '# ...' that may appear inside a fenced code block)
            heading = self._find_heading(content)
            if heading:
                heading_index, _ = heading
                content_lines = content.split('\n')
                content_lines[heading_index] = f'# {page_name}'
                content = '\n'.join(content_lines)
            # add desc field if missing (preserve existing)
            if not re.search(r'^desc:', frontmatter, re.MULTILINE):
                pos = frontmatter.rfind('---')
                frontmatter = frontmatter[:pos] + 'desc: ...\n---'
            # add page_words field if missing (updated after render)
            if not re.search(r'^page_words:', frontmatter, re.MULTILINE):
                pos = frontmatter.rfind('---')
                frontmatter = frontmatter[:pos] + 'page_words:\n---'
            # add created/updated timestamps if missing
            if not re.search(r'^created:', frontmatter, re.MULTILINE):
                pos = frontmatter.rfind('---')
                frontmatter = frontmatter[:pos] + f'created: {now}\n---'
            if not re.search(r'^updated:', frontmatter, re.MULTILINE):
                pos = frontmatter.rfind('---')
                frontmatter = frontmatter[:pos] + f'updated: {now}\n---'
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
        # render and compute word count
        result = self._render_page(frontmatter, content)
        result = self._set_page_words(result)
        return result

    def _plan_tree_words(
        self: Wiki,
        folders: list[pathlib.Path],
        overlay: dict[pathlib.Path, str],
    ) -> None:
        """Set ``tree_words`` on the staged index content, bottom-up.

        Mutates ``overlay`` in place. Processes ``folders`` in reverse
        order so children are finalized before parents. For each folder
        sums the index's own ``page_words``, ``page_words`` from direct
        pages, and ``tree_words`` from direct child folder indexes --
        all sourced from ``overlay`` via :meth:`_current_text` so the
        cascade is exact without writing to disk. Uses
        ``_parse_page``/``_render_page`` (never the index renderer) so
        the staged link block is preserved verbatim.

        Args:
            folders: Folder list from ``_find_dirs`` (depth-first,
                root first).
            overlay: Staged ``{path: content}`` to update in place.

        """
        # process folders bottom-up
        for folder in reversed(folders):
            # include the folder's own index page_words
            index_path = folder / WIKI_INDEX
            total = self._read_frontmatter_page_words(index_path, overlay) or 0
            # sum page words (markdown only; non-markdown has no frontmatter)
            for page in self._find_pages(folder):
                if page.suffix == '.md':
                    page_words = self._read_frontmatter_page_words(page, overlay)
                    if page_words:
                        total += page_words
            # sum child folder tree_words (already includes their own page_words)
            for child in sorted(folder.iterdir()):
                if child.is_dir() and not self._is_excluded_dir(child):
                    child_index = child / WIKI_INDEX
                    child_tree = (
                        self._read_frontmatter_tree_words(child_index, overlay) or 0
                    )
                    total += child_tree
            # write tree_words into the staged index frontmatter
            text = self._current_text(index_path, overlay)
            if text is not None:
                frontmatter, content = self._parse_page(text)
                if re.search(r'^tree_words:', frontmatter, re.MULTILINE):
                    frontmatter = re.sub(
                        r'^tree_words:.*$',
                        f'tree_words: {total}',
                        frontmatter,
                        count=1,
                        flags=re.MULTILINE,
                    )
                else:
                    # insert before closing --- (last occurrence)
                    pos = frontmatter.rfind('---')
                    frontmatter = frontmatter[:pos] + f'tree_words: {total}\n---'
                # recompute word count and stage the result
                result = self._render_page(frontmatter, content)
                result = self._set_page_words(result)
                overlay[index_path] = result

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
        for _target, label, description in links:
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
            # resolve child path for frontmatter reads; a page is markdown when
            # its <name>.md exists; a name test ('.' in base_name) misfires on a
            # dotted stem like my.notes(.md) -- resolving its path to a missing
            # file (word count 0) and inverting the --markdown filter -- so probe
            # the actual file instead
            if is_folder:
                child_folder = folder / base_name.rstrip('/')
                child_path = child_folder / WIKI_INDEX
                is_markdown = False
            elif (folder / (base_name + '.md')).is_file():
                child_path = folder / (base_name + '.md')
                is_markdown = True
            else:
                child_path = folder / base_name
                is_markdown = False
            # apply markdown filter (pages only)
            if not is_folder and markdown is not None and markdown != is_markdown:
                continue
            # detect unindexed folder (folder present, no _index.md)
            unindexed = is_folder and not child_path.is_file()
            # recurse into a child folder first so a category filter can prune
            # folders whose subtree contributes nothing
            child_lines = []
            if is_folder and not unindexed:
                if depth is None or current_depth < depth:
                    self._map_folder(
                        folder=folder / base_name.rstrip('/'),
                        indent=indent + indent_unit,
                        current_depth=current_depth + 1,
                        depth=depth,
                        desc=desc,
                        desc_limit=desc_limit,
                        category=category,
                        markdown=markdown,
                        words=words,
                        _lines=child_lines,
                    )
            # skip a non-matching folder with no matching descendants
            if is_folder and not matches_category and not child_lines:
                continue
            # read word counts from child frontmatter; always render a count (0 if
            # not yet computed); unindexed folders show "(unindexed)" instead
            word_label = None
            if words and not unindexed:
                count = self._read_frontmatter_page_words(child_path) or 0
                if is_folder:
                    tree = self._read_frontmatter_tree_words(child_path) or 0
                    word_label = f'{_format_words(count)}/{_format_words(tree)}'
                else:
                    word_label = _format_words(count)
            # format description (first line only)
            desc_text = ''
            if desc and description:
                desc_text, *_ = description.split('\n')
                desc_text = desc_text.strip()
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
        """Check wikilinks in content resolve to existing files."""
        # alias relative path
        result = []
        relpath = path.relative_to(self._root)
        # strip fenced code blocks and inline backticks before scanning
        lines = []
        fence = None
        for line in content.split('\n'):
            if fence is not None:
                if line.strip() == fence:
                    fence = None
                continue
            match = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
            if match:
                fence = match.group(1)
                continue
            lines.append(re.sub(r'`[^`]+`', '', line))
        stripped = '\n'.join(lines)
        for match in re.finditer(r'\[\[([^\]|]+)', stripped):
            # strip trailing backslash (escaped pipe in markdown tables)
            target = match.group(1).rstrip('\\')
            if not (self._root / (target + '.md')).exists():
                if not (self._root / target).exists():
                    # a folder-relative link (e.g. [[../overview]]) is stale because
                    # wiki targets are root-relative; when it resolves to a real page
                    # from this file's folder, name the canonical form as the fix
                    canonical = self._canonical_link_target(path, target)
                    if canonical is not None and canonical != target:
                        result.append(
                            f'{relpath}: Stale link [[{target}]] (use [[{canonical}]])'
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


def _has_conflict_markers(text: str) -> bool:
    """Return ``True`` if text contains git merge conflict markers."""
    return ('<<<<<<<' in text) or ('>>>>>>>' in text)


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
