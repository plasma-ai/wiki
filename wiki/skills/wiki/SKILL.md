---
name: wiki
description: Indexed knowledge bases with command-line tools for agents.
disable-model-invocation: true
---

# Wiki

A wiki is a structured, indexed knowledge base organized as a folder tree with
`_index.md` files. Each folder has an index that links to its children
(subfolders and pages), and a content section below a `***` delimiter for
user-authored notes.

Initialize a wiki in the current project and configure integrations:

- `wiki init` — scaffold a new wiki with a root index
- `wiki config` — install Obsidian plugins and the git merge driver
- `wiki trust` — authorize a wiki to run its `.wiki/wiki.py` hook

Maintain indexes as files are added and removed:

- `wiki lint` — validate structure and flag issues
- `wiki update` — sync index links with the filesystem

Browse structure, search across content, and read entries:

- `wiki map` — print an indented tree overview
- `wiki search` — search content with regex
- `wiki read` — read a named entry

## Usage

Install the CLI from PyPI if it is not already on your `PATH`:

```bash
pipx install plasma-wiki
```

(`pip install` or `uv tool install` work too.)

Then run commands directly:

```bash
wiki <command> ...
```

Run `wiki --help` for a list of commands, and `wiki <command> --help` for full
option descriptions.

## Working at scale

A wiki is many small, independent pages, so wiki work parallelizes well and is
often too large for one context. Default to sub-agents and dynamic workflows
rather than authoring or auditing page by page yourself:

- **Fan out sub-agents.** When seeding or expanding a wiki, give each
  independent page — or each source to research and digest — to its own
  sub-agent, then run `wiki update` once to stitch the new pages into the
  indexes. Update adds and repairs index link rows and frontmatter only — it
  never linkifies mentions in page prose, so author `[[...]]` cross-links by
  hand.
- **Drive sweeps with a dynamic workflow.** When auditing, relinking, or
  restructuring an existing wiki, pipeline its pages through a workflow so each
  is read, revised, and verified on its own — slow pages never block fast ones.

## Conventions

- **`.wiki/` is the tool's namespace.** Every root carries a `.wiki/` directory
  holding `settings.json` — the file that declares the wiki root; `wiki init`
  writes it and `wiki update` restores a missing one — plus the derived
  word-counts cache and the staged Obsidian config. Never author content there;
  the walk skips dot-directories by construction.
