Obsidian Integration
====================

A wiki doubles as an Obsidian vault. Index link rows and cross-references use
``[[wikilink]]`` syntax, every section is a plain directory of markdown notes,
and the ``.obsidian/`` vault configuration lives at the wiki root — so the
wiki root opens directly as a vault. The ``wiki config`` command materializes
that vault configuration, installing a curated plugin setup that makes the
wiki's naming scheme render properly inside Obsidian.

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
   ``.wiki/obsidian/`` is copied into ``.obsidian/`` at the wiki root, and
   the pinned plugin code is downloaded (the merge rules are below).
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

``wiki config`` exits 0 even when a plugin download fails: download failures
(no network connection, a changed upstream asset, offline mode) are warnings
on stderr and never affect the exit code. Re-run ``wiki config`` online to
finish setup. Setting the ``OFFLINE_MODE`` environment variable to ``true``
skips the downloads outright with the same re-run warning; any value other
than ``true`` or ``false`` (case-insensitive) is rejected before anything is
written.

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
and each ``_index.md`` displays as its folder's name.

The plugin's settings are copied from the staged configuration, but its code
is downloaded from the pinned upstream GitHub release at setup time — the
plugin is GPL-3.0 licensed, so ``wiki`` never redistributes it. Every
downloaded asset is verified against a sha256 digest pinned in the package
before anything is installed; a changed upstream asset is refused with a
warning rather than installed.

One step cannot be automated: Obsidian gates community plugins behind
Restricted Mode. After ``wiki init`` or ``wiki config``, the CLI prints the
reminder (on stderr, only when attached to a terminal):

.. code-block:: text

   In Obsidian: Settings -> Community plugins -> turn off Restricted Mode, then enable Front Matter Title if needed.

How the vault maps onto the wiki
--------------------------------

Open the wiki root as a vault. Every markdown page is a note, every folder is
a section, and each folder's ``_index.md`` is its section index — with the
Front Matter Title plugin active, indexes display as their folder's name and
pages as their leaf name. The generated link rows in every index are ordinary
wikilinks: click through them to navigate the tree, and the graph view shows
the wiki's structure because the index links *are* its structure.

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
- Keep markdown-formatting plugins away from the wiki: a formatter that
  rewrites ``***`` into ``---`` or backslash-escapes ``[[`` brackets corrupts
  the generated region. ``wiki lint`` names these damage signatures when they
  appear.
