Pages
=====

Every page and index in a wiki is a plain markdown file. There are two kinds:
**pages** (any ``.md`` file other than an index) hold content, and **indexes**
(the reserved ``_index.md`` in every folder) link a folder's entries
together. Both share one on-disk format: YAML frontmatter, a generated region the tool maintains, and authored
content the tool never touches.

The dividing rule is ownership. The tool owns every surface it can regenerate
from the filesystem — the index link block, the ``name:``, ``created:``, and
``updated:`` frontmatter fields, frontmatter field order, and the H1 heading —
and ``wiki update`` rewrites them whenever they drift (see
:doc:`/guide/generation`). Everything else is authored: page bodies, index
prose below the ``***`` delimiter, and the ``title:``, ``desc:``,
``category:``, ``tags:``, and ``sources:`` fields.

Page format
-----------

A page looks like this:

.. code-block:: markdown

   ---
   name: topics/example
   desc: An example page.
   tags: []
   sources: []
   created: 2026-01-01T00:00:00Z
   updated: 2026-01-01T00:00:00Z
   ---

   # topics/example

   Authored body content.

Frontmatter is YAML between two ``---`` lines, and the opening ``---`` must be
the first line of the file. Fresh frontmatter — written when a bare page is
adopted or an index is created — contains exactly ``name``, a ``desc: ...``
placeholder, ``tags: []``, ``sources: []``, ``created``, and ``updated``, plus
a ``title:`` seeded from the authored H1 when an adopted bare page carries one
(and, when ``titles.required`` is set, a ``title: null`` placeholder); no
``category:`` line is ever seeded. Everything below the frontmatter is the
page body, and apart from the H1 heading it is entirely yours.

``wiki update`` keeps fields in canonical order: ``name``, ``title``,
``desc``, ``category``, ``tags``, ``sources``, then any custom authored keys
in their original order, then the tool-owned ``created`` and ``updated`` tail.
Custom keys are allowed and preserved.

Frontmatter fields
------------------

.. list-table::
   :header-rows: 1
   :widths: 16 14 70

   * - Field
     - Owner
     - Meaning
   * - ``name``
     - tool
     - Path-derived display name; rewritten to match the file's location.
   * - ``title``
     - author (optional)
     - Overrides the H1 heading when set.
   * - ``desc``
     - author
     - Short description; the source of truth for the parent index's link
       row. Seeded as the ``...`` placeholder.
   * - ``category``
     - author (optional)
     - Renders as a ``[category] name`` prefix on the parent index's link
       label.
   * - ``tags``
     - author
     - Free-form list, seeded ``[]``.
   * - ``sources``
     - author
     - Free-form list, seeded ``[]``.
   * - custom keys
     - author
     - Preserved verbatim between the known fields and the timestamps.
   * - ``created``
     - tool
     - Stamped when the file gains frontmatter, kept from then on.
   * - ``updated``
     - tool
     - Re-stamped only when a write actually changes content.

Path-derived names
~~~~~~~~~~~~~~~~~~

Display names are derived from the file's path relative to the wiki root:
path parts join with ``/`` and the ``.md`` suffix is stripped, so
``topics/example.md`` is named ``topics/example``. The root's own name is the
root index's ``name:`` field (falling back to the folder name). ``wiki
update`` owns each entry's ``name:`` field and rewrites the H1 heading to
match — to rename an entry, move its file rather than editing ``name:``.

Names are lenient by default: spaces, dashes, mixed case, and Unicode are all
valid. Only characters that would break the wiki's structure are always
rejected — ``/``, ``*``, ``\``, ``[``, ``]``, ``|``, and ``#`` — along with
leading dots, non-printable names, and the reserved ``_index`` stem. A wiki
can opt into stricter rules through the ``naming`` block in
``.wiki/settings.json``; see :doc:`/configuration`.

Titles
~~~~~~

Any page or index may carry an authored ``title:`` field, which wins the H1
heading while ``name:`` stays tool-owned:

.. code-block:: markdown

   ---
   name: topics/example
   title: An Example Page
   desc: An example page.
   tags: []
   sources: []
   created: 2026-01-01T00:00:00Z
   updated: 2026-01-01T00:00:00Z
   ---

Unset a title by deleting the line or setting ``title: null`` — ``wiki
update`` removes the line. Lowercase ``null`` is the only reset spelling; a
quoted ``'null'`` (or ``~``, ``Null``, ``NULL``) is authored text and renders
literally. Without a title, a hand-edited H1 is rewritten back to the
path-derived name on the next update.

Setting ``titles.required`` in ``.wiki/settings.json`` demands an authored
title everywhere: ``wiki update`` seeds a ``title: null`` placeholder on every
entry missing one, and ``wiki lint`` fails each placeholder until a value is
authored. See :doc:`/configuration`.

Descriptions
~~~~~~~~~~~~

The ``desc`` field is the source of truth for how an entry appears in its
parent index: ``wiki update`` copies each child's frontmatter ``desc`` onto
the parent's link row. Editing the description directly in the parent
``_index.md`` is futile — the next update overwrites it with the page's own
``desc`` (and says so, naming the page to edit). Only whitespace and wrapping
differences are tolerated.

Authored descriptions must end in a period (``wiki lint`` fails one that does
not); the seeded ``...`` placeholder only draws a soft note until it is filled
in. A description containing a colon followed by a space must be YAML-quoted.
Do not hand-wrap a description mid-word — let the YAML block scalar carry any
line breaks.

