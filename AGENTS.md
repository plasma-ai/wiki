# AGENTS

This file provides guidance to coding agents (Claude Code, Codex) when
working with code in this repository. If you are not Claude Code (which
already reads parent directories), also check the parent directory for
`AGENTS.md`.

## Overview

`plasma-wiki` is a standalone plugin for Claude Code and Codex providing
the **wiki** skill (structured knowledge base management).

### Architecture

```
wiki/                    # Python package
  _config/               #   wiki config templates (obsidian) + git merge driver
  cli/                   #   CLI layer (typer app)
  core/                  #   business logic (Wiki)
  skills/wiki/           #   plugin skill (SKILL.md)
  util/                  #   shared utilities
tests/                   # pytest suite
```

**Wiki** manages folder-based wikis with `_index.md` files, automatic
link generation, frontmatter management, and Obsidian integration.

## Build & Development

```bash
# install dev dependencies (creates a .venv if none is active)
./install.sh --all-extras --groups=test,lint,type

# or set up the environment manually
uv sync --all-extras --group test --group lint --group type
uv run pre-commit install

# run tests
uv run --no-sync pytest

# run pre-commit
uv run --no-sync pre-commit run [--all]
```

The test suite uses `pytest` with `--doctest-modules` enabled.

## Consistency

The single most important pattern in this codebase is the pattern of
**adhering to patterns**. Every convention documented here exists so
that the code reads as if one person wrote it. This matters more than
any individual style preference because it enables:

- **Fast visual scanning** — when code follows predictable shapes,
  deviations jump out immediately
- **Regex-based refactoring** — consistent patterns mean
  find-and-replace works across the codebase
- **Trustworthy AI-generated code** — the user must be able to review
  the agent's output and have it look indistinguishable from their own

When writing or modifying code:

1. **Read the surrounding code first.** Match its patterns exactly —
   variable names, comment style, line breaking, method ordering,
   everything.
2. **Do not silently "improve" patterns.** If the existing code uses a
   particular structure, use that same structure in your current task.
   But if you see a genuinely better convention — clearer, safer, more
   idiomatic — **propose it explicitly.** The priority is consistency,
   not preservation of the status quo. Consistently good beats
   consistently bad, so make the case for why a change is worth the
   churn and the user will adopt it.
3. **Do not rename variables** that shadow outer scopes, reformat
   existing comments, reorder methods, or restructure working code
   unless specifically asked.
4. **Do not remove comments.** Line-by-line comments are intentional —
   they help the user maintain order and scan code quickly. Emulate them
   in new code.
5. **When in doubt, emulate.** Find the nearest analogous code in the
   codebase and mirror its structure.
6. **Preserve trailing newline patterns.** If a file ends with a
   trailing newline, keep it. If a file ends without one, don't add one.
   Match whatever the file already does.

### Adapting to the Codebase

The patterns documented here are a starting point, not an exhaustive
rulebook. The codebase is the authoritative style guide — these docs
just accelerate your ramp-up.

- **Pattern discovery over pattern memorization.** When working in a
  file, treat the local code as the authority. If a file uses a pattern
  not documented here, adopt it — don't introduce the documented pattern
  as a "correction."
- **Resolve conflicts in favor of local code.** If a documented pattern
  conflicts with what you see in the file you're editing, follow the
  file. Flag the discrepancy but don't "fix" it unilaterally.
- **New patterns propagate by observation.** The codebase evolves. When
  you encounter a pattern that's clearly intentional but not documented,
  follow it in your new code. The user will correct you if it's a
  mistake.
- **Scan before writing.** Before adding a new method, class, or module,
  find a few analogous examples in the codebase and mirror their
  structure. This applies to everything: error handling shape, docstring
  phrasing, test organization, import style, comment density.
- **Keep this file up to date.** When you discover conventions or
  patterns through the user's feedback or codebase observation that
  aren't yet documented here, add them to the appropriate section of
  this file.

**Propose better conventions.** If you see a pattern that could be
improved across the codebase — a more readable structure, a safer error
handling approach, a cleaner naming convention — say so. Explain *why*
it's worth the migration cost. The user values consistency over any
particular style, and will always prefer being consistently good over
consistently familiar. The rule is: don't deviate silently, but do
advocate openly.

## Templates

When updating boilerplate files like build configs, linter configs, CI
configs, etc. (e.g. `pyproject.toml`, `.pre-commit-config.yaml`), always
check whether the same change should also be applied to corresponding
`cookiecutter` files in the `templates/` repository. Projects derived
from templates should stay in sync with the source.

## Scope Discipline

- **Do not add defensive code for impossible cases.** Trust internal
  code and framework guarantees. Only validate at system boundaries —
  user input, external APIs, deserialized data. Adding error handling
  "just in case" adds noise that obscures the cases that actually
  matter.
- **Do not add abstractions for one-time operations.** A few similar
  lines of code is better than a premature helper function. Build
  abstractions when the third caller arrives, not when the first one
  does.
- **Do not add features that weren't requested.** No feature flags, no
  backwards-compatibility shims, no "while I'm here" improvements. If
  something adjacent should change, mention it — don't do it silently.
- **Do not leave cleanup artifacts.** No `# removed` comments, no
  re-exported unused symbols, no renamed `_old_thing` variables. If
  something is unused, delete it completely.
- **Do not mix refactoring with implementation.** Deliver the requested
  change against the current code, then propose refactors separately.
  Mixing the two makes review impossible.
