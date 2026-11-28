# AGENTS

This file provides guidance to coding agents (Claude Code, Codex) when working
with code in this repository. If you are not Claude Code (which already reads
parent directories), also check the parent directory for `AGENTS.md`.

Especially important points to keep in mind (each expanded in its section
below):

- **Questions are not edit requests.** When a message asks a question without
  explicitly requesting a change, answer and stop (Communication).
- **Write clear and concise English.** Write clear, concise, plain English which
  can be quickly and easily parsed by the user — answer questions directly in
  short form unless elaboration is requested (Writing Style).
- **Seek the best solution, not agreement.** When you think the user is wrong,
  don't be afraid to say so — the right answer matters more than the path of
  least resistance (Pushing Back).
- **Choose solutions that generalize.** Define the requirement before choosing
  an approach. Consider alternatives and base each choice on the problem and
  known constraints. Do not choose an approach for convenience or overfit it to
  the cases tried during development (Principled Solutions).

## Overview

`plasma-wiki` is a standalone plugin providing the **wiki** skill, and the
`wiki` command-line tool the skill drives.

The tool is a Cargo workspace under `crates/`, which maturin builds into a wheel
that holds only the `wiki` binary. `wiki/skills/` (the plugin skill) and
`wiki/_assets/` (the Obsidian config templates, the bundled Obsidian plugin and
the git merge driver) are plain folders: the binary embeds the skill and the
Obsidian files, and the plugin manifests point at the skill. The engine is a
port of v1, the Python tool at the commit `parity/oracle.toml` names. v1's
commands arrive one at a time, and `parity/ported.toml` lists the ones the
binary has; beside them it holds `install` and `completion`.

The workspace has five crates, each the home of one piece of the port.
Dependencies point one way: the binary depends on the engine, the engine on the
page format, and each of them on the two crates that know nothing about wikis.

- `wiki-yaml` reads YAML as libyaml 0.2.5 does, the parser under v1's
  frontmatter loader. It is the one crate that may hold `unsafe`.
- `wiki-pytext` is Python's text behavior as the oracle's interpreter has it:
  the character tables, the rules of `str` and `re`, `repr`, JSON and float
  text, `difflib`, paths and timestamps.
- `wiki-format` is the on-disk page format, v1's `wiki.core.format` and
  `wiki.util.markdown`: frontmatter, index rows and page text. It touches no
  file, starts no thread and prints nothing.
- `wiki-core` is the engine, v1's `wiki.core.wiki` with its search index, its
  exclusion and git helpers and its merge driver, and it embeds the files the
  binary writes. It never prints, reads the command line or ends the process: an
  operation returns what it did and what it noticed as data.
- `wiki-cli` is the binary `wiki`, v1's `wiki.cli`: arguments, output, exit
  codes, and the switches only tests use. It holds no engine logic.

`parity/` at the repository root is the parity harness, a development tool that
runs two engines on clones of one tree and reports every difference between them
(`parity/SPEC.md`). It is not part of the wheel or the sdist. Its tests are in
`parity/tests/`, with their own pytest settings (`parity/pytest.ini`) and their
own dependency group (`parity`).

A wiki is a folder tree of markdown pages indexed at every level by `_index.md`
files, with `.wiki/settings.json` declaring the root. The tool owns the
generated surfaces — `wiki update` regenerates index link blocks, frontmatter,
and path-derived names from the filesystem, and `wiki lint` checks structure —
while authored content lives below the `***` delimiter. The `_assets/` git merge
driver auto-resolves the generated region of `_index.md` merges, so parallel
edits conflict only on authored prose; `wiki config` installs the Obsidian
integration.

## Build & Development

