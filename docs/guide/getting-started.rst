Getting Started
===============

A wiki is a folder tree of markdown pages, indexed at every level by
``_index.md`` files. Each folder is a section, each markdown file is an entry,
and the ``wiki`` tool keeps the index links, frontmatter, and headings in sync
with the filesystem — you write pages; the tool wires them together. This page
walks through a complete first session: installing the tool, initializing a
wiki, writing the first pages, and running the update/lint loop that keeps
everything consistent. The commands run top to bottom in an empty project.

Installation
------------

The tool ships on PyPI as the ``plasma-wiki`` distribution and installs a
single console command named ``wiki``. It requires Python 3.11 or newer.

.. code-block:: console

   $ pip install plasma-wiki

``wiki --version`` prints the installed package version, confirming the
``wiki`` command is on ``PATH``.

To keep the tool in an isolated environment, use ``pipx install plasma-wiki``
or ``uv tool install plasma-wiki`` instead — the resulting ``wiki`` command is
identical.

Installing the agent skill
~~~~~~~~~~~~~~~~~~~~~~~~~~

The package bundles a skill that teaches coding agents to work with wikis
through the CLI. ``wiki install`` copies it into both the Claude Code
(``.claude/skills/``) and Codex (``.agents/skills/``) skill directories:

.. code-block:: console

   $ wiki install
   Installed wiki -> /home/user/.claude/skills/wiki.
   Installed wiki -> /home/user/.agents/skills/wiki.

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Option
     - Behavior
   * - ``--project``
     - Install under the current directory instead of the home directory
       (default: off — the skill installs user-wide).
   * - ``--link``
     - Symlink the bundled skill instead of copying it, so source edits apply
       without re-installing (default: off). Requires the package files on
       disk (e.g. an editable install); a zipped install raises an error.

``wiki install`` replaces any prior install at each destination. After
upgrading the package, re-run it to refresh a copied skill. The skill is also
available through the plugin marketplace: ``/plugin marketplace add
plasma-ai/plugins``, then ``/plugin install wiki@plasma``. See :doc:`/skill`
for what the skill does and how to invoke it.

Initializing a wiki
-------------------

From the project root, ``wiki init`` scaffolds a wiki in a ``wiki/`` folder
under the current directory, named after the project:

.. code-block:: console

   $ cd myproject
   $ wiki init
   Initialized wiki at: /home/user/myproject/wiki

   In Obsidian: Settings -> Community plugins -> turn off Restricted Mode, then enable Front Matter Title if needed.

The command sets up:

- ``wiki/.wiki/settings.json`` — the per-wiki settings file, which also marks
  the directory as a wiki root. Init seeds it with the default naming policy
  spelled out so the knobs are discoverable; every key is at its default. See
  :doc:`/configuration` for the full settings surface.
- ``wiki/_index.md`` — the root index, seeded with the wiki's name and a
  ``desc: ...`` placeholder to fill in.
- ``wiki/.wiki/obsidian/`` and ``wiki/.obsidian/`` — the Obsidian integration:
  a staged config template and its materialized copy, including a pinned
  community plugin downloaded at setup time. The Restricted Mode step in the
  hint above is the one manual action Obsidian requires. See
  :doc:`/guide/obsidian`.
- ``wiki/.wiki/cache/`` — a derived word-counts cache; it ignores itself via
  its own ``.gitignore`` and can be deleted at any time.
- The git merge driver for ``_index.md`` files, registered in the repository's
  local git config and named in ``.gitattributes``. See
  :doc:`/guide/merge-driver`.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Argument / option
     - Behavior
   * - ``name``
     - Optional wiki display name. Defaults to the project (current
       directory) name, or the ``--path`` folder name when ``--path`` is
       given. Must satisfy the naming policy, which is lenient by default —
       spaces, dashes, mixed case, and Unicode are all valid.
   * - ``--path``
     - Wiki root directory (default: ``{cwd}/wiki/``).
   * - ``--settings``
     - Initial ``.wiki/settings.json`` contents as a JSON object, e.g.
       ``--settings '{"naming": {"validate": ["ascii"]}}'`` (default: the
       naming-defaults seed). Validated before anything is written; ignored
       when the settings file already exists.
   * - ``--quiet``
     - Suppress the Obsidian hint and other non-error output (default: off).

``wiki init`` is safe to re-run: when the root ``_index.md`` already exists it
prints ``Wiki already initialized at: <path>`` and exits without touching
anything. It refuses to scaffold inside an existing wiki — nested wikis are
not supported.

After init, every other ``wiki`` command finds the wiki automatically from
anywhere inside the wiki tree — the nearest ancestor directory holding
``.wiki/settings.json`` wins — and from the project root, via the
``{cwd}/wiki/`` fallback. From anywhere else, pass ``--path`` to point the
command at the wiki root. The full resolution rules are in :doc:`/cli/index`.

Writing the first pages
-----------------------

Pages are plain markdown files. Create a folder for a section and drop a page
in it — no frontmatter or registration needed:

.. code-block:: console

   $ mkdir wiki/topics
   $ cat > wiki/topics/example.md << 'EOF'
   # Example

   A first page: plain markdown, nothing else required.
   EOF

The one structural rule to know is who owns what:

- **In pages**, the body is yours. The tool owns the frontmatter block's
  ``name:``, ``created:``, and ``updated:`` fields (adding the block to a bare
  page when needed) and keeps the H1 heading in sync with the name — or with
  an authored ``title:`` when you set one. Everything else, including ``desc:``,
  ``tags:``, and ``sources:``, is authored. See :doc:`/guide/pages`.
- **In indexes**, the ``***`` delimiter line splits the file: everything above
  it (frontmatter, H1, the link block) is generated and rewritten by the tool;
  your prose goes below it. Never hand-edit the link block — the next update
  overwrites it. See :doc:`/guide/structure`.

The core loop: update and lint
------------------------------

``wiki update`` syncs the generated surfaces with the filesystem. It creates
the missing ``topics/_index.md``, adopts the bare page (adding frontmatter and
seeding ``title:`` from its authored H1), and links everything into the
indexes:

.. code-block:: console

   $ wiki update
   Created 1 new index (fill in its desc)
   Adopted 1 bare page (frontmatter added)
   Added 2 new links
   Updated 3 files.

The count lines are narration on stderr; the ``Updated N files.`` summary is
the command's stdout. The adopted page now carries a full frontmatter block,
with a ``desc: ...`` placeholder waiting to be authored:

.. code-block:: markdown

   ---
   name: topics/example
   title: Example
   desc: ...
   tags: []
   sources: []
   created: 2026-01-01T00:00:00Z
   updated: 2026-01-01T00:00:00Z
   ---

   # Example

   A first page: plain markdown, nothing else required.

And the new ``topics/_index.md`` links the page, with everything above the
``***`` delimiter generated:

.. code-block:: markdown

   ---
   name: topics
   desc: ...
   tags: []
   sources: []
   created: 2026-01-01T00:00:00Z
   updated: 2026-01-01T00:00:00Z
   ---

   # topics

   [[_index|..]]

   [[topics/example|example]]: ...

   ***

``wiki lint`` checks wiki health. With every description still a placeholder,
it reports them as notes:

.. code-block:: console

   $ wiki lint
   _index.md: Needs desc
   _index.md: Empty content
   topics/_index.md: Needs desc
   topics/_index.md: Empty content
   topics/example.md: Needs desc
   No issues found (5 notes).

Lint distinguishes **issues** from **notes**. Issues are hard problems — files
that drifted from the generated form (shown as a diff), invalid names, broken
index links, malformed frontmatter — and any issue makes lint exit 1. Notes
are advisory (placeholder or oversized descriptions, empty index content,
stale prose links, CRLF endings), print on stderr, and never affect the exit
code.

Now author the descriptions. A page's ``desc:`` frontmatter is the source of
truth for its link row in the parent index — edit it in the page, not in the
index. Set ``desc: An example page.`` in ``topics/example.md`` and
``desc: Topic pages.`` in ``topics/_index.md`` (authored descriptions end
in a period — lint flags a missing one), then run the loop again:

.. code-block:: console

   $ wiki update
   Updated 2 files.
   $ wiki lint
   _index.md: Empty content
   topics/_index.md: Empty content
   No issues found (2 notes).

Update propagated each description into the parent index's link row, and lint
is down to two advisory notes — fill the sections below each index's ``***``
delimiter with prose (or leave them empty) as you see fit. This is the whole
rhythm of working with a wiki: add or move markdown files, run
``wiki update``, and let ``wiki lint`` tell you what needs a human.
``wiki map`` prints the resulting tree at any time.

``wiki update`` options:

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Argument / option
     - Behavior
   * - ``name``
     - Optional subtree scope, a folder path relative to the wiki root
       (default: the whole wiki).
   * - ``--path``
     - Wiki root directory (default: the enclosing wiki root).
   * - ``--prune``
     - Remove index links whose targets are gone from disk (default: off —
       broken links are preserved with a warning).
   * - ``--check``
     - Dry run: write nothing, list ``Would update: <path>`` lines, and exit 1
       when changes are pending, 0 when clean (default: off). Like a
       formatter's check mode, the nonzero exit signals pending work, not an
       error.
   * - ``--full``
     - Print every narration line instead of per-category counts
       (default: off).
   * - ``--count``
     - Print one count line per narration category — the default; mutually
       exclusive with ``--full``.

``wiki lint`` options:

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Argument / option
     - Behavior
   * - ``name``
     - Optional subtree scope, a folder path relative to the wiki root
       (default: the whole wiki).
   * - ``--path``
     - Wiki root directory (default: the enclosing wiki root).
   * - ``--full``
     - Print every issue and note line — the default.
   * - ``--count``
     - Print only the closing summary, e.g. ``2 issues, 1 note.`` (default:
       off; mutually exclusive with ``--full``).

Where next
----------

:doc:`/guide/pages` covers the page format in depth — every frontmatter field,
titles, and categories — and :doc:`/guide/structure` covers folders, indexes,
naming rules, and the link grammar. :doc:`/guide/generation` details exactly
what ``wiki update`` owns and how ``wiki lint`` judges health.
:doc:`/guide/obsidian` and :doc:`/guide/merge-driver` explain the
integrations ``wiki init`` set up. The full command surface, including
``wiki read``, ``wiki search``, and ``wiki map``, is in :doc:`/cli/index`;
per-wiki settings are in :doc:`/configuration`; the agent skill is in
:doc:`/skill`.
:doc:`/recipes` collects task-oriented walkthroughs, and :doc:`/examples`
tours the committed ``hello`` example wiki.
