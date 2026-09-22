# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions
follow a `major.minor.patch` scheme: while the project is young, minor releases
may include breaking changes, each listed under a Breaking heading.

## [Unreleased]

## [1.6.0] - 2026-09-22

### Breaking

- Every prose wikilink is read from the wiki root: a prefix-free target must
  name something inside the wiki, and a `./` or `../` target (any target
  carrying a `.` or `..` segment) must leave it, reaching a file or another
  wiki's page only under a `links.external` folder, whose entry is the link's
  literal prefix — so one spelling names an external file from every page
  (`[[../src/main.py]]` from `overview.md` and from `notes/meeting.md` alike)
  and moving a page never changes its links. A `./` or `../` link that lands
  outside every `links.external` folder fails `wiki lint` as a new hard issue,
  `outside_link`, whatever is on disk (with `path` and `target` payload fields,
  plus `folder` — the entry to add — and `canonical` — the root-relative
  spelling the page-folder reading names — when present; `target` is the bare
  target as written, the alias riding only in the prose), in place of the
  `link_outside` note: `LinkOutsideEvent`, the `on_link_outside` hook, and the
  `link_outside` JSON kind are removed. A `./` or `../` link that misses under a
  listed folder while naming anything in the wiki from the page's folder — the
  slip `[[../overview]]` from a nested page, meaning the in-wiki `overview.md` —
  fails as `relative_link` with the prefix-free fix. A `./` or `../` link
  authored from a nested page under 1.4.0 or 1.5.0 climbs one folder too far
  under the root reading and draws one of those issues, or a stale note with the
  root-relative fix (`(use [[../src/main.py]])` for `[[../../src/main.py]]` from
  `notes/meeting.md`); `relative_link` rows drop the `external` field, and
  `canonical` in `link_stale` and `directory_link` rows is root-relative.
  `[[..]]` names the folder holding the wiki and follows the outside-folder
  rule. A 1.4.0 or 1.5.0 clone reads a root-relative `../x` from the page's
  folder: on a root page the two readings agree, and on a nested page it fails
  every such link as `relative_link` — the outside-the-wiki spelling it offers
  (`[[../../src/main.py]]` for `[[../src/main.py]]` from `notes/meeting.md`)
  climbs one folder too far — so upgrade every clone of a shared wiki together.
  A wiki with no `./` or `../` link and no absolute path into the wiki in prose
  draws no new issue and exits as before.
- An absolute path to a target inside the wiki fails `wiki lint` as a new hard
  issue, `absolute_link`, naming the prefix-free form (`(use [[core/design]])`
  for `[[/Users/me/repo/wiki/core/design]]`): an in-wiki target has one
  spelling, and an absolute path spells one machine's layout, which no other
  clone shares. The issue carries `path`, `target`, and `canonical` when a
  target exists there. An absolute target outside the wiki stays a stale note.

### Added

- `wiki init` and `wiki config` seed Obsidian's new-link format to
  vault-absolute paths (`newLinkFormat: absolute` in `.obsidian/app.json`) when
  the vault has no value of its own, so the links Obsidian's autocomplete writes
  are the prefix-free form `wiki lint` reads; a format the vault already carries
  is left alone on every run.
- The Wiki Root Links Obsidian plugin, bundled with the package under its
  Apache-2.0 license: `wiki init` and `wiki config` copy it into
  `.obsidian/plugins/wiki-root-links/` and enable it in `community-plugins.json`
  on every run, beside Front Matter Title, with no download (it installs
  offline) and no change to the staged `.wiki/obsidian/`; Obsidian's Restricted
  Mode toggle enables both plugins in one step. Desktop only. With it enabled,
  Obsidian reads a `./` or `../` link — a wikilink or a markdown-style
  `[text](../page.md)` link, which `wiki lint` does not check — as `wiki lint`
  reads a wikilink, never resolving it to a note in the vault, and a follow of
  one — a click in reading view or Live Preview, or any path Obsidian routes
  through its link-opening method — never creates folders outside the vault: a
  markdown target inside a vault Obsidian registers on this machine opens in
  that vault through an `obsidian://open` URI, any other target draws a notice
  naming the root-relative path (with ` (not found)` when nothing is there), and
  a dot link that lands back inside the vault draws a notice naming its
  prefix-free form (`Inside the vault: use [[overview]]`, or
  `Inside the vault: overview (not found)` when nothing is there). The plugin
  never hands a filesystem path to the operating system and reads no wiki
  settings; when the link resolver or the link-opening method it wraps is
  unavailable it says so at load and falls back to stock behavior.

### Changed

- The fix `wiki lint` suggests for an absolute wikilink target under a
  `links.external` folder is root-relative (`(use [[../src/main.py]])` from
  every page).
- The Restricted Mode reminder `wiki init` and `wiki config` print names both
  plugins: `then enable Front Matter Title and Wiki Root Links if needed`.
- The strict reader's quoting verdicts and composed frontmatter blocks are held
  for one operation rather than for the life of the process: each `Wiki`
  operation (`update`, `lint`, and the rest) memoizes them while it runs and
  frees them when it returns or raises, so a library consumer that runs `update`
  or `lint` many times in one process — on one `Wiki` or a fresh one per call —
  holds one run's working set while an operation runs (the memo itself, about 23
  MB on a 5,000-page wiki of short blocks) and no memo between operations,
  instead of adding 2 to 5 KB per page with every run. A `wiki` command is
  unchanged in time, memory, issues, notices, and written files; a second
  operation in the same process composes the blocks it shares with the first
  once more, about a tenth of a second on a 5,000-page wiki, or about three
  quarters of a second on a wheel without libyaml. A `wiki.core.format` function
  called outside an operation computes without a memo, a process forked from
  inside an operation (a process pool a hook starts) opens its own runs, and a
  subclass operation that reads frontmatter itself carries
  `wiki.core.format.run_scoped` to memoize, whether or not it calls the base
  method.

### Fixed

- `wiki search` returns pages of equal score in path order, so a `--limit` cut
  picks the same pages on every run — tied pages otherwise come back in an order
  that follows the process hash seed and can change between runs. `Wiki.search`
  orders the same way.