```bash
# install the pinned Rust toolchain (reads rust-toolchain.toml)
rustup toolchain install

# install dev dependencies and the binary (creates a .venv if none is active)
./install.sh --groups=lint,docs,parity,type

# or set up the environment manually
uv sync --group lint --group docs --group parity --group type
uv run pre-commit install

# run the tool from the workspace
cargo run -- --help

# run tests
cargo nextest run
cargo test --doc

# run the YAML reader's tests under Miri, on the nightly tests.yaml names
cargo +<RUST_NIGHTLY> miri nextest run -p wiki-yaml --locked

# build the oracle checkout (v1 at the commit parity/oracle.toml names), and
# check that the file is level with v1
git fetch origin
uv run --no-sync python -m parity oracle ensure
uv run --no-sync python -m parity oracle check

# run the parity harness's tests (they run v1 from the oracle checkout)
uv run --no-sync pytest parity

# run v1's CLI tests through the harness (a new work folder outside any repo)
uv run --no-sync python -m parity suite --work <dir> --out <dir>

# run them against the binary: the ported commands go to a release build of HEAD
cargo build --release --locked
uv run --no-sync python -m parity suite --engine rust --work <dir> --out <dir>

# record the calls of v1's core and CLI tests and replay them as commands
uv run --no-sync python -m parity replay --work <dir> --out <dir>

# write the fixture families and run the matrix on each
uv run --no-sync python -m parity fixtures --work <dir> --out <dir>

# perturb the small trees by the committed seeds and run each
uv run --no-sync python -m parity perturb loop --work <dir> --out <dir>

# compare the alt side with the oracle on each corpus of a file
uv run --no-sync python -m parity interpreters --corpora <file> --work <dir> --out <dir>

# replay the merges of each corpus's history, with v1's part kept in a cache
uv run --no-sync python -m parity merges --corpora <file> --cache <dir> --work <dir> --out <dir>

# run a full gate with v1 alone: the harness checks itself and fills that cache
uv run --no-sync python -m parity gate full --corpora <file> --cache <dir> --work <dir> --out <dir>

# run a command in the Linux container, on a clone of HEAD (needs Docker)
uv run --no-sync python -m parity container -- <command>

# check what a wheel and an sdist built at HEAD keep for whoever installs them
uvx maturin@1.16.0 sdist --out <dist>
uvx maturin@1.16.0 build --release --locked --out <dist>
uv run --no-sync python -m parity contracts --wheel <wheel> --sdist <sdist> --work <dir> --out <dir>

# run pre-commit
uv run --no-sync pre-commit run [--all-files]

# run type check (not enforced)
uv run --no-sync pyright

# run supply-chain checks
cargo deny --locked check bans licenses sources
cargo shear --locked
```

The Cargo workspace under `crates/` is tested with `cargo-nextest`, and the
pre-commit hooks run `cargo fmt` and `cargo clippy` on it. The harness's tests
use `pytest` with `--doctest-modules` enabled.

## Consistency

The single most important pattern in this codebase is the pattern of **adhering
to patterns**. Every convention documented here exists so that the code reads as
if one person wrote it. This matters more than any individual style preference
because it enables:

- **Fast visual scanning** — when code follows predictable shapes, deviations
  jump out immediately
- **Regex-based refactoring** — consistent patterns mean find-and-replace works
  across the codebase
- **Trustworthy AI-generated code** — the user must be able to review the
  agent's output and have it look indistinguishable from their own

When writing or modifying code:

1. **Read the surrounding code first.** Match its patterns exactly — variable
   names, comment style, line breaking, method ordering, everything.
2. **Do not silently "improve" patterns.** If the existing code uses a
   particular structure, use that same structure in your current task. But if
   you see a genuinely better convention — clearer, safer, more idiomatic —
   **propose it explicitly.** The priority is consistency, not preservation of
   the status quo. Consistently good beats consistently bad, so make the case
   for why a change is worth the churn and the user will adopt it.
3. **Do not rename variables** that shadow outer scopes if it is sensible to
   reuse that variable name (and is unlikely to become a bug).
4. **Do not reformat** existing comments, reorder methods, or restructure
   working code unless specifically asked.
5. **Do not remove comments.** Line-by-line comments are intentional — they help
   the user maintain order and scan code quickly. Emulate existing comment
   patterns in new code.
6. **When in doubt, emulate.** Find the nearest analogous code in the codebase
   and mirror its structure.
7. **End files with a trailing newline.** Every committed file ends with one —
   the `end-of-file-fixer` hook enforces it.

### Adapting to the Codebase

The patterns documented here are a starting point, not an exhaustive rulebook.
The codebase is the authoritative style guide — these docs just accelerate your
ramp-up.

- **Pattern discovery over pattern memorization.** When working in a file, treat
  the local code as the authority. If a file uses a pattern not documented here,
  adopt it — don't introduce the documented pattern as a "correction."
- **Resolve conflicts in favor of local code.** If a documented pattern
  conflicts with what you see in the file you're editing, follow the file. Flag
  the discrepancy but don't "fix" it unilaterally.
- **New patterns propagate by observation.** The codebase evolves. When you
  encounter a pattern that's clearly intentional but not documented, follow it
  in your new code. The user will correct you if it's a mistake.
- **Scan before writing.** Before adding a new method, class, or module, find a
  few analogous examples in the codebase and mirror their structure. This
  applies to everything: error handling shape, docstring phrasing, test
  organization, import style, comment density.
- **Keep these docs up to date.** When you discover conventions or patterns
  through the user's feedback or codebase observation that aren't yet
  documented, add them to the appropriate `AGENTS.md`: repo-specific conventions
  belong in the repo's own file; org-wide conventions belong in the shared
  sections, which are maintained at the organization level — make shared-section
  changes at the org root (or flag them for promotion) and propagate them to
  repo copies.

**Propose better conventions.** If you see a pattern that could be improved
across the codebase — a more readable structure, a safer error handling
approach, a cleaner naming convention — say so. Explain *why* it's worth the
migration cost. The user values consistency over any particular style, and will
always prefer being consistently good over consistently familiar. The rule is:
don't deviate silently, but do advocate openly.

## Templates

