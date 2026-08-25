Git Merge Driver
================

Every branch that touches a wiki runs ``wiki update``, and every run rewrites
generated surfaces in ``_index.md`` files — link rows, ``updated:`` stamps,
frontmatter ordering (see :doc:`/guide/generation`). Under git's default
text merge, two branches would conflict on that churn constantly. The wiki
merge driver auto-resolves the generated region of ``_index.md`` merges, so
parallel edits conflict only on authored content.

Wiring
------

``wiki init`` and ``wiki config`` both wire the driver into the repository
that holds the wiki. Two pieces work together. The repository's
``.gitattributes`` maps index files to the driver:

.. code-block:: text

   # Wiki index merge driver
   **/_index.md merge=wiki

And the repository's *local* git config defines it:

.. code-block:: ini

   [merge "wiki"]
       name = wiki merge (auto-resolve generated sections)
       driver = wiki _merge %O %A %B %L %P

The ``.gitattributes`` line is written to the working tree only — you stage
and commit it yourself; the tool never touches the git index. The write is
skipped while ``.gitattributes`` has uncommitted changes of your own (the
driver config is still registered; the attribute line lands on the next clean
run).

**The driver is per-clone.** The committed ``.gitattributes`` only names the
driver; its definition lives in each clone's local git config. Every
contributor therefore runs ``wiki config`` once after cloning — until then,
that clone's ``_index.md`` merges silently fall back to git's default text
merge. The driver string invokes the ``wiki`` command itself rather than an
absolute path into a virtualenv (which would silently break when the
environment is rebuilt or moved), so the CLI must be on ``PATH`` when git
runs the merge — an isolated install via ``pipx`` or ``uv tool`` takes care
of that.

How a merge resolves
--------------------

The driver splits each side of the merge — ours, base, and theirs — at the
``***`` delimiter and treats the regions differently:

Frontmatter
   The tool-owned keys ``name:`` and ``updated:`` are normalized to the
   current branch's lines before merging, so their churn never conflicts. On
   an add/add merge (both branches created the index independently),
   ``created:`` joins the normalized keys — both sides seeded the stamp from
   their own ``wiki update`` runs, so it is churn, not authorship. The
   remaining, authored keys — ``title:``, ``desc:``, ``category:``,
   ``tags:``, ``sources:``, ``created:`` (ordinarily), and any custom keys —
   get a normal three-way merge that can conflict.

   A side whose frontmatter block cannot be detected at all — a formatter
   mangled or left unclosed the ``---`` delimiters of a block the base had —
   is treated as unchanged from base for the whole region above ``***``,
   frontmatter and link block alike, never as a deletion of the block. The
   other side's frontmatter survives, the damaged side's authored keys and
   link rows do not enter the merge, and its prose below ``***`` still merges
   normally. No conflict or hint is raised, so if those edits matter, repair
   the ``---`` lines by hand on that branch (``wiki update`` skips such an
   index with a notice rather than rebuilding it) and redo the merge.

Link block
   The H1 heading and the generated link rows, up to and including the
   ``***`` line, resolve to the union of both sides' rows: the current
   branch's layout wins, and each row present only on the other side rides
   over — desc continuations included — appended above the closing ``***``.
   No row is silently dropped; ``wiki update`` owns this region, and the
   post-merge run re-sorts the block and prunes whatever carried rows have
   no target on the merged filesystem.

Below ``***``
   Authored index prose gets a normal three-way merge that can conflict, like
   any other file.

A merge of two branches that each added pages, ran ``wiki update``, and
edited different prose therefore resolves cleanly — the competing link rows
and timestamps that git's text merge would trip over are resolved
automatically, and only genuinely concurrent edits to the same authored
content surface as conflicts.

Residual conflicts
------------------

Authored frontmatter keys and index prose still conflict when both branches
change the same thing. A residual conflict looks like any other git conflict,
except the markers are labeled ``ours``/``theirs`` rather than with branch
names — here both branches reworded the index's ``desc:``:

.. code-block:: text

   ---
   name: topics
   <<<<<<< ours
   desc: Topic overview pages.
   =======
   desc: Topic reference pages.
   >>>>>>> theirs
   tags: []
   sources: []
   created: 2026-01-01T00:00:00Z
   updated: 2026-01-01T00:00:00Z
   ---

   # topics

   [[topics/example|example]]: An example page.

   ***

Resolve it as usual: pick or combine the sides and delete the markers. Note
that ``wiki update`` refuses to run while any in-scope file still carries
conflict markers, and ``wiki lint`` reports each marked file as an issue
(suppressing its drift diff), so resolution comes first either way.

Some cases get an HTML-comment hint planted above the first conflict marker;
each hint says to delete its own line when resolving. A forgotten one is
merge debris, not a harmless comment — planted inside the frontmatter (the
usual spot, since the ``updated:`` stamps are the first lines to differ) it
parses as an authored key that every later rewrite carries forward — so
``wiki lint`` reports a leftover hint as an issue until the line goes:

Add/add body conflict
   Sibling branches authored the same new directory's index body
   concurrently. The hint states the convention that avoids this: leave
   new-directory index bodies empty until after the merge wave.

Lost delimiter
   A side that lost the ``***`` delimiter the base had — typically formatter
   damage, since generic markdown formatters rewrite ``***`` to ``---`` —
   cannot be split into regions at all, so its generated bytes and authored
   edits are indistinguishable. The driver refuses to guess and emits a
   whole-file conflict, with a hint to restore the ``***`` line
   (``wiki update`` repairs it), redo the merge, and delete the hint when
   resolving.

After the merge
---------------

Only the ``.gitattributes`` map travels with the repository — the
``merge.wiki.driver`` config is local, per clone — so a fresh clone carries
the map alone and git falls back to a plain text merge of ``_index.md``
files, silently, at its first merge. ``wiki lint`` notes the gap
(soft, and a typed row in ``lint --json``) until ``wiki config`` registers
the driver.

Run ``wiki update`` after every merge. It regenerates the link blocks from
the merged filesystem: carried-over rows sort into place (and any whose
target a branch deleted are pruned), labels and descriptions refresh, and a
merged-in ``title:`` finally shows in the H1 — during the merge the H1 rides
the current branch's link-block layout, so it lags the merged frontmatter
until this update.

Plumbing: the hidden ``_merge`` command
---------------------------------------

The ``wiki _merge`` command that git invokes is plumbing, not user surface —
it is hidden from ``--help`` and there is never a reason to run it by hand.
Git hands it the base, ours, and theirs versions, the conflict-marker size,
and the repo-relative pathname of the merging file. The command dispatches on
the pathname: an ``_index.md`` below a declared wiki root (an ancestor
holding ``.wiki/settings.json``) gets the region-aware index merge described
above, and every other file — including an ``_index.md`` outside any wiki,
such as a static-site generator's content page — gets git's default three-way
text merge. The exit code passes through to git: nonzero means conflicts were
left in the file.

The dispatch is per-file and policy-free — it never reads
``exclude.patterns`` (a malformed ``exclude`` block must not fail a git
merge). An orphaned ``_index.md`` left inside an excluded subtree is still
index-structured bytes below a declared root, so it keeps getting the
field-aware merge; delete orphaned indexes when excluding an already-indexed
subtree (see the ``exclude`` section in :doc:`/configuration`).
