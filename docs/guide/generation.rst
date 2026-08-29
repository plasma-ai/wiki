Generation and Linting
======================

A wiki's index link blocks, frontmatter scaffolding, and path-derived names
are generated surfaces: the tool owns them, and ``wiki update`` regenerates
them from the filesystem whenever they drift. ``wiki lint`` is the read-only
counterpart — it reports every file ``update`` would rewrite and every problem
``update`` cannot fix. Together they form the maintenance loop: edit pages,
run ``wiki update`` to stitch the tree back together, run ``wiki lint`` to
find what still needs a human.

The boundary between generated and authored content — what belongs to the tool
above the ``***`` delimiter versus what you write below it — is described in
:doc:`/guide/pages` and :doc:`/guide/structure`. This page covers the two
commands that maintain that boundary. For the full command reference, see
:doc:`/cli/index`.

What ``wiki update`` regenerates
--------------------------------

``wiki update`` walks the wiki, computes the corrected form of every file, and
rewrites only the files that differ. It owns:

**Index link blocks.**
   Each folder's ``_index.md`` link block is synced with the filesystem: new
   pages, folders, and files gain link rows (seeded with the ``...``
   placeholder description), labels are refreshed (including ``[category]``
   prefixes read from child frontmatter), and rows are sorted — parent link
   first, then categorized entries, then uncategorized entries alphabetically.
   A folder with no ``_index.md`` gets one created, with a placeholder
   description for you to fill in — or create the folder deliberately with
   ``wiki new``, whose required ``--desc``/``--content`` land it
   lint-complete in one pass; its wiring is a scoped update of the parent
   subtree, so pending maintenance there lands in the same run (see
   :doc:`/cli/index`).

**Description propagation.**
   A page's frontmatter ``desc:`` is the source of truth for its link row in
   the parent index. When the index-side description has diverged, update
   overwrites it and warns, naming the page as the place to edit. Editing a
   description directly in a parent ``_index.md`` is therefore futile whenever
   the child page carries a real ``desc:`` — the next update puts the page's
   value back. Whitespace-only differences (wrapping) are tolerated and never
   rewritten. The one exception is the ``...`` placeholder: a child carrying
   it propagates nothing, so the row keeps whatever it has (a description the
   deleted-and-reminted page used to carry, say) and update reports nothing
   pending — the child's own ``Needs desc`` note is the signal, and filling
   the page's ``desc:`` in resumes propagation.

