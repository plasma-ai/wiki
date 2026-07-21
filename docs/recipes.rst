Recipes
=======

Task-oriented walkthroughs for common wiki workflows. Each recipe is
self-contained and assumes the ``wiki`` CLI is installed (``pip install
plasma-wiki``). Run from anywhere inside the wiki — or from the directory
containing it — commands resolve the enclosing wiki root automatically; from
elsewhere, pass ``--path`` to name the root. See
:doc:`/guide/getting-started` for a first-run tour and :doc:`/cli/index` for
every command's full option surface.

Stand up a wiki in an existing repository
-----------------------------------------

Initialize
~~~~~~~~~~

From the repository root, scaffold a wiki under ``{cwd}/wiki/``:

.. code-block:: console

   $ cd myproject
   $ wiki init
   Initialized wiki at: /home/user/myproject/wiki

The wiki is named after the project (the current directory) unless you pass a
name argument. ``wiki init`` creates the root ``_index.md``, seeds
``.wiki/settings.json`` (the file that declares the root — its contents spell
out the default settings so the knobs are discoverable), stages the
Obsidian config under ``.wiki/obsidian/`` and installs it into ``.obsidian/``,
and wires the git merge driver (see the next recipe).

To place the wiki elsewhere, pass ``--path``; the name then defaults to that
folder's name:

.. code-block:: console

   $ wiki init --path docs/notes
   Initialized wiki at: /home/user/myproject/docs/notes

To start with a stricter naming policy, seed settings at creation:

.. code-block:: console

   $ wiki init --settings '{"naming": {"validate": ["ascii", "identifier"], "allow": "-"}}'

``--settings`` takes a JSON object and is validated before anything is written;
see :doc:`/configuration` for every key. ``--quiet`` suppresses non-error
output. Re-running ``wiki init`` on an existing wiki prints ``Wiki already
initialized at: <path>`` and changes nothing — in particular, ``--settings`` is
ignored once ``.wiki/settings.json`` exists.

Without network access, run ``OFFLINE_MODE=true wiki init``: the Obsidian
plugin download is skipped with a warning, and ``wiki config`` finishes the
setup later when you are online.

Adopt an existing folder of markdown
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``wiki init`` pointed at a folder that already holds markdown sweeps the whole
tree the way ``wiki update`` does: every folder gains an ``_index.md``, every
bare page is adopted with fresh frontmatter (an authored H1 is preserved by
seeding ``title:`` from it), and every entry is linked into its parent index.
The notices stream to stderr as it works:

.. code-block:: console

   $ wiki init --path docs/notes
   New index: guides/_index.md (fill in its desc)
   Adopted bare page: guides/setup.md (frontmatter added; title: seeded from its H1)
   New link: [[guides/setup|setup]] in guides/_index.md
   Initialized wiki at: /home/user/myproject/docs/notes

New indexes and adopted pages carry the ``desc: ...`` placeholder. Fill each
in — the frontmatter ``desc`` of a page is the source of truth for its link row
in the parent index — and ``wiki lint`` reports the remaining placeholders as
``Needs desc`` notes until you do. See :doc:`/guide/generation` for exactly
what the tool owns and rewrites.

Commit the scaffold
~~~~~~~~~~~~~~~~~~~

``wiki`` never commits anything. Commit the wiki root — the ``_index.md``
files, ``.wiki/settings.json``, and the staged ``.wiki/obsidian/`` template —
plus the ``.gitattributes`` line the merge-driver wiring wrote. Two artifacts
need no attention: ``.wiki/cache/`` ships its own ``.gitignore`` and ignores
itself, and ``.obsidian/`` is materialized per machine — any clone rebuilds it
with ``wiki config``.

Keep formatters off the generated syntax
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``***`` delimiter and ``[[wikilinks]]`` are load-bearing syntax, and
generic markdown formatters mangle both (rewriting ``***`` to ``---``,
backslash-escaping the brackets). ``wiki update`` repairs a mangled index and
``wiki lint`` names the damage signatures, but do not rely on the repair — pick
a lane. For mdformat, add the ``mdformat-wiki`` plugin (and remove
``mdformat-frontmatter`` if present — the two conflict):

.. code-block:: yaml

   - id: mdformat
     additional_dependencies: [mdformat-wiki]

For formatters with no plugin lane, exclude the wiki root instead — for
prettier, add it to ``.prettierignore``:

.. code-block:: text

   wiki/

Adopt the merge driver in a team repository
-------------------------------------------

Parallel edits to a wiki collide most often in ``_index.md`` files, where
``wiki update`` regenerates the link block on every run. The bundled git merge
driver auto-resolves that generated churn so index merges conflict only on
authored content. See :doc:`/guide/merge-driver` for the full mechanics.