Categories
~~~~~~~~~~

An optional ``category:`` field groups entries in their parent index: the
parent's link label renders as ``[category] name``, categorized entries sort
before uncategorized ones, and ``wiki map --category`` filters by category. A
category may hold any characters except ``]``. Unset one exactly like a title:
delete the line or set ``category: null`` (lowercase only).

Timestamps
~~~~~~~~~~

``created:`` and ``updated:`` are tool-owned. ``created`` is written once,
when the file gains frontmatter; ``updated`` is re-stamped only on writes that
actually change content, so timestamp churn never dirties a file on its own.
Stamps default to UTC in ``%Y-%m-%dT%H:%M:%SZ`` format; configure the zone and
format through the ``timestamp`` block in ``.wiki/settings.json`` (see
:doc:`/configuration`). Never hand-edit a stamp: an edit goes undetected
unless the value stops parsing under the configured format, which ``wiki
lint`` fails.

Index format
------------

Every folder's ``_index.md`` follows the same frontmatter rules as a page,
followed by a generated region and then authored content:

.. code-block:: markdown

   ---
   name: topics
   desc: Topic pages.
   tags: []
   sources: []
   created: 2026-01-01T00:00:00Z
   updated: 2026-01-01T00:00:00Z
   ---

   # topics

   [[_index|..]]

   [[topics/advanced/_index|advanced/]]: Deep-dive material.

   [[topics/example|example]]: An example page.

   ***

   Authored prose lives here, below the delimiter.

The generated region — everything above the ``***`` line — is tool-owned: the
H1 (the authored ``title`` when set, else the path-derived name) and the link
block, one row per entry in the folder.

Link rows
~~~~~~~~~

Each row has the shape ``[[target|label]]: description``:

- **Targets** are always relative to the wiki root, joined with ``/``, with
  the ``.md`` suffix stripped — ``topics/example``, never a platform path.
- The **parent row**, labeled ``..``, comes first, targets the parent
  folder's ``_index``, and carries no description. The root index has no
  parent row.
- **Folder rows** target the child folder's index (``topics/advanced/_index``)
  and are labeled with a trailing slash (``advanced/``).
- **Page rows** are labeled by the bare name (``example``). Every non-parent
  row carries a description — at minimum the ``...`` placeholder. A
  description may continue over multiple lines; blank lines inside it are
  paragraph breaks.
- **Non-markdown files** are indexed too, linked by their full filename
  (``Makefile``). A markdown page whose stem collides with the name of a
  sibling non-markdown file (``Makefile.md`` beside ``Makefile``) is likewise
  linked by its full filename.

Rows are ordered: the parent link first, then categorized entries (sorted by
category, then name), then uncategorized entries alphabetically. A row whose
target has vanished from disk is preserved — with a warning — until
``wiki update --prune`` removes it.

The ``***`` delimiter
~~~~~~~~~~~~~~~~~~~~~

A line consisting of ``***`` separates the generated region from authored
content. It is always present in a rendered index, and everything below it is
never touched by the tool — section prose, notes, anything. This is the line
that makes the ownership split mechanical. Generic markdown formatters that
rewrite ``***`` to ``---`` or escape ``[[`` brackets break the format, so keep
them off the wiki or teach them the syntax (see the formatter recipe in
:doc:`/recipes`; :doc:`/guide/generation` covers the damage repair).

Regular pages have no delimiter: the whole body below the frontmatter is
authored, apart from the H1 heading.

Bare and damaged pages
----------------------

A markdown page with no frontmatter at all is a **bare page**: the next
``wiki update`` adopts it, adding a fresh frontmatter block. An authored H1 is
preserved by seeding ``title:`` from it; a page with no H1 gains the
path-derived heading in its body. Until adoption, ``wiki lint`` flags the page
as a hard issue.

Frontmatter that opens with ``---`` but never closes is malformed: the file
parses as having no frontmatter, and ``wiki update`` leaves it untouched with
a warning rather than risk consuming the body. Likewise an index emptied of
its frontmatter is preserved as-is — restore it from git, or delete it and let
``wiki update`` rebuild it.

Region directives
-----------------

Any page or index may wrap lines in an HTML-comment region:

.. code-block:: markdown

   <!-- start: no-lint -->
   content that would otherwise be flagged
   <!-- end: no-lint -->

Each marker sits alone on its line. The ``no-lint`` directive suppresses
``wiki lint``'s positional rules — conflict markers, escaped wikilinks, wrap
mangles, stale-link notes — for the wrapped lines, so a page can display
sample conflict markers or stale-link examples without failing lint. A nested or dangling marker is itself a hard lint issue, and a malformed
pair suppresses nothing. Markers inside fenced or inline code are masked — a
marker shown in a code block is a sample, not a directive. Well-formed regions
with other directive names are inert; ``no-lint`` is the only directive with
shipped semantics.

Ownership summary
-----------------

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - The tool owns
     - You own
   * - The index link block (rows, labels, ordering)
     - Index prose below the ``***`` delimiter
   * - ``name:``, ``created:``, ``updated:``
     - ``title:``, ``desc:``, ``category:``, ``tags:``, ``sources:``, custom
       keys
   * - Frontmatter field order and seeded placeholders
     - Page bodies
   * - The H1 heading (from ``title`` else ``name``)
     - Wikilinks (``[[...]]``) in prose — the tool never linkifies mentions
   * - Line endings (CRLF is normalized to LF)
     -
