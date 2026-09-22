Obsidian Integration
====================

A wiki doubles as an Obsidian vault. Index link rows and cross-references use
``[[wikilink]]`` syntax, every section is a plain directory of markdown notes,
and the ``.obsidian/`` vault configuration lives at the wiki root — so the
wiki root opens directly as a vault. The ``wiki config`` command materializes
that vault configuration, installing a curated plugin setup that makes the
wiki's naming scheme render properly inside Obsidian and makes Obsidian read
wikilinks as ``wiki lint`` does, from the wiki root.

``wiki init`` runs the same setup on a fresh wiki, so a wiki you just
scaffolded needs nothing more. Run ``wiki config`` when you clone an existing
wiki, adopt a tree that predates the integration, or want to refresh the
installed configuration.

The ``wiki config`` command
---------------------------

.. code-block:: console

   $ wiki config
   Updated Obsidian config.

``wiki config`` does the following:

1. **Seeds the staged template.** A missing ``.wiki/obsidian/`` directory is
   created from the stock template bundled with the package. An existing
   ``.wiki/obsidian/`` is left untouched.
2. **Installs the vault configuration.** The staged configuration under
   ``.wiki/obsidian/`` is copied into ``.obsidian/`` at the wiki root, the
   pinned Front Matter Title code is downloaded, and the bundled Wiki Root
   Links plugin is copied from the package and enabled (the merge rules are
   below).
3. **Restores the settings marker.** A missing ``.wiki/settings.json`` is
   restored as ``{}`` (all defaults) with a notice on stderr — the file
   declares the wiki root; see :doc:`/configuration`.
4. **Wires the git merge driver.** The ``merge.wiki`` driver is registered in
   the enclosing repository's local git config and the ``**/_index.md`` glob
   is written to ``.gitattributes``; see :doc:`/guide/merge-driver`.

Options:

``--path <dir>``
   Wiki root directory. Defaults to the enclosing wiki root (the ancestor
   declaring ``.wiki/settings.json``, else the outermost ``_index.md``
   chain), else ``{cwd}/wiki/``.

``wiki config`` exits 0 even when the Front Matter Title download fails:
download failures (no network connection, a changed upstream asset, offline
mode) are warnings on stderr and never affect the exit code. Re-run
``wiki config`` online to finish setup. Setting the ``OFFLINE_MODE``
environment variable to ``true`` skips that download outright with the same
re-run warning — the bundled Wiki Root Links plugin is copied from the
package either way and needs no network; any value other than ``true`` or
``false`` (case-insensitive) is rejected before anything is written.

What gets installed
-------------------

Staged configuration: ``.wiki/obsidian/``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The staged configuration lives in the wiki tree and is committed with it, so
the wiki carries its own Obsidian setup and every clone materializes the same
vault. The stock template contains:

- ``community-plugins.json`` — enables the Front Matter Title plugin.
- ``plugins/obsidian-front-matter-title-plugin/data.json`` — curated settings
  for it (described below).

The staged directory is yours to extend: on the next ``wiki config`` run,
any plugin directory you add under ``.wiki/obsidian/plugins/`` is copied
into the vault, and any top-level ``.json`` file is created or merged.

Vault configuration: ``.obsidian/``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``wiki config`` materializes ``.obsidian/`` at the wiki root. It is
per-machine state, not part of the wiki: dot-directories are never indexed,
so ``.obsidian/`` (like ``.wiki/`` and ``.git/``) stays out of the page tree.

The install is safe on a vault you already use:

- Each staged plugin directory is copied over the matching
  ``.obsidian/plugins/`` directory. Other installed plugins are untouched.
