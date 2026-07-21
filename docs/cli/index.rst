CLI Reference
=============

The ``wiki`` command manages a wiki — a folder tree of markdown pages indexed
at every level by ``_index.md`` files. The command ships in the ``plasma-wiki``
package on PyPI; see :doc:`/guide/getting-started` for installation. This page
is the complete reference for the public command surface; each command has a
section below.

``wiki --version`` prints the package version and exits. The ``typer``
built-ins ``--install-completion`` and ``--show-completion`` manage shell
completion.

Conventions
-----------

Wiki root resolution
~~~~~~~~~~~~~~~~~~~~

Every command except ``wiki install`` and ``wiki init`` operates on an
existing wiki and accepts ``--path <dir>`` to name its root directly. Without
``--path``, the root resolves in this order:

1. The nearest ancestor of the current directory (itself included) holding the
   declared-root marker ``.wiki/settings.json``.
2. If the current directory holds an ``_index.md`` but no marker is declared,
   the topmost directory of the contiguous ``_index.md`` chain above it.
3. ``{cwd}/wiki/``, when that directory is declared or indexed.

When none of these produce a wiki, the command fails with
``Could not locate .wiki/settings.json, _index.md, or wiki/_index.md from the
current directory.`` Nested wikis are unsupported: resolution refuses a path
inside an enclosing wiki and an undeclared root that encloses a declared one.
See :doc:`/guide/structure` for the root and index model.

If the resolved wiki carries a ``.wiki/wiki.py`` hook that has not been
trusted, every command that resolves the wiki — reads included — refuses to
run and points at ``wiki trust``. There is no silent fallback.

Subtree scope
~~~~~~~~~~~~~

``map``, ``search``, ``update``, and ``lint`` take an optional positional
``name`` argument restricting the operation to a subtree. The scope must
resolve to a directory relative to the wiki root — a page name fails with
``Wiki folder not found: '<name>'``. Scopes outside the root or inside an
excluded directory (any dot-prefixed directory, e.g. ``.wiki``, ``.git``,
``.obsidian``) are refused.

Errors and exit codes
~~~~~~~~~~~~~~~~~~~~~

Commands exit 0 on success. An error prints ``Error: <message>`` to stderr and
exits 1; invalid option usage — mutually exclusive flags, a bad
slice/``--depth``/``--desc-limit`` value, or malformed ``--settings`` JSON —
prints a usage message and exits 2 instead; a closed downstream pipe exits 0
silently. Some commands carry their own exit-code conventions, documented in
their sections: ``search`` follows the grep convention (0 match, 1 no match,
2 error), ``update --check`` exits 1 when changes are pending, and ``lint``
exits 1 when issues are found.

``wiki install``
----------------

.. code-block:: text

   wiki install [--project] [--link]

Installs the bundled agent skill into both ``.claude/skills/`` (Claude Code)
and ``.agents/skills/`` (Codex) under the target root — the home directory by
default, the current directory with ``--project``. Any prior install at each
destination is replaced. The command does not touch any wiki; after upgrading
the package, run it again to refresh the copied skill. See :doc:`/skill` for
what the skill does.

.. list-table::
   :header-rows: 1
   :widths: 20 14 66

   * - Option
     - Default
     - Behavior
   * - ``--project``
     - off
     - Install under the current directory instead of the home directory.
   * - ``--link``
     - off
     - Symlink the bundled skill instead of copying, so source edits apply
       without re-installing. Requires the package files on disk (e.g. an
       editable install); a zipped install fails with an error.

.. code-block:: console

   $ wiki install
   Installed wiki -> /home/user/.claude/skills/wiki.
   Installed wiki -> /home/user/.agents/skills/wiki.

``wiki init``
-------------

.. code-block:: text

   wiki init [name] [--path <dir>] [--settings <json>] [--quiet]

Scaffolds a wiki: creates the root directory, seeds ``.wiki/settings.json``
(from ``--settings``, else a block spelling out the naming defaults so the
knobs are discoverable), stages the Obsidian config template under
``.wiki/obsidian/``, writes the root ``_index.md``, sweeps any existing tree
the same way ``wiki update`` would, materializes the ``.wiki/cache/`` counts
cache, installs the Obsidian config into ``.obsidian/`` (downloading pinned
plugin code — failures are stderr warnings, not errors), and configures the
git merge driver (see :doc:`/guide/merge-driver`).

