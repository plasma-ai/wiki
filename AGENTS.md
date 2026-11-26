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

`plasma-wiki` is a standalone plugin providing the **wiki** skill.

The `wiki/` Python package is organized into `cli/` (typer app), `core/`
(business logic), `skills/` (the plugin skill), `util/` (shared utilities), and
`_assets/` (wiki config templates and the git merge driver), with the pytest
suite in `tests/`.

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

`core/` decomposes into `wiki.py` (the `Wiki` engine class), `format.py`
(functions over the on-disk page format; frontmatter values come from PyYAML's
composed node graph, with the line grammar as the fallback for a block a strict
reader rejects, and every write stays byte-level; the composed blocks and
quoting verdicts are memoized for one public `Wiki` operation, bracketed by
`format.run_scoped`), `event.py` (the payload-only `Event` base), `_obsidian.py`
(the internal Obsidian integration), and `_search.py` (the internal SQLite FTS5
index). Engine diagnostics are typed notice events: each kind has an `on_<kind>`
hook delegating to the `on_notice` funnel, which logs `event.description`
through the stdlib `Wiki.logger`; hosts intercept by overriding hooks (the CLI
swaps `on_notice` per instance to capture and condense narration).

## Build & Development

```bash
# install dev dependencies (creates a .venv if none is active)
./install.sh --all-extras --groups=test,lint,type

# or set up the environment manually
uv sync --all-extras --group test --group lint --group type
uv run pre-commit install

# run tests
uv run --no-sync pytest

# run the parity harness's tests (they run v1 from the oracle checkout)
uv sync --inexact --group parity
uv run --no-sync python -m parity oracle ensure
uv run --no-sync pytest parity

# run v1's CLI tests through the harness (a new work folder outside any repo)
uv run --no-sync python -m parity suite --work <dir> --out <dir>

# record the calls of v1's core and CLI tests and replay them as commands
uv run --no-sync python -m parity replay --work <dir> --out <dir>

# write the fixture families and run the matrix on each
uv run --no-sync python -m parity fixtures --work <dir> --out <dir>

# run a command in the Linux container, on a clone of HEAD (needs Docker)
uv run --no-sync python -m parity container -- <command>

# run pre-commit
uv run --no-sync pre-commit run [--all-files]

# run type check (not enforced)
uv run --no-sync pyright

# run security scan (not enforced)
uv sync --inexact --group security
uv run --no-sync safety scan
```

The test suite uses `pytest` with `--doctest-modules` enabled.

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

Step-by-step `# verb noun` comments before logical blocks — but aim for the
middle ground: short methods need no comments; longer methods label logical
blocks, not every line, and never leave long stretches of dense logic
uncommented.

Module-level public data (constants, type aliases) carries `#:` doc-comments
rather than plain `#` comments — Sphinx autodoc renders only doc-commented data
members.

No absolute paths in persisted data — everything should be relative or derivable
from the git repo root.

See `pyproject.toml` for formatter/linter config.

### CLI Commands (`cli/cmd/`)

- All commands live in a single module, `cli/cmd/wiki.py`; there are no sub-apps
- Each command's registration function is named after the command, with
  signature `def name(app: typer.Typer) -> typer.Typer`, registered directly on
  the wiki app in `cli/main.py`
- Use the `@command(app, 'name')` decorator (from `wiki.cli.utils`) — commands
  are error-wrapped, catching all but typer's `Exit`/`Abort`/`BadParameter`
- Typer args/options as local variables before the inner function
- Do not inline method calls in `typer.echo()` — assign to a variable first
- When mixing positional and keyword args in multi-line calls, pass all as
  kwargs (unless the param is positional-only with `/`)
- A command never catches `Exception` itself; the `@command` wrapper owns that.
  The one sanctioned case is the `_merge` driver. A merge driver passes git's
  merge exit code through, and hook code is loaded from outside the package and
  can raise anything, so the driver's best-effort steps catch `Exception` around
  hook code: `_index_canonicalizer`, `_merge_stamps`, `_is_managed_page` and
  `_canonicalize_index`, each under a `# NOTE:` giving this reason. They catch
  `SystemExit` too: a hook's exit would end the driver before the merge runs

### Event Hooks