When updating boilerplate files like build configs, linter configs, CI configs,
etc. (e.g. `pyproject.toml`, `.pre-commit-config.yaml`), always check whether
the same change should also be applied to the corresponding `cookiecutter`
template files — whether they live in the `templates` repository, in an in-repo
`templates/` directory, or upstream in the template this project is derived from
(see `.cruft.json`). Templates and the projects derived from them should stay in
sync.

## Scope Discipline

- **Do not add defensive code for impossible cases.** Trust internal code and
  framework guarantees. Only validate at system boundaries — user input,
  external APIs, deserialized data. Adding error handling "just in case" adds
  noise that obscures the cases that actually matter.
- **Do not add abstractions for one-time operations.** A few similar lines of
  code is better than a premature helper function. Build abstractions when the
  third caller arrives, not when the first one does.
- **Do not add features that weren't requested.** No feature flags, no
  backwards-compatibility shims, no "while I'm here" improvements. If something
  adjacent should change, mention it — don't do it silently.
- **Do not leave cleanup artifacts.** No `# removed` comments, no re-exported
  unused symbols, no renamed `_old_thing` variables. If something is unused,
  delete it completely.
- **Do not mix refactoring with implementation.** Deliver the requested change
  against the current code, then propose refactors separately. Mixing the two
  makes review impossible.
- **Do not change signatures of functions you're not tasked with changing.**
  Adding parameters, changing defaults, or renaming arguments in existing
  functions cascades through callers and is a separate task.

## Principled Solutions

Choose approaches based on the problem and its constraints, not on what is
easiest to try.

- **Define the requirement first.** State the required outcome and the cost of
  getting it wrong without referring to a proposed approach. If the task starts
  with a failure, identify its cause before choosing a fix.
- **Consider alternatives.** Treat the first workable idea as one option.
  Consider other ways to solve the problem, including whether the full problem
  can be handled directly. Compare them against the requirement, the size of the
  problem, and the cost of being wrong.
- **Choose based on the requirements.** Time and effort are valid constraints,
  but they do not show that an approach will generalize effectively. If the
  approach that best meets the requirement needs more time or resources than are
  available, explain the tradeoff, recommend a fallback, and let the user
  choose.
- **Avoid overfitting.** Base the approach, scope, defaults, error handling,
  stopping points, and other choices on requirements and known constraints. Do
  not shape the code around the cases tried during development. The reasoning
  must also apply to cases the code has not seen.
- **Revisit the approach when something does not make sense.** An unexpected
  failure or unexplained behavior may mean that you misunderstood the problem or
  chose the wrong approach. Find the cause or revisit the choice instead of
  adding a workaround. If a stopgap is necessary, state the problem it covers,
  its limits, and when it can be removed.

## Documentation Hygiene

Comments and docs describe the current state; git records how it got there.
Change-log notes pile up as junk: each is true only of a moment, and readers
must sort through them to find what is current.

