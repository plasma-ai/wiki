# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `wiki lint --json`: one machine-readable JSON document on stdout carrying
  every finding with an explicit `issue`/`note` severity (notes typed with their
  event kind and payload fields) plus a summary with both counts; the exit-code
  contract is unchanged (1 on issues, 0 otherwise). The prose report's stream
  split -- issues on stdout, notes on stderr, exit 1 on issues only -- is now
  documented prominently in the command help, the CLI reference, the guide, and
  the skill: scripts branch on the exit code or read `--json`, never scrape the
  prose streams.

### Changed

- A `--path` (or cwd resolution) landing inside an existing wiki resolves upward
  to the enclosing root -- declared, or the topmost index of a bare chain --
  with a stderr notice naming it, instead of aborting with "Path is inside the
  wiki". The habitual root-relative invocation (e.g. `wiki update --path math`
  from inside `math/`) now works from anywhere in the tree; scoped work still
  goes through the entry argument.

### Security

- The trust store's permission self-heal opens `~/.wiki/settings.json` with
  `O_NOFOLLOW` and tightens the opened descriptor (`fchmod`), mirroring the lock
  file; a `settings.json` symlinked out of the config home is now refused
  outright, so a pre-planted symlink can never retarget the repair -- or the
  rewrite behind it -- onto a file outside the store.

### Fixed

- Lint's wrapped-list-marker rule no longer false-flags a legal bullet that
  follows a multi-line item whose continuation line is only a code span (for
  example a bare backticked path): such a line masks to blank but the list is
  still open, so list state now closes only at a raw blank line.
- `import wiki.cli.utils` works in a fresh interpreter: the top-level package no
  longer star-exports the Typer app runner over the `wiki.cli` subpackage
  attribute. The `wiki.cli(...)` callable was an accidental top-level alias --
  the CLI is the `wiki` console script (an entry point), not library API.

## [1.2.0] - 2026-07-28

### Added

- `exclude.patterns` indexing exclusions: gitignore-style globs in
  `.wiki/settings.json` that hide paths from indexing entirely. An excluded
  subtree is invisible to every walk — `wiki update` never scaffolds or rewrites
  inside it, `wiki lint` checks nothing there, and `wiki map` / `wiki search`
  never enumerate it — while `wiki read` stays permissive so deliberately
  unindexed content is still inspectable.
- Comprehensive Sphinx documentation — a guide (getting started, structure,
  pages, generation, merge driver, Obsidian), a configuration reference, the
  full CLI reference, recipes, and the skill overview.

### Changed

- `wiki lint` now flags a root-relative prose wikilink that targets a folder
  rather than its `_index` page as an issue; such a link was previously accepted
  silently, so a wiki that linted clean before may now report issues and exit
  non-zero. Stale-link notes suggest the `_index` form of the link.
- `wiki trust` is now idempotent and concurrency-safe: re-trusting an
  already-trusted root leaves the store untouched, and the trust store is
  updated under a file lock so parallel `wiki trust` calls — such as one per
  node across an agent fleet — never lose each other's entries.
- The `wiki` package is now POSIX-only: the concurrency-safe trust store uses
  `fcntl` file locking (declared via an `Operating System :: POSIX` classifier),
  so `import wiki` no longer works on Windows — the library as much as the CLI.

### Fixed

- The home directory is never a wiki root, so a trust store left at
  `~/.wiki/settings.json` no longer reads as a root marker when
  `WIKI_CONFIG_DIR` points the store elsewhere — which had made `$HOME` enclose
  every wiki beneath it, refusing every command (including `wiki init`) anywhere
  under it. `wiki init` now refuses at the home directory outright, where a wiki
  would write its policy into the store.
- `exclude.patterns`: a pattern with consecutive `**` segments no longer takes
  exponential time to match on a deep tree.
- Lint reads prose only: a wikilink in an indented code block or an HTML comment
  is no longer flagged, and a flagged link's display text is carried into the
  suggested `_index` fix.
- Merge driver: fixed a SIGPIPE race when splitting an `_index.md` file's
  frontmatter from its body.

## [1.1.0] - 2026-07-20

### Added

- Obsidian plugin downloads are verified against pinned digests before install,
  so `wiki config` never runs unvetted third-party plugin code.

## [1.0.0] - 2026-07-19

First stable release of `plasma-wiki`: the indexed-knowledge-base engine with
its `wiki` command-line tools, the `_index.md` generation model, the git merge
driver for generated index regions, and the Obsidian integration.

## [0.1.0] - 2026-07-01

Initial release.

[0.1.0]: https://github.com/plasma-ai/wiki/releases/tag/v0.1.0
[1.0.0]: https://github.com/plasma-ai/wiki/compare/v0.1.0...v1.0.0
[1.1.0]: https://github.com/plasma-ai/wiki/compare/v1.0.0...v1.1.0
[1.2.0]: https://github.com/plasma-ai/wiki/compare/v1.1.0...v1.2.0
[unreleased]: https://github.com/plasma-ai/wiki/compare/v1.2.0...HEAD