- **Do not change signatures of functions you're not tasked with
  changing.** Adding parameters, changing defaults, or renaming
  arguments in existing functions cascades through callers and is a
  separate task.

## Communication

- **Lead with the answer.** When the user asks a question, answer it in
  the first sentence. Provide reasoning and context after, not before.
  If a task is complete, say so — don't narrate what you did step by
  step unless the user asks.
- **Be direct about uncertainty.** If you're unsure about something, say
  so plainly. "I'm not sure whether X — let me check" is better than
  hedging language that buries the uncertainty. If you made a mistake,
  state it clearly and correct it.
- **Flag first, fix later.** When you notice something wrong that's
  outside the scope of the current task — a bug in adjacent code, an
  inconsistency in naming, a missing edge case — mention it. Do not fix
  it unilaterally. The user tracks their own priorities.
- **Questions are not edit requests.** When the user asks a question
  like "why is this done this way?", "what does this do?", or "why did
  you do this?" — answer the question. Do not make edits unless the
  question clearly implies the user wants something changed (e.g. "why
  is this done this way instead of X?" where X is an obvious improvement
  request, or "this looks wrong, why?"). When in doubt, ask before
  editing.

## Testing

### Philosophy

Prefer ground-up test rewrites over incremental patches — design the
test suite that *should* exist from first principles rather than
patching existing tests.

**Test behavior, not implementation.** The question a test should answer
is "does the code work?" — not "is the code implemented exactly how it's
implemented right now?" This codebase is under active development with
frequent renaming, restructuring, and refactoring. Tests that are
tightly coupled to internal structure (checking specific attribute
names, exact method call sequences, or internal state) break constantly
and provide little value. Tests that verify end-to-end behavior survive
refactors.

**Fewer, better tests.** Prefer a smaller number of end-to-end test
cases that exercise real workflows over a large number of trivial unit
tests. A single test that constructs real objects, exercises them
through a realistic scenario, and verifies the output tests more
meaningful behavior than ten tests that individually check field
initialization. When a test can only fail if the code it tests is also
changed in the same commit, it's testing implementation, not behavior —
remove it.

**Readability and parameterization.** Tests should be readable as
documentation of what the code does. Use the language's native
parameterization or data-driven testing mechanisms to cover variations
instead of duplicating test functions with different constants. Avoid
random magic numbers — use descriptive variable names or setup helpers
that make the test's intent clear.

### Good Tests

- **Tests a real workflow:** constructs objects, exercises them, checks
  observable results
- **Survives refactors:** doesn't break when internals are renamed or
  restructured
- **Has a clear purpose:** the test name and body make it obvious what
  behavior is being verified
- **Uses parameterization:** variations are covered via data-driven
  patterns, not copy-pasted functions
- **Avoids mocking internals:** mock external boundaries (network,
  filesystem) but not internal classes

### Bad Tests

- Tests that check exact internal/private state rather than observable
  behavior
- Tests that duplicate another test with a trivially different input
- Tests that only verify string representation or debug output format
- Tests that test the testing infrastructure itself (helpers testing
  helpers)
- Tests where the assertion is essentially restating the implementation

## Code Style

Key patterns (see `pyproject.toml` for formatter/linter config):

- `from __future__ import annotations` in every module
- `__all__` in every leaf module; wildcard re-exports in `__init__.py`
- `self: ClassName` on methods, `cls: type[ClassName]` on classmethods
- Google-style docstrings with double-backtick RST references
- Section headers: `# ------ section name` (module level only)
- Single quotes preferred; double quotes for docstrings

### Comments

Step-by-step `# verb noun` comments before logical blocks — but aim for
the middle ground. Short methods need no comments; longer methods should
label logical blocks, not every line:

- **Good:** `# validate folder name` before a check,
  `# build merged list, refreshing labels` before a complex loop
- **Bad:** every line gets its own comment (`# build statement`,
  `# execute statement`)
- **Bad:** long stretches of dense logic with zero comments

### CLI Commands (`cli/cmd/`)

- All commands live in a single module, `cli/cmd/wiki.py`; there are no
  sub-apps
- Each command's registration function is named after the command, with
  signature `def name(app: typer.Typer) -> typer.Typer`, registered
  directly on the wiki app in `cli/main.py` (e.g. `cmd.install(app)`,
  `cmd.read(app)`)
- Typer args/options as local variables before the inner function
- Do not inline method calls in `typer.echo()` — assign to a variable
  first
- When mixing positional and keyword args in multi-line calls, pass all
  as kwargs (unless the param is positional-only with `/`)

### Shell Scripts

`_config/git/merge_index.sh` is the repo's only git merge driver -- a
shell script for `_index.md` files (installed via `.gitattributes`
`merge=wiki-index`), invoked by git with fixed positional args
(`%A %O %B`, i.e. ours/base/theirs):

- `set -euo pipefail` at the top
- Positional args read directly (`OURS="$1"`, `BASE="$2"`,
  `THEIRS="$3"`); no `usage()`/`--help` or `while`/`case` flag parsing
  (it is a merge driver, not a CLI)
- Uppercase variable names for script-level state
- Comments before each logical section

### What Not To Do

- No backwards-compatibility shims — if behavior changes, change it
  cleanly. Do not leave legacy fallback code.
- No implementation-phase comments (`Phase N`, `TODO: move later`) — the
  code should read as if it was always this way.