- **Correct in place.** When text is wrong or out of date, rewrite it to state
  the current fact. Do not record what it used to say, when it changed, or who
  changed it ("updated on DATE", "previously did X", "corrected from Y", "no
  longer does Z"). Clean up change-log notes in and around text you are editing,
  and mention any you see elsewhere.
- **Explain changes in git.** The reason for a change belongs in its commit
  message or PR description, not in a comment beside the changed text. Files
  whose job is to record changes, such as release notes, are the exception.
- **Dates only when git cannot supply them.** A date belongs in a tracked file
  only when it is genuinely needed and git cannot supply it (e.g. when the text
  reports a changing source and the reading date is needed to interpret it, or
  when an outside event happened). The date a file was edited is git's to
  record.

## Communication

- **Questions are not edit requests.** Before acting on any message, find the
  words that request action ("fix it", "update X", "go ahead") — if you cannot
  quote them, answer and stop. A question gets an answer, not an edit. That
  holds even when the question implies something is wrong, when you discover a
  real bug while answering, or when the fix seems small and obvious: answer,
  propose the change, and wait for the user to ask. When in doubt, treat the
  message as a question — a proposal costs one turn; an unwanted edit costs a
  revert.
- **Wait for an explicit go-ahead.** An action proposed or offered earlier stays
  pending until the user clearly says to proceed — discussing it or deferring it
  ("before we do that, ...") is not approval, and approval covers only the
  action it names. When iterating, apply only the edits the current message asks
  for, and check that the user is ready before starting anything still queued.
- **Lead with the answer.** When the user asks a question, answer it in the
  first sentence. Provide reasoning and context after, not before. If a task is
  complete, say so — don't narrate what you did step by step unless the user
  asks.
- **Match the answer to the question.** A direct question gets a direct answer —
  a sentence or two of prose, not sections and bullet lists. Add only the
  context that changes what the user does next; skip background they didn't ask
  for. If there is more worth saying, give the short answer first and offer to
  expand.
- **Be direct about uncertainty.** If you're unsure about something, say so
  plainly. "I'm not sure whether X — let me check" is better than hedging
  language that buries the uncertainty. If you made a mistake, state it clearly
  and correct it.
- **Flag first, fix later.** When you notice something wrong that's outside the
  scope of the current task — a bug in adjacent code, an inconsistency in
  naming, a missing edge case — mention it. Do not fix it unilaterally. The user
  tracks their own priorities.

### Writing Style

Write all conversational prose — chat replies, questions, plans — in clear,
concise, plain English that is easy to read. Plain does not mean imprecise —
technical terms stay. These rules govern how sentences read, not what they say.
Docs, commit messages, and code comments follow local conventions.

- **Short sentences, common words.** One idea per sentence. Prefer the simple
  word over the impressive one.
- **Front-load the point.** Conclusion first, reasoning after.
- **Concrete over abstract.** Name the file, the command, the number.
- **No unexplained jargon.** Expand abbreviations on first use; define terms the
  reader may not know.
- **No flourishes.** No metaphors, no rhetorical buildup, no cleverness.
- **Cut content, not grammar.** Concision comes from dropping less-important
  points, never from compressing sentences into jargon fragments. Every sentence
  that survives stays a plain subject-verb sentence describing what actually
  happens in clear, plain English.
- **The two-read test.** If a sentence needs a second read, rewrite it.

## Pushing Back

The user is sometimes wrong, and quiet compliance produces bad code that the
user later has to undo. When you think the user is wrong:

- **Say so plainly.** "I think you're wrong about X — here's why" beats silently
  going along. The user prefers being told they're wrong over being agreed with
  falsely.
- **Distinguish misreads from disagreements.** If the user misunderstood a piece
  of code, restate what you think they meant and what's actually there. If you
  disagree on direction, lay out the reasoning.
- **Hold ground when you have evidence.** Do not fold at the first sign of
  pushback. The right answer matters more than the path of least resistance.
- **Concede when convinced.** When the user produces a reason you hadn't
  considered, say so explicitly. This is calibration, not weakness.

## Thinking Before Coding

For non-trivial tasks, lead with planning, not code:

- **Surface assumptions.** State what you're assuming before you implement. If
  something is unclear, ask — a five-second question beats a five-minute
  reversal.
- **Present alternatives instead of picking silently.** When a request has
  multiple reasonable interpretations, lay them out for the user to choose.
- **Define success criteria upfront.** "Add validation" is weak; "tests for
  invalid inputs pass" is strong. For multi-step work, sketch a brief plan with
  verifiable checks per step.
- **Apply the surgical-change test.** Every changed line should trace directly
  back to the user's request. If you can't justify a line, remove it.
- **Push back on overcomplication.** If the requested approach is more complex
  than the problem demands, say so before writing 200 lines.
- **Verify the current state before changing it.** Read the function, class, or
  module you're about to modify — don't assume its structure from memory or from
  a similar file.

## Testing

### Philosophy

Prefer ground-up test rewrites over incremental patches — design the test suite
that *should* exist from first principles rather than patching existing tests.

**Test behavior, not implementation.** The question a test should answer is
"does the code work?" — not "is the code implemented exactly how it's
implemented right now?" Expect frequent renaming, restructuring, and
refactoring. Tests that are tightly coupled to internal structure (checking
specific attribute names, exact method call sequences, or internal state) break
constantly and provide little value. Tests that verify end-to-end behavior
survive refactors.

**Fewer, better tests.** Prefer a smaller number of end-to-end test cases that
exercise real workflows over a large number of trivial unit tests. A single test
that constructs real objects, exercises them through a realistic scenario, and
verifies the output tests more meaningful behavior than ten tests that
individually check field initialization. When a test can only fail if the code
it tests is also changed in the same commit, it's testing implementation, not
behavior — remove it.

**Readability and parameterization.** Tests should be readable as documentation
of what the code does. Use the language's native parameterization or data-driven
testing mechanisms to cover variations instead of duplicating test functions
with different constants. Avoid random magic numbers — use descriptive variable
names or setup helpers that make the test's intent clear.

**Red before green across boundaries.** A failing test that must land before its
fix — crossing a commit or merge boundary — carries the test framework's strict
expected-failure marker naming the reason; the fix commit removes the marker,
and strict mode makes a lingering marker fail the suite. A red-then-fix chain
inside a single change stays bare-red and never commits red.

### Good Tests

- **Tests a real workflow:** constructs objects, exercises them, checks
  observable results
- **Survives refactors:** doesn't break when internals are renamed or
  restructured
- **Has a clear purpose:** the test name and body make it obvious what behavior
  is being verified
- **Uses parameterization:** variations are covered via data-driven patterns,
  not copy-pasted functions
- **Avoids mocking internals:** mock external boundaries (network, filesystem)
  but not internal classes

### Bad Tests

- Tests that check exact internal/private state rather than observable behavior
- Tests that duplicate another test with a trivially different input
- Tests that only verify string representation or debug output format
- Tests that test the testing infrastructure itself (helpers testing helpers)
- Tests where the assertion is essentially restating the implementation

## Code Style

No implementation-phase comments and no development-history references of any
kind, in any surface — code, comments, docstrings, test names, user-visible
strings, docs. That means no bug-ledger or review-item ids, no internal
run/phase/unit names, and no old-implementation narration ("the old default",
"previously", "no longer", "renamed from", "used to"). State every rationale in
present-tense design terms: the code should read as if it was always this way.

Comments are small until proven big. Lines are earned by non-obvious invariants,
cross-file contracts, ordering/safety requirements, or why-rationale that
prevents a plausible wrong "fix" — never by narrating what the code visibly
does, restating signatures, or baking in considered-and-rejected alternatives.
One load-bearing sentence beats four explanatory ones.

In the Python of `parity/`, step-by-step `# verb noun` comments come before
logical blocks — but aim for the middle ground: short methods need no comments;
longer methods label logical blocks, not every line, and never leave long
stretches of dense logic uncommented. Module-level public data (constants, type
aliases) carries `#:` doc-comments rather than plain `#` comments.

No absolute paths in persisted data — everything should be relative or derivable
from the git repo root.

See `Cargo.toml` for the lint table, `clippy.toml` for the linter config, and
the `cargo-fmt` hook in `.pre-commit-config.yaml` for the formatter config.
`pyproject.toml` holds the formatter and linter config of `parity/`.

### Rust Workspace

Rust code follows the org's Rust conventions, part of the pattern reference that
the `AGENTS.md` of the parent directory locates: read the ones that cover a
surface before writing code for it. This section holds only what is this
repository's own, where it adds to a convention and where it departs from one on
purpose.

The Rust tool template owns the tool configuration, the hooks, the four
workflows, `pyproject.toml` and the lint table and profiles of the root
manifest, and it seeds two crates, `wiki-core` and `wiki-cli`. `.cruft.json`
pins it and there is no skip list, so a template change to a line this
repository has changed comes back as a `.rej` to resolve by hand. Its own
additions therefore sit beside the rendered content: the integration branches in
the trigger lists of the workflows; the jobs `parity`, `perturbation`, `suite`,
`miri`, `nightly` and `issue` in `tests.yaml`, and `contracts` and the build-id
step of the `wheels` job in `build.yaml`; one block of entries after the
template's in `clippy.toml`; its hooks and the `mdformat-wiki` swap in
`.pre-commit-config.yaml`; and its entries in `pyproject.toml`, `.gitattributes`
and `docs/conf.py`. In `tests.yaml` each of those jobs that runs on a pull
request carries a guard that keeps it off the schedule and a dispatch, and the
`nightly` job carries the opposite guard; no added job carries a YAML comment.
The three other crates are the repository's own files. A seed file takes a
change only where no file of the repository's own can hold it: a `mod` line, an
`#[expect]`, a field of the configuration. Every `cruft` command runs the
template's hook, which calls `cargo fmt`, so `cargo` must be on `PATH`.

`crates/wiki-cli/build.rs` records the commit of the checkout as the build id.
It reads git's files and never runs git, which refuses a checkout that another
user owns; a build outside a checkout, such as one from the sdist, has no id.
Under `WIKI_TRACE=<path>` the binary appends its identity record to the file,
one line that holds the engine's name, its version and its build id, which is
how the harness knows which binary it ran. The record is written before the
arguments are parsed, because clap ends a run itself on a usage error or a
request for help, and only once the bundled SQLite has matched a row of a
full-text table, so a binary whose C code does not work leaves none.

- **Look-alike calls are disallowed before their helpers exist.** The block of
  entries in `clippy.toml` disallows the calls whose results differ from
  Python's: trimming, whitespace splitting and tests, `lines`, the Unicode
  property and case methods, `canonicalize`, and the constructors of `regex` and
  `fancy_regex`. Each reason names the crate that holds Python's form,
  `wiki-pytext`, and no helper function, so an entry is in force before its
  helper is written and no look-alike call is written first. The function of
  `wiki-pytext` that is the home of such a call carries
  `#[expect(clippy::disallowed_methods, reason = "...")]`, and so does a seed or
  test function that makes the call on text or a path no rule of a wiki covers.
  No entry catches a regex built through a trait (`str::parse`,
  `FromStr::from_str`, `TryFrom::try_from`): build every pattern through the
  regex gateway of `wiki-pytext`.
- **Ported code takes Rust's shapes.** No integer sentinel, no kind carried as a
  string, no unnamed tuple of three or more fields and no collected `Vec<char>`.
  A function ported from v1 keeps the order of v1's steps, so the two read side
  by side; where it is long and does not split well, a short sentence labels
  each logical block, the one case in which a Rust comment labels a block.
- **A build script is tested as a program.** Cargo runs no test inside a build
  script, and a test target on `build.rs` would put one file into two targets.
  The tests of the build script (`crates/wiki-cli/tests/it/build_script.rs`)
  compile it with the `rustc` beside the cargo that built them, once per content
  of the script, run it as cargo does on repositories that git makes in the
  test's own root, and read the `cargo::` lines it prints. Their helpers are
  child modules of the CLI test harness (`tests/it/support/build_script.rs` and
  `tests/it/support/git.rs`), so those two build a `Command` beside the helper
  that runs the binary.
- **A build script reads the variables cargo sets.** The binary reads its
  environment in the configuration module alone. The build script reads
  `CARGO_MANIFEST_DIR` when it runs, under an `#[expect]`, because its tests
  point it at other roots through that variable.
- **The manifests carry the license.** The template renders no license key.
  `pyproject.toml` holds `license` and `license-files`, `[workspace.package]`
  holds `license`, and every member inherits it with `license.workspace = true`,
  the two seed crates included, so a new crate's manifest carries the line.
- **v1's CLI suite is the conformance check.**
  `python -m parity suite --engine rust` runs v1's CLI tests, unedited, from the
  oracle checkout, with every call of a command that `parity/ported.toml` lists
  sent to the binary, in the `suite` job of every pull request. It stays until
  the Rust CLI tests hold the same contract.
- **The Miri job names the components of its toolchain.** Every other
  `rustup toolchain install` step takes no argument and reads
  `rust-toolchain.toml`. The `miri` job installs the dated nightly that
  `RUST_NIGHTLY` names in `tests.yaml` with `miri` and `rust-src`, which the
  minimal profile lacks.
- **`rusqlite` is taken without its default features.** The engine uses the
  bundled SQLite and nothing else of the crate, so the dependency graph holds no
  license beyond the template's three; a feature that brings one adds it to the
  `allow` list of `deny.toml` in the same change.

### Parity Harness

`parity/` compares two sides: each is an argv prefix that the harness runs under
one built environment, on its own copy-on-write clone of one snapshot, at the
same absolute path. `python -m parity matrix` compares two sides over the
command matrix, and `python -m parity selfcheck` proves the harness with one
side: clean under two hash seeds, then against a doctored copy for each check in
the spec's table; the unit tests in `parity/tests` pin the rules no doctored
check reaches. `parity/SPEC.md` is the specification, and a change to a rule
changes the spec and the matching doctored check or unit test in the same
commit. `parity/differences.toml` is the registry of differences, the one list
of what a comparison may excuse. A classifier (`parity/classify.py`) recognizes
an entry's difference, and `python -m parity registry` proves each one with one
side against a stand-in: on fixtures inside the entry's scope and on one just
outside it, which must still be reported. The registry is for a second engine:
between two builds of v1 nothing is registered, so `matrix` fails on an excused
command unless `--registered` is given. A change to the registry or to a
classifier changes the entry, the spec and the fixtures in the same commit, and
its pull request names the question whose Decision line approves it. Two sides
that fail the same way agree on everything, so each matrix case names the exit
codes it allows (`_case` in `parity/matrix.py`), and a search or match draws its
word from the text the command reads; a new case states both. The harness treats
a side as a black box, so it imports nothing from `wiki` and nothing from
`tests/`: its own tests run v1 as a side. Corpus paths come only from the TOML
file a run names: nothing committed names a wiki the harness ran on, its paths,
its sizes or its text, and result files stay outside the repository. The harness
snapshots a checkout and runs only in clones, in a new work folder outside any
wiki.

The oracle is v1 at the commit `parity/oracle.toml` names, the merge base of
this branch and `dev`. `python -m parity oracle ensure` builds its checkout, a
detached worktree beside the main checkout, at `worktrees/wiki-oracle-<sha7>`,
with one venv per v1 side: `oracle`, which defines parity; `alt`, the same
commit under a second interpreter that v1 supports, with its own package
versions; and `crossed`, the alt side's interpreter under the oracle side's
packages. Each side's constraints file in `parity/constraints/` pins every
package of its venv. A v1 side runs through `parity/launcher.py` and proves on
every command that it is the oracle commit. Never edit the oracle checkout, and
never sync or install into its venvs by hand: the harness refuses a checkout or
a venv that is not what the file names. This tree holds none of v1's Python,
neither its engine nor its command line nor its tests (`parity/removed.txt`
lists the paths, and `oracle check` fails when one is tracked again), so the
oracle checkout is also where v1 is read: port from its `wiki/` folder, the code
every comparison runs, and not from another branch or an installed package.
`python -m parity oracle check` holds the file level with v1; run it after
`git fetch origin` when a work session starts. When its `level` check fails, v1
has gained a commit, and a sync brings this branch level: it merges `dev`, keeps
the listed paths removed and this branch's version, moves `commit` and `version`
of the file to the new merge base, and carries the port of each change to a
command the binary has. The five scripts the harness runs inside a side's
interpreter, `launcher.py`, `facts.py`, `judge.py`, `tap.py` and `points.py`,
import nothing from `parity`, and neither do the modules in `parity/plugins/`,
which are loaded there by names of their own.

`python -m parity suite` runs v1's CLI tests (`tests/test_cli` of the oracle
checkout, unedited) and a short table of commands on a pseudo-terminal
(`parity/terminal.py`). Every `wiki` call goes through the dispatcher
(`parity/dispatch.py`), one executable that sends the calls of the ported
commands to the side `--engine` names and every other call to v1.
`parity/ported.toml` is the list of ported commands, and a change that ports a
command adds it there. The side named `rust` is built in (`parity/binary.py`):
the release build of this checkout, held on every command to the version in
`Cargo.toml` and to the commit of `HEAD`, and refused when it is older than a
file cargo built it from, so build again after each edit and each commit. A run
against a binary built over uncommitted changes prints and records the paths
that differ from `HEAD`, and a gate refuses it. The tests' own assertions are
the verdict; a skipped test and an expected failure are findings, and so is a
call that does not leave the identity record of the side it went to.
`parity/plugins/` holds only what the oracle's interpreter loads, so nothing
else may be added to that folder.