- `wiki search` rebuilds the index when a journal file beside it
  (`search.db-wal` or `search.db-shm` under `.wiki/cache/`) cannot be written,
  whatever SQLite build the Python carries. With Apple's system SQLite, which a
  `pyenv`-built Python on macOS typically links, a stale read-only
  `search.db-shm` otherwise fails every search immediately with
  `database is locked` instead of rebuilding as it does under the SQLite that
  `uv` and Homebrew Pythons carry. `Wiki.search` rebuilds the same way.
- `wiki init` and `wiki config` fail a `.obsidian/*.json` file holding bytes
  that are not UTF-8 as `Malformed JSON in .obsidian/<file>: ...`, naming the
  file as they do for bad JSON, instead of the bare codec error.

## [1.5.0] - 2026-09-19

### Changed

- The strict reader's quoting verdicts and composed frontmatter blocks are held
  for the life of the process, however many pages the wiki holds: a `wiki`
  command frees them at exit, while a library consumer that runs `update` or
  `lint` many times in one process keeps them. A command's peak memory rises by
  about 13 MB on a 5,000-page wiki of short blocks, 26 MB on one whose blocks
  carry a paragraph-long `desc`, and about 100 MB on a 24,000-page wiki of short
  blocks; a later run in the same process adds 2 to 5 KB per page.

### Fixed

- `wiki lint` and `wiki update` on a wiki of several thousand pages spend far
  less time in the strict reader: the run's `created:`/`updated:` stamp is
  judged for quoting once rather than parsed as YAML at every write, `wiki lint`
  takes its malformed-frontmatter verdicts from the plan it already diffs
  against rather than repairing every block a second time, and the composed
  blocks are kept across the run's passes rather than evicted between them (the
  Changed entry above states what a long-lived process then holds). On a
  5,000-page wiki this alone brings `wiki lint` to about 1.08x the time before
  the strict reader and `wiki update --check` to 1.15x, down from about 1.4x and
  1.5x; a 300-page wiki pays nothing measurable. On a tree no other writer edits
  during the run, issues, notices, and written files are unchanged, except for
  the one lint row the next entry describes.
- `wiki lint` reports a frontmatter body that is valid YAML but not `key: value`
  pairs — a quoted scalar spanning lines with a column-0 `updated:`-shaped line
  inside, as the whole body or inside a collection, on a page or an index — as
  its `Invalid YAML frontmatter` issue alone, matching `wiki update`'s
  `Malformed frontmatter (not a key: value mapping)` notice, rather than also as
  `Malformed frontmatter (its repair would break the YAML)` for a repair update
  never attempts.
- `wiki lint` and `wiki update` on a wiki of several thousand pages repeat far
  less of their own work: a run walks its scope once for its plan and the checks
  before it, a walk `wiki lint` inside a repository holds until it finishes at
  about 2.5 KB per indexed folder and per non-markdown file (5 MB on a
  5,000-page wiki of about 2,000 folders, 17 MB on one of about 4,000 folders
  and 3,100 non-markdown files); an index is planned from one listing of its
  folder; a content scan skips a text that cannot hold the shape it looks for;
  and the frontmatter reader takes a fast path through a block with no blank
  line and a body that cannot nest past its bound. On a 5,000-page wiki
  `wiki lint` runs in about 0.55x the time before the strict reader and
  `wiki update --check` in about 0.63x, a converged `wiki update` in about 0.6x,
  and a 300-page wiki gains about a quarter. On a tree no other writer edits
  during the run, issues, notices, exit codes, and written files are unchanged,
  with two exceptions: `wiki map`, a scoped `wiki update`, and a `wiki new`
  below the top level complete on a tree holding an empty folder the process may
  list but not enter (outside the scope the update or `new` sweeps), instead of
  aborting with an error naming the folder's `_index.md` on an interpreter whose
  `pathlib` raises for it; and on a tree holding a page the process cannot read
  and, later in walk order, a folder it cannot list, the same commands' error
  names the page rather than the folder, with the same exit code. A page or
  folder another writer creates after the run's one walk gains its parent index
  row this run and its own frontmatter or `_index.md` on the next.

## [1.4.0] - 2026-09-17

### Breaking

- `wiki lint` reports frontmatter a strict YAML reader rejects as a new hard
  issue, `invalid_yaml` (with `line` and `reason` payload fields) — for example
  an unquoted `: ` inside a one-line value, a value ending in `:`, a value
  opening with an indicator character (`- % @ * | >` and the like), a duplicate
  key, a control character, a key that is not a scalar, or a body that is not
  `key: value` pairs. A wiki that linted clean may lint red until the offending
  values are quoted; the fix is the author's — `wiki update` never rewrites an
  authored value. The verdict is the installed PyYAML build's: a wheel without
  libyaml runs the pure-Python loader, which words its errors differently and
  rejects a tab inside a plain value the C loader accepts (such a block then
  reads through the line grammar). A tag no reader constructs (`!custom`,
  `!!int` over text) is not checked: the block composes, so it lints clean and
  reads as its text.
- Frontmatter values are read the way a strict YAML reader reads them: a value
  continued on indented lines folds into one, a quoted value decodes, an
  indented `# comment` under a bare key is a comment, a bare key with trailing
  spaces reads its body, a space followed by `#` starts a comment, and `null`
  followed by a comment unsets like `null`. The first `wiki update` after
  upgrading may rewrite a `name:` line, a parent row, a `[category]` label, or
  an H1 once on pages carrying such shapes — run `wiki update --check` after
  upgrading to list them — and the search index rebuilds on the next
  `wiki search`. A clone still on an earlier version reverts those rewrites on
  its next `wiki update` (re-stamping `updated:`) and reads a comment-carrying
  placeholder (`desc: ... # note`) as an authored description, so upgrade every
  clone of a shared wiki together. A block a strict reader rejects still reads
  through the line grammar. A UTF-8 BOM opening a page or index file is removed
  on that first update, as one opening the block's body is. `wiki lint` and
  `wiki update` take longer on wikis of several thousand pages (about 1.5x on a
  5,000-page wiki), since every frontmatter value is read through the YAML
  reader.
