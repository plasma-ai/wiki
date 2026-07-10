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
  the indexes. Update adds and repairs index link rows and frontmatter
  only — it never linkifies mentions in page prose, so author `[[...]]`
  cross-links by hand.
- **Drive sweeps with a dynamic workflow.** When auditing, relinking, or
  restructuring an existing wiki, pipeline its pages through a workflow
  so each is read, revised, and verified on its own — slow pages never
  block fast ones.

## Conventions

- **`.wiki/` is the tool's namespace.** Every root carries a `.wiki/`
  directory holding `settings.json` — the file that declares the wiki
  root; `wiki init` writes it and `wiki update` restores a missing one —
  plus the derived word-counts cache and the staged Obsidian config.
  Never author content there; the walk skips dot-directories by
  construction.
- **Name validation is configurable.** By default the wiki rejects only
  structural characters (`/`, `\`, `*`, `[`, `]`, `|`, `#`), a leading
  dot, and the reserved `_index` stem — spaces, dashes, and unicode all
  pass. Stricter rules (e.g. ASCII identifiers) are opt-in per wiki via
  `naming.validate` in `.wiki/settings.json` (seed it at creation with
  `wiki init --settings`); `wiki init` and `wiki lint` enforce whatever
  policy is set.
- **Timestamps are configurable.** `created`/`updated` default to UTC in
  `%Y-%m-%dT%H:%M:%SZ`; set `timestamp.timezone` (an IANA name) and
  `timestamp.format` (a strftime string) in `.wiki/settings.json` to
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
- **Descriptions end in a period.** `wiki lint` fails a `desc` (or an
  authored link description) that lacks a trailing period; the seeded
  `...` placeholder only draws a soft note. Author the desc in the child
  page's frontmatter — `wiki update` copies it onto the parent index's
  link line. A desc containing `: ` must be YAML-quoted; surrounding
  quotes are stripped when the value is read.
- **Fill in auto-created index descs.** `wiki update` creates a missing
  `_index.md` for every new directory with a `desc: ...` placeholder and
  announces the batch in its condensed summary
  (`Created N new indexes (fill in their descs)`; run with `--full` for
  the per-path `New index:` lines). Fill in the desc right after the
  update — lint soft-notes the placeholder until you do.
- **Suppress lint locally with a `no-lint` region.** A page that must
  display otherwise-flagged content (sample conflict markers,
  deliberately stale links) wraps those lines in
  `<!-- start: no-lint -->` … `<!-- end: no-lint -->`, which silences
  the positional rules for just that span. Regions never affect
  file-level checks or `wiki update`, and a dangling or nested marker is
  itself a hard lint issue.
- **Give markdown formatters the wiki plugin.** The `***` delimiter and
  `[[wikilinks]]` are load-bearing syntax; mdformat/prettier-style hooks
  rewrite `***` to `---` and escape the brackets, demoting the generated
  link block to plain text. `wiki update` repairs a mangled index and
  `wiki lint` names the damage signatures (escaped wikilinks, a thematic
  break standing where `***` belongs), but don't rely on the repair: for
  mdformat add the `mdformat-wiki` plugin (under pre-commit,
  `additional_dependencies: [mdformat-wiki]` on the hook, dropping a
  coexisting `mdformat-frontmatter` — both register a frontmatter
  renderer and whichever is discovered first wins), which makes wiki
  faces round-trip byte-identically; for formatters with no plugin lane
  (e.g. prettier) exclude the wiki root instead (`wiki/` in
  `.prettierignore`).
- **The git merge driver resolves only the generated region.** For
  `_index.md` files it takes *ours* for the regenerated parts above
  `***` (the link block plus the `name`/`updated` keys `wiki update`
  regenerates) and three-way merges everything authored — the remaining
  frontmatter fields (`desc`/`created`/`category`/`tags`/`sources`) and
  the user content below `***` — which can still conflict for
  hand-resolution. A side missing its `***` entirely (formatter damage)
  can't be split into regions, so it conflicts whole-file with a hint
  comment naming the repair — restore the `***` on that branch
  (`wiki update` does it), then redo the merge. Run `wiki update` after
  a merge to regenerate the link rows from the filesystem.
  `init`/`config` register the driver in local git config and write the
  `**/_index.md` glob to `.gitattributes` in the working tree only — you
  stage and commit it yourself, and each clone runs `wiki config` once
  to register the driver.
- **Leave new-directory index bodies empty during concurrent work.**
  When sibling branches both create the same new directory, its two
  `_index.md`s merge add/add with no common ancestor: the generated
  region resolves automatically — including the seeded `created` stamps,
  which are `wiki update` churn on both sides — but body prose authored
  below `***` on both sides conflicts for hand-union (empty or identical
  bodies merge clean). Concurrent cohorts should leave a new directory's
  index body empty until after the merge wave, then author it once. The
  merge driver plants a one-line HTML-comment hint above such add/add
  conflict markers naming this convention — delete it as you resolve.
