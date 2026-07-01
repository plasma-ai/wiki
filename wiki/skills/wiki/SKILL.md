---
name: wiki
description: Indexed knowledge bases with command-line tools for agents.
disable-model-invocation: true
---

# Wiki

A wiki is a structured, indexed knowledge base organized as a folder
tree with `_index.md` files. Each folder has an index that links to its
children (subfolders and pages), and a content section below a `***`
delimiter for user-authored notes.

Initialize a wiki in the current project and configure Obsidian plugins:

- `wiki init` — scaffold a new wiki with a root index
- `wiki config` — install plugins into `.obsidian/`

Maintain indexes as files are added and removed:

- `wiki lint` — validate structure and flag issues
- `wiki update` — sync index links with the filesystem

Browse structure, search across content, and read entries:

- `wiki map` — print an indented tree overview
- `wiki search` — search content with regex
- `wiki read` — read a named entry

## Usage

Install the `wiki` CLI from PyPI if it is not already on your `PATH`:

```bash
pipx install plasma-wiki
```

(`pip install plasma-wiki` or `uv tool install plasma-wiki` work too.)

Then run commands directly:

```bash
wiki <command> ...
```

Run `wiki --help` for a list of commands, and `wiki <command> --help`
for full option descriptions.

## Working at scale

A wiki is many small, independent pages, so wiki work parallelizes well
and is often too large for one context. Default to sub-agents and
dynamic workflows rather than authoring or auditing page by page
yourself:

- **Fan out sub-agents.** When seeding or expanding a wiki, give each
  independent page — or each source to research and digest — to its own
  sub-agent, then run `wiki update` once to stitch the new pages into
  the indexes.
- **Drive sweeps with a dynamic workflow.** When auditing, relinking, or
  migrating an existing wiki, pipeline its pages through a workflow so
  each is read, revised, and verified on its own — slow pages never
  block fast ones.

## Conventions

- **Name validation is configurable.** By default the wiki rejects only
  structural characters (`/`, `\`, `*`, `[`, `]`, `|`, `#`), a leading
  dot, and the reserved `_index`/`_config` stems — spaces, dashes, and
  unicode all pass. Stricter rules (e.g. ASCII identifiers) are opt-in
  per wiki via `naming.validate` in `_config/settings.json` (seed it at
  creation with `wiki init --settings`); `wiki init` and `wiki lint`
  enforce whatever policy is set.
- **Timestamps are configurable.** `created`/`updated` default to UTC in
  `%Y-%m-%dT%H:%M:%SZ`; set `timestamp.timezone` (an IANA name) and
  `timestamp.format` (a strftime string) in `_config/settings.json` to
  change them — use `%z` rather than a literal `Z` for a non-UTC zone.
- **Names are path-derived.** `wiki update` sets each page's `name` and
  H1 heading to the path-joined name (e.g. `core/design`). An authored
  title is intentionally overwritten so names stay consistent with the
  tree structure — to rename an entry, move its file rather than editing
  the title or `name:`.
- **Wikilinks stay inside the wiki.** A wikilink (`[[...]]`) must target
  another page in the same wiki. Files outside the wiki (source files,
  configs, another wiki's pages) can be referenced by name or in
  backticks, but never linked.
- **The git merge driver auto-resolves index conflicts.**
  `init`/`config` wire the `_index.md` merge driver and write the
  `**/_index.md` glob to `.gitattributes` in the working tree only --
  you stage and commit it yourself.