- Each staged top-level ``.json`` file (like ``community-plugins.json``) is
  created when absent; otherwise it is merged — arrays are union-merged
  (staged items appended when missing) and objects deep-merged with the
  staged side winning, so your own enabled plugins and settings survive.
  Malformed JSON in an existing ``.obsidian/*.json`` file fails the command
  with an error naming the file.

The Front Matter Title plugin
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Entry names are path-derived and live in each note's ``name:`` frontmatter
(see :doc:`/guide/pages`), and every folder's index is a file literally named
``_index.md`` — neither renders usefully as a raw filename. The integration
installs the `Front Matter Title
<https://github.com/snezhig/obsidian-front-matter-title>`_ plugin by Snezhig,
configured to display each note's ``name:`` frontmatter as its title
throughout Obsidian: the file explorer (including sorting), the graph,
bookmarks, search, link suggestions, tab titles, the inline title, and the
window frame. A ``replace`` processor strips any path prefix from the
displayed name, so a page named ``topics/example`` displays as ``example``
and each ``_index.md`` displays as its folder's name. The plugin reads
``name:`` with Obsidian's own YAML parser, so a page whose frontmatter a
strict reader rejects (an unquoted ``': '`` inside a value, say) displays its
raw filename instead; ``wiki lint`` reports such a block as an
``invalid_yaml`` issue.

The plugin's settings are copied from the staged configuration, but its code
is downloaded from the pinned upstream GitHub release at setup time — the
plugin is GPL-3.0 licensed, so ``wiki`` never redistributes it. Every
downloaded asset is verified against a sha256 digest pinned in the package
before anything is installed; a changed upstream asset is refused with a
warning rather than installed.

The Wiki Root Links plugin
~~~~~~~~~~~~~~~~~~~~~~~~~~

Every wikilink target is read from the wiki root: a prefix-free target must
name something inside the wiki, and a target carrying a ``.`` or ``..``
segment must leave it, reaching a file or another wiki's page only under a
``links.external`` folder, whose entry is the link's prefix (see
:doc:`/configuration`). Stock Obsidian reads a ``./`` or ``../`` link from
the note's folder instead, so the integration installs the bundled Wiki Root
Links plugin, which makes Obsidian read links as ``wiki lint`` does. It
wraps Obsidian's link resolver so that a target carrying a ``.`` or ``..``
segment never resolves to a note in the vault — the graph, backlinks, and
reading view show such a link unresolved — and it intercepts a follow of
such a link (a click, the follow-link hotkey, a graph node) so Obsidian
never creates the missing target as folders outside the vault. On a follow,
when the target is a markdown file inside a vault Obsidian registers on this
machine — a sibling wiki opened as its own vault — the plugin opens it there
through an ``obsidian://open`` URI, switching to that vault's window;
otherwise (the vault unregistered, the target a folder or a non-markdown
file, or nothing at the path) it shows a notice naming the root-relative
path, with `` (not found)`` appended when nothing is there, and opens
nothing. That URI is the only thing the plugin ever dispatches: it never
hands a filesystem path to the operating system, so a link naming a script
or an application is never run, opened, or revealed in the file manager. It
reads no wiki settings — the ``links.external`` allowlist stays a lint rule.

The plugin is bundled with the package under the same Apache-2.0 licence:
``wiki init`` and ``wiki config`` copy it into
``.obsidian/plugins/wiki-root-links/`` and enable it in
``community-plugins.json`` on every run, with no download and no digest,
offline or not, and never write it into the staged ``.wiki/obsidian/``. It
is desktop only — the phone build has no filesystem access it could use, so
stock behaviour applies there. The methods it wraps are Obsidian internals,
not a public API: when one is unavailable after an Obsidian update, the
plugin says so in a notice at load and installs the layers it can, and on
any error it falls back to stock behaviour.

One step cannot be automated: Obsidian gates community plugins behind
Restricted Mode. When ``wiki init`` or ``wiki config`` completes with no
download warnings, the CLI prints the reminder (on stderr, only when attached
to a terminal; ``wiki init --quiet`` suppresses it). A skipped or failed
plugin download prints its warning in place of the reminder — re-run
``wiki config`` online to finish setup and see it:

.. code-block:: text

   In Obsidian: Settings -> Community plugins -> turn off Restricted Mode, then enable Front Matter Title and Wiki Root Links if needed.

How the vault maps onto the wiki
--------------------------------

Open the wiki root as a vault — not a folder above it: Obsidian resolves a
prefix-free link from the vault root, and the Wiki Root Links plugin joins a
``./`` or ``../`` target onto it, so both agree with ``wiki lint`` only when
the vault root is the wiki root. Every markdown page is a note, every folder
is a section, and each folder's ``_index.md`` is its section index — with
the Front Matter Title plugin active, indexes display as their folder's name
and pages as their leaf name. The generated link rows in every index are
ordinary wikilinks: click through them to navigate the tree, and the graph
view shows the wiki's structure because the index links *are* its structure.

Day-to-day use
--------------

- Author page bodies, frontmatter fields like ``title:`` and ``desc:``, and
  index prose below the ``***`` delimiter in Obsidian. The region above the
  delimiter — frontmatter ``name:``/``created:``/``updated:``, the H1, and
  the link rows — is tool-owned and regenerated by ``wiki update``; see
  :doc:`/guide/generation`.
- After creating, moving, or deleting notes, run ``wiki update`` to stitch
  the changes into the indexes, and ``wiki lint`` to check wiki health.
- ``wiki update`` never linkifies prose: author ``[[wikilink]]``
  cross-references in page bodies by hand (Obsidian's link suggestions help
  here).
- Every wikilink target is read from the wiki root, by ``wiki lint`` and,
  with the Wiki Root Links plugin enabled, by Obsidian: a prefix-free target
  (``[[topics/example]]``) names a note in the vault, and a ``./`` or ``../``
  target leaves the wiki for a file, folder, or another wiki's page under a
  ``links.external`` folder (see :doc:`/configuration`), so Obsidian shows it
  unresolved and the plugin turns a follow into a notice, or opens a markdown
  target in a sibling wiki's own registered vault. Stock Obsidian — the
  plugin disabled, a phone, a clone that has not run ``wiki config`` — reads
  a ``./`` or ``../`` link from the note's folder instead, and ``wiki lint``
  says nothing about it, since the link is correct: a correct ``[[../tools]]``
  from ``sub/page.md`` beside an in-wiki ``tools.md`` (a wiki at ``wiki/``
  with a ``tools/`` folder beside it) opens that in-wiki note there, and
  renaming that note rewrites the link into an in-wiki link; a click on an
  unresolved ``./`` or ``../`` link there creates the missing target at that
  path, as folders outside the vault, through a known bug — do not click it.
  Enable the plugin on every machine that opens the vault.
- Link an outside file, never embed it: ``![[../img.png]]`` shows unresolved,
  since Obsidian cannot render a file outside the vault; write
  ``[[../img.png]]`` instead.
- Keep markdown-formatting plugins away from the wiki: a formatter that
  rewrites ``***`` into ``---`` or backslash-escapes ``[[`` brackets corrupts
  the generated region. ``wiki lint`` names these damage signatures when they
  appear.