Wire it once and commit
~~~~~~~~~~~~~~~~~~~~~~~

One team member runs ``wiki config`` (``wiki init`` does the same wiring) and
commits the result. The command registers the driver in the clone's local git
config and appends the attribute mapping to ``.gitattributes``:

.. code-block:: text

   # Wiki index merge driver
   **/_index.md merge=wiki

``wiki config`` writes ``.gitattributes`` in the working tree only — you stage
and commit it yourself — and skips the write entirely while the file has
uncommitted changes (the line lands on the next run with a clean
``.gitattributes``).

Once per clone
~~~~~~~~~~~~~~

The committed ``.gitattributes`` only *names* the driver; the driver command
itself lives in each clone's local git config. Every contributor therefore runs
``wiki config`` once after cloning:

.. code-block:: console

   $ wiki config
   Updated Obsidian config.

Without this step, ``_index.md`` merges silently fall back to git's plain text
merge. (``wiki config`` also refreshes the Obsidian integration; a failed
plugin download is a stderr warning, never a failure — the git wiring still
applies. See :doc:`/guide/obsidian`.)

Know what still conflicts
~~~~~~~~~~~~~~~~~~~~~~~~~

During a merge of an ``_index.md`` below a declared wiki root, the driver
normalizes the tool-owned surfaces — the ``name:`` and ``updated:`` frontmatter
keys (plus ``created:`` when both sides added the file independently) and the
generated link block — so they never conflict. Everything authored still gets a
normal three-way merge that can conflict: ``title:``, ``desc:``, ``category:``,
``tags:``, ``sources:``, and the content below the ``***`` delimiter. Any file
that is not an ``_index.md`` under a wiki root merges as ordinary text.

After every merge, run ``wiki update``: it regenerates the link rows from the
merged filesystem, and a merged-in ``title:`` shows up in its H1 only after
that run.

Rename and restructure pages safely
-----------------------------------

Display names are path-derived: the tool owns each entry's ``name:``
frontmatter and rewrites the H1 to match the path. Renaming therefore means
*moving the file* — editing ``name:`` by hand is futile, because the next
``wiki update`` rewrites it back from the path. Authored fields (``title:``,
``desc:``, ``category:``, the body) travel with the file untouched.

Rename a page
~~~~~~~~~~~~~

Move the file, then update. The old link row in the parent index is now broken;
``update`` preserves broken rows by default (so an accidental deletion never
silently drops a row) — pass ``--prune`` to remove it as part of the rename:

.. code-block:: console

   $ git mv topics/example.md topics/sample.md
   $ wiki update --prune
   Added 1 new link
   Pruned 1 broken link
   Updated 2 files.

The moved page's ``name:`` and H1 are rewritten to the new path, and the parent
index gets a fresh row carrying the page's ``desc``. Without ``--prune``, the
stale row stays behind: ``wiki map`` renders it ``(broken)`` and ``wiki lint``
reports ``Broken link [[topics/example|example]]`` until you prune or restore
the target.

Move folders
~~~~~~~~~~~~

The same flow restructures whole subtrees — move the directory (its
``_index.md`` moves with it) and run ``wiki update --prune`` from the wiki
root, so every affected index on both ends of the move is swept. Any brand-new
folder gains an ``_index.md`` with a ``desc: ...`` placeholder to fill in.

Fix prose cross-links
~~~~~~~~~~~~~~~~~~~~~

``wiki update`` regenerates *index* link blocks; it never rewrites
``[[wikilinks]]`` inside page prose. Find references to the old path and edit
them by hand:

.. code-block:: console

   $ wiki search '\[\[topics/example' --lines
   notes/related.md:10: See [[topics/example]] for the walkthrough.

Any you miss show up as ``Stale link [[topics/example]]`` notes the next time
``wiki lint`` runs.

Preview before writing
~~~~~~~~~~~~~~~~~~~~~~

``wiki update --check`` is a dry run: it writes nothing, lists the files the
sweep would rewrite, and exits 1 while changes are pending. Run it between the
move and the real sweep to preview the rename:

.. code-block:: console

   $ wiki update --check --prune
   Would add 1 new link
   Would prune 1 broken link
   Would update: topics/sample.md
   Would update: topics/_index.md

   2 files would change (run without --check to apply).

Audit a wiki with lint
----------------------

Run it
~~~~~~

.. code-block:: console

   $ wiki lint
   topics/sample.md: Needs desc
   topics/_index.md: Broken link [[topics/example|example]]

   1 issue, 1 note.

``wiki lint`` exits 1 when issues are found and 0 when the wiki is clean.
Issues print to stdout; *notes* print to stderr and never affect the exit code.
Scope the audit to a subtree by passing a folder name (``wiki lint topics``),
and pass ``--count`` to condense the run to the closing summary alone.