Re-running ``init`` on an initialized wiki (root ``_index.md`` present) prints
``Wiki already initialized at: <path>`` and exits 0 without changing anything;
``--settings`` is ignored whenever the settings file already exists.

``init`` refuses to scaffold inside an enclosing wiki, refuses an invalid wiki
name (naming the violated rule) before any write, refuses a re-init sweep over
merge conflict markers, and fails on an invalid ``OFFLINE_MODE`` value before
touching the filesystem (``OFFLINE_MODE=true`` skips the plugin downloads with
a warning; see :doc:`/configuration`).

.. list-table::
   :header-rows: 1
   :widths: 20 22 58

   * - Argument / option
     - Default
     - Behavior
   * - ``name`` (positional)
     - the project (cwd) name, or the ``--path`` folder name
     - Wiki display name; must satisfy the naming policy (including one seeded
       by ``--settings``) or ``init`` fails before any write.
   * - ``--path``
     - ``{cwd}/wiki/``
     - Directory to create the wiki in.
   * - ``--settings``
     - none
     - Initial ``.wiki/settings.json`` contents as a JSON object string, e.g.
       ``'{"naming": {"validate": ["ascii", "identifier"]}}'``; validated
       before any write. See :doc:`/configuration` for the keys.
   * - ``--quiet``
     - off
     - Suppress the Obsidian hint and other non-error output.

.. code-block:: console

   $ wiki init myproject
   Initialized wiki at: /home/user/myproject/wiki

``wiki config``
---------------

.. code-block:: text

   wiki config [--path <dir>]

Installs or refreshes the wiki's editor and git integration. The command
syncs the staged ``.wiki/obsidian/`` template into ``.obsidian/`` — plugin
settings are copied, pinned plugin code is downloaded from the upstream
release and verified against pinned sha256 digests, and top-level ``.json``
files are created when absent or merged when present (arrays union-merged,
dicts deep-merged with the source winning). It restores a missing
``.wiki/settings.json`` as ``{}``, registers the ``merge.wiki`` driver in the
repository's local git config, and writes the ``**/_index.md merge=wiki`` line
to ``.gitattributes`` when that file has no uncommitted changes — it never
stages or commits. See :doc:`/guide/obsidian` and :doc:`/guide/merge-driver`.

Because the merge driver lives in each clone's local git config while
``.gitattributes`` only names it, every contributor runs ``wiki config`` once
per clone.