- PyYAML (`pyyaml>=6,<7`) is a runtime dependency; environments synced before
  this release need a `uv sync` (or a reinstall).
- `wiki lint` reports a `./` or `../` prose wikilink that lands inside the wiki
  as a new hard issue, `relative_link` (with `path`, `target`, `canonical`, and
  `external` payload fields — the last two only when a fix resolves). A prefixed
  target is read from the page's folder, as Obsidian and markdown read it, and
  must point outside the wiki: the in-wiki form is prefix-free, so the issue
  names it (`(use [[overview]])`) and, when the same text read from the wiki
  root reaches a real file under a `links.external` folder, that file's
  page-relative spelling too
  (`(use [[../../src/main.py]] for the path outside the wiki)`). The
  folder-relative slip `[[../overview]]` from a nested page, which was a soft
  `Stale link` note carrying the same `(use [[overview]])` suggestion, is now
  this issue; so is a `./` target, or a `.` or `..` segment after a plain one,
  that resolved to a real page from the wiki root (`[[./overview]]`,
  `[[core/../overview]]`, `[[../wiki/overview]]` through the wiki's own folder
  name), which linted clean before. A wiki with no such link draws no new issue
  and exits as before.

### Added

- `links.external` external link folders: a list in `.wiki/settings.json` of
  folder paths relative to the wiki root, each climbing out of it with leading
  `..` (`{"links": {"external": ["../src", "../math"]}}`), under which prose
  wikilinks may leave the wiki. Two link bases, one rule each: a prefix-free
  target is read from the wiki root and must name something inside it; a `./` or
  `../` target is read from the page's folder, as Obsidian and markdown read it,
  and must leave the wiki. Under a listed folder present on this machine
  `wiki lint` holds a link live when the file, its `.md` page, or the folder
  exists and notes a missing target stale; a target inside another wiki (a
  folder holding `.wiki/settings.json`) is judged by that wiki's own settings —
  a page by stem is live, a folder it indexes is the `directory_link` issue
  naming its `_index` page, a folder its `exclude.patterns` keeps out is live —
  read as a JSON parse that runs none of that wiki's code (a settings file there
  that is not valid JSON or not a JSON object, whose `exclude` block is invalid,
  or that cannot be read — its `.wiki` folder included — fails lint naming that
  wiki once a link needs its rules, any target but a page by stem; the home
  directory and the config home never count as a wiki). A real file outside
  every listed folder is noted with the entry to add, and a folder holding an
  `_index.md` with the index page a wiki would link it by (`LinkOutsideEvent`,
  hook `on_link_outside`, JSON kind `link_outside` with `path`, `target`,
  `folder`, and an optional `canonical`), and an entry naming no folder on this
  machine draws one note per run while the links into it go unchecked
  (`LinkFolderMissingEvent`, hook `on_link_folder_missing`, JSON kind
  `link_folder_missing` with `folder` and no `path`). The allowlist is a lint
  rule alone: `wiki read`, `map`, `match`, `search`, `update`, and `new` stay
  confined to the root, and generated index rows never carry an external target,
  so `wiki map` never shows an external folder. Obsidian reads `./` and `../`
  from the note's folder too, so nothing lint accepts renders as a different
  file there, but it cannot see outside the vault, so an external link shows
  unresolved — do not click it: Obsidian creates the missing target at that
  path. `wiki init` does not seed the block but validates one passed through
  `--settings`; `wiki lint` reads it on every run and `wiki update` never does;
  earlier versions ignore it and note external links as stale.

### Changed

- The fields of a block a strict reader accepts are bounded by the parser's own
  key positions, so a quoted key (`"desc": x`) is the field it names, and a
  quoted scalar or flow collection continued at column 0 moves as one field
  under `wiki update` and matches as one under `wiki match --field`. A block the
  parser rejects keeps one line grammar for the repair, the field order, and
  `match --field` alike: a column-0 `[\w.-]+` key whose colon is followed by a
  space or the line end — so a dotted key (`com.example:`) sorts as a custom
  field below the known fields and above the timestamps instead of riding along
  under the field before it, and a `key:value` line with no space after the
  colon is text to the wiki as it is to YAML (`wiki lint` names it; the fix is
  one space).
- Reading the shapes at the strict reader's edges: a double-quoted `\0` escape
  in a title or desc is dropped on read, a double-quoted carriage-return escape
  in a value reads as a line break, and an escape naming no character (past
  U+10FFFF, or a lone surrogate) reads verbatim; a self-referential or shared
  alias graph (`tags: &a [*a]`) composes in bounded time, and collections nested
  past 100 levels are an `invalid_yaml` finding on the line that passes the
  bound.
- `wiki lint`'s `invalid_yaml` line is the offending line whatever precedes it:
  a NEL, LS, or PS the parser counts as a line break does not shift it; an
  unterminated quote or a stray line is reported where it starts rather than
  where the parser gave up; a `key:value` typo is reported on its own line
  rather than the line after it (a blank line between them included), while a
  `: ` on a key line after a multi-line field is reported on the key line; a
  duplicate key inside a nested mapping is reported on its own line, nested
  duplicate keys are listed in line order, and a duplicate anchor names the
  first occurrence; an unclosed flow collection is reported on the line holding
  it; and a second document's reason reads as a sentence.
- Under the line grammar an unclosed `[` or `{` reads to the next key line,
  never through the stamps and the closing fence into the H1 and the parent row;
  a quote left open inside a flow collection carries to the next line, so a
  stray blank after the collection is stripped; a quote mid-text is content
  (`title: 'Bob's Page'` reads whole — the close is the quote nothing but
  whitespace or a comment follows — and `wiki match --field` sees the same
  value), and a quoted value ends at its closing quote; a `null` on the key line
  over an indented `null` is the text `null null`; node properties separated by
  more than one space strip as one does; and a `desc: |` or stamp header over
  column-0 comment lines alone is an empty value the repair fills, the comments
  kept under it.
