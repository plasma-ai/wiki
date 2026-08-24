Configuration
=============

``wiki`` reads configuration from the following places:

- the per-wiki settings file, ``.wiki/settings.json``, at the wiki root —
  naming policy, timestamp rendering, map presentation, and title
  requirements for that wiki;
- the user-global file ``~/.wiki/settings.json`` — the machine-local trust
  store, not wiki policy;
- the environment variables ``OFFLINE_MODE`` and ``WIKI_CONFIG_DIR``.

The per-wiki file doubles as the *declared-root marker*: its presence tells
every command where the wiki begins. This page covers each surface, plus how
the root is resolved when you run a command without ``--path``.

Wiki root resolution
--------------------

Every command except ``wiki install`` and ``wiki init`` operates on a wiki
root. With ``--path <dir>`` the root is taken as given (``~`` expanded,
relative paths resolved against the current directory). Without it, the root
is resolved from the working directory, in order:

1. The nearest ancestor (the current directory included) containing
   ``.wiki/settings.json``. The walk checks the whole ancestor chain: two
   markers on one chain fail with ``Ambiguous wiki root`` rather than
   silently picking one.
2. If the current directory holds an ``_index.md`` but no ancestor declares a
   root, the topmost directory of the contiguous ``_index.md`` chain — an
   *undeclared* wiki, tolerated with a stderr notice that ``wiki update``
   will restore the marker.
3. ``{cwd}/wiki/``, when that directory is declared or at least indexed.
4. Otherwise the command fails: ``Could not locate .wiki/settings.json,
   _index.md, or wiki/_index.md from the current directory.``

The resolved root must be a real wiki — declared by ``.wiki/settings.json``
or at least indexed by ``_index.md``. These configurations are refused
outright, because nested wikis are unsupported:

- a path *inside* an enclosing wiki (a declared marker above it, or a parent
  ``_index.md`` chain) — scoped work goes through a command's positional
  ``name`` argument instead;
- an undeclared root that *encloses* a declared root below it — run the
  command from that declared root.

Non-fatal diagnostics print to stderr when resolution tolerates something: a
missing settings marker, a declared root missing its ``_index.md``, or an
``_index.md`` chain extending above the declared root (a foreign or damaged
outer index).

See :doc:`/cli/index` for the per-command ``--path`` and ``name`` surfaces.

The settings file: ``.wiki/settings.json``
------------------------------------------

A JSON object at the wiki root. An absent file means all defaults; malformed
JSON or a non-object top level fails every command that reads policy, with a
message naming the file. The recognized blocks — ``naming``, ``timestamp``,
``map``, ``titles``, and ``exclude`` — are all optional, all objects.
Unknown top-level keys are ignored, but a wrong-typed known block or key is
an error naming the file and key.

Seeding and restoration are asymmetric:

- ``wiki init`` seeds the file with the full ``naming`` defaults block (shown
  below) so the knobs are discoverable, or with the object passed via
  ``--settings '<json>'``. The seed is validated before anything is written,
  and re-init never overwrites an existing settings file.
- ``wiki update`` and ``wiki config`` restore a *missing* file as ``{}`` —
  all defaults; custom policy is never re-invented. Deleting the file
  therefore silently drops any authored policy once the next mutating run
  restores the bare marker. (``wiki update --check`` is a dry run and does
  not restore it.)

The init-seeded file:

.. code-block:: json

   {
     "naming": {
       "validate": [],
       "allow": "",
       "deny": "",
       "pattern": null,
       "min_length": null,
       "max_length": null,
       "leading_digits": true,
       "reserved": []
     }
   }

Seeding a stricter policy at scaffold time:

.. code-block:: console

   $ wiki init myproject --settings '{"naming": {"validate": ["identifier"], "allow": "-"}}'

``naming`` — page and folder names
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Controls which page and folder names the wiki accepts. The default policy is
lenient: spaces, dashes, mixed case, and unicode are all valid. Stricter
rules are opt-in. An entry whose name breaks the policy is skipped by
``wiki update`` (with a warning) and flagged by ``wiki lint``.

``naming.validate``
   List of strings, default ``[]``. Predicate names applied to a candidate
   name after removing any ``allow`` characters: ``ascii``, ``alpha``,
   ``alphanum``, or ``identifier`` (each maps to the corresponding
   Python ``str.is*`` check). Unknown names are rejected when the policy
   loads.

