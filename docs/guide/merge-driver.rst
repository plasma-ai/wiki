Git Merge Driver
================

Every branch that touches a wiki runs ``wiki update``, and every run rewrites
generated surfaces in ``_index.md`` files — link rows, ``updated:`` stamps,
frontmatter ordering (see :doc:`/guide/generation`). Under git's default
text merge, two branches would conflict on that churn constantly. The wiki
merge driver auto-resolves the generated region of ``_index.md`` merges and
settles the stamps of every page it merges, so parallel edits conflict only
on authored content.

Wiring
------

``wiki init`` and ``wiki config`` both wire the driver into the repository
that holds the wiki. Three pieces work together. The repository root's
``.gitattributes`` maps index files to the driver:

.. code-block:: text

   # Wiki index merge driver
   **/_index.md merge=wiki

A ``.gitattributes`` in the wiki root itself maps the wiki's pages, at any
depth below it:

.. code-block:: text

   # Wiki page merge driver
   *.md merge=wiki

Markdown outside the wiki is never mapped, so it keeps git's own merge and
any rule the repository sets for it (a ``merge=union`` changelog, say). A
wiki at the repository root has one file for both, the index block first.
And the repository's *local* git config defines the driver:

.. code-block:: ini

   [merge "wiki"]
       name = wiki merge (auto-resolve generated sections)
       driver = if command -v wiki >/dev/null 2>&1; then wiki _merge %O %A %B %L %P; else git merge-file -L ours -L base -L theirs --marker-size=%L %A %O %B; fi

The ``.gitattributes`` lines are written to the working tree only — you
stage and commit them yourself; the tool never touches the git index. Each
block is appended once, after the file's last line and in the file's own
line ending, when its mapping line is missing; existing lines are never
rewritten. A file with uncommitted changes of your own — a new, untracked
one included — is left alone (the driver config is still registered; the
lines land on the next clean run).

**The driver is per-clone.** The committed ``.gitattributes`` only names the
driver; its definition lives in each clone's local git config. Every
contributor therefore runs ``wiki config`` once after cloning — until then,
that clone's ``_index.md`` and page merges silently fall back to git's
default text merge. The driver string invokes the ``wiki`` command itself
rather than an absolute path into a virtualenv (which would silently break
when the environment is rebuilt or moved), so the CLI must be on ``PATH``
when git runs the merge — an isolated install via ``pipx`` or ``uv tool``
takes care of that. When it is not, the driver runs git's own text merge
instead, its conflicts marked as the wiki's are, so a merge without the CLI
still shows both sides. An external driver never sees ``git merge -X``
options, so ``-X ours`` and ``-X theirs`` do not apply to the files it
merges.

How a merge resolves
--------------------

The driver splits each side of the merge — ours, base, and theirs — at the
``***`` delimiter and treats the regions differently:

Frontmatter
   The tool-owned keys ``name:`` and ``updated:`` are normalized to the
   current branch's lines before merging, so their churn never conflicts; a
   side that lacks one of them gains the current branch's copy, so the other
   side adding the key is no change to merge. When the base index carries no
   ``created:`` — an add/add merge (both branches created the index
   independently), a hand-written or imported index — ``created:`` joins the
   normalized keys: both sides seeded the stamp from their own ``wiki update``
   runs, so it is churn, not authorship. Before normalizing, the driver gives
   the current branch the later of the two sides' ``updated:`` stamps and,
   for a normalized ``created:``, the earlier, both compared as instants
   under the wiki's timestamp policy (``timestamp.timezone``/
   ``timestamp.format`` in ``settings.json``). A stamp either side spells in a form the policy does
   not parse, or a wiki whose hook or settings cannot load, leaves the
   current branch's. The remaining, authored keys —
   ``title:``, ``desc:``, ``category:``, ``tags:``, ``sources:``,
   ``created:`` (ordinarily), and any custom keys — get a normal three-way
   merge that can conflict.

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
   over — desc continuations included. A row both sides carry keeps the
   current branch's text, unless only the other side changed it against the
   base — an authored desc on a row ``wiki update`` does not regenerate, such
   as an asset's — in which case the other side's text lands. A row the base
   had that one side deleted and the other left as it was stays deleted, as
   a three-way merge would leave it. A row only one side added rides over,
   and an edit only one side made lands; when both sides edit the same row,
   the current branch's text wins.

   A row a formatter escaped (``\[\[``) reads as ``wiki update`` reads it,
   with the escapes undone, so a side whose link rows carry that damage
   merges like any other: an escaped row and the other side's copy of it
   are one entry, an escape alone is no edit, and a row the driver brings
   over from the other side lands with its row line repaired. A line in the
   damage shape that still reads as no row — an escaped desc line that opens
   on a wikilink — says nothing about what that side deleted, so the driver
   keeps every row that side dropped, and the next ``wiki update`` prunes
   any whose page or asset is gone.

   When the whole file merges cleanly, the driver then rewrites the H1 from
   the merged ``title:`` (else ``name:``) and puts the rows in the order
   ``wiki update`` writes them, sorting with the wiki's own class — a
   trusted ``.wiki/wiki.py`` hook's ``category_order`` included — and
   rendering them as update does. It does so only when that class leaves
   the current branch's own index as written: an index another class wrote
   (an embedder's update with its own category order) stays as merged, as
   does one whose hook cannot load or run — the H1 the current branch's, the
   other side's rows below its own. A merge with two rows for one target
   stays as merged too, since only ``wiki update``, which reads the
   filesystem, can choose between them. The check
   sees a class's category order only where the current branch's rows span
   two or more categories, so a merge that brings a second category into an
   index with fewer is sorted in this class's order even when an embedder's
   update orders categories its own way. A clean merge in which each side
   only added, deleted, or retitled entries is then current as merged.

Below ``***``
   Authored index prose gets a normal three-way merge that can conflict, like
   any other file.

Every other page the wiki manages gets a normal three-way text merge, after
the driver settles its stamps on both sides: ``updated:`` takes the later of
the two sides' stamps, and over a base with no ``created:`` (an add/add
merge) ``created:`` the earlier, compared as instants under the same policy,
so both sides carry the same stamp lines. A page whose sides differ only in
their stamps — each branch edited its own part and ran ``wiki update`` —
merges clean; one with a genuine conflict keeps it, with the stamps already
settled. A stamp either side spells in a form the policy does not parse
leaves both sides' stamps as they are. A Markdown file inside the wiki that
the wiki does not manage — one ``exclude.patterns`` or the repository's
gitignore keeps out of the index, or one under a dot folder — gets a plain
text merge, its stamps untouched, as does every page when the wiki's hook or
settings cannot load. Markdown outside the wiki never reaches the driver.

A merge of two branches that each added or deleted pages, ran
``wiki update``, and edited different prose therefore resolves cleanly and
checks clean as merged — the competing link rows and timestamps that git's
text merge would trip over are resolved automatically, and only genuinely
concurrent edits to the same authored content surface as conflicts.

Residual conflicts
------------------

Authored frontmatter keys, index prose, and page text still conflict when
both branches change the same thing. A residual conflict looks like any other
git conflict, except the markers are labeled ``ours``/``theirs`` rather than
with branch names — here both branches reworded the index's ``desc:``:

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
   resolving. A side whose authored prose holds its own ``***`` thematic
   break cannot be told apart this way: the driver takes the first ``***``
   as the delimiter, so restore the delimiter line before merging.

After the merge
---------------

Only the ``.gitattributes`` map travels with the repository — the
``merge.wiki.driver`` config is local, per clone — so a fresh clone carries
the map alone and git falls back to a plain text merge of ``_index.md``
files and pages, silently, at its first merge. ``wiki lint`` notes the gap
(soft, and a typed row in ``lint --json``) until ``wiki config`` registers
the driver.

Run ``wiki update --check`` after a merge, and ``wiki update`` when it
reports changes. Once the merge is committed, a clean merge in which each
side only added, deleted, or retitled entries checks clean — the driver
already set its H1 and row order and kept each side's deletions — but some
clean merges still leave rows to regenerate, such as: a row one side changed
and the other deleted is kept, pointing at nothing; when both sides change
the same row, the current branch's text wins; a merge that changes the
hook's ``category_order`` sorts by the order the current branch had; a merge
with two rows for one target, or a line in the escape-damage shape, is left
for the update to resolve. Resolving conflicts by hand can leave the H1 or
rows to regenerate too. Before the merge is committed — with
``--no-commit``, ``--squash``, or a conflict elsewhere — a file the driver
merged keeping the current branch's later ``updated:`` differs from HEAD
under HEAD's stamp, so update restamps it as a hand edit (see
:doc:`/guide/generation`). The update regenerates the link blocks from the
merged filesystem: the H1 follows the title, rows sort into place, any
whose target is gone are pruned, and labels and descriptions refresh.

Plumbing: the hidden ``_merge`` command
---------------------------------------

The ``wiki _merge`` command that git invokes is plumbing, not user surface —
it is hidden from ``--help`` and there is never a reason to run it by hand.
Git hands it the base, ours, and theirs versions, the conflict-marker size,
and the repo-relative pathname of the merging file. The command dispatches on
the pathname: an ``_index.md`` below a declared wiki root (an ancestor
holding ``.wiki/settings.json``) gets the region-aware index merge described
above, a page the wiki manages gets its stamps settled and a three-way text
merge, and every other file — including an ``_index.md`` outside any wiki,
such as a static-site generator's content page — gets a plain three-way text
merge, its conflicts labeled ``ours``/``theirs``. The exit code passes
through to git: nonzero means conflicts were left in the file. After a clean
index merge the command sets the H1 and the row order with the wiki's class,
loaded as every command loads it — except that a hook it cannot load or run,
or an index that class did not write, skips that step rather than failing
the merge. The rewrite replaces the file whole: a write that fails leaves it
as merged, its exit code intact.

The index dispatch is per-file and policy-free — it never reads
``exclude.patterns`` (a malformed ``exclude`` block must not fail a git
merge); a page's judgment reads the wiki's settings, and one it cannot read
gives the plain text merge rather than failing. An orphaned ``_index.md``
left inside an excluded subtree is still index-structured bytes below a
declared root, so it keeps getting the field-aware merge; delete orphaned
indexes when excluding an already-indexed subtree (see the ``exclude``
section in :doc:`/configuration`).