- A repair that would close a quote the line grammar cannot see around the lines
  it writes (`tags: "open` above a `name: "x` line, a stamp continued at column
  0 above an open quote) is refused with the malformed notice, which `wiki lint`
  reports too; a comment line after a quoted stamp's closing quote
  (`updated: "..."` over `# todo: verify`) does not refuse the repair. A `#`
  inside a quoted stamp is text, never a comment for the re-stamp to re-attach,
  and a stamp written as a flow sequence or mapping on its key line
  (`created: [2025-01-01]`, `created: {}`) is an unparseable stamp to
  `wiki lint`, as one written under the key is.
- `wiki match --field` composes a block over 64 KB once per file.
- Pages are planned before their indexes, so a parent index row reads its
  child's repaired frontmatter in the same run and `wiki update` narrates page
  notices — and their condensed count lines — before index notices. The H1 and
  the parent row are read from the block as the write leaves it, so a block the
  parser rejects only until the `updated:` re-stamp closes its quote or drops
  its bracket reads through the parser in the same run; a block whose only alias
  sat on `updated:` is ordered by the write that replaces the alias; a UTF-8 BOM
  at column 0 of any block line (which the C loader skips, so a `# comment`
  behind one is a comment) is dropped by the repair; and under `titles.required`
  the plan orders a block after seeding `title: null`, as the write does — so
  each converges in one run.
- The `wiki update` narration counts files with malformed frontmatter without
  the `(no closing ---)` suffix; each per-file notice names its reason.
- A `./` or `../` prose wikilink that leaves the wiki and resolves to a real
  file or folder outside every `links.external` folder is noted as
  `Link [[../docs/guide]] points outside the wiki (add '../docs' to links.external in .wiki/settings.json to allow it)`
  instead of `Stale link [[../docs/guide]]` — still a note, never an issue; a
  target with nothing at its path keeps the stale note, as does one whose entry
  the policy would refuse (reached through a symlink alias of the wiki, at the
  filesystem root, or through a folder name carrying a backslash).
- A prose wikilink's target is read without surrounding spaces and tabs:
  `[[ page ]]` links `page` (so `[[ notes ]]` is judged as the folder `notes` —
  the directory-link issue where the padded text was a stale note), and a target
  of only spaces and tabs (`[[ ]]`) is no link at all, as `[[]]` never was. A
  target carrying `[` (`[[[page]]`, `[[page[x]]`) is junk no name can carry: it
  stays the stale note it always was, quoted as written, and is never read from
  the page's folder.
- The `(use [[...]])` suggestion also names a raw file's spelling inside the
  wiki — `(use [[Makefile]])` on the relative-link issue for a `[[../Makefile]]`
  slip to a root-level `Makefile`, `(use [[notes/Makefile]])` on the stale note
  for a `[[Makefile]]` in `notes/` that resolves only from the page's folder —
  and the page-relative spelling of an allowlisted file a prefixed or absolute
  link missed (`Stale link [[/repo/src/main.py]] (use [[../../src/main.py]])`).
- An interrupted command (Ctrl-C) prints `Interrupted.` on stderr before it
  exits 130.

### Fixed

- A `.wiki/settings.json` nested past the JSON parser's recursion limit fails as
  `Malformed JSON in .wiki/settings.json: ...`, naming the file (and, for an
  allowlisted wiki, that wiki), instead of a bare recursion error.
- A `HOME` or `WIKI_CONFIG_DIR` that is a symlink loop no longer fails every
  command with `Symlink loop from ...` on Python 3.11 and 3.12: root resolution
  compares real paths, as the engine's guest-wiki lookup does, and probes the
  root marker through `os.path`, so an unsearchable folder on the way up reads
  as no marker instead of raising.
- `wiki lint` no longer slows quadratically with the number of wikilinks on one
  page: the scan counts line numbers once across the page instead of from its
  top for every link.
- A stamp written as a bare `created:`/`updated:` key over an indented line is a
  value: `wiki update` no longer stamps the run's clock onto the key line and
  strands the authored stamp below it, and the `updated:` re-stamp replaces the
  whole value. A quoted-empty stamp (`created: ''`) is stamped like a blank one;
  a stamp written as a sequence is an unparseable stamp to `wiki lint`.
- Refreshing a `name:` keeps the comment on its key line and an indented
  `# comment` under a bare key, and reads past a UTF-8 BOM opening the body
  instead of hiding the `name:` line behind it and drawing a duplicate.
- `title: null # comment` and `desc: | # comment` are unset like `title: null`
  and `desc: |`: update removes the title line and restores the desc
  placeholder. A block the parser rejects reads its fields the same way:
  `null # comment`, a bare key over an indented `null`, and a comment-only value
  are unset there too, so the reader and the repair agree and update converges
  in one run.
- Filling or removing a valueless field keeps its comments: `desc: # note`
  becomes `desc: ... # note`, the comment on an unset `title:` or `category:`
  stays behind as a column-0 comment line (never indented into a block-scalar
  neighbor as its content), and the comment lines under a stamp stay under the
  fresh stamp, the `updated:` re-stamp's tail comment included. Every leading
  valueless copy of `title:`/`category:` goes in one run.
- A sequence written at column 0 under `desc:`, `category:`, or a timestamp key
  is that field's value, and so is a column-0 `# comment` between a key and its
  indented body: update no longer restores the placeholder or stamps the key
  line and strands the lines under the field before it.
- A frontmatter block that is valid YAML but not `key: value` pairs is left
  untouched with a notice instead of gaining fields appended under the text; so
  is a mapping whose keys the byte-level repair cannot place (a flow mapping, an
  indented one, an explicit `? key`, an alias used as a key, a `<<` merge key,
  two keys on one line broken by a NEL), which `wiki lint` reports as malformed
  frontmatter. A block carrying an alias is never reordered, since the anchor
  must stay above it; a re-stamped or filled stamp keeps the anchor on its key
  line, so an alias of it keeps resolving; and a repair that would leave an
  accepted block rejected is refused with the same notice, which `wiki lint`
  reports too. A trailing `...` document-end marker stays the block's last line.