``naming.allow``
   String of characters, default ``""``. Characters stripped from the name
   before the ``validate`` predicates run — for example, allow ``-`` so
   dashed names pass ``identifier``.

``naming.deny``
   String of characters, default ``""``. Extra characters rejected anywhere
   in a name.

``naming.pattern``
   Regex string or ``null``, default ``null``. When set, the whole name must
   match the pattern. An invalid regex is rejected when the policy loads.

``naming.min_length``
   Integer ``>= 1`` or ``null``, default ``null``. Minimum name length;
   ``null`` means 1, not "no minimum".

``naming.max_length``
   Integer ``>= 1`` or ``null``, default ``null``. Maximum name length;
   ``null`` means no cap.

``naming.leading_digits``
   Boolean, default ``true``. Only affects the ``identifier`` predicate: when
   true, a leading digit passes (the check runs with an underscore prefixed).

``naming.reserved``
   List of strings, default ``[]``. Names rejected outright. ``_index`` is
   always reserved regardless of this list.

Where the ``naming`` keys require integers, booleans are rejected — the
checks are exact-type.

Some rules apply regardless of the ``naming`` block, because the on-disk
grammar depends on them: the characters ``/``, ``*``, ``\``, ``[``, ``]``,
``|``, and ``#`` are always denied, as are empty names, non-printable names,
and names starting with a dot.

``timestamp`` — ``created:`` and ``updated:`` stamps
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Controls how the tool-owned ``created:`` and ``updated:`` frontmatter fields
are rendered (see :doc:`/guide/generation` for when they are written).

``timestamp.timezone``
   IANA zone name (e.g. ``America/New_York``) or ``null``, default ``null``
   (UTC). An unknown zone name is rejected when the policy loads.