`python -m parity replay` runs v1's core and CLI tests under a recorder
(`parity/plugins/parity_record.py`) and then runs each call they made again as a
command line, on a copy of the tree the call found (`parity/replay.py`). The
sides are compared by the rules of the matrix, and v1's side is also held to
what the call did in its test: a replay that does not repeat the test's own call
compares another scenario. A call with no command line and a call v1's command
line does not repeat are findings, never skips, unless `parity/routing.toml`
names the test with the check that holds it in the replay's place. A row for a
test the replay holds is a finding too, so the table stays as short as the
replay allows. The recorder also keeps the format calls of the format tests
(`test_format.py` and the two tests of `test_update.py` that `parity/record.py`
names), which the replay makes again in v1. A change to a recorded method's
command line, to a row of the fidelity table or to a rule of the replay changes
the spec and its test in `parity/tests/test_replay.py` in the same commit; a
change to a rule the table's loader refuses on changes
`parity/tests/test_routing.py`.

A whole replay of v1 alone ends with an account of v1 itself. Every test v1
collects has one home (`parity/homes.py`): the CLI suite, the replay, the format
calls, or a row of the routing table, so a test of v1 that calls nothing the
harness can run on a second engine needs a row. Every notice and issue kind of
the engine has one home in `parity/kinds.toml` (`parity/kinds.py`): a test whose
replayed call shows the kind, or the perturbation. And a call that v1 runs on
input outside the supported set is a finding unless the table drops its test
under D3. A new v1 test or kind, and new input of a recorded call, therefore
fails the `parity` job until the table or the list accounts for it. The inputs
of a test that records no call are not judged.