- A block scalar opened by a sequence item or a nested key (`- k: |+`,
  `meta:\n  inner: |+`), or behind a node property (`key: &a |+`), keeps its
  trailing blank line, and a quoted scalar continued at column 0 keeps the blank
  lines inside it.
- A node property over nothing (`created: !!str`, `desc: &d`) is a blank the
  repair fills behind the property, and `wiki match --field` matches the value
  past a property rather than the property itself; a block scalar behind a node
  property (`desc: &a |`) reads its body and a collection's node property
  (`tags: &t [a]`) is not its text, so neither the parent row nor
  `wiki search --tag` sees the property; a `... # end` marker, or comment lines
  after the marker, stay the block's last lines; a body that is not a mapping
  reads no fields at all, so a `key:` spelled inside a list draws no other
  finding and matches no field.
- `wiki match --field` strips a quoted key whose quotes escape or double a quote
  from its line, keeps a flow collection whole past a `#` inside its quotes
  (`tags: ['alpha #note', beta]`), and leaves a quoted scalar's continuation
  line (`two: three"`) unstripped, since the composed block says it opens no
  key.
- A collection's `' #'` tails (`tags: [a, b] # note`, `- alpha # note`) are not
  its text, so `wiki search --tag` no longer sees comment words as tags; under
  the line grammar a bare key over column-0 items reads the items (as the
  composed block does), a block scalar ends at the first line indented less than
  its body (`title: |4` over a two-space `# note`), and a column-0 item after a
  value on the key line (`name: x` over `- draft`) is text outside the field,
  kept in place rather than deleted with the refreshed name.
- A value a strict reader resolves as a number and cannot construct (`0x_`,
  `0b_`) is written quoted, as a date no calendar has is; `strip_blank_lines`
  reads a block header behind a quoted key spelled with a space before its colon
  (`"desc" : |+`), so its trailing blank survives; and a tab on a trailing
  whitespace-only line of the block is reported on that line.
- `wiki update` and `wiki lint` recognize conflict markers of any length git's
  `conflict-marker-size` attribute lengthens them to, not only the
  seven-character default the driver forwards.
- The `_index.md` merge driver normalizes `created:` whenever the base index
  lacks the stamp (a hand-written or imported index, not only an empty add/add
  base) and gives a side lacking a regenerated key the current branch's copy of
  it where `wiki update` places the key, so two branches that each ran
  `wiki update` over such an index merge clean; and a link row both sides carry
  takes the other side's text when only that side changed it against the base
  (however the rows are spaced on each side), so an authored desc on an asset
  row or a placeholder-desc child is no longer lost to the current branch's
  copy. An add/add merge of two indexes keeps both sides' rows, the H1, and the
  `***` line.