**Frontmatter repair.**
   On every page and index, update refreshes the tool-owned ``name:`` field
   from the file's path, fills a missing or blank ``desc:`` with the ``...``
   placeholder, stamps missing ``created:``/``updated:`` timestamps, removes
   an unset ``title:`` or ``category:`` line (blank or plain ``null``), drops
   stray blank lines, and enforces the canonical field order. Authored values
   — ``title:``, ``desc:``, ``category:``, ``tags:``, ``sources:``, and any
   custom keys — are preserved, and so are the comments on a field the
   repair fills or removes (``desc: # note`` becomes ``desc: ... # note``).
   Values are read the way a strict YAML reader reads them (a value continued
   on indented lines folds, a quoted value decodes, ``null # comment`` unsets
   like ``null``); a block a strict reader rejects still reads through the
   line grammar, as authored.

**Headings.**
   The H1 of every page and index is rewritten to the authored ``title:`` when
   one is set, else to the path-derived name. A page that has no H1 keeps
   none.

**Bare-page adoption.**
   A markdown page with no frontmatter gains a fresh block. An authored H1 is
   preserved by seeding ``title:`` from it; a page with no H1 gains the
   path-derived heading in its body instead. Each adoption is announced.

**Line endings.**
   A file with CRLF line endings is rewritten to LF, even when nothing else
   drifted.

**Formatter-damage repair on indexes.**
   An index mangled by a generic markdown formatter — the ``***`` delimiter
   rewritten to a thematic break, wikilinks backslash-escaped — is repaired in
   place rather than having its link block duplicated. This repair applies to
   index structure only; escaped wikilinks in page prose are a human fix that
   ``wiki lint`` flags.

**Tool housekeeping.**
   A missing ``.wiki/settings.json`` (the declared-root marker) is restored as
   ``{}`` — all defaults; custom settings are never reinvented — and a deleted
   ``.wiki/cache/`` directory is recreated, each with a notice.

``wiki init`` runs the same sweep over any existing tree when it scaffolds a
wiki, and running ``wiki update`` after a git merge regenerates the merged
link rows — see :doc:`/guide/merge-driver`.

Running update
--------------

.. code-block:: console

   $ wiki update
   Created 1 new index (fill in its desc)
   Added 2 new links
   Updated 2 files.

The summary line (``Updated N file(s).`` or ``Nothing to update.``) prints to
stdout; all narration prints to stderr. Update exits 0 after a successful run
— warnings such as skipped names or files do not change the exit code.

.. list-table::
   :header-rows: 1
   :widths: 22 14 64

   * - Argument / option
     - Default
     - Effect
   * - ``name`` (positional)
     - whole wiki
     - Restrict the sweep to a subtree. Must name a folder relative to the
       wiki root; a page name fails with ``Wiki folder not found``.
   * - ``--path``
     - resolved root
     - Wiki root directory. Defaults to the enclosing wiki root (the ancestor
       declaring ``.wiki/settings.json``, else the outermost ``_index.md``
       chain), else ``{cwd}/wiki/``.
   * - ``--check``
     - off
     - Dry run: report the files that would change without writing them.
   * - ``--full``
     - off
     - Print every narration line individually, in emission order.
   * - ``--count``
     - on (the effective default)
     - Print one count line per narration category. Mutually exclusive with
       ``--full``.

One caveat on scoped runs: a category change on the page or index at the
scope's root cannot refresh its label in the *parent* index, because the
parent folder sits outside the scope. A whole-wiki run picks it up.

Broken links
~~~~~~~~~~~~

When an index row's target no longer resolves to an indexed entry, update
**prunes** the row, announcing each removal — the filesystem is the source of
truth, so a deleted target takes its row with it (git carries the history,
and a dangling row would otherwise trip every later merge). Until the sweep
runs, ``wiki map`` renders such rows as ``(broken)`` and ``wiki lint``
reports each one:

.. code-block:: console

   $ wiki update
   Pruned 2 broken links
   Updated 1 file.

A pruned row whose target is still on disk — as a symlink, under an
``exclude.patterns`` glob, or behind the enclosing repo's gitignore fence —
gets a cause line naming the exclusion beside the prune notice, so nobody
hunts for a deleted file that is not deleted.

Checking without writing
~~~~~~~~~~~~~~~~~~~~~~~~

``--check`` computes the same plan but writes nothing, listing each pending
file:

.. code-block:: console

   $ wiki update --check
   Would add 2 new links
   Would update: _index.md
   Would update: topics/_index.md

   2 files would change (run without --check to apply).

Like a formatter's check mode, it **exits 1 when changes are pending** and 0
(``Nothing to update.``) when the wiki is clean — a nonzero exit is not an
error, and a real error (an unresolvable wiki, a bad subtree) exits 2, so a
gate reading 1 as pending drift never mistakes a typo'd path for one.
Pending-action narration (create, adopt, add, prune, overwrite) uses
``Would ...`` wording; the state-report categories — skipped names and files,
malformed frontmatter, truncated indexes — keep their normal wording. A dry
run performs no housekeeping either: it neither restores a missing
``.wiki/settings.json`` nor reports it (the resolver's stderr diagnostic
covers the gap).

What update leaves alone
~~~~~~~~~~~~~~~~~~~~~~~~

Some states are skipped with a notice rather than rewritten, because a rewrite
would destroy authored content or race a concurrent editor:

- **A page whose frontmatter never closes** (opening ``---`` with no closing
  ``---``) is left untouched — update cannot tell frontmatter from body.
- **A frontmatter block that is not a mapping** (valid YAML, but a bare
  sentence or a list between the fences) is left untouched — it has no fields
  to repair, and appending fields under the text would leave a block no
  reader accepts.
- **An emptied or truncated index** (an ``_index.md`` with no closed
  frontmatter) is kept as-is rather than rebuilt; rebuilding would discard
  whatever authored content survives. Restore it from git, or delete it so
  the next update regenerates it from scratch.
- **Entries whose names violate the naming policy** are not linked into their
  parent index (see :doc:`/configuration` for the ``naming`` settings).
- **Symlinked files and directories** are excluded from the walk entirely; an
  index row targeting one is named as a symlink skip rather than a broken
  link when its prune is announced.
- **Paths the enclosing git repository ignores** are excluded from the walk
  the same way ``exclude.patterns`` paths are: what the repo fences out of
  version control is not wiki content, so a driver's stray output is never
  adopted, never gains a minted ``_index.md``, and never draws a parent link
  row. A wiki that is itself gitignored is exempt — the fence never empties
  a deliberately unindexed wiki.
- **Files edited between plan and write** are skipped — writing the staged
  content would silently revert the concurrent edit. The next run converges.

When update refuses to run
~~~~~~~~~~~~~~~~~~~~~~~~~~

These conditions abort the whole sweep (write and ``--check`` alike, exit 2 —
the error code, never ``--check``'s pending-changes 1):

- **Merge conflict markers** in any in-scope file: the plan would read the
  markers as authored content and bake one conflict side into the rewrite.
  The error names every marked file; resolve the conflicts and rerun. A page
  that intentionally demonstrates conflict markers must wrap them in a
  ``no-lint`` region (below). See :doc:`/guide/merge-driver` for how index
  merges are auto-resolved in the first place.
- **A nested declared wiki**: a scope that sits inside, or sweeps across, a
  directory declaring its own ``.wiki/settings.json``. Nested wikis are
  unsupported, unless the nested root sits under an ``exclude.patterns``
  subtree — an excluded nested root drops out of the sweep entirely (see
  :doc:`/configuration`).
- **A legacy ``_config/settings.json`` layout**, refused with a migration
  message.

What ``wiki lint`` checks
-------------------------

``wiki lint`` reads the whole scope, computes the same plan update would, and
reports two kinds of problem — without writing anything:

- **Issues** (stdout): drift that update would rewrite, plus everything update
  *cannot* fix. Any issue makes lint **exit 1** — and nothing else does: a
  command error exits 2, so 1 always means exactly "issues found".
- **Notes** (stderr): soft advisories — unauthored descriptions, stale prose
  links, pending CRLF normalization, an unconfigured merge driver in a fresh
  clone. Notes **never affect the exit code**.

.. code-block:: console

   $ wiki lint
   topics/example.md: Needs desc
   guides/: Missing index
   topics/_index.md: Requires update
       @@ -8,3 +8,5 @@
        [[topics/example|example]]: An example page.
       +
       +[[topics/other|other]]: ...

   2 issues, 1 note.

Notes stream to stderr as the walk encounters them; issues print to stdout
once the walk completes.

Every issue line begins with the root-relative path. The output is prose for
humans; a script must branch on the exit code (0 clean, 1 issues found,
2 error) or read ``wiki lint --json`` — one JSON document on stdout with
every finding severity-tagged and typed (a machine ``kind`` plus per-kind
payload fields beside the rendered ``text``) — never classify findings by
scraping the streams: a stderr note is not a blocking issue.

.. list-table::
   :header-rows: 1
   :widths: 22 14 64

   * - Argument / option
     - Default
     - Effect
   * - ``name`` (positional)
     - whole wiki
     - Restrict the check to a subtree (a folder, as for update).
   * - ``--path``
     - resolved root
     - Wiki root directory (resolved as for update).
   * - ``--full``
     - on (the effective default)
     - Print every issue and note line.
   * - ``--count``
     - off
     - Print only the closing summary (``N issue(s), M note(s).``). Mutually
       exclusive with ``--full``.
   * - ``--json``
     - off
     - Emit one machine-readable JSON document on stdout instead of the prose
       report; same exit codes. Mutually exclusive with ``--full``/``--count``.

Note the opposite defaults: update condenses its narration unless you pass
``--full``, while lint prints every line unless you pass ``--count`` — issues
are lint's product.

Out-of-date files
~~~~~~~~~~~~~~~~~

Any file update would rewrite is reported as ``<path>: Requires update``
followed by an indented unified diff of exactly the change update would make —
the comparison is byte-exact against update's plan, so lint and update can
never disagree about what is pending. A change only to the file's final
newline reports as ``Requires update (final newline differs)``. A bare page
(no frontmatter) is additionally named — ``Bare page (no frontmatter); update
will adopt it`` — so the pending adoption is legible without reading its diff.

The fix for every out-of-date file is the same: run ``wiki update``.

Problems update cannot repair
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The remaining issues need a human (or a different command). Each message and
its meaning:

``Missing index``
   The folder has no ``_index.md``. Update creates it.

``Invalid folder name: <rule>`` / ``Invalid page name: <rule>``
   The name violates the naming policy (see :doc:`/configuration`); update
   skips the entry rather than linking it. Rename the file or folder. An
   invalid root display name reports as ``Invalid wiki name '<name>':
   <rule>`` on the root index.

``Shadowed by folder <name>/``
   A page and a folder share a name; ``wiki read`` resolves directory-first,
   so the page is unreachable by name. Rename one of them.

``Merge conflict markers``
   The file carries ``<<<<<<<``/``>>>>>>>`` lines; update refuses the whole
   sweep until they are resolved. The file's drift diff is suppressed while
   the markers stand.

``Leftover merge repair hint; delete the hint comment line``
   An ``_index.md`` still carries a hint the merge driver planted for the
   hand-resolution (see :doc:`/guide/merge-driver`). Landing inside the
   frontmatter it reads as an authored key that update carries forward, so
   delete the line.

``Malformed frontmatter (no closing ---)``
   A page's frontmatter block never closes; update leaves the file untouched.
   Close the block by hand.

``Invalid YAML frontmatter (line N): <reason>; a strict reader drops the whole block, so quote or rewrite the value``
   A strict YAML reader rejects the block — an unquoted ``': '`` inside a
   one-line value, a value ending in ``:``, a duplicate key, a control
   character, a key that is not a scalar, or a body that is not ``key: value``
   pairs — so Obsidian and any YAML library lose every field, while the wiki
   still reads the block through its line grammar (or, for a body that is not
   a mapping, leaves it untouched). Quote the value, drop the duplicate, or
   rewrite the block.

``Empty or truncated index (no frontmatter); restore it from git or delete it to rebuild``
   An emptied or truncated ``_index.md``; update keeps it as-is. Restore or
   delete it.

``Nested wiki root (declared by .wiki/settings.json); update refuses to sweep across it``
   A subdirectory declares its own wiki root. Remove the nested marker or
   move the tree out.

``Escaped wikilinks: likely formatter damage (keep generic markdown formatters off the wiki; see README)``
   Backslash-escaped ``[[`` brackets — the signature of a generic markdown
   formatter run over the wiki. Update repairs index structure, but escaped
   links in page prose must be fixed by hand.

``Index missing *** delimiter``
   The generated/authored boundary is gone; when a ``---``-style thematic
   break stands in its place, the message names likely formatter damage.
   Update repairs the index.

``Hyphen dangle (line N)`` / ``Wrapped list marker (line N)``
   Hand-wrapping artifacts: a line break inside a hyphenated word, or a
   wrapped continuation line that renders as a phantom list item. Rejoin the
   wrapped line.

``Missing period in desc`` / ``Missing period in [[target|label]]``
   An authored description must end in a period. (A description propagated
   from a child page is exempt here — its drift shows up in the diff
   instead.)

``Unparseable created: stamp '<value>'`` (likewise ``updated:``)
   The timestamp does not parse under the configured ``timestamp.format``.
   Changing the format requires rewriting existing stamps by hand; the
   message says so. A stamp that parses is never judged for freshness — the
   fields are tool-owned.

``Broken link [[target|label]]``
   A generated-index row whose target no longer exists. Run ``wiki update``
   to prune it, or restore the target. A target that still exists as a
   symlink reports as ``Link [[target|label]] targets a symlink; symlinked
   files are not indexed`` instead; one under an ``exclude.patterns`` glob or
   the enclosing repo's gitignore fence is likewise named with its cause.

``Link [[target]] targets a folder, not a page (use [[target/_index]])``
   A prose wikilink naming a folder rather than the folder's index page.
   The link renders but does not resolve when followed; append ``/_index``
   (an anchor suffix rides along, and a target reports once per file).
   Only a folder the walk reaches is flagged — a dot-prefixed, symlinked,
   or ``exclude.patterns`` segment keeps its whole subtree out of the
   index, leaving nothing to name.

``Nested '<!-- start: no-lint -->' (line N)`` / ``Dangling '<!-- start: no-lint -->' (line N)`` / ``Dangling '<!-- end: no-lint -->' (line N)``
   A malformed region directive (below): a second start inside an open
   region, a start never closed (N is the start's line), or an end with no
   open start. A malformed pair suppresses nothing.

``Missing title (author a value)``
   Only under ``titles.required`` (see :doc:`/configuration`): the file's
   ``title:`` is absent, blank, or the seeded ``null`` placeholder.

Soft notes
~~~~~~~~~~

Notes print to stderr and are counted in the summary, but a wiki with only
notes is clean — lint exits 0.

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Note
     - Meaning
   * - ``<path>: Needs desc``
     - The ``desc:`` is still the ``...`` placeholder; author one.
   * - ``<path>: Desc is N chars; keep descs under 500``
     - The description is oversized; length is author judgment, not
       structure.
   * - ``<path>: Empty content``
     - An index's user-content section below ``***`` is empty.
   * - ``<path>: CRLF line endings; update will normalize``
     - The next writing update rewrites the file to LF.
   * - ``<path>: Stale link [[target]]``
     - A ``[[wikilink]]`` in authored prose points at nothing. When a
       folder-relative target (e.g. ``../overview``) resolves to a real
       page, the note suggests the root-relative form: ``(use
       [[canonical]])``. Prose links are soft because pages come and go —
       the generated link block's broken-link check is the hard surface.
       A target notes once per file, however often the prose repeats it.
   * - ``<path>: indexed, but this machine's git ignores it (<source>:<line> '<pattern>'); its generated row ships where the file cannot, so every other clone reds on a broken link``
     - The gitignore fence reads only the repository's own rules (pinned, so
       indexing is identical on every clone), but the named ignore rule —
       typically a personal ``core.excludesFile`` pattern — stops this
       machine's ``git add`` from tracking the file, so its generated row
       would be committed without it. Drop the rule, or keep the path out of
       the index with ``exclude.patterns``. Only emitted inside a git
       repository.
   * - ``.gitattributes maps merge=wiki but merge.wiki.driver is not configured; run `wiki config` to register the driver (index merges fall back to a plain text merge until then)``
     - A fresh clone carries the committed ``.gitattributes`` map but not the
       per-clone ``merge.wiki.driver`` config, so git text-merges
       ``_index.md`` files until ``wiki config`` registers the driver (see
       :doc:`/guide/merge-driver`).
   * - ``Gitignore fence unavailable: `git check-ignore` failed inside the enclosing repository (is git installed?); indexing proceeds unfenced, so paths the repository ignores are adopted``
     - A ``.git`` directory encloses the wiki root but ``git check-ignore``
       could not run (git missing or broken), so the walk proceeds without
       the gitignore fence and adopts paths the repository ignores.
   * - resolver diagnostics (``resolver_notice`` in ``--json``)
     - Emitted by wiki-root resolution before the walk, prefixed with the
       absolute root rather than a root-relative path: an upward resolution
       into an enclosing root (``<path>: inside the wiki at <root>; using
       that root``), a missing settings marker (``.wiki/settings.json
       missing; `wiki update` will restore it``), a declared root missing
       its ``_index.md`` (``restore it from git or run `wiki update` to
       rebuild it``), or an ``_index.md`` extending above the declared root.
       They stream to stderr as resolution runs and join the note count and
       the ``--json`` document as typed rows.

Suppressing positional checks: ``no-lint`` regions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A region directive wraps lines that intentionally contain lint-triggering
text — conflict-marker samples, escaped-bracket examples, deliberately
wrapped lines:

.. code-block:: markdown

   <!-- start: no-lint -->
   A demonstration line with escaped \[\[brackets]] that lint must not flag.
   <!-- end: no-lint -->

Each marker stands alone on its line. The region suppresses the positional
rules — conflict markers, escaped wikilinks, wrap mangles, stale-link notes,
directory-link issues — for the wrapped lines only; file-level checks are
unaffected. Content inside fenced or inline code is already masked, so those
code samples need no region. The link rules — stale-link notes and
directory-link issues — also skip a wikilink inside an HTML comment or an
indented code block, so a link sample in either needs no wrapping; the
escaped-wikilink and wrap-mangle checks do not mask those, so an
escaped-bracket sample or a deliberately wrapped line in a comment or
indented block still does. A nested or dangling marker is itself a hard
issue, and a malformed pair suppresses nothing. Conflict markers in
particular *must* be wrapped: the scan that makes update refuse the sweep
deliberately looks inside code fences (a real conflict can land there), so
only a ``no-lint`` region sanctions them.

Update narration reference
--------------------------

Update's narration streams to stderr. By default each category of event
condenses to one count line; ``--full`` prints every event individually, in
emission order (e.g. ``New link: [[topics/example|example]] in
topics/_index.md``). The condensed lines and what they mean:

.. list-table::
   :header-rows: 1
   :widths: 52 48

   * - Count line (default narration)
     - What happened
   * - ``Created N new indexes (fill in their descs)``
     - Missing ``_index.md`` files were created, each with a placeholder
       description to author.
   * - ``Adopted N bare pages (frontmatter added)``
     - Frontmatterless pages gained a fresh block; an authored H1 is
       preserved as a seeded ``title:``.
   * - ``Added N new links``
     - New entries were linked into their parent indexes with ``...``
       placeholder descriptions.
   * - ``Pruned N broken links``
     - Rows whose targets no longer resolve to an indexed entry were
       removed.
   * - ``Overwrote N link descs (page frontmatter descs win)``
     - Diverged index-side descriptions were replaced by the child pages'
       ``desc:`` values; edit the page, not the index.
   * - ``Skipped N invalid names``
     - Entries violating the naming policy were left unlinked.
   * - ``Skipped N concurrently-edited files (re-run `wiki update`)``
     - Files changed while update was planning; re-run to converge.
   * - ``N pages with malformed frontmatter``
     - Left untouched; the per-file notice names the reason — close an
       unclosed block by hand, or rewrite a body that is not ``key: value``
       pairs.
   * - ``N empty or truncated indexes (restore from git or delete to rebuild)``
     - Left untouched; restore or delete.

Under ``--check`` the pending-action categories read ``Would create ...``,
``Would add ...``, ``Would prune ...``, and so on; the state-report categories
(skipped names and files, malformed frontmatter, truncated indexes) keep
their normal wording. A few notices have no category and always print
verbatim, even in condensed mode:

- ``Link targets a symlink: [[target|label]] in <path> (symlinked files are
  not indexed)`` — likewise the excluded-path and gitignored-path cause
  lines beside a prune
- ``Restored missing .wiki/settings.json ({} -- all defaults)``
- ``Recreated .wiki/cache/ (derived counts cache)``
- ``<path>: indexed, but this machine's git ignores it (<source>:<line>
  '<pattern>'); its generated row ships where the file cannot, so every other
  clone reds on a broken link``
- ``Gitignore fence unavailable: `git check-ignore` failed inside the
  enclosing repository (is git installed?); indexing proceeds unfenced, so
  paths the repository ignores are adopted``

Idempotency and safety of re-running
------------------------------------

Both commands are safe to run at any time, as often as you like:

- **Update converges.** It writes only files whose corrected content differs
  byte-for-byte from disk; an immediate second run reports ``Nothing to
  update.`` The only exception is a concurrently-edited file, which is
  skipped with a notice — the next run picks it up.
- **Timestamps never churn.** ``updated:`` is re-stamped only on a real
  content change; a would-be timestamp-only difference never triggers a
  write, and a rewrite forced solely to normalize CRLF endings does not
  re-stamp. Re-running update on a clean wiki changes nothing, including the
  stamps.
- **Authored content is never regenerated away.** User content below ``***``,
  authored frontmatter values, and unrecognized frontmatter keys are
  preserved; damaged files (unclosed frontmatter, truncated indexes) are
  skipped with a notice rather than rebuilt. The two authored surfaces
  update does remove or overwrite — a broken row's description goes with its
  pruned row (git carries it), and a diverged index-side link description is
  replaced — are each announced, the latter naming the page whose ``desc:``
  is the place to edit.
- **Lint writes nothing**, and ``update --check`` writes nothing — not even
  the settings-marker restore.
- **The cache is disposable.** ``.wiki/cache/`` holds derived data only — the
  word-count cache and the ``wiki search`` index (``search.db``). Delete it
  freely: update recreates the directory and its counts (with a notice), and
  the next ``wiki search`` rebuilds the index.
- **A restored ``.wiki/settings.json`` is empty.** Update restores the
  missing marker as ``{}`` — all defaults. If the file held custom settings,
  restore it from version control instead of relying on the marker restore;
  see :doc:`/configuration`.
