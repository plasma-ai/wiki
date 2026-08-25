Structure
=========

A wiki is a folder tree of markdown files, indexed at every level. Each folder
is a section; each markdown file is an entry; every folder carries an
``_index.md`` linking its parent and children together. This page covers the
tree itself — how it is laid out, found, and kept — while :doc:`/guide/pages`
covers the file format inside each entry.

The folder tree
---------------

A typical wiki for a project called ``myproject`` lives in a ``wiki/`` folder
and looks like this:

.. code-block:: text

   wiki/
   ├── .wiki/
   │   ├── settings.json
   │   ├── cache/
   │   └── obsidian/
   ├── _index.md
   ├── overview.md
   └── topics/
       ├── _index.md
       ├── example.md
       └── advanced/
           ├── _index.md
           └── internals.md

Non-markdown files (a diagram, a ``Makefile``) may live in the tree too — they
are indexed alongside pages, linked by their full filename. Five things are
excluded from the tree entirely:

- **Dot-prefixed files and directories** — which is how ``.wiki/``, ``.git/``,
  and ``.obsidian/`` stay out of the indexes by construction.
- **Symlinked files and directories** — never followed, never indexed.
- **The name ``_index``** — reserved in every folder for the index itself.
- **Paths matching ``exclude.patterns``** — opt-in gitignore-style globs in
  ``.wiki/settings.json`` (see :doc:`/configuration`).
- **Paths the enclosing git repository ignores** — no configuration needed:
  what the repo's own rules (its ``.gitignore`` files and
  ``.git/info/exclude``, never a personal ``core.excludesFile``) fence out of
  version control is never walked, adopted, or linked, and a pruned row whose
  target is gitignored is announced as a gitignore skip. A force-tracked file
  matching a rule is still fenced; a wiki whose own root is ignored is exempt
  (see :doc:`/configuration`).

``wiki map``, ``wiki search``, ``wiki match``, ``wiki update``, and
``wiki lint`` all accept an optional positional argument naming a subtree
to work on. It must be a folder, not a page:

.. code-block:: console

   $ wiki map topics
   $ wiki update topics/advanced

An index at every level
-----------------------

Every folder has an ``_index.md`` whose generated link block names the
folder's entries — the parent, each child folder, and each page — with a
description per row (see :doc:`/guide/pages` for the row format). A reader,
human or agent, navigates by consulting an index and opening only the entries
a task needs.

A folder without an ``_index.md`` is **unindexed**: ``wiki update`` creates
the missing index (with a ``desc: ...`` placeholder to fill in), and until
then ``wiki map`` renders the folder as ``name/ (unindexed)`` with no
children, and ``wiki lint`` reports the missing index.

Navigation is index-driven, not filesystem-driven: ``wiki map`` walks the link
blocks, so a file added to disk does not appear until ``wiki update`` links it
in, and a row whose target has vanished renders as ``(broken)`` until pruned.
The map is a view of the indexes — treat drift between map and disk as a
prompt to run ``wiki update``, not as a map bug.

Pages and folders
-----------------

Content starts as leaf pages. A page that grows into several distinct
concerns becomes a folder: a section with its own index, child pages, and room
to keep growing.

To create a folder deliberately, use ``wiki new``: it writes the folder's
``_index.md`` with an authored ``desc:`` and overview prose below the ``***``
delimiter — both required, a blank or ``...`` value is refused — and runs a
scoped update on the parent so the parent's new row carries the description
at once, with no placeholder left to fill in:

.. code-block:: console

   $ wiki new topics/guides --desc "How-to guides." --content "Start here."

The parent folder (``topics/`` here) must already exist and be indexed — each
level carries its own authored index.

To convert a page into a folder — say ``topics/example.md`` into
``topics/example/`` — create the directory, move the page's content into it
(as one or more child pages, with overview prose below the new index's ``***``
delimiter), and run ``wiki update``:

.. code-block:: console

   $ mkdir topics/example
   $ mv topics/example.md topics/example/basics.md
   $ wiki update

``wiki update`` creates the new folder's index, links the children in,
rewrites each moved file's ``name:`` and H1 to match its new path, and prunes
the parent's now-broken row for the vanished page (announcing the removal).
``wiki new`` accepts the folder in this state too — an existing folder of raw
files is its expected shape — so ``wiki new topics/example``, with its
``--desc`` and ``--content``, in place of the bare ``wiki update`` wires the
parent subtree the same way (children linked, the broken row pruned) with
the index's description and overview prose authored up front, leaving no
placeholder to fill in.
Any ``[[topics/example]]`` wikilinks in prose now
name a folder rather than a page, which ``wiki lint`` reports as an issue
naming the ``[[topics/example/_index]]`` form to use instead (a plain rename,
where the old name vanishes, draws a stale-link note — once per target).
Wikilinks in prose are authored by hand, so find them (``wiki match``) and
update them yourself.