Read the output
~~~~~~~~~~~~~~~

Issues come in two flavors. Anything ``wiki update`` would rewrite reports as
``Requires update`` with an indented unified diff of the pending rewrite — run
``wiki update`` to clear the whole class at once. Update also creates missing
indexes (fill in each new ``desc:``) and repairs a mangled ``***`` delimiter.
Everything else needs a human: names that violate the naming policy, pages
shadowed by a same-named folder, merge conflict markers, malformed
frontmatter, truncated indexes, escaped wikilinks in page prose (formatter
damage), broken links in generated index blocks, and unparseable
``created:``/``updated:`` stamps.

Notes flag soft hygiene: placeholder (``...``) and oversized descriptions,
empty index content sections, CRLF line endings, and stale ``[[wikilinks]]`` in
prose — where a folder-relative target that resolves to a real page gets a
``(use [[canonical]])`` suggestion, since wiki targets are root-relative.

Exempt intentional content
~~~~~~~~~~~~~~~~~~~~~~~~~~

A page that must *display* otherwise-flagged content — sample conflict markers,
stale-link examples — wraps those lines in a region that suppresses the
positional rules for just that span:

.. code-block:: markdown

   <!-- start: no-lint -->
   <<<<<<< example conflict marker, shown as documentation
   =======
   >>>>>>> theirs
   <!-- end: no-lint -->

Each marker stands alone on its line; a dangling or nested marker is itself a
lint issue, and a malformed pair suppresses nothing.

Gate it in CI
~~~~~~~~~~~~~

The pair of check modes makes a two-command gate — fail the build on generated
drift, then on structural issues:

.. code-block:: console

   $ wiki update --check && wiki lint --count

Both commands signal by exit code: ``update --check`` exits 1 while a sweep is
pending (like a formatter's check mode — nonzero is not an error), and ``lint``
exits 1 on issues, with notes never tipping the result.

Search and navigate from the terminal
-------------------------------------

Map the tree
~~~~~~~~~~~~

``wiki map`` prints an indented overview driven by the index link blocks —
entries appear in index order, with descriptions from the parent index and
cached word counts (``(page)`` for a page, ``(page/tree)`` for a folder):

.. code-block:: console

   $ wiki map --depth 1
   guides/ (100/2.0k): Task-oriented guides.
     setup (100): How to set up the project.
   topics/ (100/1.0k): Topic pages.
     sample (100): An example page.

Useful knobs: ``--depth N`` limits recursion (``0`` shows top-level entries
only); ``--no-desc`` and ``--no-words`` trim the annotations; ``--desc-limit N``
caps each description (``-1`` disables truncation; the ``map.desc_limit``
setting supplies the default); ``--category a,b`` filters to entries in those
categories (an empty string shows uncategorized entries only);
``--markdown``/``--no-markdown`` filters pages versus non-markdown files.
Before dumping a large wiki, size it first:

.. code-block:: console

   $ wiki map --stat
   100 lines, 5000 chars, 1000 words

The map reads the indexes, not the filesystem: a file not yet linked
in (update pending) does not appear, and an unindexed folder shows
``(unindexed)`` with no children.

Search content and frontmatter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``wiki search`` runs a Python regex over page bodies (frontmatter excluded; the
H1 and an index's link block count as body). Default output is matching file
paths; ``--lines`` shows ``path:lineno: line`` and ``--lineno`` just
``path:lineno``:

.. code-block:: console

   $ wiki search 'parser' topics --lines -i
   topics/sample.md:10: The parser accepts one production per line.

Scope with a folder name argument, add ``-i`` for case-insensitive matching,
and ``-a``/``--all`` to include non-markdown files. ``--field``/``-f`` searches
the *values* of named frontmatter fields instead of the body:

.. code-block:: console

   $ wiki search -f desc,tags 'draft'
   topics/sample.md

Search follows the grep convention — a match exits 0, no match prints ``No
matches found.`` on stderr and exits 1, and an error (bad regex, no resolvable
wiki) exits 2 — so scripts branch on the exit code rather than parse output.

Read entries
~~~~~~~~~~~~

``wiki read`` prints a named entry verbatim (no appended newline, so redirected
output round-trips byte-for-byte). A folder name resolves to its ``_index.md``;
a page name resolves by appending ``.md``:

.. code-block:: console

   $ wiki read topics/sample
   $ wiki read guides

For large pages, slice the body by lines (``-l``), words (``-w``), or
characters (``-c``) — ``n:m``, ``n:``, or ``:m``, 0-indexed and half-open, one
unit at a time:

.. code-block:: console

   $ wiki read topics/sample --lines :10

A sliced read keeps the frontmatter above the slice and appends a trailing
newline. See :doc:`/guide/pages` for the page format the output reflects.
