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
``map``, and ``titles`` — are all optional, all objects. Unknown top-level
keys are ignored, but a wrong-typed known block or key is an error naming
the file and key.

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
under a ``0700`` directory); a missing or corrupt file reads as an empty
store. The config home is exempt from root resolution, so its
``settings.json`` never declares your home directory a wiki root.

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
Read paths (``read``, ``search``, ``map``) keep working in the meantime — a
half-working wiki is the migration signature.