Do not leave a page and a folder with the same name side by side
(``topics/example.md`` beside ``topics/example/``): entry resolution is
directory-first, so ``wiki read topics/example`` returns the folder's index
and the page becomes unreachable by its bare name. ``wiki lint`` flags this as
``Shadowed by folder``.

Converting a folder back into a page is the inverse: collapse its content into
a single ``.md`` file, delete the folder, and run ``wiki update``.

The wiki root
-------------

The root of a wiki is **declared** by the presence of ``.wiki/settings.json``.
``wiki init`` writes it, and every mutating command restores a missing one (as
``{}``, meaning all defaults) — the file is load-bearing even when empty,
because it marks where the wiki begins.

Every command except ``install`` and ``init`` resolves the wiki to operate on,
in order:

1. An explicit ``--path``, used as given.
2. The nearest ancestor of the current directory (itself included) holding
   ``.wiki/settings.json``.
3. From the nearest directory in the chain holding ``_index.md`` (the current
   directory itself, or — for a raw, not-yet-indexed folder — its nearest
   indexed ancestor): the topmost ``_index.md`` in that chain. Such an
   **undeclared** tree works, with a notice on stderr that ``wiki update``
   will restore the marker. A raw directory that holds an indexed folder of
   its own is a project directory, not part of an outer tree, and does not
   climb.
4. A ``wiki/`` folder under the current directory, when declared or indexed.

If none match, the command fails, naming what it looked for. See
:doc:`/cli/index` for the ``--path`` option on each command.

Nested wikis are unsupported in every direction: ``wiki init`` refuses to
scaffold inside an existing wiki; a ``--path`` inside an enclosing wiki
resolves upward to that wiki's root with a notice (use the subtree argument
for scoped work), while a path enclosing a declared wiki is refused; two
``.wiki/settings.json`` markers on one ancestor chain are an ambiguous-root
error; and ``wiki update`` refuses to sweep across a nested declared root
(``wiki lint`` completes the sweep and reports the nested root as a hard
issue). An undeclared root is likewise refused when an indexed tree sits
below it behind an unindexed folder — a vendored or nested checkout is its
own wiki, not a subtree, so run the command with ``--path`` at that inner
root or index the folder between them.

The ``.wiki/`` directory
------------------------

Every wiki root carries a ``.wiki/`` directory — the tool's namespace. Its dot
prefix keeps it out of the wiki walk, and nothing under it is ever indexed;
never author content there. Contents:

``settings.json``
   The per-wiki settings file and the declared-root marker. All keys are
   optional and documented at :doc:`/configuration`. ``wiki init`` seeds it
   with the full ``naming`` defaults block so the knobs are discoverable;
   a restored marker is bare ``{}`` — both mean the same defaults, so deleting
   the file drops any custom policy for good.

``cache/``
   Derived state: the word-counts cache and the ``wiki search`` index (see
   below). Safe to delete at any time.

``obsidian/``
   The staged Obsidian configuration template, copied into ``.obsidian/`` by
   ``wiki init`` and ``wiki config``. See :doc:`/guide/obsidian`.

``wiki.py``
   Optional: a hook declaring a custom engine subclass. Because it runs code
   with your privileges, every command refuses to operate on a wiki with an
   untrusted hook until you vet it and run ``wiki trust``. Most wikis have no
   hook and need no trust.

The user-global ``~/.wiki/settings.json`` is a different file with the same
basename: it is the machine-local trust store for hooks, holds no wiki policy,
and never declares your home directory a wiki root. See :doc:`/configuration`.

The cache
---------

``wiki map`` shows word counts — ``(N)`` per page, ``(page/tree)`` per folder.
The counts are derived state, cached in ``.wiki/cache/word_counts.json`` as
one entry per markdown file keyed by root-relative path, each carrying the
file's mtime, size, and word count. Counts cover the body only — everything
below the frontmatter, including the H1 and an index's link block — matching
the region the ``wiki read`` slice options select and ``wiki match`` scans
(an unsliced read also returns the frontmatter).

The cache maintains itself: entries whose mtime or size no longer match are
recomputed lazily, a corrupt cache is discarded and rebuilt, and a failed
cache write never fails a command. The cache directory carries its own
``.gitignore`` (containing ``*``) so it never needs host-repo ignore
configuration, and it can be deleted at any time — the worst case is a full
recompute on the next run.

``wiki search`` keeps a second derived store beside the counts,
``.wiki/cache/search.db``, on the same contract: each search refreshes the
index for added, changed, and removed pages (by mtime and size) before
ranking, a corrupt database is deleted and rebuilt, and a schema change drops
and rebuilds the tables. Deleting the cache directory therefore also costs a
full reindex on the next search. ``wiki map`` and ``wiki search`` recreate
the directory silently; only ``wiki update`` announces the recreation.