- Under the line grammar a quoted value closing on a later line folds its lines
  up to the closing quote (a hand-wrapped `title: "..."` reads whole instead of
  as its first line with a stray quote), a flow collection's `' #'` tail is not
  its text, a node property over nothing is an absent value, and a `: ` on the
  third or later line of a plain value is reported on its own line. The nesting
  bound counts a bracket only where a value opens with one — a block scalar's
  body, a plain value's text (`rock 'n roll`, `a [b`), and its continuation
  lines are never collections — so a valid block never lints red for its
  brackets.
- A quoted item or scalar wrapped across lines keeps a `#` inside its span
  (`tags: ['a wrapped\n  item #x', beta]` reads whole, so `search --tag` sees
  every item). The `_index.md` merge driver's row rule compares rows without
  their trailing blank lines under CRLF and whitespace-only separators too, so
  such an index does not lose the other side's row edit to a re-spaced current
  branch.
- Under the line grammar a bare `desc:`/`title:` over an indented quoted body
  reads the quoted scalar (`desc:` over `'A: colon here.'` reads
  `A: colon here.`), a quoted item keeps its `#` (`- 'notes #draft'`), and
  `wiki match --field` matches a quoted value continued on the next line from
  its first character. The `_index.md` merge driver treats a bare or
  quoted-empty `created:` in the base as no stamp, so two branches that each
  filled it merge clean.
- A repair that would fold an authored key line into a quoted value it closes
  (`zebra: 27"` moved under `desc: "open`, or a `desc: D.` line inside a quoted
  `name:` the refresh rewrites) is refused with the malformed notice instead of
  written; a `[category]` label reads the block as the write leaves it, like the
  H1 and the row; and a blank line an unclosed quote or bracket in `updated:`
  kept as content goes with the re-stamp that closes it, so neither takes a
  second run.
- A sequence or mapping value reads as its source lines joined (comment lines
  dropped), so `wiki search --tag` sees a tag written at column 0 or on the
  second line of a flow sequence, and a `' #'` inside a quoted item stays; under
  the line grammar a plain value folds its indented continuation lines, node
  properties before a key or value are not the value, and `wiki match --field`
  strips a key spelled with spaces from its line. A date-shaped value no
  calendar has (`2024-02-30`) is written quoted, since a strict reader cannot
  construct it plain.
- The `_index.md` merge driver treats every comment under a regenerated key as
  authored: a deletion or rewording on one side lands through the ordinary
  three-way merge instead of ours' copy resurrecting it or a conflict. A comment
  between the key and its indented value belongs to the value's extent, so it
  moves with the value rather than stranding the value lines under the other
  side's one-liner.
- `wiki new` (`NAME`, `--desc`, `--content`) and `wiki init` (`NAME`) refuse an
  argument holding a byte no UTF-8 decodes, before anything lands on disk; the
  `_index.md` merge driver merges such a byte verbatim instead of aborting.
- Values the tool writes — a `name:`, an adopted heading seeded as `title:`, a
  `wiki new` desc, a timestamp — are quoted whenever a plain scalar would
  misread them: a leading indicator character, a ` #` comment start, leading or
  trailing whitespace; a value holding a control, C1, line-separator, or
  noncharacter code point is double-quoted with it escaped, a multi-line
  `wiki new` desc included, and `wiki match --field` decodes every double-quoted
  escape a strict reader decodes. The tool writes `<<` and `=` quoted, as a
  strict reader constructs them as merge and value indicators otherwise.
- The `_index.md` merge driver moves a regenerated key with its indented
  continuation lines, so a block-scalar `name:` on one side no longer strands
  its body under the other side's one-liner; leaves a blank line separating the
  key from the next field in place on every side, byte for byte (a CRLF or
  whitespace-only separator no longer conflicts); keeps a `# comment` the other
  side wrote under the key; matches a key spelled with a space before its colon;
  and handles an extent of any size.
- `wiki lint`'s "Missing period in desc" names a ` #` comment as the likely
  cause when a plain value's lines carry one, the key line or a continuation
  line, in a block the parser accepts.
- `wiki match --field` sees a sequence item written at column 0 whose text holds
  a colon (`- https://doi.org/...`) and, in a block the parser accepts, a
  flow-sequence continuation line (`https://b]`) as part of its field, and a key
  written with a space before its colon (`desc : x`) as the field it names, so
  `wiki update` no longer inserts a duplicate `desc:` beside one.
- `wiki update` keeps a whitespace-only line indented past a block scalar's body
  and the trailing blank of a keep-chomping block inside a column-0 sequence
  item, both of which are content.
- A multi-line desc whose first content line opens with a space or a tab writes
  its block scalar with an explicit indentation indicator (`desc: |2`), so a
  strict reader takes the leading whitespace as content instead of mis-detecting
  the block's indentation and rejecting it.
- `wiki lint` no longer exits 2 on Python 3.11 to 3.13 when a prose wikilink or
  an index row names a path the filesystem cannot stat (a name over 255 bytes,
  an unreadable directory): every link probe goes through `os.path`, so the
  target reads as missing — a stale note for prose, a broken-link issue for a
  generated row — as it does on 3.14.

## [1.3.1] - 2026-08-25

### Fixed

- A read-only search index heals like a corrupt one: a fault in the
  `SQLITE_READONLY` family — a read-only `search.db`, or the stale read-only WAL
  companion a permission fault leaves behind — discards and rebuilds the derived
  index, so `wiki search` recovers once the cause is gone instead of failing on
  every later query. A read-only cache directory stays a single clean error.
- Duplicate terms in a `wiki search` query collapse before matching, so a
  repeated word no longer inflates query cost quadratically. Results are
  unchanged — with `--prefix`, the final term still keeps its exact twin.
- `wiki update`, `lint`, `search`, `match`, and `map` tolerate pages and folders
  deleted while a run is in flight: a path that vanishes between the walk and
  its read drops out of the walk, and the next run converges, instead of the
  command aborting with an error.

## [1.3.0] - 2026-08-24

### Breaking

- `wiki search` is now ranked full-text retrieval; the regex line search
  previously at `wiki search` lives at `wiki match`, with flags, output, and the
  grep exit triple unchanged. `Wiki.search` and `Wiki.match` split the same way
  in the library API.
- Every command reserves exit 1 for its own nonzero outcome — `lint`'s issues
  found, `search`'s no match, `update --check`'s pending changes: a command
  error (an unresolvable wiki, a bad subtree entry, a refused hook) exits 2 with
  an `Error:` line on stderr, beside typer's usage errors. A script gating on
  `lint` can never read a failed run as a red corpus, and one gating on
  `update --check` can never read a typo'd `--path` as pending drift.
- `wiki update` prunes broken links: an index row whose target no longer
  resolves to an indexed entry is removed, each removal announced
  (`Pruned N broken links`), so a deleted target takes its row with it instead
  of leaving a dangle for the next merge to trip on — git carries the history.
  The `--prune` flag and the `Wiki.update(prune=...)` parameter are removed
  (pruning is the behavior, not a mode), and the preserved-broken-link narration
  (`N broken links (run wiki lint to list them)`) is gone with the
  `LinkBreakEvent`/`on_link_break` hook pair. `wiki lint` still reds on every
  dangling row until the sweep runs.

### Added

- `wiki new <folder> --desc <text> --content <text>` (and `Wiki.new`): the
  generator for deliberate index creation. Both inputs are required — blanks and
  the `...` placeholder are refused outright, since descriptions and content are
  authored, never auto-stubbed — and the command writes the folder's `_index.md`
  and wires its rows plus the parent's new row (desc propagated) in one pass, so
  a mechanically generated adoption lands lint-complete instead of hiding a
  hand-fill step. The wiring runs as a scoped `wiki update` of the parent
  subtree — the whole wiki for a top-level folder — so pending maintenance in
  that scope (adoptions, prunes) lands in the same run. The parent must already
  exist and be indexed — each level carries its own authored index, so an
  unindexed parent is refused rather than minted a placeholder dangling from the
  root chain. Every refusal — the wiring sweep's own included — lands before the
  write, so a refused adoption leaves nothing on disk.
- `wiki lint --json`: one machine-readable JSON document on stdout carrying
  every finding fully typed — an explicit `issue`/`note` severity, a machine
  `kind`, and per-kind payload fields (`path` always among them, plus e.g.
  `target`/`label` on a broken link or `line` on a wrap mangle) beside the
  rendered prose `text` — with a summary carrying both counts; exit 1 still
  means issues found. `Wiki.lint` returns `Issue` rows to back it: a `str`
  subclass reading as the prose line everywhere while carrying `kind` and
  `fields`, so library consumers keep string semantics and machine consumers
  never parse prose. The stream split — issues on stdout, notes on stderr — is
  documented prominently in the command help, the CLI reference, the guide, and
  the skill: scripts branch on the exit code or read `--json`, never scrape the
  prose streams.
- Ranked full-text search through `wiki search`. A zero-dependency SQLite FTS5
  index under `.wiki/cache/` refreshes incrementally before each query, weights
  titles, descs, headings, and tags above body prose, and supports subtree, tag,
  prefix, raw-FTS5, limit, and JSON controls. Index pages stay out of the
  results — their link blocks duplicate child names and descs.

### Changed

- The `_index.md` merge driver resolves the generated link block to the union of
  both sides' rows instead of taking the current branch's copy wholesale: ours'
  layout wins, and rows present only in theirs (desc continuations included) are
  appended above the closing `***`, so a merge never silently drops rows one
  side added. A row deleted on one side rides back in and the next `wiki update`
  prunes it against the filesystem — deletion custody lives with update, never
  with the merge — and lint stays red on any carried row whose target is gone
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
  the repository's own rules only — the user-global `core.excludesFile` is
  pinned out of the probe, so fencing is identical on every clone instead of one
  machine's personal patterns pruning rows every other machine re-adds, and the
  probe drops the caller's `GIT_*` environment, so an inherited `GIT_DIR` (git
  hooks export one) can neither fence corpus content by another repository's
  rules nor, pointed where no repository is discoverable, drop the fence and
  adopt the residue it was holding back. A probe that fails inside a repository
  — git off `PATH`, a broken install — narrates the degrade
  (`GitFenceUnavailableEvent`/`on_git_fence_unavailable`, a `lint --json` note)
  instead of sweeping unfenced in silence.