Event hooks follow a uniform shape: the per-kind `on_<kind>` hooks on `Wiki`
(`core/wiki.py`) each carry a `logging_level: int = logging.<LEVEL>` signature
default and delegate to the `on_notice` funnel. Two sanctioned exceptions skew
the audit (the `logging_level: int = logging\.` grep count vs the `def on_`
count): `on_notice` itself takes `logging_level: Optional[int] = None` (severity
falls back to the event's own `logging_level`), and the `Event` base in
`core/event.py` declares a `logging_level` class default that the grep counts
without being a hook. The two offset, so the raw totals happen to match — audit
per-kind hooks against per-kind signature defaults, not the raw grep totals.

### Clock

Every wall-clock read goes through `_now` (`core/wiki.py`), so the
`WIKI_FREEZE_TIME` test seam governs all of them: when the variable holds an RFC
3339 instant, `_now` returns that instant instead of the clock's. `_pinned_now`
is the one reader and validator of the variable. The `@command` wrapper calls it
as each public command starts — after typer has parsed the command line, before
the command's own option checks: an invalid value is a command error, and a
valid one opens stderr with the fixed `_PINNED_CLOCK_NOTICE` line, so a seam
left set in a real shell is never silent. Help, `--version` and a parse error
never reach the wrapper, so they never read the variable. A private command is
exempt: git reads the `_merge` driver's exit code as the merge outcome, and the
driver reads no clock. A new clock read calls `_now`, never `datetime.now`
(`grep -rn --include='*.py' 'datetime\.now' wiki` shows the one call, in
`_now`).

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
file a run names: nothing committed names a private wiki, its paths, its sizes
or its text, and result files stay outside the repository. The harness snapshots
a checkout and runs only in clones, in a new work folder outside any wiki.

The oracle is v1 at the commit `parity/oracle.toml` names, the merge base of
this branch and `dev`. `python -m parity oracle ensure` builds its checkout, a
detached worktree at `worktrees/wiki-oracle-<sha7>`, with one venv per v1 side:
`oracle`, which defines parity, and `campaign`, the same commit under the
interpreter and package versions the consumers of 1.9.x run. Each side's
constraints file in `parity/constraints/` pins every package of its venv. A v1
side runs through `parity/launcher.py` and proves on every command that it is
the oracle commit. Never edit the oracle checkout, and never sync or install
into its venvs by hand: the harness refuses a checkout or a venv that is not
what the file names. `python -m parity oracle check` holds the file level with
v1; run it after `git fetch origin` when a work session starts. The three
scripts the harness runs inside a side's interpreter, `launcher.py`, `facts.py`
and `judge.py`, import nothing from `parity`, and neither do the modules in
`parity/plugins/`, which are loaded there by names of their own.

`python -m parity suite` runs v1's CLI tests (`tests/test_cli` of the oracle
checkout, unedited) and a short table of commands on a pseudo-terminal
(`parity/terminal.py`). Every `wiki` call goes through the dispatcher
(`parity/dispatch.py`), one executable that sends the calls of the ported
commands to the side `--engine` names and every other call to v1.
`parity/ported.toml` is the list of ported commands, and a change that ports a
command adds it there. The tests' own assertions are the verdict; a skipped test
and an expected failure are findings, and so is a call that does not leave the
identity record of the side it went to. `parity/plugins/` holds only what the
oracle's interpreter loads, so nothing else may be added to that folder.

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
engine wrote and `update` has to take it in. The generator writes the same
bytes, modes, times and commit on every host. A new fixture goes into the family
it belongs to, or into a new one, with its row in the spec's table. Keep every
folder of a family's first two levels one a command can be scoped to, because
the matrix sample draws them; a folder the wiki excludes or refuses lies deeper.
A family whose wiki `update` refuses runs its own stage alone, and each of its
cases states the exit codes it allows. The family `linux` and the three refused
trees need a file system that keeps case and bytes, so on macOS they are left
out: run them in the container. The refused trees show what registry entry D4
says v1 does on a name that is no valid UTF-8; the tests in
`parity/tests/test_fixtures.py` hold v1 to that on Linux, so a change in v1
there changes the spec's table and, as a change to the registry does, the entry.

`python -m parity container` runs one command in the Linux container
(`parity/container.py`, `parity/Dockerfile`): the Ubuntu release of the CI
runners, with a clone of the repository at `HEAD` on the container's own file
system. Its git and its processor architecture are not the runners', so a result
there stands for Linux, not for CI. The clone holds what is committed, so commit
before a run. Nothing a side runs on is mounted from the host. A change to the
container changes the spec's Container section in the same commit.

### Run Bracket

Every operation the `Wiki` class docstring names carries `@format.run_scoped`
(`core/format.py`): the bracket memoizes composed frontmatter blocks and quoting
verdicts for one operation. A nested operation — `new` runs `update` — joins the
enclosing run rather than opening its own, so the memo is freed when the
outermost operation returns or raises. A subclass operation that reads
frontmatter itself carries the decorator too, whether or not it calls the base
method: a call inside a run joins it. The decorator count
(`grep -c '@format.run_scoped' wiki/core/wiki.py`) equals the count of
operations the class docstring names.

### Shell Scripts

The only shell script in the `wiki/` package is `_assets/git/merge_index.sh`,
the git merge driver for `_index.md` files — `wiki init`/`wiki config` wire it
into a target wiki's `.gitattributes` (`merge=wiki`), git invokes the hidden
`wiki _merge` command, and the command dispatches `_index.md` pathnames to the
script with fixed positional args. It is a merge driver, not a CLI; match its
existing conventions when editing it.
