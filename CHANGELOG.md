# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `wiki new <folder> --desc <text> --content <text>` (and `Wiki.new`): the
  generator for deliberate index creation. Both inputs are required -- blanks
  and the `...` placeholder are refused outright, since descriptions and content
  are authored, never auto-stubbed -- and the command writes the folder's
  `_index.md` and wires its rows plus the parent's new row (desc propagated) in
  one pass, so a mechanically generated adoption lands lint-complete instead of
  hiding a hand-fill step. The wiring runs as a scoped `wiki update` of the
  parent subtree -- the whole wiki for a top-level folder -- so pending
  maintenance in that scope (adoptions, prunes) lands in the same run. The
  parent must already exist and be indexed -- each level carries its own
  authored index, so an unindexed parent is refused rather than minted a
  placeholder dangling from the root chain. Every refusal -- the wiring sweep's
  own included -- lands before the write, so a refused adoption leaves nothing
  on disk.
- `wiki lint --json`: one machine-readable JSON document on stdout carrying
  every finding fully typed -- an explicit `issue`/`note` severity, a machine
  `kind`, and per-kind payload fields (`path` always among them, plus e.g.
  `target`/`label` on a broken link or `line` on a wrap mangle) beside the
  rendered prose `text` -- with a summary carrying both counts; exit 1 still
  means issues found. `Wiki.lint` returns `Issue` rows to back it: a `str`
  subclass reading as the prose line everywhere while carrying `kind` and
  `fields`, so library consumers keep string semantics and machine consumers
  never parse prose. The stream split -- issues on stdout, notes on stderr -- is
  documented prominently in the command help, the CLI reference, the guide, and
  the skill: scripts branch on the exit code or read `--json`, never scrape the
  prose streams.

### Changed

- Every command reserves exit 1 for its own nonzero outcome -- `lint`'s issues
  found, `search`'s no match, `update --check`'s pending changes: a command
  error (an unresolvable wiki, a bad subtree entry, a refused hook) exits 2 with
  an `Error:` line on stderr, beside typer's usage errors. A script gating on
  `lint` can never read a failed run as a red corpus, and one gating on
  `update --check` can never read a typo'd `--path` as pending drift.

- `wiki update` prunes broken links: an index row whose target no longer
  resolves to an indexed entry is removed, each removal announced
  (`Pruned N broken links`), so a deleted target takes its row with it instead
  of leaving a dangle for the next merge to trip on -- git carries the history.
  The `--prune` flag and the `Wiki.update(prune=...)` parameter are removed
  (pruning is the behavior, not a mode), and the preserved-broken-link narration
  (`N broken links (run wiki lint to list them)`) is gone with the
  `LinkBreakEvent`/`on_link_break` hook pair. `wiki lint` still reds on every
  dangling row until the sweep runs.

- The `_index.md` merge driver resolves the generated link block to the union of
  both sides' rows instead of taking the current branch's copy wholesale: ours'
  layout wins, and rows present only in theirs (desc continuations included) are
  appended above the closing `***`, so a merge never silently drops rows one
  side added. A row deleted on one side rides back in and the next `wiki update`
  prunes it against the filesystem -- deletion custody lives with update, never
  with the merge -- and lint stays red on any carried row whose target is gone
  until that sweep runs.

- The enclosing git repository's ignore rules now fence indexing, beside
  `exclude.patterns`: a gitignored path is never walked, adopted, minted an
  `_index.md`, or linked, so battery residue can no longer turn lint red and get
  committed by the approved repair (`wiki update` writing frontmatter into
  driver files and minting index cards). Matching is pattern-pure
  (`git check-ignore --no-index`), so a force-tracked file matching a fence is
  fenced all the same; a wiki whose own root is ignored is exempt, and a pruned
  row whose target is fenced gets a cause line
  (`GitignoreSkipEvent`/`on_gitignore_skip`) naming the fence. The fence reads
  the repository's own rules only -- the user-global `core.excludesFile` is
  pinned out of the probe, so fencing is identical on every clone instead of one
  machine's personal patterns pruning rows every other machine re-adds, and the
  probe drops the caller's `GIT_*` environment, so an inherited `GIT_DIR` (git
  hooks export one) can neither fence corpus content by another repository's
  rules nor, pointed where no repository is discoverable, drop the fence and
  adopt the residue it was holding back. A probe that fails inside a repository
  -- git off `PATH`, a broken install -- narrates the degrade
  (`GitFenceUnavailableEvent`/`on_git_fence_unavailable`, a `lint --json` note)
  instead of sweeping unfenced in silence.

- A `--path` (or cwd resolution) landing inside an existing wiki resolves upward
  to the enclosing root -- declared, or the topmost index of a bare chain --
  with a stderr notice naming it, instead of aborting with "Path is inside the
  wiki". The habitual root-relative invocation (e.g. `wiki update --path math`
  from inside `math/`) now works from anywhere in the tree; scoped work still
  goes through the entry argument. `wiki new` keeps the refusal: its name
  argument is a write target, so a rebased root would silently relocate the
  write. A `--path` naming a declared root resolves to that root itself -- a
  vendored wiki inside an excluding host is a sovereign tree, never silently
  retargeted to the host.

### Security

- The trust store's permission self-heal opens `~/.wiki/settings.json` with
  `O_NOFOLLOW` and tightens the opened descriptor (`fchmod`), mirroring the lock
  file; a `settings.json` symlinked out of the config home is now refused
  outright, so a pre-planted symlink can never retarget the repair -- or the
  rewrite behind it -- onto a file outside the store. A multiply-linked store is
  refused the same way (`st_nlink > 1` on the opened descriptor): a hard link is
  the same attack without the symlink `O_NOFOLLOW` can see -- the store is the
  attacker's inode, so the repair would re-mode their file and every trusted
  root written afterwards would be editable through a name the `0700` home does
  not cover.

### Fixed

- `wiki lint` reports a leftover merge repair hint in an `_index.md` as an
  issue. The driver plants its hint above the first conflict marker, which
  normally lands inside the frontmatter (the `updated:` stamps differ first),
  where the hint parses as an authored key -- so a resolution that dropped the
  markers but forgot the hint kept it in every later rewrite with both
  instruments blind to it.
- The description-propagation docs state the `...` placeholder exemption: a
  child carrying the placeholder propagates nothing, so its parent row keeps the
  description it has -- no overwrite, no warning, nothing pending -- rather than
  the unconditional overwrite the guide described.
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
