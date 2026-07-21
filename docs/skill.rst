Agent Skill
===========

The ``wiki`` package bundles a skill for coding agents — a set of
instructions that teaches Claude Code and Codex what a wiki is, which commands
to reach for, and the conventions that keep a wiki healthy. Giving an agent the
skill means it navigates and maintains your wiki with the CLI instead of
guessing at the format: it reads pages through ``wiki read``, finds content
with ``wiki search``, and leaves index maintenance to ``wiki update`` rather
than hand-editing generated link blocks.

This page covers installing the skill, when it triggers, and the workflow it
drives. The command surface itself is documented under :doc:`/cli/index`; the
on-disk format under :doc:`/guide/pages` and :doc:`/guide/structure`.

What the skill provides
-----------------------

The skill is a ``SKILL.md`` instruction file (plus a Codex policy file) that
gives the agent:

- **The wiki shape** — a folder tree of markdown pages indexed at every level
  by ``_index.md`` files, with authored content below the ``***`` delimiter
  and generated surfaces above it.
- **A command map** — ``init``/``config``/``trust`` for setup,
  ``lint``/``update`` for maintenance, and ``map``/``search``/``read`` for
  consumption, with the instruction to consult ``--help`` for full option
  surfaces.
- **A bootstrap path** — if the ``wiki`` CLI is not on the agent's ``PATH``,
  the skill instructs it to install the package from PyPI
  (``pipx install plasma-wiki``; ``pip install`` and ``uv tool install`` work
  too).
- **Scaling guidance** — fan wiki work out to sub-agents rather than authoring
  page by page (see `The workflow it drives`_).
- **The conventions** — precise rules for frontmatter, naming, timestamps,
  descriptions, wikilinks, markdown formatters, merges, and the trust boundary
  around ``.wiki/wiki.py`` hooks (see `Conventions the skill teaches`_).

The skill does not add new capabilities to the agent — everything it describes
is the same CLI you run yourself. Its value is that the agent follows the
tool's ownership model (regenerate, don't hand-edit) instead of fighting it.

Installing the skill
--------------------

Plugin marketplace
~~~~~~~~~~~~~~~~~~

The skill is available through the plugin marketplace. In Claude Code:

.. code-block:: text

   /plugin marketplace add plasma-ai/plugins
   /plugin install wiki@plasma

In Codex:

.. code-block:: console

   $ codex plugin marketplace add plasma-ai/plugins
   $ codex plugin add wiki@plasma

``wiki install``
~~~~~~~~~~~~~~~~

The CLI can install the bundled skill directly. ``wiki install`` copies it
into both agents' skill directories — ``.claude/skills/`` (Claude Code) and
``.agents/skills/`` (Codex) — under your home directory:

.. code-block:: console

   $ wiki install
   Installed wiki -> /home/user/.claude/skills/wiki.
   Installed wiki -> /home/user/.agents/skills/wiki.

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Option
     - Default
     - Behavior
   * - ``--project``
     - off
     - Install under the current directory instead of the home directory, so
       the skill applies to one project only.
   * - ``--link``
     - off
     - Symlink the bundled skill instead of copying it, so source edits apply
       without re-installing. Requires the package files on disk (e.g. an
       editable install); a zipped install raises an error.

Each run replaces any prior install at the destinations, so re-running
``wiki install`` after upgrading the package refreshes the copied skill.
A ``--link`` install tracks the package automatically and needs no refresh.

``wiki install`` touches only the skill directories — it never reads or
modifies a wiki.

When the skill triggers
-----------------------

The skill never triggers on its own. It ships with model invocation disabled
(``disable-model-invocation: true`` in its frontmatter, mirrored for Codex as
``allow_implicit_invocation: false``), so the agent does not load it
implicitly when a task merely looks wiki-shaped. You invoke it explicitly —
``/wiki`` in Claude Code — when you want the agent working under the wiki
conventions.

This is a deliberate design: the skill's instructions are prescriptive enough
(never hand-edit generated surfaces, never run ``wiki trust`` autonomously)
that they should apply when you decide they apply, not whenever the model
guesses they might.

The workflow it drives
----------------------

Navigating: map, search, read
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For consuming a wiki, the skill directs the agent to the read commands:

- ``wiki map`` for an indented tree overview — the agent's first move when
  orienting in an unfamiliar wiki, scoped and filtered as needed.
- ``wiki search`` for regex search over page content (or frontmatter fields
  with ``--field``).
- ``wiki read`` to print a named entry, optionally sliced by lines, words, or
  characters to keep large pages out of the context window.

Maintaining: update and lint
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For writing, the ownership model is central: the agent authors page bodies,
frontmatter descriptions, and index content below ``***``, then runs
``wiki update`` to sync everything generated — index link blocks, frontmatter
order and placeholders, path-derived names. ``wiki lint`` verifies the result
and names anything the agent must fix by hand (missing descriptions, broken
links, naming violations). The generated surfaces are never edited directly.

Setup: init, config, trust
~~~~~~~~~~~~~~~~~~~~~~~~~~

The skill also covers standing up a wiki: ``wiki init`` to scaffold one,
``wiki config`` to install the Obsidian integration and register the git merge
driver (once per clone), and ``wiki trust`` to authorize a wiki's
``.wiki/wiki.py`` hook — though the skill forbids the agent from running
``wiki trust`` itself (see `The trust boundary`_).

Working at scale
~~~~~~~~~~~~~~~~

A wiki is many small, independent pages, so the skill tells the agent to
parallelize rather than author or audit page by page in one context:

- **Fan out sub-agents.** When seeding or expanding a wiki, each independent
  page (or each source to digest) goes to its own sub-agent; a single
  ``wiki update`` afterwards stitches the new pages into the indexes. Update
  maintains index link rows and frontmatter only — it never linkifies mentions
  in page prose, so the agent authors ``[[...]]`` cross-links by hand.
- **Drive sweeps with a dynamic workflow.** Audits, relinking, and
  restructures pipeline pages through a workflow so each is read, revised, and
  verified on its own.

Conventions the skill teaches
-----------------------------

The bulk of ``SKILL.md`` is a conventions list — precise rules that keep agent
output indistinguishable from tool output. Highlights, with the pages that
document each area in full:

- **The tool's namespace is off limits.** The agent never authors content
  under ``.wiki/`` (see :doc:`/guide/structure`).
- **Names are path-derived; titles are authored.** Renames happen by moving
  files, never by editing ``name:``; ``title:`` and ``category:`` are unset
  with lowercase ``null`` only (see :doc:`/guide/pages`).
- **Timestamps are tool-owned.** The agent never hand-edits ``created:`` or
  ``updated:``; formats are configured via the ``timestamp`` settings block
  (see :doc:`/configuration`).
- **Descriptions live in the child page's frontmatter** and end in a period;
  ``wiki update`` propagates them onto parent index rows, and placeholder
  ``desc: ...`` values get filled in promptly.
- **Wikilinks stay inside the wiki.** Files outside it are referenced by name,
  never linked. Stale prose links are soft lint notes; broken generated-index
  links are hard issues.
- **Markdown formatters need the wiki plugin.** Generic formatters corrupt the
  ``***`` delimiter and ``[[wikilinks]]``; the sanctioned fixes are the
  ``mdformat-wiki`` plugin or excluding the wiki root (see the formatter
  recipe in :doc:`/recipes`; :doc:`/guide/generation` covers the damage
  repair).
- **Merges follow the driver contract.** The agent runs ``wiki update`` after
  a merge and leaves new-directory index bodies empty during concurrent work
  so add/add merges resolve cleanly (see :doc:`/guide/merge-driver`).
- **Lint suppression is local.** Content that must display flagged material
  (sample conflict markers, stale-link examples) is wrapped in a ``no-lint``
  region rather than left failing.

The trust boundary
------------------

A wiki may ship a ``.wiki/wiki.py`` hook — a custom `wiki.core.wiki.Wiki`
subclass that runs with the user's privileges. The CLI refuses to load an
untrusted hook, and the skill makes that refusal a hard stop for the agent:
it must surface the error and let *you* run ``wiki trust`` for a wiki you
have vetted, never run the command itself or work around the refusal. This
keeps the decision to execute a cloned wiki's code with a human. A hookless
wiki needs no trust and never hits this gate.

See :doc:`/configuration` for the trust store's location and the
``trust`` command reference under :doc:`/cli/index`.