The command exits 0 even when a plugin download fails — download failures
(network, ``OFFLINE_MODE=true``, a digest mismatch) are stderr warnings, never
the exit code; re-run online to finish setup. It fails (exit 1) on an
unresolvable wiki, malformed ``.obsidian/*.json``, or an untrusted
``.wiki/wiki.py`` hook. Obsidian's Restricted Mode step is manual; the
reminder prints on stderr only when attached to a terminal.

.. list-table::
   :header-rows: 1
   :widths: 20 22 58

   * - Option
     - Default
     - Behavior
   * - ``--path``
     - the enclosing wiki root
     - Wiki root directory (see `Wiki root resolution`_).

.. code-block:: console

   $ wiki config
   Updated Obsidian config.

``wiki trust``
--------------

.. code-block:: text

   wiki trust [--path <dir>]

Records the resolved wiki root in the user-global trust store
(``~/.wiki/settings.json``; the ``WIKI_CONFIG_DIR`` environment variable
overrides the ``~/.wiki`` home) so the wiki's ``.wiki/wiki.py`` hook may run.
A hook is a Python file whose ``__all__`` names exactly one
`wiki.core.wiki.Wiki` subclass; it runs arbitrary code with your privileges,
which is why every command refuses an untrusted one. Only trust a wiki whose
hook you have read.

Resolving the root for ``trust`` never executes the hook. The command refuses
a path that is not a real wiki (neither ``.wiki/settings.json`` nor
``_index.md`` present). Trusting a wiki with no hook is harmless
future-proofing; the output notes it.

.. list-table::
   :header-rows: 1
   :widths: 20 22 58

   * - Option
     - Default
     - Behavior
   * - ``--path``
     - the enclosing wiki root
     - Wiki root directory (see `Wiki root resolution`_).

.. code-block:: console

   $ wiki trust
   Trusted wiki: /home/user/myproject/wiki

``wiki read``
-------------

.. code-block:: text

   wiki read <name> [--path <dir>] [-l|--lines n:m] [-w|--words n:m] [-c|--chars n:m]

Prints a named entry to stdout verbatim, with no appended newline — redirected
output round-trips byte-for-byte for LF files (reads normalize CRLF endings).

The name resolves relative to the wiki root: a directory reads its
``_index.md``; an existing file reads itself; otherwise ``.md`` is appended
(appended, not substituted — ``app.config`` resolves to ``app.config.md``). A
miss fails with ``Wiki entry not found: '<name>'``, suggesting the full key
when exactly one page's stem matches; a path escaping the root fails with
``Path is outside wiki root: '<name>'``. Resolution is directory-first: a
folder shadows a same-named page.

The slice options are pairwise mutually exclusive and share the format
``n:m``, ``n:``, or ``:m`` (0-indexed, half-open). A slice applies to the
body only: the frontmatter is lifted off, structural leading and trailing
blank lines are stripped, the slice is taken, and the output re-emits the
frontmatter plus the slice — with a trailing newline appended, unlike an
unsliced read. Non-markdown files slice as a whole.

.. list-table::
   :header-rows: 1
   :widths: 24 18 58

   * - Argument / option
     - Default
     - Behavior
   * - ``name`` (positional, required)
     - —
     - File or directory path to read, relative to the wiki root.
   * - ``--path``
     - the enclosing wiki root
     - Wiki root directory (see `Wiki root resolution`_).
   * - ``-l``, ``--lines``
     - none
     - Slice the body by line range.
   * - ``-w``, ``--words``
     - none
     - Slice the body by word range (original inter-word spacing preserved).
   * - ``-c``, ``--chars``
     - none
     - Slice the body by character range.

.. code-block:: console

   $ wiki read topics/example
   ---
   name: topics/example
   desc: An example page.
   tags: []
   sources: []
   created: 2026-01-01T00:00:00Z
   updated: 2026-01-01T00:00:00Z
   ---

   # topics/example

   Body prose for the example page.

``wiki search``
---------------

.. code-block:: text

   wiki search <pattern> [name] [--path <dir>] [-f|--field <fields>]
               [-i|--ignore-case] [-a|--all] [--lines | --lineno]

Searches wiki content with a Python regular expression. The exit code follows
the grep convention: a match exits 0; no match prints ``No matches found.`` on
stderr and exits 1; an error (invalid regex, no resolvable wiki, a refused
hook) exits 2. Scripts should branch on the exit code, not parse the output.

By default the search runs over page bodies — everything below the
frontmatter, which includes the H1 and an index's generated link block — and
the default output is the matching file paths, deduplicated, in match order.
``--field`` switches to frontmatter search: the pattern runs against each
named field's value (the ``key:`` prefix and YAML quotes are stripped;
block-scalar continuation lines are included). An empty ``--field ""`` is an
explicit empty field set that matches nothing — it does not fall back to a
body search.

.. list-table::
   :header-rows: 1
   :widths: 26 18 56

   * - Argument / option
     - Default
     - Behavior
   * - ``pattern`` (positional, required)
     - —
     - Python ``re`` pattern to search for.
   * - ``name`` (positional)
     - the whole wiki
     - Restrict scope to a subtree (must be a folder).
   * - ``--path``
     - the enclosing wiki root
     - Wiki root directory (see `Wiki root resolution`_).
   * - ``-f``, ``--field``
     - none (body search)
     - Comma-separated frontmatter fields to search instead of the body.
   * - ``-i``, ``--ignore-case``
     - off
     - Case-insensitive matching.
   * - ``-a``, ``--all``
     - off
     - Include non-markdown files, searched whole (``--field`` never matches
       them; undecodable files are skipped).
   * - ``--lines``
     - off
     - Print ``path:lineno: line`` for each match.
   * - ``--lineno``
     - off
     - Print ``path:lineno`` for each match (mutually exclusive with
       ``--lines``).

.. code-block:: console

   $ wiki search -i 'parser' topics
   topics/example.md
   topics/parser.md

``wiki update``
---------------

.. code-block:: text

   wiki update [name] [--path <dir>] [--prune] [--check] [--full | --count]

Rewrites whatever drifted from the generated form — the maintenance sweep that
keeps the tool-owned surfaces in sync with the filesystem (see
:doc:`/guide/generation`). An update:

- **Syncs index link blocks** — adds links for new entries (with a ``...``
  placeholder description), refreshes labels and ``[category]`` prefixes from
  child frontmatter, sorts rows, and creates missing ``_index.md`` files.
  Links whose targets are gone from disk are preserved with a warning;
  ``--prune`` removes them instead.
- **Propagates descriptions** — a page's frontmatter ``desc`` is the source of
  truth for its parent index's link row; a diverged index-side description is
  overwritten with a warning naming the page as the place to edit.
- **Repairs frontmatter** — refreshes ``name:`` from the path, fills missing
  ``desc``/``created``/``updated`` fields, removes unset ``title:`` and
  ``category:`` lines, and enforces the canonical field order.
- **Rewrites the H1** to the authored ``title`` or the path-derived name.
- **Adopts bare pages** — a markdown page with no frontmatter gains a fresh
  block, seeding ``title:`` from its authored H1 when one exists.
- **Normalizes CRLF endings** to LF, without re-stamping ``updated:``.
- **Repairs formatter-mangled indexes** (an escaped link block or a rewritten
  ``***`` delimiter) in place.
- **Restores a missing** ``.wiki/settings.json`` as ``{}`` and recreates a
  deleted ``.wiki/cache/``.

``updated:`` is re-stamped only on files whose content actually changed.
Files the sweep cannot safely fix are left alone with a notice: pages whose
frontmatter never closes, emptied or truncated indexes (restore from git or
delete to rebuild), entries with policy-invalid names, symlinks, and files
edited concurrently during the run (re-run to converge).

Narration goes to stderr — condensed to one count line per category by
default (e.g. ``Created 1 new index (fill in its desc)``, ``Added 2 new
links``); ``--full`` prints every line instead. The stdout summary is
``Updated N file(s).`` or ``Nothing to update.``

The sweep refuses (exit 1, dry run included) when the scope crosses a nested
declared wiki or when any in-scope file carries git merge conflict markers
outside a ``no-lint`` region — resolve the conflicts and re-run.

.. list-table::
   :header-rows: 1
   :widths: 22 18 60

   * - Argument / option
     - Default
     - Behavior
   * - ``name`` (positional)
     - the whole wiki
     - Restrict scope to a subtree (must be a folder).
   * - ``--path``
     - the enclosing wiki root
     - Wiki root directory (see `Wiki root resolution`_).
   * - ``--prune``
     - off
     - Remove broken links instead of preserving them.
   * - ``--check``
     - off
     - Dry run: writes nothing, lists ``Would update: <path>`` lines, and
       exits 1 when changes are pending, 0 when clean. A nonzero exit is not
       an error — it is a formatter-style check result. ``--check`` never
       restores a missing ``.wiki/settings.json``; only a writing run does.
   * - ``--full``
     - off
     - Print every narration line in emission order.
   * - ``--count``
     - on (the default behavior)
     - Print one count line per narration category (mutually exclusive with
       ``--full``).

.. code-block:: console

   $ wiki update
   Created 1 new index (fill in its desc)
   Added 1 new link
   Updated 2 files.

``wiki lint``
-------------

.. code-block:: text

   wiki lint [name] [--path <dir>] [--full | --count]

Checks wiki health. The command exits 1 when issues are found and 0 when the
wiki is clean; soft notes never affect the exit code. Issues print to stdout,
notes to stderr; the output is prose for humans — scripts branch on the exit
code.

**Issues** (hard, exit 1) cover everything ``wiki update`` would rewrite —
each shown as ``<path>: Requires update`` with an indented unified diff — plus
problems update cannot fix: missing indexes, invalid page/folder names, pages
shadowed by same-named folders, merge conflict markers, malformed frontmatter
(no closing ``---``), empty or truncated indexes, nested wiki roots,
formatter-damage signatures (escaped wikilinks, a missing ``***`` delimiter),
hand-wrap mangles, authored descriptions missing their trailing period,
unparseable ``created:``/``updated:`` stamps, broken links in the generated
index block, dangling or nested region markers, and — when the
``titles.required`` setting is on — missing titles.

**Notes** (soft, stderr) flag placeholder (``...``) descriptions,
descriptions over 500 characters, empty index content sections, CRLF line
endings, and stale ``[[wikilinks]]`` in authored prose (suggesting the
canonical target when one resolves).

A ``<!-- start: no-lint -->`` … ``<!-- end: no-lint -->`` region suppresses
the position-based rules (conflict markers, escaped wikilinks, wrap mangles,
stale-link notes) for the lines it wraps; a malformed pair is itself an issue
and suppresses nothing.

.. list-table::
   :header-rows: 1
   :widths: 22 18 60

   * - Argument / option
     - Default
     - Behavior
   * - ``name`` (positional)
     - the whole wiki
     - Restrict scope to a subtree (must be a folder).
   * - ``--path``
     - the enclosing wiki root
     - Wiki root directory (see `Wiki root resolution`_).
   * - ``--full``
     - on (the default behavior)
     - Print every issue and note line.
   * - ``--count``
     - off
     - Print only the closing summary (mutually exclusive with ``--full``).

.. code-block:: console

   $ wiki lint
   topics/example.md: Needs desc
   topics/drafts/: Missing index

   1 issue, 1 note.

``wiki map``
------------

.. code-block:: text

   wiki map [name] [--path <dir>] [--depth N] [--desc/--no-desc]
            [--desc-limit N] [--category <list>] [--markdown/--no-markdown]
            [--words/--no-words] [--stat]

Prints an indented tree overview of the wiki, driven by the index link blocks
rather than a raw directory walk: each folder's ``_index.md`` rows are
iterated in index order, so a file not yet linked (update pending) does not
appear. An unindexed folder renders as ``name/ (unindexed)`` with no children;
a preserved broken link renders ``(broken)`` and is never recursed into. Each
line has the shape ``[category] name[/] (count): desc``. An empty result
prints ``Wiki is empty.``; an index missing its ``***`` delimiter still maps
via a best-effort recovery of its link block, with a stderr warning to run
``wiki update``.

.. list-table::
   :header-rows: 1
   :widths: 30 24 46

   * - Argument / option
     - Default
     - Behavior
   * - ``name`` (positional)
     - the whole wiki
     - Restrict scope to a subtree (must be a folder).
   * - ``--path``
     - the enclosing wiki root
     - Wiki root directory (see `Wiki root resolution`_).
   * - ``--depth``
     - none (unlimited)
     - Maximum tree depth; ``0`` means top-level entries only; a negative
       value is rejected.
   * - ``--desc`` / ``--no-desc``
     - ``--desc``
     - Show link descriptions, folded to one line each; the ``...``
       placeholder is hidden.
   * - ``--desc-limit``
     - the ``map.desc_limit`` setting, else untruncated
     - Character cap per description, truncated with the configured ellipsis;
       ``-1`` disables truncation. See :doc:`/configuration` for the ``map``
       settings block.
   * - ``--category``
     - none (no filter)
     - Show only entries in these comma-separated categories; an empty string
       shows uncategorized entries only. Folders show when they or a
       descendant match.
   * - ``--markdown`` / ``--no-markdown``
     - unset (show all)
     - Show only markdown pages, or only non-markdown files; folders are
       always shown.
   * - ``--words`` / ``--no-words``
     - ``--words``
     - Show word counts — ``(N)`` for a page, ``(page/tree)`` for a folder
       (its index over its whole subtree) — abbreviated with ``k``/``m``/
       ``b``/``t`` suffixes past a thousand and cached under ``.wiki/cache/``.
   * - ``--stat``
     - off
     - Print a one-line size summary (lines, chars, words) of the tree the
       same flags would render, instead of the tree — the cheap probe before
       dumping a large wiki.

.. code-block:: console

   $ wiki map --depth 1
   guides/ (100/2.0k): Task-oriented guides.
     setup (100): How to set up the project.
   notes (100): Loose notes.
   topics/ (100/1.0k): Topic pages.
     example (100): An example page.