`python -m parity fixtures` writes the fixture families (`parity/fixtures.py`)
and runs each as a corpus: the stages of the matrix, then the family's own,
whose commands name its pages. A family is one converged base wiki with the
family's own files added raw, so the read-only commands report on input no
engine wrote and `update` has to take it in. The generator writes the same bytes
and times on every host and the same modes under any umask; a symlink's mode is
the platform's, and the commit of `names` differs on macOS, where git composes a
decomposed name. A new fixture goes into the family it belongs to, or into a new
one, with its row in the spec's table. Keep every folder of a family's first two
levels one a command can be scoped to, because the matrix sample draws them; a
folder the wiki excludes or refuses lies deeper. A family whose wiki `update`
refuses runs its own stage alone, and each of its cases states the exit codes it
allows. The family `linux` and the four refused trees need a file system that
keeps case and bytes, so on macOS they are left out: run them in the container.
CI runs `fixtures --every`, which fails where a family would be left out. The
refused trees show what v1 does on a name that is no valid UTF-8 where registry
entry D4 has a second engine refuse the tree. Only the tests in
`parity/tests/test_fixtures.py` write them, and they hold v1 to the spec's table
on Linux, so a change in v1 there changes the table and, as a change to the
registry does, the entry.

`python -m parity perturb` changes trees by a seed (`parity/perturb.py`) and
runs each as a corpus, because a converged wiki gives `update` and `lint` little
to do. `perturb corpus` perturbs a snapshot of each corpus once and runs the
matrix on it; `perturb loop` runs many seeds on small trees through a short
stage: the example wiki and the fixture families, or the wiki of each corpus
copied into a repository of its own. Each seed gives three trees: the one the
operators leave, that tree with every index deleted, and that tree with conflict
markers, which `update` refuses. An operator is one kind of change, and it names
the notice and issue kinds its change puts into the tree. The tap
(`parity/tap.py`) runs `lint` and `update` inside v1's interpreter on each tree,
and a named kind that v1 does not meet there is a finding. So an operator names
a kind only where its change shows it whatever else the perturbation did: draw a
page, an index or a folder through the tree's own methods, which hand out what
the engine left and keep the operators off each other's files. One seed gives
one tree, byte for byte: draw only from the generator the operator is handed,
and write only through the tree, which sets every file's time (one of two fixed
times far from any stamp, or the time an operator gives the file, which the file
then keeps) and refuses a path that leads out of the checkout through a symlink.
The matrix of `perturb corpus` may draw any folder of the first two levels for
its scoped commands, and it expects each command to work there. An operator that
adds a folder with no index under a denied name lists it as denied, as
`invalid-folder` does, and the matrix then expects `read` of it and `new` below
it to exit 2. The list fits that shape alone: a folder on which v1 refuses a
scoped command for another reason needs cases of its own. A new operator goes
into `operators()`, with a row in the spec's table, and each kind it names takes
the home `perturbation` in `parity/kinds.toml`; a run exits 2 while the list and
the operators disagree. `parity/seeds.toml` holds the seeds every pull request
runs. A change to an operator changes what every seed leaves, so run the loop
again on the committed seeds.

