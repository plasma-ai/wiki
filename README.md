# wiki

[![license](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/plasma-ai/wiki/blob/main/LICENSE)
[![build](https://github.com/plasma-ai/wiki/actions/workflows/build.yaml/badge.svg)](https://github.com/plasma-ai/wiki/actions/workflows/build.yaml)
[![docs](https://github.com/plasma-ai/wiki/actions/workflows/docs.yaml/badge.svg)](https://github.com/plasma-ai/wiki/actions/workflows/docs.yaml)
[![lint](https://github.com/plasma-ai/wiki/actions/workflows/lint.yaml/badge.svg)](https://github.com/plasma-ai/wiki/actions/workflows/lint.yaml)
[![tests](https://github.com/plasma-ai/wiki/actions/workflows/tests.yaml/badge.svg)](https://github.com/plasma-ai/wiki/actions/workflows/tests.yaml)
[![codecov](https://codecov.io/gh/plasma-ai/wiki/branch/main/graph/badge.svg?token=D8LJA7CZ2K)](https://codecov.io/gh/plasma-ai/wiki)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)

Indexed knowledge bases with command-line tools for agents.

A wiki keeps project knowledge as plain markdown, indexed at every level by
`_index.md` files, read by consulting the index and opening only the pages that
a task needs. Andrej Karpathy named this shape the
[LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
pattern, and Google's
[Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)
describes a standardized format for such markdown knowledge bases. Most
approaches leave a great deal of structuring for agents to maintain by hand;
here a deterministic CLI generates the indexes and cross-links and auto-resolves
the generated region of `_index.md` merges when parallel edits collide —
authored content below the delimiter still merges (and can conflict) like any
other text — so content is the only judgment call.

______________________________________________________________________

**Source**:
[https://github.com/plasma-ai/wiki](https://github.com/plasma-ai/wiki)

**Package**:
[https://pypi.org/project/plasma-wiki/](https://pypi.org/project/plasma-wiki/)

**Documentation**: [https://docs.plasma.ai/wiki](https://docs.plasma.ai/wiki)

______________________________________________________________________

## Installation

Install the `wiki` package from PyPI:

```bash
pip install plasma-wiki
```

Use `pipx install` or `uv tool install` to install the package in an isolated
environment.

### Skill

Install the skill for your agent via the plugin marketplace (Claude Code and
Codex):

```bash
# Claude Code
/plugin marketplace add plasma-ai/plugins
/plugin install wiki@plasma

# Codex
codex plugin marketplace add plasma-ai/plugins
codex plugin add wiki@plasma
```

Another install route is from the CLI, which copies (or symlinks) the skill into
`~/.claude/skills` and `~/.agents/skills` (add `--project` for the current
project only):

```bash
wiki install [--link]
```

After upgrading the package, re-run `wiki install` to refresh the copied skill
(pass `--link` for symlinked install).

## Usage

A wiki is a tree of markdown files linked together by `_index.md` files. Each
folder becomes a section, and each markdown file becomes an entry. Wikis are
designed to be read and written by both humans and agents: humans author content
in Obsidian (or any editor), and agents query the wiki through the CLI to ground
their work in project-specific knowledge.

Every wiki root carries a `.wiki/` directory — the tool's namespace, holding
`settings.json` (the file that declares the root; `wiki init` writes it and
`wiki update` restores a missing one), the derived word counts and ranked
full-text search caches, and the staged Obsidian config. Page, folder, and wiki
names are lenient by default: spaces, dashes, mixed case, and unicode are all
fine. Only characters that would break the wiki's structure — its path, link,
and index syntax — are rejected, along with leading dots (hidden files) and the
reserved `_index` name. A wiki can opt into stricter rules, such as ASCII-only
or identifier-style names, through the `naming` block in `.wiki/settings.json`;
`wiki lint` flags any name that violates the policy. Whole subtrees can be
excluded from indexing with gitignore-style globs in `exclude.patterns` —
excluded paths are never walked or linted, though `wiki read` still serves them.
Prose wikilinks stay inside the wiki unless `links.external` lists folders
outside it, as paths relative to the wiki root (`../src`, `../math`): a `./` or
`../` link, read from the page's folder as Obsidian reads it, may then target a
file or another wiki's page under a listed folder. The allowlist is a lint rule
alone — `wiki lint` checks such links in place, while `wiki read` and every
other command stay confined to the wiki root.

Frontmatter timestamps default to UTC in ISO-8601. To change them, set a
timezone (any IANA name) and format (a strftime string) under `timestamp` in
`.wiki/settings.json`. The stamps are tool-owned: `created:` is written when a
file gains frontmatter and kept from then on, and `updated:` is rewritten on
every write that changes content (a write that only normalizes CRLF line endings
leaves it untouched). A hand edit goes undetected unless the value stops parsing
under the configured format — `wiki lint` fails an unparseable stamp.

Display names are path-derived: `wiki update` owns each entry's `name:`
frontmatter and rewrites the H1 heading to match. An optional authored `title:`
field — on any index or page — overrides the H1 while `name` stays tool-owned;
set `title: null` (or delete the line) to unset it. When a page first gains
frontmatter, `wiki update` seeds `title:` from its existing H1, so the authored
heading survives adoption; a page with no H1 gains the path-derived heading and
no title. Setting `titles.required` in `.wiki/settings.json` demands an authored
title everywhere: `wiki update` seeds a `title: null` placeholder on every entry
missing one and `wiki lint` fails each placeholder until a title is authored.

Word counts shown by `wiki map` are computed from page bodies and cached in
`.wiki/cache/word_counts.json` under the wiki root — never stored in
frontmatter, so editing a page dirties nothing else. The cache directory ignores
itself via its own `.gitignore` and can be deleted at any time; it is rebuilt on
demand. In the map, a page shows its own count and a folder shows `page/tree`
(its index's words over the subtree total), abbreviated with `k`/`m` suffixes
past a thousand. Descriptions print in full by default — `--desc-limit` (or the
`map.desc_limit` setting) caps them to a character budget, and `-1` forces no
truncation — while `wiki map --stat` sizes the dump (lines, chars, words)
without printing it. The map's indent unit and truncation marker are
configurable via `map.indent` and `map.ellipsis` in `.wiki/settings.json`.

Ranked search uses the same self-ignored cache directory. `wiki search` builds
an SQLite FTS5 index on first use and refreshes added, changed, and removed
Markdown pages before each query. BM25 ranking weights titles, descriptions,
headings, and frontmatter tags above body prose; the default query form safely
combines terms with AND, while `--prefix`, `--tag`, `--raw`, and `--json` cover
agent workflows.

### CLI

Use the `/wiki` skill to manage wikis, or drive the `wiki` CLI directly.

Initialize a wiki in the current project and configure integrations:

- `wiki init` — scaffold a new wiki with a root index
- `wiki config` — install Obsidian plugins and the git merge driver

The merge driver itself lives in each clone's local git config; the committed
`.gitattributes` only names it, so every contributor runs `wiki config` once
after cloning.

A wiki may define a `.wiki/wiki.py` hook — a custom `Wiki` subclass the tool
loads to change indexing or formatting. Because the hook runs code with your
privileges, `wiki` refuses to load one from a wiki you have not trusted (every
command that resolves the wiki fails, naming the hook) and points you at:

- `wiki trust` — authorize the enclosing wiki to run its `.wiki/wiki.py`

Run it once from inside a wiki whose contents you have vetted; it records the
wiki's resolved root in `~/.wiki/settings.json` (override the config home with
`WIKI_CONFIG_DIR`). A wiki with no hook needs no trust. Never trust a wiki
cloned from an untrusted source without first reading its `.wiki/wiki.py`.

Maintain indexes as files are added and removed:

- `wiki lint` — validate structure and flag issues
- `wiki update` — sync index links with the filesystem
- `wiki new` — create an indexed folder with an authored desc and content

`wiki lint` exits 1 on issues and 0 on a clean wiki (soft notes go to stderr and
never affect the exit code — a stale wikilink in prose, or a prose link to a
real file outside every allowlisted folder, is a note, while a broken link in a
generated index block, a prose link naming a folder rather than its `_index`
page, a `./` or `../` prose link that lands inside the wiki, or frontmatter a
strict YAML reader rejects, is an issue). Scripts should read `wiki lint --json`
— one JSON document on stdout listing every issue and note with a `severity` and
`kind` — rather than parse the prose; the exit code is unchanged. A page that
must display otherwise-flagged content — sample conflict markers, stale link
examples — wraps those lines in a `<!-- start: no-lint -->` ...
`<!-- end: no-lint -->` region, which suppresses the positional rules, notes
included, for just that span.

Browse structure, search across content, and read entries:

- `wiki map` — print an indented tree overview
- `wiki search` — rank relevant pages with SQLite FTS5
- `wiki match` — match content with regex
- `wiki read` — read a named entry

Commands other than `init` operate on the enclosing wiki when run from inside
one (the root is the ancestor declaring itself with `.wiki/settings.json`; an
undeclared index tree resolves to its outermost `_index.md`, unless the tree
encloses a declared root — then resolution refuses and directs you to that
root), or else on the `wiki/` folder under the current directory; pass `--path`
to target another wiki. `map`, `search`, `match`, `update`, and `lint` accept an
optional name argument to restrict scope to a subtree. Run `wiki --help` and
`wiki <command> --help` for full option descriptions.

### Formatters

The `***` delimiter and `[[wikilinks]]` are load-bearing syntax:
mdformat/prettier-style hooks rewrite `***` to `---` and backslash-escape the
brackets, demoting the generated link block to plain text. `wiki update` repairs
a mangled index and `wiki lint` names the damage signatures (escaped wikilinks,
a thematic break standing where `***` belongs), but don't rely on the repair —
pick a lane.

For mdformat, add the [mdformat-wiki](https://pypi.org/project/mdformat-wiki/)
plugin, which teaches it to leave wikilinks, frontmatter, and the `***`
delimiter untouched, so wiki faces round-trip byte-identically. Under
pre-commit:

```yaml
- id: mdformat
  additional_dependencies: [mdformat-wiki]
```

If the hook already lists `mdformat-frontmatter`, remove it — both plugins
register a frontmatter renderer and whichever the environment discovers first
wins; when `mdformat-frontmatter` wins, it re-serializes the YAML (quoting
values, blanking `null`s) instead of leaving it untouched.

For formatters with no plugin lane, exclude the wiki root instead — for
prettier, add it to `.prettierignore`:

```text
wiki/
```

## Development

### Install

Run `install.sh` in the package root. With no environment active it creates and
uses a local `.venv`; with one active (e.g. pyenv) it installs into that
environment (editable), without recreating it:

```bash
./install.sh --all-extras --groups=test,lint,type
```

Run `./install.sh --help` for all options. Alternatively, run
`uv sync --all-extras --group test --group lint --group type` and
`uv run pre-commit install` to set up the environment manually.

Installing a dependency as editable (e.g. a sibling package) is left to the
caller: `uv pip install --editable <path>`.

With an editable install, `wiki install --link` symlinks the bundled skill into
the agent skill directories instead of copying it, so skill edits apply without
re-running the install.

Once installed, run tools with `uv run --no-sync <command>`, or activate the
environment first (`source .venv/bin/activate`).

### Tests

Run the test suite:

```bash
pytest .
```

### Linting

Run linters and formatters:

```bash
pre-commit run --all-files
```

### Contributing

The contribution workflow, repository conventions, and release process (version
sources, tagging, CI guard) are documented in:

- Contribution workflow (organization-wide):
  [CONTRIBUTING.md](https://github.com/plasma-ai/.github/blob/main/CONTRIBUTING.md)
- Repository conventions:
  [AGENTS.md](https://github.com/plasma-ai/wiki/blob/main/AGENTS.md)
- Release process (organization-wide):
  [RELEASING.md](https://github.com/plasma-ai/.github/blob/main/RELEASING.md)

Pull requests should be branched from `dev`, not `main`, and opened against
`dev` — `main` only advances at releases.

## Third-Party Software

`wiki` sets up the
[Front Matter Title](https://github.com/snezhig/obsidian-front-matter-title)
Obsidian plugin by Snezhig, which displays each note's `name` frontmatter as its
title. The plugin is licensed GPL-3.0; `wiki init`/`wiki config` download
version 4.1.0 from the upstream GitHub release at setup time rather than
redistributing it.

## License

Licensed under the Apache License 2.0 — see
[LICENSE](https://github.com/plasma-ai/wiki/blob/main/LICENSE).

Copyright © 2026 Plasma AI