- **Name validation is configurable.** By default the wiki rejects only
  structural characters (`/`, `\`, `*`, `[`, `]`, `|`, `#`), a leading dot, and
  the reserved `_index` stem — spaces, dashes, and unicode all pass. Stricter
  rules (e.g. ASCII identifiers) are opt-in per wiki via `naming.validate` in
  `.wiki/settings.json` (seed it at creation with `wiki init --settings`);
  `wiki init` and `wiki lint` enforce whatever policy is set.
- **Timestamps are tool-owned and configurable.** `wiki update` writes both
  stamps when a file gains frontmatter, keeps `created:` from then on, and
  rewrites `updated:` on every actual write — never hand-edit them; an edit goes
  undetected unless the value stops parsing under the configured format, which
  `wiki lint` fails. `created`/`updated` default to UTC in `%Y-%m-%dT%H:%M:%SZ`;
  set `timestamp.timezone` (an IANA name) and `timestamp.format` (a strftime
  string) in `.wiki/settings.json` to change them — use `%z` rather than a
  literal `Z` for a non-UTC zone.
- **Names are path-derived; titles are authored.** `wiki update` sets each
  page's `name` and H1 heading to the path-joined name (e.g. `core/design`) so
  names stay consistent with the tree structure — to rename an entry, move its
  file rather than editing `name:`. Any index or page may carry an optional
  authored `title:` frontmatter field, which wins its H1 (`wiki update` keeps
  the line directly under `name:`, and adding frontmatter to a bare page seeds
  `title:` from its authored H1); without one, a hand-edited heading is still
  rewritten to `name`. Unset a title by deleting the line or setting
  `title: null` — update removes it, and lowercase `null` is the only reset
  spelling (`~`/`Null`/`NULL` render literally as the heading). Keep titles on a
  single line, quote a title containing `: `, and prefer plain text.
  `wiki search --field title` matches only authored titles — an unset entry has
  no line to match. Setting `titles.required` to true in `.wiki/settings.json`
  demands a title everywhere: update seeds a `title: null` placeholder on every
  index and page missing one, and lint fails each placeholder until a value is
  authored.
- **Categories are authored and optional.** An index or page may carry a
  `category:` frontmatter field; `wiki update` copies it into the parent index's
  link label as a `[category] name` prefix, and `wiki map --category` filters by
  it. Fresh frontmatter carries no `category:` line — unset one by deleting the
  line or setting `category: null` (update removes the line; as with titles,
  lowercase `null` is the only reset spelling). Keep categories on a single
  line.
- **Frontmatter order is tool-enforced.** `wiki update` keeps every block in
  canonical order — `name`, `title`, `desc`, `category`, `tags`, `sources`,
  `created`, `updated` — moving each field (with its block-scalar body) verbatim
  into its slot. Custom keys are allowed: they keep their relative order below
  the known fields, above the timestamps.
- **Wikilinks stay inside the wiki.** A wikilink (`[[...]]`) must target another
  page in the same wiki. Files outside the wiki (source files, configs, another
  wiki's pages) can be referenced by name or in backticks, but never linked.
- **Stale wikilinks are soft notes.** A `[[...]]` in index or page prose whose
  target no longer exists draws a stderr note from `wiki lint` without failing
  the run. Broken links in the generated index link block — the rows
  `wiki update` maintains — stay hard issues (`--prune` removes them).
- **Descriptions end in a period.** `wiki lint` fails a `desc` (or an authored
  link description) that lacks a trailing period; the seeded `...` placeholder
  only draws a soft note. Author the desc in the child page's frontmatter —
  `wiki update` copies it onto the parent index's link line. A desc containing
  `: ` must be YAML-quoted; surrounding quotes are stripped when the value is
  read. Never hand-wrap a desc mid-word or onto a list-marker start — let the
  block scalar carry the breaks; lint fails the wrap artifacts (a hyphen dangle,
  a phantom list item).
- **Fill in auto-created index descs.** `wiki update` creates a missing
  `_index.md` for every new directory with a `desc: ...` placeholder and
  announces the batch in its condensed summary
  (`Created N new indexes (fill in their descs)`; run with `--full` for the
  per-path `New index:` lines). Fill in the desc right after the update — lint
  soft-notes the placeholder until you do.
- **Bare pages are adopted loudly.** A page with no frontmatter gains it on the
  next `wiki update` — with `title:` seeded from its authored H1, while a page
  with no H1 gains the path-joined heading in its body, never a seeded title —
  and each adoption is announced (`Adopted N bare pages (frontmatter added)` in
  the condensed summary; `--full` prints the per-page lines). Until then
  `wiki lint` names the page as a hard issue
  (`Bare page (no frontmatter); update will adopt it`) alongside the adoption
  diff.
- **Suppress lint locally with a `no-lint` region.** A page that must display
  otherwise-flagged content (sample conflict markers, stale link examples) wraps
  those lines in `<!-- start: no-lint -->` ... `<!-- end: no-lint -->`, which
  silences the positional rules — hard issues and soft notes alike — for just
  that span. Regions never affect file-level checks, and a dangling or nested
  marker is itself a hard lint issue.
- **Give markdown formatters the wiki plugin.** The `***` delimiter and
  `[[wikilinks]]` are load-bearing syntax; mdformat/prettier-style hooks rewrite
  `***` to `---` and escape the brackets, demoting the generated link block to
  plain text. `wiki update` repairs a mangled index and `wiki lint` names the
  damage signatures (escaped wikilinks, a thematic break standing where `***`
  belongs), but don't rely on the repair: for mdformat add the `mdformat-wiki`
  plugin (under pre-commit, `additional_dependencies: [mdformat-wiki]` on the
  hook, dropping a coexisting `mdformat-frontmatter` — both register a
  frontmatter renderer and whichever is discovered first wins), which makes wiki
  faces round-trip byte-identically; for formatters with no plugin lane (e.g.
  prettier) exclude the wiki root instead (`wiki/` in `.prettierignore`).
- **The git merge driver resolves only the generated region.** For `_index.md`
  files it takes *ours* for the regenerated parts above `***` (the link block
  plus the `name`/`updated` keys `wiki update` regenerates) and three-way merges
  everything authored — the remaining frontmatter fields
  (`title`/`desc`/`created`/`category`/`tags`/ `sources`) and the user content
  below `***` — which can still conflict for hand-resolution. A side missing its
  `***` entirely (formatter damage) can't be split into regions, so it conflicts
  whole-file with a hint comment naming the repair — restore the `***` on that
  branch (`wiki update` does it), then redo the merge. Run `wiki update` after a
  merge to regenerate the link rows from the filesystem — the H1 rides the
  taken-ours region, so a merged-in `title:` shows in its H1 only after that
  update. `init`/`config` register the driver in local git config and write the
  `**/_index.md` glob to `.gitattributes` in the working tree only — you stage
  and commit it yourself, and each clone runs `wiki config` once to register the
  driver.
- **Leave new-directory index bodies empty during concurrent work.** When
  sibling branches both create the same new directory, its two `_index.md`s
  merge add/add with no common ancestor: the generated region resolves
  automatically — including the seeded `created` stamps, which are `wiki update`
  churn on both sides — but body prose authored below `***` on both sides
  conflicts for hand-union (empty or identical bodies merge clean). Concurrent
  cohorts should leave a new directory's index body empty until after the merge
  wave, then author it once. The merge driver plants a one-line HTML-comment
  hint above such add/add conflict markers naming this convention — delete it as
  you resolve.
- **A `.wiki/wiki.py` hook needs explicit trust.** A wiki may ship a
  `.wiki/wiki.py` (a custom `Wiki` subclass) that runs code with the user's
  privileges, so `wiki` refuses to load an untrusted hook — every command that
  resolves the wiki fails, naming the hook and pointing at `wiki trust`. This is
  a security decision for the human: surface the error and let the user run
  `wiki trust` for a wiki they have vetted, rather than running it yourself or
  working around the refusal. A hookless wiki needs no trust; trust is recorded
  per resolved root in `~/.wiki/settings.json` (`WIKI_CONFIG_DIR` overrides the
  config home).