- A `--path` (or cwd resolution) landing inside an existing wiki resolves upward
  to the enclosing root — declared, or the topmost index of a bare chain — with
  a stderr notice naming it, instead of aborting with "Path is inside the wiki".
  The habitual root-relative invocation (e.g. `wiki update --path math` from
  inside `math/`) now works from anywhere in the tree; scoped work still goes
  through the entry argument. `wiki new` keeps the refusal: its name argument is
  a write target, so a rebased root would silently relocate the write. A
  `--path` naming a declared root resolves to that root itself — a vendored wiki
  inside an excluding host is a sovereign tree, never silently retargeted to the
  host.
- `wiki update` and `wiki lint` note an indexed path the running machine's git
  ignores — a personal `core.excludesFile` rule the repository does not carry.
  The fence stays pinned to the repository's own rules, so what gets indexed
  never varies by machine; but the row that ships for such a file points at
  content the author's `git add` refuses, reddening every other clone while
  their own lint stays green. The note (`PathUntrackableEvent`, a typed
  `path_untrackable` row in `lint --json`) names the path, the excluding source
  and line, and the pattern. The row is still minted: refusing it would make
  indexing machine-dependent, the non-determinism the pinned fence exists to
  prevent.
- `wiki lint` folds resolver diagnostics into its notes: the upward resolution
  notice, a missing settings marker or root index, and an outer index above the
  declared root are counted in the closing summary and land typed in
  `lint --json` (`resolver_notice` rows carrying the diagnostic `text`), so a
  machine consumer no longer has to scrape them off stderr. Resolved `Wiki`
  instances expose them as `resolver_notices`.
- `wiki lint` notes an unconfigured merge driver: a `.gitattributes` mapping
  `merge=wiki` whose repository has no `merge.wiki.driver` configured — the
  fresh-clone state, since only the attributes map travels — draws a soft note
  (`MergeDriverUnconfiguredEvent`, a typed `lint --json` row) naming
  `wiki config` as the fix, instead of the clone's first merge silently
  text-merging `_index.md` files.
- Bare invocation and `--path .` agree from a raw (unindexed) folder of an
  undeclared wiki: cwd resolution climbs the ancestor index chain from the
  nearest indexed ancestor at any depth — exactly the climb an explicit path
  runs — instead of erroring or falling through to a different wiki via the
  `wiki/` fallback. An indexed path still climbs only a contiguous parent chain,
  so a standalone wiki under a stray outer index stays its own root. The climb
  declines from a raw folder that holds a wiki of its own — that folder is a
  project directory, not a folder of the outer tree — so the `wiki/` fallback
  still answers there and the standalone wiki is never handed to an outer chain
  to absorb. An undeclared root that would sweep up a wiki islanded below it by
  an unindexed folder refuses instead, naming the island and both ways out (run
  against it, or index the folder between them).