`python -m parity interpreters` compares the `alt` side with the oracle over the
matrix on a snapshot of each corpus (`parity/interpreters.py`), with no
perturbation: it asks what the second interpreter makes of the wikis' real names
and text. The stages that differ run once more on all three sides, and what the
`crossed` side does there lays each difference to the interpreter, to a package
or to both, so `crossed` keeps the alt side's interpreter and the oracle side's
constraints file whenever either moves. The run also asks both interpreters what
they make of every code point (`parity/points.py`) and reads the name of the
wiki root and every name below it for a code point they do not agree on; such a
name fails the corpus even where the matrix shows no difference. A property an
engine starts to read from the Unicode tables goes into `PROPERTIES` there and
into the spec's table.

`python -m parity merges` replays the merge commits of a corpus's history
(`parity/merges.py`), because the trees real merges leave were written by every
older version of the engine. On every side it checks out the first parent in a
repository of the harness's own, which borrows the snapshot's objects and holds
no hook, filter or config of the checkout; merges the other parents with the
side's merge driver; and runs `update --check`, `update` and `update --check` on
each wiki the merged tree declares. A merge runs at a path its commit names, so
the paths a side prints are those of every other run. `--cache` keeps what v1
did on each merge: a run of v1 alone stores a merge that shows no difference and
skips the ones the cache holds, and a run with an engine reads v1's part from
it. An entry is keyed on the oracle's engine files, its constraints file, the
interpreter, git, the merge, the instant and the path, so a release that changes
no engine file keeps the cache. It is keyed on the harness files that make a
pass and an entry too (`_SOURCES`): a change to one of them empties the cache,
so fill it again with a run of v1 alone before the next gate with an engine, and
add a module to the list when a pass comes to run through it. The runs that
share a cache replay their merges in one folder, which `--at` names.