``timestamp.format``
   strftime string, default ``%Y-%m-%dT%H:%M:%SZ``. When a ``timezone`` is
   configured and no ``format`` is authored, the default becomes
   ``%Y-%m-%dT%H:%M:%S%z`` — the stock default's literal ``Z`` asserts UTC,
   so the swap keeps the rendered offset honest. An authored format always
   passes through untouched (a literal ``Z`` alongside a non-UTC zone is the
   author's own claim). The format must render a single non-empty line:
   empty or whitespace-only values, ``%n``, ``%t``, and literal line breaks
   are rejected.

Changing ``timestamp.format`` on an existing wiki requires rewriting the
existing stamps by hand — ``wiki lint`` flags each stamp that no longer
parses under the configured format.

``map`` — tree rendering
~~~~~~~~~~~~~~~~~~~~~~~~

Presentation defaults for ``wiki map``.

``map.desc_limit``
   Integer ``>= -1`` or ``null``, default ``null`` (equivalent to ``-1``,
   untruncated). Character cap per rendered description. The CLI's
   ``--desc-limit`` flag overrides this setting; ``-1`` at either level
   disables truncation.

``map.indent``
   String, default two spaces. The per-level indent unit of the tree.

``map.ellipsis``
   String, default ``"..."``. Suffix appended to a truncated description.

``titles`` — required titles
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``titles.required``
   Boolean, default ``false``. When true, every index and page must carry an
   authored ``title:`` — ``wiki update`` seeds a ``title: null`` placeholder
   on files missing the field, and ``wiki lint`` fails each placeholder
   until a value is authored. (When false, a ``null`` title is the transient
   unset idiom and update removes the line — see :doc:`/guide/pages`.)

``exclude`` — indexing exclusions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Excludes paths from indexing entirely. An excluded subtree is invisible to
every walk: ``wiki update`` never scaffolds indexes, adopts pages, or
rewrites anything inside it, ``wiki lint`` checks nothing there, ``wiki
map`` and ``wiki match`` never enumerate it (``--all`` included), and its
word counts drop from the cache. ``wiki read`` stays permissive — exclusion
is indexing policy, not access control, and ``wiki read`` is how one
inspects deliberately unindexed content (dot-paths read the same way).

.. code-block:: json

   {
     "exclude": {
       "patterns": ["vendor/", "**/evidence/util", "*.tmp", "/scratch.md"]
     }
   }

``exclude.patterns``
   List of strings, default ``[]``. Gitignore-style globs matched against
   each entry's root-relative path (``/``-joined), case-sensitively, in the
   on-disk byte form, full-match only:

   - A pattern containing no ``/`` **floats** — ``*.tmp`` matches its
     single segment at any depth. A pattern containing ``/`` is
     **anchored** at the wiki root, as is one with a leading ``/`` (which
     is stripped): ``/scratch.md`` matches only the root-level file.
   - One trailing ``/`` is stripped (before the anchoring rule applies),
     so a pasted gitignore directory line (``vendor/``) just works and
     floats like ``vendor``; there is no file/dir distinction.
   - Excluding a directory excludes its whole subtree: ``vendor`` (or
     ``vendor/``) covers everything under ``vendor``. The wiki root itself
     can never be excluded.
   - A whole segment of ``**`` spans directories: ``**/build`` matches
     ``build`` at any depth, ``a/**/b`` matches ``a/b`` through any
     nesting, and ``vendor/**`` matches everything *strictly inside*
     ``vendor`` — never ``vendor`` itself, which stays indexed as an empty
     folder (use ``vendor`` or ``vendor/`` to exclude the folder too).
   - Within a segment, ``*``, ``?``, and ``[...]`` never cross ``/``.
     Classes are fnmatch-style (``[!...]`` negates); a reversed range
     like ``[z-a]`` matches nothing. An embedded ``**`` (``a**b``)
     collapses to ``*``.
   - No negation (a leading ``!`` is rejected; the syntax is reserved) and
     no escapes (``\`` is rejected — ``/`` is the separator). Empty or
     whitespace-only patterns, empty segments (``//``), and ``.``/``..``
     segments are rejected when the policy loads. A pattern that matches
     nothing is not an error (the gitignore precedent).

The built-in exclusions need no pattern: dot-paths (including ``.wiki``),
symlinked files and directories, and ``_index.md`` files are always
excluded, and are checked first. A pattern matching only an ``_index.md``
path has no effect — indexes are tool-owned per folder, so the unit of
exclusion is the folder or the page.

A parent index row pointing into a newly excluded target is pruned by the
next ``wiki update``, which names the matching pattern as the cause beside
the prune notice; until then ``wiki lint`` reports the row as a hard issue
naming the pattern. Prose
wikilinks into excluded-but-present files stay live — the generated index
link block is the hard surface, body prose is not. Scoping ``update``,
``lint``, ``map``, ``search``, or ``match`` at or under an excluded
directory is refused with an error naming the pattern.

Any ``_index.md`` files already inside an excluded subtree become inert
unmanaged bytes — never rewritten, never deleted. A nested wiki (a
directory carrying its own ``.wiki/settings.json``) under an excluded
directory no longer trips the nested-wiki sweep refusal, so a vendored or
checked-out wiki can sit inside a host wiki once its subtree is excluded. It
is not operable in place, though — from inside the guest every command reports
an ambiguous root — so drive it from its own checkout.

To exclude an already-indexed subtree: add the pattern, delete any
``_index.md`` inside the subtree, run ``wiki update`` once, and
``wiki lint`` to confirm. Older plasma-wiki versions ignore unknown
settings blocks, so a wiki carrying ``exclude`` silently re-indexes the
subtree when driven by an old version — upgrade, delete the stray
``_index.md`` files, and run ``wiki update`` to recover.

The enclosing git repository's ignore rules are a second exclusion source
needing no configuration: a path the repo's gitignore fences is excluded
from indexing exactly like a pattern match — never walked, adopted, or
linked — so a driver's stray output beside tracked content cannot be swept
into the corpus by the next update. Matching is pattern-pure (a
force-tracked file matching a fence is still fenced), and a wiki whose own
root is ignored is exempt, so a deliberately unindexed wiki inside a repo
keeps working. The repository is always the one enclosing the wiki root: the
probe drops the caller's ``GIT_*`` environment, so a command run from a git
hook (which exports ``GIT_DIR``) fences exactly as the same command run from
a shell. Outside a git repository — or with git unavailable — no fence
applies; inside one, an unreadable fence (git off ``PATH``, a broken install)
is narrated as a note, since the sweep is about to adopt what the repository
ignores.

Because the fence reads the repository's own rules alone, a personal
``core.excludesFile`` never changes what is indexed — but it still decides
what your ``git add`` accepts. A file only you ignore is indexed here and
gets a generated row that ships while the file cannot, so every other clone
reds on a broken link your own ``wiki lint`` never shows. Both the sweep that
mints such a row and the lint that audits it emit a note naming the path and
the excluding source; the row is still minted, since refusing it would make
indexing depend on the machine.

The trust store: ``~/.wiki/settings.json``
------------------------------------------

The user-global config home is ``~/.wiki`` (override with the
``WIKI_CONFIG_DIR`` environment variable). Its ``settings.json`` shares a
basename with the per-wiki file but has an unrelated schema and a single
purpose: recording which wiki roots you trust.

.. code-block:: json

   {
     "trusted": {
       "/home/user/myproject/wiki": "2026-01-01T00:00:00Z"
     }
   }

Trust gates the ``.wiki/wiki.py`` hook — an optional per-wiki file declaring
a custom `wiki.core.wiki.Wiki` subclass. Because the hook runs arbitrary
code with your privileges, every command that resolves a wiki carrying one
refuses to run until you record trust with ``wiki trust``; the hook is never
silently ignored. A wiki without a hook needs no trust. Before trusting a
wiki you cloned, read its ``.wiki/wiki.py``.

The file is managed by ``wiki trust`` (written with ``0600`` permissions
under a ``0700`` directory); a missing, empty, or corrupt file reads as an
empty store, and anything the store's custody cannot vouch for is refused —
on reads as firmly as on writes, since a trust decision must never be read
through what a trust write would refuse. That covers a ``settings.json``
that is a symlink, a hard link to a file outside the config home, a
non-regular file, or one any other local user may write, and a config home
that is itself a symlink: the store must be an inode only the ``0700`` home
names. Rewriting a *corrupt* store is refused outright rather than folding
it into an empty one and dropping every trusted root — repair or remove it
first. Both the config home and your home directory are exempt from root
resolution, so a ``settings.json`` in either never declares a wiki root —
the home exemption holds even when ``WIKI_CONFIG_DIR`` points the store
elsewhere and leaves ``~/.wiki`` behind. ``wiki init`` refuses to scaffold
a wiki at the home directory for the same reason.

Environment variables
---------------------

``OFFLINE_MODE``
   ``true`` or ``false`` (case-insensitive), unset meaning ``false``. When
   ``true``, ``wiki init`` and ``wiki config`` skip the Obsidian plugin
   downloads with a warning ("Re-run ``wiki config`` online to finish
   setup"). Any other value is rejected before any filesystem change — e.g.
   ``OFFLINE_MODE=1`` fails ``wiki init`` outright. See
   :doc:`/guide/obsidian`.

``WIKI_CONFIG_DIR``
   Path. Overrides the ``~/.wiki`` config home, relocating the trust store.

Other files under ``.wiki/``
----------------------------

``.wiki/`` is the tool's namespace at the wiki root; nothing under it is ever
indexed, and you should not author content there. Besides ``settings.json``
it holds:

``.wiki/cache/word_counts.json``
   The derived word-count cache behind ``wiki map``'s counts. Stale entries
   recompute lazily; the cache directory writes its own ``.gitignore``
   (containing ``*``), so it never needs host-repo ignore configuration.
   Safe to delete at any time; it is rebuilt on demand.

``.wiki/obsidian/``
   The staged Obsidian configuration template that ``wiki init`` and
   ``wiki config`` install into ``.obsidian/``. See :doc:`/guide/obsidian`.

``.wiki/wiki.py``
   The optional trust-gated hook described above. Its ``__all__`` must name
   exactly one `wiki.core.wiki.Wiki` subclass.

The settings marker also drives the git merge driver: ``wiki _merge``
dispatches an ``_index.md`` to the index-aware merge only when the file sits
below a declared root — see :doc:`/guide/merge-driver`.

Legacy layout
~~~~~~~~~~~~~

A wiki whose settings live at the legacy ``_config/settings.json`` location
makes every sweep-planning command (``init``, ``update``,
``update --check``, ``lint``, ``config``) refuse with a migration message:
move ``_config/`` to ``.wiki/``, run ``wiki config``, then ``wiki update``.
Read paths (``read``, ``search``, ``match``, ``map``) keep working in the
meantime — a half-working wiki is the migration signature.