- The merge-driver wiring (`wiki init`/`wiki config`) drops git's repo-discovery
  environment the way the gitignore fence does: an inherited `GIT_DIR` can no
  longer land `merge.wiki.driver` in an unrelated repository's config while
  dropping `.gitattributes` inside the wiki itself (`GIT_DIR` defaults the work
  tree to the probe's cwd). The wiring pins to the repository enclosing the
  wiki.

### Fixed

- A case-mismatched subtree scope (`wiki search ... CORE` for the on-disk
  `core`) on a case-insensitive filesystem canonicalizes to the on-disk
  spelling, so `wiki search` and `wiki match` agree — search's path prefix
  filter matched nothing under the spelled casing while match still found the
  pages — and reported paths carry the true casing.
- `wiki trust` bounds its wait for the trust-store lock instead of blocking on
  it forever: one stopped holder (or a stalled network-filesystem write) wedged
  every fleet-wide spawn-time trust call with no diagnostic at all. The wait
  polls a non-blocking acquisition and, once the budget is spent, refuses naming
  the lock path.
- Undecodable bytes in the trust store read as corruption instead of escaping as
  a `UnicodeDecodeError`: one bad byte — a partial write, a truncated restore,
  an encoding mishap — turned every `wiki update`/`lint`/`map` on a hooked wiki
  into a hard failure whose message named no file. Reads fail safe (nothing is
  trusted) and the rewrite refuses naming the store, as both already did for
  unparseable JSON.
- An unreadable trust store is refused in plain language
  (`Cannot read the trust store: <path> (check its permissions).`) rather than
  leaking a bare `EACCES` — the one store state the guarded open never
  converted, and the one a restrictive umask or a chmod typo reaches by
  accident.
- A config home symlinked at a non-directory — a dotfiles target not yet
  materialized, a link into an unmounted volume — reaches the
  `Refusing symlinked config home` refusal instead of a bare `File exists` about
  a path that, to the user, does not exist: the named refusal sat downstream of
  a `mkdir(parents=True, exist_ok=True)` that raised first.
- `wiki lint` reports a leftover merge repair hint in an `_index.md` as an
  issue. The driver plants its hint above the first conflict marker, which
  normally lands inside the frontmatter (the `updated:` stamps differ first),
  where the hint parses as an authored key — so a resolution that dropped the
  markers but forgot the hint kept it in every later rewrite with both
  instruments blind to it.
- The description-propagation docs state the `...` placeholder exemption: a
  child carrying the placeholder propagates nothing, so its parent row keeps the
  description it has — no overwrite, no warning, nothing pending — rather than
  the unconditional overwrite the guide described.
- Lint's wrapped-list-marker rule no longer false-flags a legal bullet that
  follows a multi-line item whose continuation line is only a code span (for
  example a bare backticked path): such a line masks to blank but the list is
  still open, so list state now closes only at a raw blank line.
- `import wiki.cli.utils` works in a fresh interpreter: the top-level package no
  longer star-exports the Typer app runner over the `wiki.cli` subpackage
  attribute. The `wiki.cli(...)` callable was an accidental top-level alias —
  the CLI is the `wiki` console script (an entry point), not library API.

### Security

- The trust store is refused inside a wiki, on the read path and the write path
  alike: a wiki holding the store can list itself as trusted, so cloning a
  repository and pointing `WIKI_CONFIG_DIR` inside it would run its own
  `.wiki/wiki.py`. Pointed at a wiki's own `.wiki/`, the store and the
  declared-root marker are one file, which also merged the machine-local trust
  map into the repository's committed settings; both are named by one refusal.
- The trust store's permission self-heal opens `~/.wiki/settings.json` with
  `O_NOFOLLOW` and tightens the opened descriptor (`fchmod`), mirroring the lock
  file; a `settings.json` symlinked out of the config home is now refused
  outright, so a pre-planted symlink can never retarget the repair — or the
  rewrite behind it — onto a file outside the store. A multiply-linked store is
  refused the same way (`st_nlink > 1` on the opened descriptor): a hard link is
  the same attack without the symlink `O_NOFOLLOW` can see — the store is the
  attacker's inode, so the repair would re-mode their file and every trusted
  root written afterwards would be editable through a name the `0700` home does
  not cover.
- The trust store's READ path now opens through the same tamper guards as the
  write path: a symlinked or hard-linked `settings.json` is refused when
  `is_trusted` (and the hook gate behind it) consults the store, so a file
  outside the config home can never confer hook-execution trust that the write
  path would refuse to record. A non-regular file planted as the store is
  refused up front with a plain message naming the path — a FIFO no longer
  blocks every trust-consulting invocation on a writer that never comes, and a
  directory no longer fails deep in the rewrite with a cryptic error.
- The config home directory gets the same custody: `wiki trust` tightens it
  through an `O_NOFOLLOW`/`O_DIRECTORY` descriptor and refuses a symlinked home
  outright — a planted link can no longer have the `0700` repair chmod a foreign
  directory (and the store then written inside it). The refusal names the
  sanctioned relocation: point `WIKI_CONFIG_DIR` at the real directory.
- The `.settings.lock` sibling gets the store's custody too: its mode is
  re-tightened to `0600` on every locked write (`O_CREAT` applies its mode at
  creation only, umask-masked, so a loosened lock stayed loose forever), and a
  symlinked lock is refused with the store's plain-language message instead of
  surfacing a raw `ELOOP` errno.
- `wiki trust` refuses to rewrite a corrupt store: a tolerant read folds
  unparseable JSON into an empty store — right for a trust decision (nothing is
  trusted, fail-safe), catastrophic for the rewrite, which silently dropped
  every trusted root with a clean exit. The refusal names the store and the
  stakes; the corrupt bytes survive for repair.
- The config home's symlink guard moved onto the open the read path and the
  write path share, so a redirected home can no longer decide trust.
  `O_NOFOLLOW` on `settings.json` covers only its final component, so a
  `~/.wiki` (or `WIKI_CONFIG_DIR`) symlinked at a foreign directory was refused
  for `wiki trust` while `wiki map` still read the `settings.json` inside that
  directory — and executed the `.wiki/wiki.py` of a wiki the user had never
  trusted. The store is now opened relative to the guarded home descriptor, so
  there is no window between the check and the open either.
- A group- or world-writable trust store is refused, read path and write path
  alike. It is the hard-link attack without the hard link — any local user
  rewrites the list that decides which wikis run code, needing write permission
  on one file rather than control of a second name — and re-tightening the mode
  cannot unplant an entry already written, so the store is refused (naming the
  mode and the fix) instead of self-healed. Loosened *read* bits still
  self-heal: nothing behind them was forgeable.
- The `.settings.lock` sibling gets the store's full custody, not just
  `O_NOFOLLOW`. The `st_nlink` probe refuses a lock hard-linked to a file
  outside the config home — the per-call `fchmod` re-moded that file to `0600`,
  with the process holding a writable descriptor and an exclusive lock on a
  foreign inode — and the `S_ISREG` probe refuses a FIFO or a directory in plain
  language instead of a raw `ENOTSUP`/`EISDIR` (on Linux, `flock` on a planted
  FIFO succeeds, silently voiding the mutual exclusion the lock exists to
  provide). The lock is opened read-only now: `flock` and `fchmod` need no write
  access.
- A `trusted` value that is not an object is refused by the rewrite exactly as a
  corrupt top level is, instead of being discarded and replaced by a fresh
  single-entry map — the one key that matters was the one shape the strict read
  did not cover, so a hand-edit or a version skew that made it a list lost every
  root on the next spawn-time trust call. A *blank* store is the opposite case
  and now writes cleanly: an empty file holds no trusted roots, so the "a
  rewrite would drop every trusted root" refusal was vacuous and served only to
  wedge every trust call on the machine until a human removed the file.

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
[1.3.0]: https://github.com/plasma-ai/wiki/compare/v1.2.0...v1.3.0
[1.3.1]: https://github.com/plasma-ai/wiki/compare/v1.3.0...v1.3.1
[1.4.0]: https://github.com/plasma-ai/wiki/compare/v1.3.1...v1.4.0
[1.5.0]: https://github.com/plasma-ai/wiki/compare/v1.4.0...v1.5.0
[1.6.0]: https://github.com/plasma-ai/wiki/compare/v1.5.0...v1.6.0
[unreleased]: https://github.com/plasma-ai/wiki/compare/v1.6.0...HEAD