`python -m parity gate` runs a gate (`parity/gate.py`): the layers of the
harness on the corpora, one after the other, each step a harness command in a
process of its own. `gate full` checks the harness with v1 alone, compares v1
with an engine if `--engine` names one, and ends with the merge history;
`gate daily` holds the comparison and the merges the cache does not hold yet. It
records the seconds, the processor seconds and the load of each layer, so run it
on a host that does nothing else, and with the same `--work` every time: the
cache of the merge history is keyed on the path, and the gate hands every step
that replays merges the folder `<work>/at`. A new harness command that a gate
should hold gets its layer in `layers` and its row in the spec's table.

`python -m parity contracts` holds a built wheel and sdist to what the package
keeps for whoever installs it (`parity/contracts.py`): the distribution name,
the bare version, a `wiki` that is the wheel's binary and works where each
installer puts it (uv, pip, pipx, `uv tool`, `uv run --with`), an upgrade from
v1's wheel and back, and a project that pins the commit and builds it. It
compares no two engines. What the files must be is read from the checkout, the
version in `Cargo.toml` and the commit of `HEAD`, so build the wheel and the
sdist at the commit they are checked at. Every install runs on a scratch host
below the work folder, which inherits nothing and holds no `cargo`: the sdist
and the pin are built as on a host with no Rust, which fetches the pinned
toolchain and needs the network. The `contracts` job of `build.yaml` runs the
checks on the wheels that workflow builds, on macOS and on Linux, and the
`publish` job waits for it. A new contract of the package gets its check in
`CHECKS` and its row in the spec's table. A check that no wheel can fail proves
nothing, so `parity/tests/test_contracts.py` doctors a stand-in wheel in the one
thing each of twelve checks holds; for `sdist` that is the empty build id, with
a stand-in in the sdist's place. No test builds the sdist or runs `pipx`,
`upgrade` or `lock`, which fetch pipx, v1's packages and a Rust toolchain: only
the job's real runs hold those.

`python -m parity container` runs one command in the Linux container
(`parity/container.py`, `parity/Dockerfile`): the Ubuntu release of the CI
runners, with a clone of the repository at `HEAD` on the container's own file
system. Its git and its processor architecture are not the runners', so a result
there stands for Linux, not for CI. The clone holds what is committed, so commit
before a run. Nothing a side runs on is mounted from the host. A change to the
container changes the spec's Container section in the same commit.
