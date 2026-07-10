# wiki

[![license](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![build](https://github.com/plasma-ai/wiki/actions/workflows/build.yaml/badge.svg)](https://github.com/plasma-ai/wiki/actions/workflows/build.yaml)
[![docs](https://github.com/plasma-ai/wiki/actions/workflows/docs.yaml/badge.svg)](https://github.com/plasma-ai/wiki/actions/workflows/docs.yaml)
[![lint](https://github.com/plasma-ai/wiki/actions/workflows/lint.yaml/badge.svg)](https://github.com/plasma-ai/wiki/actions/workflows/lint.yaml)
[![tests](https://github.com/plasma-ai/wiki/actions/workflows/tests.yaml/badge.svg)](https://github.com/plasma-ai/wiki/actions/workflows/tests.yaml)
[![codecov](https://codecov.io/gh/plasma-ai/wiki/branch/main/graph/badge.svg?token=D8LJA7CZ2K)](https://codecov.io/gh/plasma-ai/wiki)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)

Indexed knowledge bases with command-line tools for agents.

A wiki keeps project knowledge as plain markdown, indexed at every level
by `_index.md` files, read by consulting the index and opening only the
pages that a task needs. Andrej Karpathy named this shape the
[LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
pattern, and Google's
[Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)
describes a standardized format for such markdown knowledge bases. Most
approaches leave a great deal of structuring for agents to maintain by
hand; here a deterministic CLI generates the indexes and cross-links and
auto-resolves the generated region of `_index.md` merges when parallel
edits collide — authored content below the delimiter still merges (and
can conflict) like any other text — so content is the only judgment
call.

______________________________________________________________________

**Source**:
[https://github.com/plasma-ai/wiki](https://github.com/plasma-ai/wiki)

**Package**:
[https://pypi.org/project/plasma-wiki/](https://pypi.org/project/plasma-wiki/)

**Documentation**:
[https://docs.plasma.ai/wiki](https://docs.plasma.ai/wiki)

______________________________________________________________________

## Installation

Install the `wiki` package from PyPI:

```bash
pip install plasma-wiki
```

Use `pipx install plasma-wiki` or `uv tool install plasma-wiki` to
install in an isolated environment.

### Skill

Install the `/wiki` skill for your agent via the plugin marketplace
(Claude Code and Codex):

```bash
# Claude Code
/plugin marketplace add plasma-ai/plugins
/plugin install wiki@plasma

# Codex
codex plugin marketplace add plasma-ai/plugins
codex plugin add wiki@plasma
```

Or from the CLI, which copies the skill into `~/.claude/skills` and
`~/.agents/skills` (add `--project` for the current project only):

```bash
wiki install
```

## Usage

A wiki is a tree of markdown files linked together by `_index.md` files.
Each folder becomes a section, and each markdown file becomes an entry.
Wikis are designed to be read and written by both humans and agents:
humans author content in Obsidian (or any editor), and agents query the
wiki through the CLI to ground their work in project-specific knowledge.

Every wiki root carries a `.wiki/` directory — the tool's namespace,
holding `settings.json` (the file that declares the root; `wiki init`
writes it and `wiki update` restores a missing one), the derived word
counts cache, and the staged Obsidian config. Page, folder, and wiki
names are lenient by default: spaces, dashes, mixed case, and unicode
are all fine. Only characters that would break the wiki's structure —
its path, link, and index syntax — are rejected, along with leading dots
(hidden files) and the reserved `_index` name. A wiki can opt into
stricter rules, such as ASCII-only or identifier-style names, through
the `naming` block in `.wiki/settings.json`; `wiki lint` flags any name
that violates the policy.

Frontmatter timestamps default to UTC in ISO-8601. To change them, set a
timezone (any IANA name) and format (a strftime string) under
`timestamp` in `.wiki/settings.json`.

Word counts shown by `wiki map` are computed from page bodies and cached
in `.wiki/cache/word_counts.json` under the wiki root — never stored in
frontmatter, so editing a page dirties nothing else. The cache directory
ignores itself via its own `.gitignore` and can be deleted at any time;
it is rebuilt on demand. In the map, a page shows its own count and a
folder shows `page/tree` (its index's words over the subtree total),
abbreviated with `k`/`m` suffixes past a thousand; the map's indent unit
and description-truncation marker are configurable via `map.indent` and
`map.ellipsis` in `.wiki/settings.json`.

### CLI

Use the `/wiki` skill to manage wikis, or drive the `wiki` CLI directly.

Initialize a wiki in the current project and configure integrations:

- `wiki init` — scaffold a new wiki with a root index
- `wiki config` — install Obsidian plugins and the git merge driver

The merge driver itself lives in each clone's local git config; the
committed `.gitattributes` only names it, so every contributor runs
`wiki config` once after cloning.

Maintain indexes as files are added and removed:

- `wiki lint` — validate structure and flag issues
- `wiki update` — sync index links with the filesystem

`wiki lint` exits 1 on issues and 0 on a clean wiki (soft notes go to
stderr and never affect the exit code). A page that must display
otherwise-flagged content — sample conflict markers, deliberately stale
links — wraps those lines in a `<!-- start: no-lint -->` …
`<!-- end: no-lint -->` region, which suppresses the positional rules
for just that span.

Browse structure, search across content, and read entries:

- `wiki map` — print an indented tree overview
- `wiki search` — search content with regex
- `wiki read` — read a named entry

Commands other than `init` operate on the enclosing wiki when run from
inside one (the root is the ancestor declaring itself with
`.wiki/settings.json`; an undeclared index tree resolves to its
outermost `_index.md`, unless the tree encloses a declared root — then
resolution refuses and directs you to that root), or else on the `wiki/`
folder under the current directory; pass `--path` to target another
wiki. `map`, `search`, `update`, and `lint` accept an optional name
argument to restrict scope to a subtree. Run `wiki --help` and
`wiki <command> --help` for full option descriptions.

### Formatters

The `***` delimiter and `[[wikilinks]]` are load-bearing syntax:
mdformat/prettier-style hooks rewrite `***` to `---` and
backslash-escape the brackets, demoting the generated link block to
plain text. `wiki update` repairs a mangled index and `wiki lint` names
the damage signatures (escaped wikilinks, a thematic break standing
where `***` belongs), but don't rely on the repair — pick a lane.

For mdformat, add the
[mdformat-wiki](https://pypi.org/project/mdformat-wiki/) plugin, which
teaches it to leave wikilinks, frontmatter, and the `***` delimiter
untouched, so wiki faces round-trip byte-identically. Under pre-commit:

```yaml
- id: mdformat
  additional_dependencies: [mdformat-wiki]
```

If the hook already lists `mdformat-frontmatter`, remove it — both
plugins register a frontmatter renderer and whichever the environment
discovers first wins; when `mdformat-frontmatter` wins, it re-serializes
the YAML (quoting values, blanking `null`s) instead of leaving it
untouched.

For formatters with no plugin lane, exclude the wiki root instead — for
prettier, add it to `.prettierignore`:

```text
wiki/
```

## Development

### Install

Run `install.sh` in the package root. With no environment active it
creates and uses a local `.venv`; with one active (e.g. pyenv) it
installs into that environment (editable), without recreating it:

```bash
./install.sh --all-extras --groups=test,lint,type
```

Run `./install.sh --help` for all options. Alternatively, run
`uv sync --all-extras --group test --group lint --group type` and
`uv run pre-commit install` to set up the environment manually.

Installing a dependency as editable (e.g. a sibling package) is left to
the caller: `uv pip install --editable <path>`.

Once installed, run tools with `uv run <command>`, or activate the
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

The contribution workflow is covered by the organization-wide
[CONTRIBUTING.md](https://github.com/plasma-ai/.github/blob/main/CONTRIBUTING.md);
repository conventions live in [AGENTS.md](AGENTS.md), and the release
process (version sources, tagging, CI guard) in the organization-wide
[RELEASING.md](https://github.com/plasma-ai/.github/blob/main/RELEASING.md).

## Third-Party Software

`wiki` sets up the
[Front Matter Title](https://github.com/snezhig/obsidian-front-matter-title)
Obsidian plugin by Snezhig, which displays each note's `name`
frontmatter as its title. The plugin is licensed GPL-3.0;
`wiki init`/`wiki config` download version 4.1.0 from the upstream
GitHub release at setup time rather than redistributing it.

## License

Licensed under the Apache License 2.0 — see [LICENSE](LICENSE).

Copyright © 2026 Plasma AI
