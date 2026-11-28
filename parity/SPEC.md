# Parity Harness Specification

The harness runs two engines on the same tree, with the same clock, environment
and arguments, and reports everything they do differently: a byte of either
stream, an exit code, a byte or a mode of any file, a file that one of them
touched, and a cache entry. It excuses only what the registry of differences
lists (Registry, below). A run with no other difference is the proof that the
two engines are interchangeable on that tree.

The harness is a development tool. It lives in `parity/` and is not part of the
wheel or the sdist.

## Running It

```bash
# build the oracle checkout and the venvs of the v1 sides
python -m parity oracle ensure

# hold the oracle file level with v1
python -m parity oracle check

# compare two sides over the matrix
python -m parity matrix --a NAME --b NAME [--registered] [--sides SIDES] \
    --corpora CORPORA --work DIR --out DIR

# check the harness itself with one side
python -m parity selfcheck --side NAME [--sides SIDES] \
    --corpora CORPORA --work DIR --out DIR

# check the classifiers of the registry with one side
python -m parity registry --side NAME [--sides SIDES] \
    --corpora CORPORA --work DIR --out DIR [--release]

# run v1's CLI tests and the terminal table through the dispatcher
python -m parity suite [--engine NAME] [--sides SIDES] --work DIR --out DIR

# record the calls of v1's tests and replay them as command lines
python -m parity replay [--engine NAME] [--sides SIDES] --work DIR --out DIR

# write the fixture families and run the matrix on each
python -m parity fixtures [--engine NAME] [--sides SIDES] [--every] --work DIR --out DIR

# perturb each corpus by a seed and run the matrix on what the seed leaves
python -m parity perturb corpus [--engine NAME] [--sides SIDES] \
    --corpora CORPORA [--seed N] --work DIR --out DIR

# perturb small trees by many seeds and run a short stage on each
python -m parity perturb loop [--engine NAME] [--sides SIDES] [--corpora CORPORA] \
    [--seed N | --fresh N] [--jobs N] --work DIR --out DIR

# compare the alt side with the oracle and read the names of each wiki
python -m parity interpreters --corpora CORPORA --work DIR --out DIR

# replay the merges of each corpus's history and compare the sides on each
python -m parity merges [--engine NAME] [--sides SIDES] --corpora CORPORA \
    [--cache DIR] [--new] [--sample N] [--commit COMMIT] [--jobs N] [--at DIR] \
    --work DIR --out DIR

# run a gate: the layers of the harness on the corpora, each timed
python -m parity gate {full,daily} [--engine NAME] [--sides SIDES] --corpora CORPORA \
    [--loop LABEL] --cache DIR [--jobs N] --work DIR --out DIR

# run a command in the Linux container
python -m parity container [--commit COMMIT] [--out DIR] COMMAND [ARG ...]

# hold a built wheel and sdist to what the package keeps for whoever installs it
python -m parity contracts --wheel FILE --sdist FILE [--check NAME] --work DIR --out DIR
```

A side name is `oracle`, `alt` or `crossed`, the three v1 sides that
`parity/oracle.toml` names (Oracle, below), `rust`, the binary cargo builds from
this checkout (Sides, Rust side), or a side that the `SIDES` file declares.
`SIDES` may not declare a side under the name of a v1 side or of the Rust side.

Every command reads `parity/oracle.toml` and asks git where the repository and
its main checkout are, whichever sides a run names. So the harness needs git on
`PATH`, and its folder must lie in a checkout of the repository.

`--corpus LABEL` (repeatable) picks corpora; the default is every corpus of the
file. `--now` sets the fixed instant. `matrix` and `selfcheck` take `--seed`,
the seed of the matrix sample. `matrix` also takes `--stage NAME` (repeatable)
and `--keep`, which leaves the snapshot and every tree in the work folder.

The three commands exit 0 when every result is the expected one and 1 otherwise.
For `matrix` that is no difference, and no command the registry excuses unless
`--registered` is given (Registry, Second engine). A run that would compare
nothing exits 2 with a message, before it snapshots anything: a corpora file
with no corpus, an unknown corpus label, stage name or side name, the same side
named as `--a` and `--b`, a sides or corpora file whose values have the wrong
shape, a `--now` that is not an instant in the form `WIKI_FREEZE_TIME` takes, a
checkout or a work folder the harness refuses (see Snapshots), a side or a
`describe` command that cannot be started, a side whose `--version` fails or
prints nothing, a side that does not prove its identity, a v1 side the oracle
rules refuse, a Rust side that is not built or is stale (Sides, Rust side), or a
registry file that breaks a rule of Registry. An error of the harness itself
prints a Python traceback and exits 2 as well, so exit 1 always means that a
result is not the expected one.

The harness makes `<work>/<label>` for each corpus; it must not exist
beforehand. `matrix` removes it at the end unless `--keep` is given. `selfcheck`
and `registry` remove it when every check passed; otherwise they leave the
snapshot and, under `failed/<check>/<stage>/<run>/`, the slot of every run of
each failed check. An error of the harness itself leaves the folder as it stood.

`SIDES` is a TOML file with one table per side:

```toml
[candidate]
argv = ["/path/to/venv/bin/wiki"]

[reference]
argv = ["/path/to/python", "-P", "/path/to/wrapper.py"]
notice = false
env = { EXAMPLE = "1" }
describe = ["/path/to/python", "-P", "/path/to/describe.py"]
judge = ["/path/to/python", "-P", "/path/to/judge.py"]
```

`describe` is optional. It is a whole command, run once under the side's
environment before anything is snapshotted, and its stdout goes into the result
file. Both sides of a release may report the same version, so this is where a
result file says what each side is made of: the interpreter, the versions of the
dependencies, and the commit or the path of the package. A `describe` that fails
ends the run with exit 2.

`judge` is optional too. It is a whole command that answers what the side's
interpreter makes of an input, as `parity/judge.py` does (Registry,
Classifiers).

`CORPORA` is a TOML file with one table per corpus. The harness reads corpus
paths from this file only. Labels are neutral (`corpus-a`), and the file that
maps them to real checkouts stays outside the repository.

```toml
[corpus-a]
checkout = "/path/to/checkout"
root = "wiki"
```

`checkout` is the folder to snapshot, and `root` is the wiki root below it.

Each corpus gets one result file, `<out>/<label>.json`. It records the host
(platform, Python, SQLite, git, the system git config), the instant, each side's
argv, variables, identity, reported version and description, for the Rust side
the paths that differ from its commit (`dirty`), the status of every registry
entry, the sample, for every stage how many commands of each run ended with each
exit code, which searches found nothing and which commands a registry entry
excused, and for every case the exit code, the size and sha256 of both streams,
and the touched count on every run. `matrix` saves the streams of a case under
`<out>/<label>/<stage>/` when they differ, an excused case included, and
`selfcheck` and `registry` save those of a failed check under
`<out>/<label>/<check>/<stage>/`. `selfcheck` and `registry` both write
`<out>/<label>.json`, so each takes an `--out` folder of its own. Result files
hold corpus paths and text, so they stay outside the repository too.

`suite` takes no corpus: its tests make their own wikis (CLI Suite, below). It
exits 0 when every test passes and the terminal table shows no difference, and 1
otherwise. It exits 2 with a message, before it runs a test: on an `--engine`
that is the v1 side itself or that no file declares, on an `--engine` when
`parity/ported.toml` lists no command, on a list that breaks a rule of Ported
Commands, on a side that cannot be started or does not prove its identity, and
on a work folder it refuses. `--test ID` (repeatable) runs the named test files
or tests in place of all of them, and `--jobs N` sets how many processes run the
tests. It makes `<work>/suite`, which must not exist beforehand, and removes it
when the run passed. It writes `<out>/suite.json`, what pytest printed as
`<out>/suite/pytest.txt`, and both outputs of each terminal case whose output
differs under `<out>/suite/`. The first two are written once the tests have run,
before the terminal table starts, and the result file takes the table when it
ends: an error of the table leaves the tests' result behind.

`replay` takes no corpus either: it runs on the trees v1's tests make (Replay,
below). It exits 0 when the sides differ in nothing and it has no finding, and 1
otherwise. It exits 2 with a message, before it runs a test, on what `suite`
refuses, on a routing table that breaks a rule of Routing and on a kind list
that breaks a rule of Kinds. `--test` and `--jobs` are those of `suite`. It
makes `<work>/replay`, which must not exist beforehand, and removes it when the
run passed. It writes `<out>/replay.json`, what pytest printed as
`<out>/replay/pytest.txt`, the recorded format calls as
`<out>/replay/formats.jsonl`, and both streams of every run of each call that
differs or is unfaithful under `<out>/replay/`.

`fixtures` takes no corpus: it writes its own (Fixture Families, below) and runs
each as `matrix` runs a corpus. It exits 0 when no family shows a difference,
and 1 otherwise. It exits 2 with a message, before it writes a family: on an
`--engine` that is the v1 side itself or that no file declares, on a family the
generator does not have, on a family named twice, on a tree a second engine
refuses (Refused Trees, below), on a named family that the file system of the
work folder cannot hold, on a side that cannot be started or does not prove its
identity, and on a work folder that exists, lies inside a repository or lies
below a folder that holds `.wiki/settings.json`. `--family NAME` (repeatable)
picks families; the default is every family the file system can hold, and the
run names the ones it leaves out. `--every` leaves none out: the run exits 2
where the file system cannot hold a family, and CI runs the families this way.
`--now` and `--seed` are those of `matrix`. It makes `<work>/fixtures`, which
must not exist beforehand, and removes it when the run passed; a failed run
leaves the checkouts it wrote under `<work>/fixtures/trees/`. Each family gets a
result file, `<out>/<family>.json`, in the form `matrix` writes for a corpus,
and the streams of a case that differs are saved under
`<out>/<family>/<stage>/`.

`perturb` changes trees by a seed and runs each as `matrix` runs a corpus
(Perturbation, below). `perturb corpus` takes each corpus of the file once, and
`--seed N` names the seed in place of a fresh one. `perturb loop` takes small
trees once for every seed: the example wiki and the fixture families, or with
`--corpora` the wiki of each corpus. `--family NAME` (repeatable) and `--every`
pick among the small trees as they pick families for `fixtures`. Its seeds are
the committed list, the ones `--seed N` (repeatable) names, or `--fresh N` new
ones, and `--jobs N` sets how many trees run at once. `--engine`, `--sides`,
`--corpus` and `--now` are those of the other commands. Both exit 0 when no tree
shows a difference or a finding, and 1 otherwise. They exit 2 with a message,
before a tree is written: on what `fixtures` refuses of an engine, a tree and a
work folder; on a corpus the harness cannot snapshot, a corpus named twice, and
in a loop a corpus whose wiki root is no folder; on a seed list or a kind list
that breaks a rule; on a kind list that disagrees with the operators
(Perturbation, Kinds); on `--fresh` with `--seed`, or with a count below one; on
`--jobs` below one; and on `--family` or `--every` with `--corpora`. They make
`<work>/perturb`, which must not exist beforehand, and remove it when the run
passed. A corpus gets a result file `<out>/<label>.json` in the form `matrix`
writes, with the seed, what the perturbation did, the kinds each of its trees
showed and the findings; the streams of a case that differs are saved under
`<out>/<label>/<stage>/`. A tree of a loop gets `<out>/<label>.json` with one
row for each seed (differences, findings, excused commands, seconds), written
again as each seed ends, and for each seed that failed the whole result as
`<out>/<label>/<seed>.json`, with the streams under
`<out>/<label>/<seed>/<stage>/`. A tree that ends in an error under a seed (a
copy or a tap that fails, a write that is refused) costs the run no other
result: its line and its row hold the error, the traceback goes to stderr, the
other trees and seeds run on, and the run exits 2 with its work folder left in
place. `<out>/perturb.json` records the run: the mode, the seeds, the trees, how
many failed, how many ended in an error, and the findings of the whole run. A
corpus that failed keeps its perturbed snapshot under
`<work>/perturb/runs/<label>/<seed>/`, because its checkout may move on; a tree
of a loop is written again from its seed.

`interpreters` compares the alt side with the oracle on each corpus of the file
(Interpreters Run, below). `--corpus`, `--now` and `--seed` are those of
`matrix`. It exits 0 when no corpus shows a difference, a command the registry
excuses, or a name that holds a changed code point, and 1 otherwise. It exits 2
with a message, before it snapshots anything: on what `matrix` refuses of a
corpora file, a corpus, a work folder and a side, on a corpus named twice, and
on an oracle file whose crossed side does not cross the other two. It makes
`<work>/<label>` for each corpus, which must not exist beforehand, and removes
it when the corpus passed; a corpus that failed keeps its snapshot there,
because its checkout may move on. A corpus gets a result file
`<out>/<label>.json` in the form `matrix` writes, with the three sides, how many
code points the two interpreters do not agree on, the second run with the cause
of each difference and what the crossed side shows alone, and the names the scan
read and found. The streams of a case that differs are saved under
`<out>/<label>/<stage>/`, and those of the second run under
`<out>/<label>/crossed/<stage>/`.

`merges` replays the merge commits of each corpus's history (Merge History,
below). `--engine`, `--sides`, `--corpus` and `--now` are those of the other
commands, and `--jobs N` sets how many merges run at once. `--commit COMMIT`
(repeatable) names the merges to replay, each by its commit or the start of it,
in place of all of them. It exits 0 when no merge shows a difference, and 1
otherwise; with no `--engine` a command the registry excuses is a result that is
not the expected one. It exits 2 with a message, before it snapshots a
repository: on what `matrix` refuses of a corpora file, a side and a work
folder; on a corpus named twice; on a corpus whose checkout is no repository; on
`--sample` or `--jobs` below one; on `--new` with no `--cache`; and on a
`--commit` that names no merge commit of the run's histories, which it reads
from the checkouts. It makes `<work>/merges`, which must not exist beforehand,
and removes it when the run passed. `--at DIR` names the folder the merges run
in (Merge History, Path) in place of `<work>/merges/at`: a folder the harness
refuses as it refuses a work folder, and removes when the run ends. A history
gets a result file `<out>/<history>.json`: the host and the sides as `matrix`
records them, what the cache is keyed on, the commit the snapshot stood at, how
many merges its history holds, what the replayed merges were, and one row for
each merge, in the form `matrix` writes for a stage, with the seconds each
side's pass took and the processor seconds of the side's own processes. The
streams of a case that differs are saved under
`<out>/<history>/<commit>/<stage>/`. A merge that ends in an error (a checkout
git refuses) costs the run no other result: its row holds the error, the
traceback goes to stderr, the other merges run on, and the run exits 2 with its
work folder left in place.

`gate` runs a full or a daily gate (Gates, below) and exits with the highest
exit code of its steps, as that section says.

`container` runs one command in the Linux container (Container, below) and exits
with that command's exit code. It exits 2 with a message when it is given no
command, when `--commit` names no commit, and when Docker is not installed or
its daemon does not answer.

`contracts` takes no corpus and no side: it checks the wheel and the sdist that
a build of this checkout made (Contracts, below). It exits 0 when every check
holds, and 1 otherwise. It exits 2 with a message, before it installs anything:
on a wheel or an sdist that is no file, on a check named twice, on a work folder
that exists or lies inside a repository, on a `PATH` that holds no uv or no git,
and on a system folder that holds a `cargo`. `--check NAME` (repeatable) picks
checks; the default is every check. It makes `<work>/contracts`, which must not
exist beforehand, and removes it when the run passed. It writes
`<out>/contracts.json`: the host with its git and its uv, the paths that differ
from the commit among those a build reads (`dirty`), the two files, the version,
the commit, the interpreter, v1's commit and version, and each check with its
findings and its seconds. What each command of an installer printed is saved
under `<out>/contracts/<check>/`.

The sections below are the rules of the harness, one part each: the oracle,
sides, snapshots, comparison, caches, the matrix, the fixture families, the
perturbation, the merge history, the interpreters run, the registry, the
self-check, the CLI suite, the replay, with the account a whole replay gives of
v1's tests and kinds, the gates, the container, and the contracts.

## Oracle

The oracle is v1 at the commit that `parity/oracle.toml` names: the merge base
of this branch and the branch v1 lives on. Every comparison against v1 runs v1
from one checkout of that commit, and `python -m parity oracle ensure` is the
only way that checkout is made.

```toml
commit = "<the oracle commit>"
version = "<what wiki --version prints at that commit>"
base = "<the branch v1 lives on>"
pending = "<a branch that holds the commit until the base branch does>"

[sides.oracle]
python = "<the interpreter's version>"
unicode = "<the version of its Unicode tables>"
libyaml = "<the libyaml its PyYAML C loader is built on>"
constraints = "constraints/oracle.txt"

[[unmerged]]
commit = "<a v1 commit this branch has not merged>"
reason = "<why it waits>"
```

`pending` and `unmerged` are optional.

- **Checkout.** A detached worktree at the oracle commit, beside the main
  checkout: `worktrees/<repository>-oracle-<the commit's first seven digits>`.
  When the oracle moves, the old checkout is removed with `git worktree remove`
  once no branch names its commit. `ensure` makes a checkout again when its
  folder was deleted some other way.
- **Sides.** v1 has three sides, and all run the oracle commit. `oracle` defines
  parity. `alt` is the commit under a second interpreter that v1 supports, with
  its own package versions. `crossed` is the alt side's interpreter under the
  oracle side's packages, the side a difference between the other two runs on
  once more (Interpreters Run). Each side has a venv in the checkout,
  `.venv/<side>`: a uv-managed interpreter of the named version; exactly the
  packages its constraints file pins; and the checkout itself installed in
  place, so the side imports `wiki` from the checkout.
- **Interpreter build.** The named version is the same CPython release on every
  host, but not the same build: uv installs the build its own release lists and
  keeps one it has already installed. The build and the SQLite inside it can
  therefore differ between two hosts. Nothing names them, and the result file
  records both for each v1 side.
- **Pins.** A constraints file pins every package of its venv, one
  `name==version` per line. `oracle.toml` names what a package pin cannot: the
  interpreter, its Unicode tables, and the libyaml inside PyYAML's C loader.
  Every side pins the PyYAML built on libyaml 0.2.5, so the loader is no
  difference between the sides.
- **Refusals.** `ensure`, and every run that names a v1 side, refuse a checkout
  whose `HEAD` is not the oracle commit, and one with a changed, added or
  deleted file under `wiki/` or `tests/`. They refuse a venv whose interpreter,
  Unicode tables, libyaml, `wiki` version, `wiki` import path or set of
  installed packages differs from what the side names. `ensure` builds such a
  venv again from nothing, and refuses it if it still differs; a run never
  builds.
- **Inside a side's interpreter.** The checkout is installed in place, so a
  side's interpreter can import every top-level folder of the oracle commit, a
  `parity` folder among them. Harness code that runs there is a script run by
  its path with `-P` (the launcher; `facts.py`, which prints what the
  interpreter is made of; `judge.py`, which answers the classifiers; `tap.py`,
  which names the kinds a tree shows; and `points.py`, which prints what the
  interpreter makes of every code point), never an import of `parity`. The
  pytest plugins of the CLI suite and of the replay are loaded there by names of
  their own (`parity_suite`, `parity_record`), from a folder that holds the
  harness's plugins and nothing else.

`python -m parity oracle check` holds `oracle.toml` level with v1, by three
checks. It reads branches as the remote-tracking references hold them, so fetch
first. It exits 1 when a check fails, and `--skip NAME` (repeatable) leaves one
out. An error of the harness itself exits 2 from the `oracle` commands as well,
so exit 1 always means that a check failed.

| Check     | Holds when                                                                                        |
| --------- | ------------------------------------------------------------------------------------------------- |
| `base`    | `commit` is the merge base of `HEAD` and the branch that holds v1.                                |
| `level`   | Every commit of v1 is merged into `HEAD`, or `unmerged` lists it with a reason.                   |
| `removed` | No path that `parity/removed.txt` lists, one per line, is tracked: a removed v1 path is not back. |

- **Pending branch.** While `pending` names a branch that holds a commit the
  base branch lacks, that branch holds v1: `base` takes the merge base with it,
  and `level` counts the unmerged commits of both branches. Once the base branch
  holds it, or the branch is gone, the base branch alone holds v1.
- **Removed paths.** A line of `parity/removed.txt` names a file or a folder,
  and a folder covers every file below it. A file that v1 adds to a removed
  folder merges with no conflict at the next sync, and the folder's line is what
  fails the check then.
- **Where they run.** `base` and `removed` depend on the tree alone and run on
  every pull request. `level` changes when v1 gains a commit, so it runs where a
  person or an agent reads it: at the start of a work session and at each gate.

## Sides

A side has a name, an argv prefix, variables of its own, a `notice` flag, an
optional `describe` command, an optional `judge` command, an optional `tap`
command, an optional `points` command, and an optional identity. The harness
appends the wiki arguments to the prefix. The environment is built from nothing:

| Variable              | Value                                                                            |
| --------------------- | -------------------------------------------------------------------------------- |
| `PATH`                | the folder of the git the harness resolves, then `/usr/bin:/bin:/usr/sbin:/sbin` |
| `HOME`                | `<slot>/home`                                                                    |
| `WIKI_CONFIG_DIR`     | `<slot>/home/wiki_config`                                                        |
| `OFFLINE_MODE`        | `true`                                                                           |
| `GIT_CONFIG_GLOBAL`   | `<slot>/home/.gitconfig`, an empty file                                          |
| `GIT_CONFIG_NOSYSTEM` | `1`                                                                              |
| `LANG`                | `C.UTF-8`                                                                        |
| `TZ`                  | `<+0545>-05:45`                                                                  |
| `WIKI_FREEZE_TIME`    | the run's instant                                                                |

The side's own variables are applied last. stdin is the null device.

- **Zone.** `TZ` is a POSIX zone string, which needs no zone files, at an odd
  offset from UTC. An engine that renders a stamp in local time where the other
  uses the wiki's zone then shows a difference; under `TZ=UTC` it would not.
- **Git config.** The Python engine removes every `GIT_*` variable but
  `GIT_CEILING_DIRECTORIES` and `GIT_OPTIONAL_LOCKS` from the git commands it
  runs. Those commands find the same empty global config as `$HOME/.gitconfig`,
  and they still read the system config, which no variable left to them can
  switch off. The result file records `git config --system --list`.
- **Notice.** An engine prints one fixed line first on stderr when
  `WIKI_FREEZE_TIME` fixes its clock. On a side with `notice = true` (the
  default) the harness removes that line when it is the first line of stderr. A
  side that fixes its clock through a wrapper prints no notice and sets
  `notice = false`. A notice on a side that does not declare it is a stderr
  difference. Every matrix command reads the clock variable, so a side that
  declares the notice must print it on each one: a command without it is a
  `notice` difference, since that side's clock was not fixed. A command that the
  argument parser ends, with a usage error or a help page, reads no clock. Its
  case says so, and a side that declares the notice must then print none: one
  that does shows a `notice` difference, and the line stays in its stderr.
- **Hash seed.** Outside the self-check a side has no `PYTHONHASHSEED` unless
  its table sets one.
- **v1 sides.** The argv prefix of a v1 side is its venv's interpreter, `-P` and
  `parity/launcher.py`. The launcher runs the command line of the `wiki` package
  the interpreter imports, with `argv[0]` set to `wiki`. Its `describe` command
  runs `parity/facts.py`, its `judge` command `parity/judge.py`, its `tap`
  command `parity/tap.py` (Perturbation, Kinds), and its `points` command
  `parity/points.py` (Interpreters Run, Names). Only a v1 side has a tap or a
  `points` command.
- **Rust side.** The side named `rust` is the binary of a release build of the
  checkout that holds the harness, `target/release/wiki`, called by its resolved
  path. The harness builds nothing: `cargo build --release --locked` runs first,
  and a checkout with no such binary is refused. The side's identity is read
  from the checkout, not from the binary: the engine `rust`, the version the
  workspace's manifest states, and the commit `HEAD` holds. So a binary built at
  another commit, or under another version, fails every command it is given. A
  sides file that declares a side named `rust` is refused.
- **Uncommitted edits.** The commit says nothing of an edit that is not
  committed: a binary carries the commit of `HEAD` whatever the tree held when
  cargo built it. Two rules hold the binary to the tree as it stands.
  - *Stale.* cargo lists what it built the binary from in
    `target/release/wiki.d`: the sources of the crates, their build scripts, the
    embedded files, and the files of the git folder that hold the commit; a
    listed folder stands for everything below it. A binary is stale when that
    file is missing, or when a listed path is gone or was written after the
    binary. Every command refuses a stale Rust side, with the command that
    builds it. These are the cases in which cargo builds again, so a build
    always clears the refusal, and a test file, which is no input of the binary,
    never causes one.
  - *Dirty.* The paths that differ from `HEAD`, changed, added or deleted, among
    `crates`, `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`, `wiki/_assets`
    and `wiki/skills`. A binary that is not stale is a build of its commit only
    when there are none. A gate whose engine is the Rust side exits 2 before a
    step runs when there is one (Gates). Every other command runs, prints the
    paths and records them as `dirty` with the Rust side in its result file, so
    a change can be tried before it is committed and no result passes for a
    result of the commit.
- **Identity.** A side with an identity gets `WIKI_TRACE=<file>` on every
  command, a fresh file outside the slot. Before it runs the command, the side
  appends one line of JSON to the file: `engine`, `version` and `build`. For a
  v1 side the launcher writes it, with the engine `python`, the package's
  version and the commit its checkout's `HEAD` holds, and takes the variable out
  of the command's environment. The Rust binary writes it itself, before it
  reads its arguments, with the engine `rust`, the workspace's version and the
  commit of the checkout it was built in, which its build script reads from
  git's files; a binary built outside a checkout writes an empty `build`. It
  writes the record only once its bundled SQLite has matched a row of a
  full-text table. A command that leaves anything but the expected record, once,
  is an `identity` difference: the side is not the engine it names.

## Snapshots

`prepare` clones the checkout once into `<work>/<label>/snapshot` and hashes it.
Every tree a side runs on is a clone of that snapshot, placed at one path:

```text
<work>/<label>/
    snapshot/        the clone of the checkout
    slot/            where every side runs, one at a time
        tree/        a clone of the snapshot
        home/        HOME, with the git config
        cwd/         the neutral folder commands run from
    kept/<stage>/<run>/   a slot moved aside for a later stage, or by --keep
    failed/<check>/       the kept slots of a failed check of the self-check
```

- **Clones.** `cp -Rpc` on macOS (`clonefile`), `cp -a --reflink=auto`
  elsewhere. Files and folders keep their modes and modification times, so both
  sides see the same tree. The hand-edit check of `update` reads mtimes and
  `git status`, so both must match.
- **Index.** A copy has new inodes, which makes every git index entry stale, and
  git would hash every tracked file on each call. Each clone therefore runs
  `git update-index -q --refresh` before the first command.
- **Refusals.** Each corpus of a run is checked before the first one is
  snapshotted. A checkout that is not a folder is refused, a symlink to one
  included: the copy of a link would point at the live folder. A checkout whose
  `.git` is a file or a symlink is refused: a linked worktree keeps its state in
  the main repository. A checkout with no `.git` is refused when the work folder
  lies inside a repository, because git would answer from that one. A work
  folder below a folder that holds `.wiki/settings.json` is refused: every side
  runs under a scratch home, so even the user's own `~/.wiki` reads as an
  enclosing wiki, and a command run from inside the snapshot's wiki would fail
  the same way on both sides. A `<work>/<label>` that exists is refused and left
  as it stood.
- **Swept folders.** A clone keeps its source's times, so its entries look old
  to a cleaner of temporary files. On a path the system sweeps, such as `/tmp`
  on macOS, a snapshot can lose old empty folders while a run is under way, and
  the runs on either side of the sweep then differ at `(baseline)`. Choose a
  work folder the system leaves alone.
- **Live repositories.** The harness runs no `git worktree` subcommand, no
  commit and no checkout in a clone. Its one git write is the index refresh. The
  merge replay checks out and merges in a repository of its own, which holds no
  file of the checkout's repository (Merge History).
- **Cold stages.** A stage marked cold removes `<root>/.wiki/cache` from its
  clone before the first command.
- **Links out of a clone.** A clone keeps the target of a symlink as it is
  written, so a write through a link that points out of the clone changes
  another tree: the live checkout, where the target is absolute. The harness
  writes two things into a clone, the settings file of a stage that sets one and
  the files of a perturbation. Each path is resolved first, and a path that
  leads out of the clone is refused with an error.

## Comparison

The harness scans the whole slot (the clone outside its top-level `.git`, the
home and the neutral folder) before the first command and after each one.

- **Streams and exit code.** stdout, stderr (after the notice rule) and the exit
  code are compared as they are.
- **Manifest.** Each path has a type (`file`, `dir`, `link`, `other`), its
  permission bits, and for a file its size and sha256, for a symlink its target.
  After each command the two sides must hold the same manifest for every path
  either of them changed, outside `<root>/.wiki/cache`. The two manifests are
  also compared before the first command.
- **Touched set.** A path is touched when it was created or deleted, or when it
  is not a folder and its inode or mtime changed. The touched sets of a command
  must be equal. A file written again with the same bytes is touched and leaves
  the manifest unchanged, so the two rules see different things. The rule reads
  nothing but the inode number and the mtime. So a file that is removed and
  copied again with its source's mtime reads as untouched wherever the file
  system gives it the freed inode number back, as Linux file systems do.
- **Read-only commands.** `lint`, `update --check`, `read`, `match`, `map` and
  `search` may touch nothing outside `<root>/.wiki/cache`. This is checked on
  each side by itself. One path is allowed: where the wiki has no `.wiki` folder
  yet, a command that writes a cache makes that folder to put the cache folder
  in. The two sides are still compared on it, as on any path.
- **Allowed exit codes.** Two sides that fail the same way agree on everything,
  so each case names the exit codes it allows, and each side is checked by
  itself. A command that must succeed allows 0 (`update`, `new`, `map`, `read`).
  One that also reports through its exit code allows 0 and 1 (`lint`,
  `update --check`, `search`, `match`). A search for a word no page holds allows
  1, and a command built to fail allows 2. Any other code is an `unexpected`
  difference: the side did not run the command the case is built for.
- **Scans.** The first scan of a clone takes its digests from the snapshot for
  every file whose size, mode and mtime are the snapshot's. A later scan hashes
  a file again when its inode, size, mode, mtime or ctime changed; no write
  leaves the ctime as it was, so a write that puts the old mtime back is still
  seen. After the last command every file is hashed again, and the tree must
  hold exactly what the commands left.

Each difference names the stage, the run that differs from the first run, the
case, an aspect and a detail:

| Aspect       | Meaning                                               | Detail        |
| ------------ | ----------------------------------------------------- | ------------- |
| `stdout`     | the bytes of stdout differ                            | empty         |
| `stderr`     | the bytes of stderr differ                            | empty         |
| `exit`       | the exit codes differ                                 | empty         |
| `tree`       | the manifests differ at a path                        | the path      |
| `touched`    | one side touched a path and the other did not         | the path      |
| `cache`      | the caches differ                                     | cache and key |
| `unmerged`   | a merge left a path unmerged in another way           | the path      |
| `wrote`      | a read-only command touched a path outside the caches | the path      |
| `unexpected` | a command ended with an exit code its case forbids    | the exit code |
| `notice`     | a side broke the notice rule of Sides                 | empty         |
| `identity`   | a side did not leave its identity record              | empty         |

The last four rows are checked on each run by itself, and the run they name is
the one that breaks the rule. Paths are relative to the slot, so a page reads
`tree/<root>/<page>`. The case is `(baseline)` for a difference before the first
command and `(end)` for a tree that does not hold what its commands left.

## Caches

The caches are compared before the first command and after every command in
which either side wrote anything.

- **File list.** The paths below `<root>/.wiki/cache`, their types and their
  modes must be equal, and so must the bytes of its `.gitignore`.
- **`word_counts.json`.** The two files must hold the same keys with the same
  entries. A recorded `mtime` that equals the page's current mtime on its own
  side reads as "current"; any other recorded `mtime` is compared as it is. Two
  sides that wrote a page at different moments therefore agree, and a stale
  entry must be stale in the same way on both.
- **Form.** The file's bytes must be the form the Python engine writes for the
  value they hold: `json.dumps(value, indent=2, sort_keys=True)` and a newline.
  The two sides must agree on whether that holds.
- **Bytes.** Where the entries and the form agree, the bytes are compared too,
  as long as neither side has written a page in the stage and the stage started
  from the snapshot.
- **`search.db`.** The harness reads a copy of the database and its write-ahead
  files, never the files themselves. `meta` must be equal, and so must every row
  of `sqlite_master` (name, type, table and SQL text), which holds the tables,
  their columns and the tokenizer of the index. `files` is compared by path:
  `size` as it is, `mtime_ns` by the "current" rule. `notes_fts` is compared by
  path over all seven columns, without rowids, so the order of insertion does
  not count.

## Matrix

A stage is a list of commands that every side runs on one fresh tree. The matrix
has five stages:

| Stage      | Tree                                         | Commands                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ---------- | -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pristine` | the snapshot                                 | `lint` plain, `--json`, `--full` and `--count`; `update --check` plain, `--full` and `--count`; `lint` and `update --check` scoped to each sampled folder; then `lint` and `update --check` from the wiki root, and `lint` from a sampled folder, with no `--path`.                                                                                                                                                                    |
| `query`    | the snapshot, with the caches it holds       | `map` with each view option and scoped; `search` in text and JSON, with two terms, `--limit`, `--prefix`, `--raw`, `--tag`, scoped, with a word no page holds and with an invalid query; `match` in each output mode, with `--ignore-case`, `--all` and `--field`, scoped, with no hit and with an invalid pattern; `read` of the root index, a folder, a missing page, each sampled page, and line, word and character slices of one. |
| `write`    | the snapshot without `<root>/.wiki/cache`    | `search` and `map` (both caches built from nothing), `update`, `update --check`, `new` at the root and under a sampled folder, `update --check`, `lint`, `map`, `search`.                                                                                                                                                                                                                                                              |
| `full`     | the snapshot                                 | `update --check --full`, `update --full`.                                                                                                                                                                                                                                                                                                                                                                                              |
| `cross`    | each tree that `write` left, caches included | `map`, `search`, `lint`, `update --check`, `update`.                                                                                                                                                                                                                                                                                                                                                                                   |

- **Runs.** A stage that starts from the snapshot has one run per side. `cross`
  has one run per pair: each side on the tree each side left, so each engine
  reads the caches the other wrote. Every run is compared with the first.
- **Sample.** The folders, pages and words a matrix names are drawn from the
  snapshot with a seeded generator, so one seed and one tree give one matrix. It
  takes up to four top-level folders and two second-level folders, and up to six
  pages from folders that hold an index. Dot folders, symlinks and names that
  start with a dash are not drawn.
- **Words.** A search or a match compares more than "no match" only when it
  finds something, so each word comes from the text its command reads. The
  sample reads the head of the pages that carry a `name` field (the wiki writes
  one on every page it manages), in a drawn order, until it holds: two words of
  one page's body (the search terms, so the two-term search can find that page);
  a body word of a page below the first sampled folder (the scoped search); a
  word of a `desc` field (`match --field desc`); a word of a `name` or `tags`
  field (`match --field name,tags`); and a body word and a tag of one page with
  tags (`search --tag`). A word is a whole run of five or more ASCII letters.
  Where the wiki holds no such word, the command takes a body word instead and
  the sample in the result file shows the gap.
- **Found nothing.** Every search and match is built to find something, except
  the two for a word no page holds and the two that are invalid. One that exits
  1 on every run is printed as `found nothing` and listed under `no_hit` in its
  stage of the result file. It is no difference, but a clean run with such a
  line compared less than the matrix names.
- **Where commands run.** From `<slot>/cwd` with `--path <root>`, except the
  group at the end of `pristine`, which runs from inside the wiki.
- **Left out.** The matrix has no `--help` call and no usage error. Their layout
  belongs to the version of the argument parser, not to the engine.
- **Refusals.** Three cases are built to fail: an invalid search query, an
  invalid match pattern and a page that does not exist. A sampled folder or page
  the wiki refuses on both sides (a name its policy rejects, a folder its
  settings exclude) compares only the refusal, so it is an `unexpected`
  difference and the run does not pass; another `--seed` draws another sample. A
  sample may name folders as refused: such a folder holds no index, and its name
  is one the wiki takes no folder below. Where the first sampled folder is one
  of them, `read` of it and `new` below it are built to fail too, and that `new`
  may write nothing. Only the first sampled folder counts: `read` and `new` name
  no other, so a folder named as refused later in the sample changes no case.
  Such a run compares less than the matrix names: it has no `read` of a folder
  that holds an index and no `new` that writes below a sampled folder, and its
  scoped `map`, `match` and `search` run on a folder with no index. The sample
  in the result file names the folder under `refused`. The draw names none; a
  perturbation names the folders it adds that way (Perturbation, Names).

## Fixture Families

A fixture family is a checkout the harness writes from nothing: a small wiki
that holds one kind of input the corpora lack. `python -m parity fixtures`
writes the families and runs each as a corpus. The generator is
`parity/fixtures.py`.

- **Base.** Every family starts from one converged wiki, as the Python engine
  leaves one: three folders with their indexes and pages, of which one is named
  for the family and holds an index and one page.
- **Own files.** The family's own files lie in its folder, as a person adds
  them: with no row in any index, and most with no name and no stamp. So the
  read-only commands report on input that no engine wrote, and `update` has to
  take it in.
- **Depth.** The matrix sample draws folders of the first two levels for its
  scoped commands, so each of those is a folder a command can be scoped to. A
  folder the wiki excludes or refuses lies at the third level or below.
- **Same on every host.** A family has the same bytes on every host, and every
  path has one fixed modification time. Files and folders are made under one
  fixed umask, so their modes are the same too; a symlink has the mode its
  platform gives one. The tree is committed under a fixed identity and date, so
  the commit is the same wherever git stores the names as they are written. On
  macOS git composes a decomposed name, so the commit of `names` there is
  another than on Linux. A family may be a plain folder with no repository.
- **File system.** A family may need a file system that keeps two names which
  differ only in case, or a name that is no valid UTF-8. The generator asks the
  file system of the work folder. With no `--family`, a family it cannot hold is
  left out: the run prints its name, and every result file lists it under
  `left_out`. A named family that cannot be written ends the run with exit 2,
  and so does any family that cannot be written under `--every`.
- **Stages.** A family runs the stages of the matrix (Matrix), then stages of
  its own, whose commands name its pages. The stage `fixture` starts from the
  snapshot: `lint --json`, `update --check --full`, a `read` of each page the
  family names, `update --full`, `update --check`, `lint --json`, `lint --full`,
  `map`, the same pages again, and a `match --lines` and a `search --json` for a
  word that the family's own pages hold.
- **Alone.** The matrix is built for a wiki that `update` takes: a refusal there
  is an `unexpected` difference. A family whose wiki `update` refuses runs its
  own stage alone, and each of its cases names the exit codes it allows.
- **Sides.** With no `--engine` both sides are v1, under `PYTHONHASHSEED=1` and
  `PYTHONHASHSEED=2`, and a command the registry excuses fails the run, as in
  the self-check. With one, the runs are v1, the engine, and the engine with
  `WIKI_THREADS=1`, and what the registry excuses passes.

| Family           | Holds                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `frontmatter`    | A page for each way a block fails to be a mapping of fields: empty, a sequence, a scalar, a key that is no scalar, a key twice, a key that is an alias, a key twice in a nested mapping, collections nested 101 deep, and one page for each error of the YAML reader (a colon in a plain value, a key with no space, a reserved first character, a tab line, an open flow sequence, a value with no key, an anchor twice, an alias to nothing, a control character, an escape past the last code point, a lone surrogate escape, a byte order mark on a later line, a second document, an open quote). Well-formed blocks that only the line readers and writers meet: folded and literal scalars, quoted keys, comments with a document end, `title: null`, a tag and an anchor. Files whose trouble lies around the block: no closing fence, a byte order mark first, an indented fence, CR LF line ends, no block, and a rule that reads as a fence. Three indexes: one with a block no reader takes, one with no bytes, one with no delimiter. |
| `names`          | Names that are not ASCII, in both normal forms, and names the naming policy rejects. A page beside a folder of its name. Symlinks to a page, to a folder, to nothing and out of the wiki. A page `X.md` beside a file, a symlink and a gitignored file named `X`, and `readme.md` beside `Readme`. Links that spell a page as its file is, and in another case. Whether a sibling or such a link is found turns on the file system, so the family's results differ between hosts, and the sides must agree on each.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `nested`         | Two wikis declared inside the wiki: one with a root index and a page, one with a page and no index. It runs alone. v1 reports each inner root, serves its pages, and exits 2 on `update`, on `update --check` and on a command run from inside an inner wiki.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `stamps-zone`    | A timestamp policy with a named zone and a format of literal text, every supported directive but `%%`, and `%z`. Pages with stamps in that form, in the default form, in no form, on one field only and in quotes, and a page with no block.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `stamps-literal` | The same pages under a format with literal text and `%%`, and no zone.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `control`        | A page whose file name and body hold a color sequence, a title sequence that a bell ends, a unit separator and a delete; a page whose desc holds the same as YAML escapes; and an index whose rows hold a unit separator before a row, behind its colon, inside its desc and at its end. Its own stage runs twice: `fixture` under the built environment, and `fixture forced` with `NO_COLOR=1`, `CLICOLOR_FORCE=1` and `TERM=dumb` on every side.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `plain`          | A wiki outside any repository, with 5,001 files in a folder an exclude pattern names. An engine lists them all for git, which ends outside a repository before it has read the list.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `linux`          | Two pages and two folders that differ in case only, with links in each case and in a third. Three names that are no valid UTF-8, each where no engine reads it as a name: in a folder an exclude pattern names, in a gitignored folder, and as a gitignored file in a managed folder. It needs a file system that keeps case and bytes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |

### Refused Trees

The generator also writes four trees that are no differential fixtures: each
holds one name that is no valid UTF-8 where v1 reads it as text, and a second
engine refuses the tree (registry entry D4). Only the tests write them: no run
compares on them, and `fixtures` exits 2 when `--family` names one. They need a
file system that keeps bytes. The tests in `parity/tests/test_fixtures.py` hold
v1 to this table on Linux. What the entry says of v1 holds on the first two
trees; on the other two v1 runs, and the refusal is the second engine's own.

| Tree              | The name                                    | v1                                                                                                                                                                                                                                                                                                                                                           |
| ----------------- | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `refused-page`    | a page of a managed folder                  | `update` exits 0: it gives the page no row, and writes the page's `name` with the byte spelled as the escape `\udcXX`. `lint` then reports that block as invalid YAML, in a JSON document that holds the byte itself and so is no valid UTF-8. `search` exits 2 with the text of a codec error.                                                              |
| `refused-folder`  | a managed folder                            | The same for the page inside it. `update` exits 2 with the codec error where it would write the folder's index, with the pages before it already written.                                                                                                                                                                                                    |
| `refused-pattern` | a page that only an exclude pattern removes | The pattern is matched against the name as text, and every command runs as on a wiki without the page.                                                                                                                                                                                                                                                       |
| `refused-file`    | a file of a managed folder that is no page  | `update` exits 0: it names the skip, gives the file no row, and changes no file, because a file that is no page gets no `name`. A second `update --check` finds nothing to do, and `search` runs. `lint` exits 1 on every run with one issue, the invalid name, and no invalid YAML, in a JSON document that holds the byte itself and so is no valid UTF-8. |

## Perturbation

The corpora and the example wiki are converged: `update` writes almost nothing
there and `lint` reports almost nothing. A perturbation changes a tree by a
seed, the way hands, tools and merges change a wiki, so that the read-only
commands have something to report and `update` something to take in. The fixture
families are not converged: most hold malformed pages by design, and show kinds
with no perturbation at all (Kinds, Limits). `python -m parity perturb` perturbs
trees and runs each as a corpus. The operators are in `parity/perturb.py`. A
perturbation runs no engine: it edits files, and what the engines make of the
edits is what the run compares.

- **Seed.** One seed, one tree and one path for the wiki to stand at give one
  perturbation, byte for byte. Each operator draws from a generator of its own,
  seeded with the seed and the operator's name, so a new operator moves no
  other's draws. The seed is also the seed of the matrix sample.
- **Every operator, every tree.** Each operator applies once or twice on every
  tree; the seed draws how often and where. The one operator that needs a
  repository is left out on a tree without one.
- **What an operator draws.** A page, an index or a folder that the engine
  manages, as far as the files say: a page with a closed block that carries a
  `name`, an index with such a block, a delimiter and a row, and a folder whose
  index has a closed block. Each has its row in the index of its folder, and
  that folder its row in the index above, up to the root: a converged wiki lists
  what it manages, and a file that its settings exclude or that git ignores has
  no row. The operators that rewrite a block, a body under a kept stamp or a row
  each take a file that no other of them has taken. Those that add a link to a
  body or a row to an index draw among all, and their changes add up. A tree
  that runs short gets new pages and folders, in the form the engine writes
  under its default settings. Dot folders, symlinks, a wiki declared inside the
  wiki and names that open with a dash are never drawn, and neither is a folder
  whose index is a symlink, with its pages. A file is read and written with its
  line ends and its bytes as they are.
- **Writes.** Every file a perturbation writes goes through one function, which
  refuses a path that leads out of the checkout (Snapshots, Links out of a
  clone). The wiki root is checked the same way, before a folder is renamed or
  an index deleted. A wiki whose settings file, settings folder or top
  `.gitignore` is a symlink out of its checkout therefore cannot be perturbed:
  the tree ends in an error under every seed.
- **Names.** A name the perturbation makes is `perturbed` and a number, which
  the default naming policy and one that asks for identifiers both take. The two
  invalid names end in `[`, which is link grammar and so denied by every policy.
  v1 reads a page under such a name like any other page. The folder comes with a
  page and no index: `lint`, `update --check`, `map`, `search` and `match` run
  when they are scoped to it, and `read` of it finds no entry and exits 2.
  `update` gives the folder an index and its page a block, and gives the folder
  no row in the index above. `new` refuses a folder below such a name with exit
  2, before `update` and after it. A perturbation lists these folders, and a
  test holds v1 to the two refusals.
- **Order.** A folder is renamed first, before anything else is drawn. An index
  is cut short next, and no later operator adds to its folder: an index that was
  cut takes no row, so a file added beside it would show less than its operator
  names.
- **Times.** v1 reads a page that git holds with its `updated` stamp, and whose
  text differs from the committed one, as edited since the stamp, and restamps
  it. It makes one exception: a page last written in the period its stamp names
  under the wiki's timestamp policy (a second, or a day under a date format), or
  within 60 seconds after the instant the stamp names, was written by the run
  that stamped it. A perturbation sets the modification time of every file it
  writes. The default is one of two fixed times, 2001-09-09 and 2033-05-18,
  which the seed draws: both lie far from any stamp a wiki holds, so v1 restamps
  at either. `dirty-edit` also leaves a page at a time its stamp covers: the
  instant the stamp names under the wiki's own format and zone, plus 0 to 58
  seconds, which the seed draws. A time an operator gives a file stays with the
  file, whatever a later operator adds to it.
- **Three trees.** The operators without a stage leave the tree `edited`. Two
  more trees come from it, each by one staged operator: `unindexed`, where every
  index is deleted and `update` builds each again, and `conflicted`, where a
  page and an index end in merge conflict markers and `update` refuses. A stage
  of the perturbation makes its change on every tree it runs on, before the
  first command, and every file of such a tree is hashed at the first scan.
- **Corpus mode.** `perturb corpus` snapshots a corpus, perturbs the snapshot
  and runs the stages of the matrix (Matrix) on the edited tree, then the stages
  `unindexed` and `conflicted`. The matrix's search by tag names the
  perturbation's tag, where its own sample could have drawn a tag the wiki held
  before. A folder under an invalid name that the sample draws is named as
  refused (Matrix, Refusals); where it is the first sampled folder, the scoped
  search takes the word every page of the perturbation's own holds, since the
  sample draws no word from a page without a `name`. It runs once for each
  corpus, under a fresh seed that the result file records, or under the seed
  `--seed` names.
- **Loop mode.** `perturb loop` runs many seeds on small trees, each seed on
  each tree, several at once. In place of the matrix it runs the short stage
  `edited`, then `unindexed` and `conflicted`. With no `--corpora` the trees are
  the repository's example wiki, `hello`, as a committed checkout of the files
  git tracks there, and the fixture families `update` takes: a family that runs
  alone is no tree of the loop, and no stage of a family's own runs, since it
  names pages the perturbation may have moved. With `--corpora` each tree is the
  wiki of a corpus, copied into a small repository of its own: the wiki at the
  path it has in its checkout and the `.gitignore` from the top of that
  checkout, in one commit. A loop snapshots its tree once for every seed, and a
  wiki may lie in a checkout many times its size. The copy leaves behind the
  history, the checkout's other folders and whatever git holds of the wiki's own
  files, so a loop on a corpus proves less about that corpus than corpus mode
  does.
- **Seeds.** `parity/seeds.toml` is the committed list: the seeds that every
  pull request runs on the small trees. A list is refused when it holds a key
  other than `seeds`, or when `seeds` is not a list of whole numbers from zero
  up, each once, with at least one. A fresh seed is a whole number below 2^32,
  and a run prints its fresh seeds before it runs a tree, so a run that ends in
  an error can be run again.
- **Sides.** With no `--engine` both sides are v1, under `PYTHONHASHSEED=1` and
  `PYTHONHASHSEED=2`, and a command the registry excuses fails the run. With
  one, the runs are v1, the engine, and the engine with `WIKI_THREADS=1`, and
  what the registry excuses passes.

| Stage        | Tree                                         | Commands                                                                                                                                                                                                                                                                                    |
| ------------ | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `edited`     | the perturbed snapshot, in a loop            | `lint --json`, `lint`, `update --check --full`, `map`, a `search --json` and a `match --lines` for the word the perturbation's own text holds, a `search --tag` for a word and the tag of a page it tagged, a `read` of a page it edited, `update --full`, `update --check`, `lint --json`. |
| `unindexed`  | the perturbed snapshot without any index     | `lint --json`, `update --check --full`, `update --full`, `update --check`, `lint --json`, `map`.                                                                                                                                                                                            |
| `conflicted` | the perturbed snapshot with conflict markers | `lint --json`, `update --check` and `update`, which both must exit 2, `map`.                                                                                                                                                                                                                |

| Operator            | Change                                                                                                                                                                                                                                                                                               | Notice kinds it names    | Issue kinds it names     |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------ | ------------------------ |
| `rename-folder`     | renames an indexed folder below the root, with all it holds                                                                                                                                                                                                                                          | `link_add`               | `requires_update`        |
| `truncate-index`    | cuts an index short, inside its block or before it                                                                                                                                                                                                                                                   | `index_truncated`        | `truncated_index`        |
| `dirty-edit`        | adds a paragraph to two pages and leaves their stamps: pages that git holds with those stamps, where they are left. One gets a time far from its stamp and names the kind. The other gets a time its stamp covers and names none: v1 must leave its stamp. The seed draws which takes its page first | `restamp`                |                          |
| `strip-frontmatter` | takes the block off a page                                                                                                                                                                                                                                                                           | `page_adopt`             | `bare_page`              |
| `blank-stamps`      | leaves both stamps of a page with no value                                                                                                                                                                                                                                                           |                          | `requires_update`        |
| `shuffle-keys`      | puts the fields of a page's block in another order                                                                                                                                                                                                                                                   |                          | `requires_update`        |
| `add-tags`          | gives a page two tags, one of them the perturbation's own, so a search by tag finds a page                                                                                                                                                                                                           |                          |                          |
| `missing-desc`      | puts the placeholder `...` in place of a page's desc                                                                                                                                                                                                                                                 | `desc_missing`           |                          |
| `long-desc`         | gives a page a desc of more than 500 characters                                                                                                                                                                                                                                                      | `desc_long`              |                          |
| `bare-file`         | adds a page with no block                                                                                                                                                                                                                                                                            | `page_adopt`, `link_add` | `bare_page`              |
| `crlf-file`         | adds a page whose lines end in CR LF                                                                                                                                                                                                                                                                 | `crlf_notice`            |                          |
| `other-file`        | adds a file that is no page                                                                                                                                                                                                                                                                          | `link_add`               |                          |
| `stale-link`        | adds a link to a page that does not exist to a body                                                                                                                                                                                                                                                  | `link_stale`             |                          |
| `directory-link`    | adds a link that names a folder, not its index, to a body                                                                                                                                                                                                                                            |                          | `directory_link`         |
| `absolute-link`     | adds a link to a page by its absolute path, as the wiki stands when the commands run, to a body                                                                                                                                                                                                      |                          | `absolute_link`          |
| `outside-link`      | adds a link that leaves the wiki to a body                                                                                                                                                                                                                                                           |                          | `outside_link`           |
| `region-marker`     | adds the end of a region that never started to a body                                                                                                                                                                                                                                                |                          | `region_marker`          |
| `invalid-page`      | adds a page whose name ends in `[`                                                                                                                                                                                                                                                                   | `name_skip`              | `invalid_page_name`      |
| `invalid-folder`    | adds a folder whose name ends in `[`, with a page inside                                                                                                                                                                                                                                             | `name_skip`              | `invalid_folder_name`    |
| `drift-row`         | changes the desc of a row of an index: a row whose page has a desc of its own, which the engine writes over the row's                                                                                                                                                                                | `desc_overwrite`         | `requires_update`        |
| `drop-row`          | takes a row out of an index: a row whose entry stands, so the engine adds it again                                                                                                                                                                                                                   | `link_add`               | `requires_update`        |
| `broken-row`        | adds a row for a page that does not exist to an index                                                                                                                                                                                                                                                | `link_prune`             | `broken_link`            |
| `symlink-row`       | adds a symlink to a page, and a row for it                                                                                                                                                                                                                                                           | `symlink_skip`           | `symlink_link_target`    |
| `excluded-row`      | adds a page, a row for it, and an exclude pattern that names the page to the wiki's settings, which it makes if absent                                                                                                                                                                               | `exclude_skip`           | `excluded_link_target`   |
| `gitignored-row`    | adds a page, a row for it, and a pattern that names the page to the `.gitignore` at the top of the checkout; needs a repository                                                                                                                                                                      | `gitignore_skip`         | `gitignored_link_target` |
| `delete-indexes`    | deletes every index; staged, on `unindexed`                                                                                                                                                                                                                                                          | `index_create`           | `missing_index`          |
| `conflict-markers`  | ends a page and an index in the lines a merge leaves around a conflict; staged, on `conflicted`                                                                                                                                                                                                      |                          | `conflict_markers`       |

### Kinds

A perturbation is held to what its operators name: a change that does not show
its kind compares less than the table says.

- **Tap.** A command line prints the text of a notice and not its kind. So the
  tap of the reference side (`parity/tap.py`) runs `lint` and then `update` on
  the wiki inside v1's interpreter, as the command line runs them, and prints
  the kind of each issue and of each notice they met, the path of each file
  `update` restamped as edited since its stamp, and the text of the error if
  `update` refused. It runs once on each of the three trees: a fresh clone of
  the snapshot at the path every side runs at, changed as the tree's stage
  changes it. The tap writes to that clone, and no run is compared on it.
- **Named kinds.** Each change names the kinds it put into its tree, where the
  table says it does. A named kind that the tap does not meet on that tree is a
  `kind` finding, and the run fails: the operator did not do what it names. A
  kind is named only where the change by itself shows it: `lint` does not judge
  the stamp of a hand-edited page, so `dirty-edit` names no issue.
- **Covered stamps.** A page that `dirty-edit` left at a time its stamp covers,
  and that git holds with that stamp, must not be among the files the tap's
  `update` restamps. One that is restamped is a `stamp` finding: the harness and
  v1 read the stamp, the format or the zone differently.
- **Tag.** `add-tags` names no kind, so the search holds it. Every run searches
  by the perturbation's tag, for a body word of a page it tagged: a loop in the
  stage `edited`, a corpus in the matrix. A search by that tag that finds no
  page on any side is a `tag` finding. Any other search that finds nothing is
  printed and fails nothing, as in `matrix`.
- **One home.** The kinds the operators name are exactly the kinds whose home in
  `parity/kinds.toml` is `perturbation`. A run exits 2 on a list that gives a
  kind that home and no operator names it, and on an operator that names a kind
  whose home is a test.
- **Whole run.** A run that names no tree (no `--corpus`, no `--family`) also
  holds the kind list to what its trees showed: a kind whose home is the
  perturbation and that no tree of the run shows is a `kind` finding.
- **Limits.** A wiki whose settings make a stale link an issue shows
  `link_stale` as an issue, and the notice the operator names is then a finding.
  No corpus and no small tree has such settings. A kind that a tree shows before
  it is perturbed is not held to the operator there: the tap meets it whatever
  the operator did. Most fixture families show such kinds (`control`,
  `frontmatter`, `names` and the two `stamps` families among them); the example
  wiki and the corpora show none. A kind that several operators cause is not
  held to each of them by a run either: `restamp` shows wherever an operator
  changes a file git holds and leaves its stamp. A test applies each operator
  alone on a converged wiki and holds it to the kinds it names. A stamp in
  quotes, or one that runs over several lines, is not read: `dirty-edit` then
  leaves both pages far from their stamps.

## Merge History

A wiki is merged by git, and the engine is part of the merge: git hands each
index that both sides changed to the engine's merge driver, and the tree the
merge leaves is one the engine must then take. The trees real merges leave were
written by every older version of the engine, so no generated tree is as far
from converged, in as many ways. `python -m parity merges` replays every merge
commit of a corpus's history on every side and compares the sides on each. The
replay is `parity/merges.py`.

- **History.** The corpora of one checkout share one history. A run groups the
  corpora it names by checkout and replays each repository once, with the wiki
  roots those corpora declare there. The label of a history is the labels of its
  corpora, sorted and joined by `+`.
- **Snapshot.** Only the repository of the checkout is cloned, its `.git`
  folder: a replay checks out trees of the history, and nothing of the tree the
  checkout stands at today.
- **Merges.** Every merge commit that the snapshot's `HEAD` reaches, oldest
  first. A merge on a branch that `HEAD` does not hold is not replayed.
- **Repository.** A merge is replayed in a repository of the harness's own: a
  new one, with the snapshot's object store as its alternate. It holds nothing
  else of the checkout: no hook, no filter, no local config and no linked
  worktree, each of which a copy of the checkout's own repository would run or
  read. Git packs nothing there by itself. A file the checkout keeps through a
  filter stands in the tree as git stores it.
- **Path.** Each merge runs at a path of its own, `<at>/<commit>`, a slot with
  the three folders of Snapshots. A worker moves its repository there for the
  merge and back when the merge ends. `--at DIR` names the folder, which must
  not exist; the default is `<work>/merges/at`. So the paths a side prints are
  those of every other side, and of every other run that names the same folder,
  however many workers it has. The folder is empty when a run ends, and the run
  removes it.
- **Pass.** One side's pass over one merge has four steps. The first parent is
  checked out, detached, and every file git does not hold there is removed, the
  ignored ones too. The side's merge driver is registered in the repository:
  `merge.wiki.driver` is the side's own command line with
  `_merge %O %A %B %L %P`. Git then merges the other parents under the side's
  environment (`git merge --no-ff --no-edit`): it commits a merge that ends
  clean, at the run's instant and under a fixed identity, and leaves one with
  conflicts in progress, as a merge in a working copy does. Last, each wiki of
  the history that the merged tree declares runs three commands from the neutral
  folder: `update --check --full`, `update --full` and `update --check`.
- **The merge.** The merge is a stage of one case, `merge`. Git's stdout, its
  stderr, which holds what the driver printed there with every notice line, and
  its exit code are compared as they are. The exit code may be 0 or 1. The tree
  and the touched set are compared as after any command. The index entries the
  merge left unmerged (`git ls-files -u`: mode, blob, stage and path) must be
  the same, and a path where they are not is an `unmerged` difference. Every
  call of the driver leaves the side's identity record, and any other record is
  an `identity` difference; the result records how often git called the driver.
- **The wikis.** Each wiki is a stage named for its root, and its three commands
  are compared by the rules of Comparison and Caches. A merged tree may hold
  anything, so each command may exit 2 as well: `update` refuses a tree that
  holds conflict markers. A wiki that only one side's merged tree declares is a
  `tree` difference at its settings file, before the first command. The
  classifiers are asked about a wiki's commands and never about a merge.
- **Scans.** The whole slot is scanned before the merge, after it and after the
  last command. After a wiki's command only that wiki, the home and the neutral
  folder are scanned, and a path a command changed anywhere else shows at
  `(end)`, as a `tree` difference of the run that changed it. A worker keeps its
  manifest from one pass to the next: a file keeps its digest while its inode,
  size, mode and both times are what the last scan saw. The caches of the other
  wikis are no part of a stage's tree: each side wrote them at its own moment,
  and the stage of their own wiki compares them by the cache rule.
- **What a merge was.** A result records for each merge, as the reference side
  ran it: how often git called the driver, how many index entries stayed
  unmerged, the wikis `update` refused, and the wikis whose second
  `update --check` still found something to do. None of these fails a run: they
  are what v1 makes of that tree, and the other side must make the same of it.
- **Sides.** With no `--engine` both sides are v1, under `PYTHONHASHSEED=1` and
  `PYTHONHASHSEED=2`, and a command the registry excuses fails the run. With
  one, the runs are v1, the engine, and the engine with `WIKI_THREADS=1`, and
  what the registry excuses passes.
- **Workers.** `--jobs N` replays that many merges at once, each worker a
  process with one repository. A worker takes up to eight merges at a time,
  neighbors in the history, since the tree of a merge is close to the tree of
  the merge before it; the workers then end together, though the merges of a
  history differ widely in what they cost. Fewer merges than eight for each
  worker are dealt out evenly, so every worker has some.
- **Sample.** `--sample N` replays the fixed sample: the merges whose commit,
  read as a number, is a multiple of `N`. A merge is in the sample of every run
  or of none, however far the history has grown.

### History Cache

v1 does the same on a merge every time, so what it did is kept. `--cache DIR`
names the folder, and an entry holds the reference side's pass over one merge:
the streams, the exit code, the touched set, the changed manifest content and
the cache view of every command, the unmerged entries, the count of driver
calls, a digest of the tree the merge started from, and the merge's part of the
result file.

- **Key.** An entry is keyed on what its pass depends on. Of v1: a digest of the
  engine files of the oracle commit, which are the mode, blob and path of every
  file under `wiki/` outside `wiki/skills`, with the file that states the
  version counted without that line; a digest of the reference side's
  constraints file; and the version of its interpreter. Of the host:
  `git --version` and the platform. Of the run: the merge commit, the instant,
  the path the merge ran at, and the wiki roots of the history. Of the harness:
  a digest of the files that make a pass and an entry, which `parity/merges.py`
  lists: the replay itself, the run and the launcher of a side, the git calls,
  the manifest, the cache view, the folders of a slot, and the classes an entry
  holds. So a merge commit on the branch v1 lives on, a change to its docs or a
  release that only moves the version keeps every entry; a change to an engine
  file, to a package pin or to one of those harness files leaves none, and the
  next run replays every merge on v1.
- **v1 alone.** A run with no `--engine` does not replay a merge the cache
  holds: its row comes from the entry and says `cached`. A merge it replays is
  stored when the two sides show no difference and no excused command. Only this
  run stores, because it alone holds v1 against itself.
- **With an engine.** The reference side's pass comes from the cache where the
  cache holds it, and only the engine runs. The cached pass stands on the tree
  the engine's pass started the merge from, and each later stage on what the
  entry's own commands made of it. Where the digest of that first tree is not
  the entry's, the merge shows a `tree` difference at `(baseline)` with no path:
  git checked out another tree than when the entry was made. A merge the cache
  does not hold runs v1 beside the engine, and nothing is stored.
- **New merges.** `--new` replays only the merges the cache does not hold. With
  an engine that is each merge v1 has not run yet, against v1 as it runs there.
- **Same folder.** The path a merge ran at is part of its key, so a run finds
  the entries of another only where both replay their merges in the same folder.
  Two runs with the same `--work` do; runs with work folders of their own, as
  the layers of a gate have, name one folder with `--at`.

Limits of the replay:

- **Times.** Git writes the files of a checkout and of a merge at the moment of
  each pass, and v1 reads a page's modification time against its stamp (see
  Perturbation, Times). A page that a merge leaves changed and uncommitted, with
  a stamp that names the minute or, under a date format, the day of the pass,
  can be restamped on one side and left on the other. The sides then differ on
  that merge, and a second run shows whether the time was the cause.
- **The tree a merge left in history.** A replay reads the parents of a merge
  commit, never its tree: the tree is what git and the side's driver make of the
  parents today, not what the people who merged resolved by hand.
- **The driver's line in git's config.** The harness registers the side's
  command line. That the line an engine's own `init` registers finds the engine
  is a test's to hold.

## Interpreters Run

v1 has no single answer where an answer turns on its interpreter: the Unicode
tables behind its name rules, whitespace, `\w` and NFC, and the build of the
YAML loader. The oracle's interpreter defines parity, and v1 supports others,
with other tables. A wiki that moves from one of those to an engine that follows
the oracle is rewritten wherever the two differ. So
`python -m parity interpreters` compares the alt side, v1 under a second
interpreter, with the oracle on real wikis, and a corpus that fails goes to a
person before an engine is built on the oracle's tables.

- **Sides.** The reference is `oracle` and the compared side `alt`. The run
  takes no other, and no `SIDES` file.
- **Matrix.** Both sides run the whole matrix on a snapshot of the corpus, as it
  stands: the run asks what each interpreter makes of the wiki's real names and
  text, and a perturbation would add only generated ones. Every rule of
  Comparison and Caches holds, stderr included. Two builds of v1 have nothing
  registered between them, so a command the registry excuses fails the corpus
  too.
- **Second run.** Where the two sides differ, the stages that show it run once
  more on the same snapshot, on all three sides. `crossed` shares its
  interpreter with `alt` and its packages with `oracle`. Each of its runs is
  compared, by the rules of Comparison and with no registry entry asked, with
  the reference and with the `alt` run it stands for.
- **Cause.** A difference between `alt` and `oracle` gets its cause from what
  `crossed` does at the same stage, case, aspect and detail. The cause is
  `packages` where `crossed` does not differ from `oracle`, `interpreter` where
  it differs from `oracle` and not from `alt`, and `both` where it differs from
  each: a stream that `crossed` prints a third way is laid to both. An excused
  command is given a cause the same way, with every aspect of its command
  counted. A rule that a run breaks by itself (`wrote`, `unexpected`, `notice`,
  `identity`, a tree at `(end)`) is no difference between the sides: it starts
  no second run and has no cause.
- **Crossed alone.** Whatever the second run shows of a `crossed` run that the
  first run does not show of the `alt` run it stands for is listed apart: a
  difference from the oracle, a rule the run broke by itself, or an excused
  command. It is printed below the corpus's differences and recorded as
  `rerun.alone`.
- **Runs that stand for none.** A stage that continues another runs each side on
  the tree each side left. In the second run it therefore holds `crossed` on the
  tree of `alt` and `alt` on the tree of `crossed`, which stand for no run of
  the first. The result file records them, and no cause reads them.
- **Crossing.** A run exits 2 on an oracle file whose `crossed` side names
  another interpreter or other Unicode tables than `alt`, or another constraints
  file or libyaml than `oracle`: such a side tells the two causes apart no more.

### Names

A difference in the tables that no command of the matrix reads today on the
wiki's names is still one a later command may show. So the run also holds the
names against the tables themselves.

- **Tables.** Before the first corpus, the `points` command of each of the two
  sides (`parity/points.py`) prints what its interpreter makes of every code
  point: the properties in the table below. A code point with a property the two
  interpreters do not agree on is a changed one. The result file records how
  many there are, and for how many each property differs.
- **Scan.** The run reads the name of the wiki root of the snapshot, which names
  the wiki where its index names none, and the name of every file and folder
  below it, whatever the wiki excludes: an engine reads a name before it can
  exclude it. It follows no symlink and reads nothing below a `.git` folder. A
  name that holds a changed code point fails the corpus, and the result file
  lists its path with each such code point and the properties that differ for
  it; the path of the root is `.`.

| Read through  | Properties                                                                                                                                                                                                                     |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `unicodedata` | `category`, `bidirectional`, `combining`, `east_asian_width`, `mirrored`, `decimal`, `digit`, `numeric`, and the normal forms NFC, NFD, NFKC and NFKD                                                                          |
| `str`         | `lower`, `upper`, `title`, `casefold`; `isalpha`, `isalnum`, `isdecimal`, `isdigit`, `isnumeric`, `isspace`, `islower`, `isupper`, `istitle`, `isprintable`; `isidentifier`, as the start and as a later part of an identifier |
| `re`          | the classes `\w`, `\s` and `\d` of a `str` pattern                                                                                                                                                                             |

`unicodedata.decomposition` is left out: the normal forms hold the mapping it
prints, and what it answers for a Hangul syllable turns on the interpreter's
version, not on its tables. The scan reads names, not text: the matrix runs
`lint`, `update` and the cache builds over every page of the wiki, and a
property of a code point in a page's text reaches a file only through those.

## Registry

`parity/differences.toml` is the registry of differences: the one list of what a
second engine may do differently from v1. A comparison excuses a difference only
when an entry explains it, and every other difference fails the run.

```toml
[D1]
scope = "<what differs, and what must still match>"
reason = "<why the difference is registered and not matched>"
status = "approved"
question = [7]
check = "classifier"
tests = []
```

- **Status.** `proposed` or `approved`. An approved entry cites in `question`
  the numbers of the questions whose Decision lines approve it. A proposed entry
  is excused like an approved one, and holds a release.
- **Check.** `classifier`: harness code recognizes the difference in a
  comparison (Classifiers, below). `test`: the tests that `tests` names pin it,
  and a difference of its kind anywhere else is unregistered. `none`: nothing
  checks it, and `unchecked` says why. An entry with a classifier names tests
  too where a test pins one of its clauses. `tests` is empty until the test
  exists.
- **Refusals.** Every run reads the file first, and exits 2 on an entry that is
  not a table, an entry with a missing field, a field under another name than
  those above, a field of the wrong shape (`question` is a list of numbers and
  `tests` a list of strings; every other field is a string), a status or a check
  outside those above, an approved entry that cites no question, or an unchecked
  entry that gives no reason. The entries with the check `classifier` must be
  exactly the entries the harness has a classifier for.
- **Second engine.** The registry lists what a second engine may do differently
  from v1. Two builds of v1 have nothing registered between them: the alt side
  against the oracle, or one release against the next. A `matrix` run therefore
  fails on a command the registry excuses, and lists it as `registered`, unless
  `--registered` says that side `b` is a second engine. The self-check and its
  doctored checks run one engine and fail the same way.
- **Release.** `python -m parity registry --release` also exits 1 while an entry
  is proposed, or names no test where tests check it. Without `--release` the
  command prints the same lines and they do not fail it.

### Classifiers

A classifier is the part of the harness that recognizes one registered
difference. It exists only for an entry whose difference can appear on input the
harness did not choose: D1, D2 and D3.

- **Reference.** The first run of a stage is the reference, and the classifiers
  take it for v1. Each later run is judged against it.
- **Whole commands.** A classifier reads one command as the two runs ran it: the
  arguments, both streams, both exit codes, what the later run touched, and the
  bytes of `<root>/.wiki/settings.json` as the reference's command found them.
  Every difference between the two runs on that command is excused together, or
  none is. The result lists the command once, under the entry's id, as
  `registered`.
- **Never excused.** A difference before the first command or after the last
  (`(baseline)`, `(end)`), and the rules a run breaks by itself: `wrote`,
  `notice` and `identity`. `unexpected` is excused in one case only: the exit
  codes differ, and the entry that explains the command covers that.
- **Judge.** Whether v1's own library raises on an input, and whether an input
  is outside the supported set, is decided by the reference side's interpreter
  and by nothing else. `parity/judge.py` runs there, under the variables the
  side's commands get, and answers one JSON request per line. A reference side
  with no judge leaves every such question unanswered, and the difference stays
  a difference.

| Entry | Excuses                                                             | Holds when                                                                                                                                                                                                                                                                                                       |
| ----- | ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D1    | `stderr` alone                                                      | After v1's `FutureWarning` lines are dropped, the two streams are equal, or they are equal up to the end of one wiki-written prefix and each then ends with one line of text. The lines dropped are those whose message the judge's `re` gives for a pattern of the command, each with the source line below it. |
| D2    | `stderr` alone, or `stdout` alone                                   | `stderr`: both sides exit 2 with an empty stdout, and v1's stderr is a usage error. `stdout`: the arguments hold `--help`, both sides exit 0, v1's stdout opens with `Usage: ` and the other side's is not empty.                                                                                                |
| D3    | every aspect that compares two runs, and the refusal's `unexpected` | v1 exits 0 or 1. The other side exits 2, touches nothing, prints nothing on stdout and one `Error: ` line on stderr. The judge calls an input of the command unsupported. A refusal of a settings field names the field.                                                                                         |

- **D1, prefixes.** A text may follow four prefixes. `Error: ` on a `match`
  command, where the judge's `re.compile` raises on the pattern and v1's text is
  what it raises with.
  `Error: naming.pattern in .wiki/settings.json is not a valid regex: `, held
  the same way for the pattern of the settings file.
  `Error: Malformed JSON in .wiki/settings.json: `, where the judge's
  `json.loads` raises on the file's text.
  `Error: Invalid FTS5 query <the query's repr>: `, which needs no judge: the
  text behind it is SQLite's.
- **D1, one line.** A text runs from the prefix to the end of its line, and that
  line ends the stream on both sides. A line behind it is not a library's text,
  so the command is reported. That holds for v1's side too: a text that carries
  a line break of the input (`re`'s for a pattern such as `[z-<newline>]`,
  SQLite's for a query that names a column across a line break) is reported and
  not excused.
- **D1, patterns.** The pattern of a `match` command is its first positional
  argument, compiled with the flag `i` under `--ignore-case`. The pattern of the
  settings file is its `naming.pattern`.
- **D2, usage errors.** v1 prints `Usage:`, a `Try '... --help' for help.` line,
  an empty line and `Error: <message>`. A message that opens with
  `Invalid value: ` was raised by wiki's own code: the other side's stderr,
  without its empty lines and the lines that open with `Usage:`, `Try '` or
  `For more information`, must be `Error: <message>` and nothing else. Any other
  message is the parser's own, and the other side's stderr only has to hold
  something.
- **D2, help.** The classifier excuses the layout of a help page. That its
  commands, options, defaults and sentences are v1's is a test's to check.
- **D3, inputs.** The settings file's text, its `naming.pattern`,
  `timestamp.format` and `timestamp.timezone`, and the pattern of a `match`
  command. A value of the JSON text that only Python's reader takes must be
  refused under the `Error: Malformed JSON in .wiki/settings.json: ` prefix.
- **D3, the refusal's line.** v1 reads the three settings fields only in the
  commands that use them, so a tree whose field is outside the set would
  otherwise excuse any one-line error of any command. A refusal of a field must
  hold the field's name as v1's own settings errors spell it (`naming.pattern`,
  `timestamp.format`, `timestamp.timezone`), and the field must be one the judge
  calls unsupported. The line that refuses the pattern of a `match` command is
  not held to any words: the input is that command's own argument.

The supported set, as the judge decides it:

| Input              | Inside the set                                                                                                                                                                                                                                                                                        | Outside it                                                                                                                                |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| A regex            | Literals and escapes, `.`, character classes, the digit, word and space classes with their negations, `^`, `$`, `\A`, `\Z`, `\b`, `\B`, capturing, non-capturing and named groups, alternation, greedy and lazy quantifiers, lookahead, lookbehind, backreferences, and the flags `i`, `m`, `s`, `x`. | Possessive quantifiers, atomic groups, conditional groups, the flag `a`, a backreference compared without case, and any other parse node. |
| `timestamp.format` | `%Y`, `%m`, `%d`, `%H`, `%M`, `%S`, `%z`, `%%` and literal text.                                                                                                                                                                                                                                      | Any other `%` directive, and a `%` at the end.                                                                                            |
| A named zone       | A zone with a file on `zoneinfo.TZPATH`.                                                                                                                                                                                                                                                              | A zone with none, which v1 loads only where the `tzdata` package is installed.                                                            |
| The settings text  | Every value a strict JSON reader takes.                                                                                                                                                                                                                                                               | `NaN`, `Infinity`, `-Infinity`, a surrogate escape outside a pair, and arrays and objects nested 128 deep or more, counted from the text. |

- **Regex walk.** The judge walks the tree `re._parser.parse` returns. A flag
  counts where it is in force: an inline group that sets `i` puts the
  backreferences inside it outside the set, and one that clears it does the
  reverse. The flag `u` changes nothing in a text pattern and is inside the set.
- **Spellings the walk cannot see.** The judge decides from the parse tree, so a
  spelling the parser folds into a supported node is inside the set: a comment
  group `(?#...)`, which leaves no node; a named character `\N{...}`, which is a
  literal; and `\z`, which Python 3.14 reads as `\Z` and Python 3.13 refuses as
  a bad escape. A second engine that refuses one of them is reported.
- **Outside the classifiers.** Five clauses of the three entries have no
  classifier code, so a difference of their kind is reported. Two are a test's
  to hold: the text of a failed plugin download (D1), and typer's two completion
  options, which a second engine does not have (D2). The label of a `_merge`
  error line (D1) is never excused: the merge replay runs the driver, and no
  classifier is asked about a merge. A pattern past the compiled-size limits,
  and nesting past the depth limit (D3), are limits of the second engine's regex
  library, and the judge's walk does not hold them.
- **No entry.** One library text has no clause in any entry. `init --settings`
  with text that is not JSON ends in a parameter error of wiki's own code,
  `Invalid value: --settings must be valid JSON (<text>).`, whose `<text>` is
  what Python's `json` raises with. D2 holds a message that opens with
  `Invalid value: ` byte for byte, and D1 covers JSON text only behind the
  malformed-settings prefix, so another text there is reported.

## Self-Check

A result of the harness counts only after the self-check passes on the same
corpus. It runs one engine on both sides.

- **Clean.** The whole matrix, with `PYTHONHASHSEED=1` on one side and
  `PYTHONHASHSEED=2` on the other, must show no difference.
- **Doctored.** A doctored side is a wrapper that runs the real side and changes
  one thing in one command: the last command of one stage, so the change cannot
  reach another command's output. Each check runs that one stage and must report
  exactly the differences in the table, on that command, and no other.
- **Nothing registered.** One engine has nothing registered against itself. A
  clean or doctored check fails when a classifier excuses any command, whatever
  it reports.

| Check        | The doctored side                                                      | Stage   | Must show as                                |
| ------------ | ---------------------------------------------------------------------- | ------- | ------------------------------------------- |
| `stdout`     | changes one byte of stdout                                             | `full`  | `stdout`                                    |
| `stderr`     | adds one byte to stderr                                                | `full`  | `stderr`                                    |
| `exit`       | changes the exit code                                                  | `full`  | `exit`, `unexpected`                        |
| `file`       | changes one byte of `.wiki/settings.json` in place, old times put back | `full`  | `tree`                                      |
| `mode`       | changes the mode of `.wiki/settings.json`                              | `full`  | `tree`                                      |
| `extra`      | writes one extra file                                                  | `full`  | `tree`, `touched`                           |
| `rewrite`    | writes `.wiki/settings.json` again with the same bytes                 | `full`  | `touched`                                   |
| `stray`      | writes one extra file during a read-only command                       | `write` | `tree`, `touched`, `wrote`                  |
| `counts`     | adds one to a word count in place, old times put back                  | `write` | `cache` (`word_counts.json`)                |
| `index`      | adds a row to each table of `search.db`, old times put back            | `write` | `cache` (`search.db`: `files`, `notes_fts`) |
| `cache-mode` | changes the mode of `word_counts.json`                                 | `write` | `cache` (file list)                         |
| `schema`     | adds a table to `search.db`, old times put back                        | `write` | `cache` (`search.db`: `schema`)             |
| `notice`     | drops the first line of stderr                                         | `full`  | `notice`                                    |
| `identity`   | changes the build id in the identity record                            | `full`  | `identity`                                  |

The first six are one stdout byte, one stderr byte, one exit code, one file
byte, one mode and one extra written file. The others prove the rules those six
do not reach: the touched set by itself, the read-only rule, four parts of the
cache rule (a cache file's mode, one counts entry, an index row that one side
lacks and the index schema), the notice and the identity. The `exit` check also
proves the rule of allowed exit codes. The `notice` check is left out for a side
that prints no notice, and the `identity` check for a side that has no identity.
The other parts of the cache rule (the bytes of `.gitignore`, the form and the
bytes of `word_counts.json`, a recorded mtime that is not current, a recorded
size, the text of an index row, and `meta`), each half of the touched rule, a
deleted path, a symlink's target, an unreadable file, the `(baseline)` and
`(end)` cases, the runs after the second, and an identity record left twice or
not at all have no doctored check; the unit tests in `parity/tests` pin them.

### Registry Check

`python -m parity registry` proves the classifiers the way the doctored checks
prove the comparison rules. It runs one engine against a stand-in: a wrapper
(`parity/standin.py`) that runs the same engine and rewrites the streams and the
exit code of the commands it is told to, so it prints what a second engine may
print. A result against a second engine counts only after this check passes with
the same harness and the same oracle. The fixtures bring their own inputs and
arguments, so one corpus proves them.

- **Fixtures.** Each classifier has fixtures it must excuse, and at least one
  negative fixture: a difference just outside the entry's scope, which the
  harness must report as unregistered, with exactly the differences in the
  table. A fixture is one command.
- **Trees.** Each tree is one stage: a clone of the snapshot whose settings file
  holds what the snapshot's holds, with the one member the tree sets. Every
  other setting of the wiki stays, so each tree changes one input of one
  classifier. `snapshot` keeps the file as it is, and `malformed` holds its text
  without the last character.
- **Zones.** Both sides run with `PYTHONTZPATH` set to an empty folder and with
  a `tzdata` package on `PYTHONPATH` that holds the one zone `Parity/Zone`. v1
  then loads a zone that has no zone file, which is the case D3 registers.
- **Stand-in.** An edit whose expression matches nothing fails the stand-in: a
  stand-in that prints the real side's words would prove nothing.

| Fixture                     | Entry | Tree        | Command                        | The stand-in                             | Must show as                             |
| --------------------------- | ----- | ----------- | ------------------------------ | ---------------------------------------- | ---------------------------------------- |
| `pattern text`              | D1    | `snapshot`  | `match (`                      | prints another text after `Error: `      | registered                               |
| `pattern warning`           | D1    | `snapshot`  | `match` with a nested set      | drops the `FutureWarning` lines          | registered                               |
| `query text`                | D1    | `snapshot`  | `search '"' --raw`             | prints another text after the query      | registered                               |
| `settings text`             | D1    | `malformed` | `lint`                         | prints another text after the prefix     | registered                               |
| `changed prefix`            | D1    | `malformed` | `update --check`               | changes the wiki-written prefix          | `stderr`                                 |
| `naming text`               | D1    | `pattern`   | `lint`                         | prints another text after the prefix     | registered                               |
| `naming warning`            | D1    | `warned`    | `lint`                         | drops the `FutureWarning` lines          | registered                               |
| `parser message`            | D2    | `snapshot`  | `lint` with an unknown option  | prints the usage error in another layout | registered                               |
| `parameter message`         | D2    | `snapshot`  | `search --limit 0`             | keeps the message line in another layout | registered                               |
| `changed parameter message` | D2    | `snapshot`  | `map --depth -1`               | changes the message line                 | `stderr`                                 |
| `help page`                 | D2    | `snapshot`  | `lint --help`                  | prints another page                      | registered                               |
| `regex construct`           | D3    | `snapshot`  | `match` with a possessive `*+` | refuses                                  | registered                               |
| `supported construct`       | D3    | `snapshot`  | `match` with a greedy `+`      | refuses                                  | `stderr`, `exit`, `unexpected`           |
| `settings value`            | D3    | `value`     | `lint`                         | refuses under the malformed-JSON prefix  | registered                               |
| `timestamp directive`       | D3    | `directive` | `lint`                         | refuses in a line that names the field   | registered                               |
| `unnamed field`             | D3    | `directive` | `map`                          | refuses in a line that names no field    | `stdout`, `stderr`, `exit`, `unexpected` |
| `named zone`                | D3    | `zone`      | `lint`                         | refuses in a line that names the field   | registered                               |

| Tree        | Sets in the settings file                  |
| ----------- | ------------------------------------------ |
| `snapshot`  | nothing                                    |
| `malformed` | nothing: the text ends one character early |
| `pattern`   | a `naming.pattern` that does not compile   |
| `warned`    | a `naming.pattern` with a nested set       |
| `value`     | a member with the value `NaN`              |
| `directive` | a `timestamp.format` with `%j`             |
| `zone`      | the `timestamp.timezone` `Parity/Zone`     |

A refusal of the stand-in is an empty stdout, one `Error: ` line behind the
notice, and exit code 2. The command it refuses is one that writes nothing on
v1, so the stand-in leaves the tree as a refusal does.

## CLI Suite

v1's CLI tests drive the console script as a subprocess and assert on what it
prints, returns and writes. The CLI suite runs them against a second engine with
their own assertions as the verdict, and adds a short table of commands on a
terminal. It runs v1's tests where v1 runs them: from the oracle checkout, under
the `oracle` side's interpreter, with v1's own pytest settings. The checkout is
not edited. One thing changes: every call of `wiki` goes through the dispatcher.

### Dispatcher

The dispatcher is one executable named `wiki`, in a folder that holds nothing
else. It sends each call to a side and becomes that side's command, so the
caller's streams, signals and exit code are the side's own.

- **Routing.** A call belongs to its first argument: the name of a command, or a
  root option such as `--version`. A call whose first argument is a ported
  command goes to the second engine. Every other call goes to v1, a call with no
  argument included. With no second engine every call goes to v1.
- **Sides.** A call runs under the caller's environment with the side's own
  variables set over it. A side with no identity gets no `WIKI_TRACE`.
- **Notes.** Before it hands a call on, the dispatcher appends one line of JSON
  to the file `WIKI_TRACE` names: the side the call goes to, and the ported
  command if it is one. A call made with no `WIKI_TRACE` is noted in a file of
  the run's own.
- **Identity.** A trace file holds the notes of its calls and the identity
  records the sides left. The records must be exactly those the notes ask for:
  one record of the side's own for each call to a side that has an identity. A
  record too few is a call that did not run its side. A record too many is an
  engine that ran without passing the dispatcher.
- **Environment.** Whatever calls the dispatcher runs under the built
  environment of the v1 side (Sides), without `WIKI_FREEZE_TIME`: the tests
  bring their own clock, the terminal table reads none, and no notice opens a
  command's stderr.

### Ported Commands

`parity/ported.toml` lists the ported commands: the first arguments of the calls
a second engine takes. Each porting change adds its commands to the list.

```toml
commands = ["read", "--version"]
```

- **Refusals.** A list is refused when it holds a key other than `commands`, a
  value that is not a list of strings, an empty string, or one command twice.
- **Nothing ported.** With an empty list no call would reach a second engine, so
  a run that names one is refused.

### Tests

- **Where.** The tests are the folder `tests/test_cli` of the oracle checkout.
  The run leaves out what v1's pytest settings leave out, and the result file
  lists those tests.
- **Plugin.** v1's tests take the console script from one helper module, which
  names its path and its folder. A pytest plugin (`parity/plugins`) sets both to
  the dispatcher. A test then reaches the dispatcher by the helper's runner, by
  the path, and by the bare name `wiki` on the `PATH` the helper builds, as git
  does for the merge driver. A helper that is not imported, or does not hold
  both names, ends the run with an error.
- **When.** The plugin sets the names as the helper is imported, between the
  helper's own code and the first module that takes a name from it. v1's test
  packages import their test modules, so the first import of any of them imports
  them all, before pytest loads the helper as a `conftest`; an import finder is
  the one place early enough. Whatever a test module takes from the helper is
  then the dispatcher: a name it binds, a command it builds while it is
  imported, a parameter of a test. A call a module makes while it is imported
  belongs to no test, so it is an `untraced` finding.
- **Version.** One test holds what `--version` prints to the version of the
  package it imports, which its module names `WIKI_VERSION`. A second engine has
  a version of its own. The plugin sets that name, in every test module that
  holds it, to the version the side that takes `--version` states in its
  identity. The test then holds the engine's output to the version its checkout
  states. A side with no identity states no version, and the name stays as it
  is.
- **Past the helper.** A test that finds the console script without the helper,
  for example beside the interpreter, runs v1 unseen: v1's console script leaves
  no identity record. No test of the oracle does. The two tests that run
  `python -c` test v1's Python code and count as tests with no call.
- **Trace.** The plugin sets `WIKI_TRACE` to a fresh file before a test's first
  fixture runs, and the file stays until the next test gets its own. A fixture
  that several tests share is built inside the first test that asks for it, so
  its calls count for that test.
- **Interrupts.** A caller that ignores interrupts, as a shell does for a
  detached job, hands that on to every process below it. The run takes the
  interrupt back before pytest starts, so a test that interrupts a command still
  does.
- **Where trees go.** pytest's base folder for temporary files lies in the work
  folder, and pytest writes no cache into the checkout. A work folder inside a
  repository is refused: `init` registers the merge driver in the repository it
  finds, and that must be one a test made. A work folder below a folder that
  holds `.wiki/settings.json` is refused as in Snapshots.

A run passes when it has no finding:

| Finding                                            | Means                                                                 |
| -------------------------------------------------- | --------------------------------------------------------------------- |
| `failed`, `error`, `skipped`, `xfailed`, `xpassed` | A test did not pass. A skip and an expected failure are findings too. |
| `error` or `skipped`, with a test file's name      | The file failed while it was imported, or skipped itself there.       |
| `identity`                                         | A test's trace file breaks the identity rule of Dispatcher.           |
| `untraced`                                         | Calls reached the dispatcher with no `WIKI_TRACE`.                    |
| `unused`                                           | No test called a side of the run, or a ported command.                |

- **Skips.** A test that is skipped, or that fails as its mark expects, asserted
  nothing about the engine, so it is no pass.
- **Files.** A test file that is not loaded has no test that runs, so no test's
  outcome tells of it. Where several processes run the tests, pytest goes on
  past a file that fails while it is imported. The file is a finding by itself.
- **Unused.** A run in which a side or a ported command took no call proved
  nothing about it. A run of some tests only is held to the same rule.
- **No call.** `tests/test_cli` also holds tests that call no `wiki`: they test
  v1's Python code in the interpreter. They run and must pass, and the result
  file shows them with no call.
- **Counts.** The run prints, and the result file records, how many tests and
  calls each side took, and the same for each ported command. The number of
  calls turns on how many processes run the tests, because each process builds a
  shared fixture again.
- **Not run.** A run in which pytest collects nothing, or ends on an error of
  its own, is an error of the harness, never a pass. So is a run that pytest
  ends as failed while no test and no file is a `failed` or `error` finding.

### Terminal Table

An engine prints other bytes to a terminal than to a pipe, and only a terminal
turns a typed Ctrl-C into an interrupt. Each case of the table is one command
with a pseudo-terminal as its three streams. It runs twice on one wiki at one
path: on the v1 side, and through the dispatcher, which sends it to the side
that has its command. With no second engine both runs are v1, and the table
checks itself.

| Case        | Command                               | Typed  | Allowed exit code |
| ----------- | ------------------------------------- | ------ | ----------------- |
| `sequences` | `read` of a page with terminal bytes  | none   | 0                 |
| `error`     | `read` of a page the wiki lacks       | none   | 2                 |
| `interrupt` | `read` of a page of several megabytes | Ctrl-C | 130               |

- **Wiki.** The v1 side makes the wiki with `init`, and the harness writes the
  pages. The first page holds a color sequence, a title sequence, a bell, a unit
  separator and a delete. The long page's body is one line of dots. The table's
  commands write nothing, so both runs read the same tree.
- **Terminal.** The pseudo-terminal is the command's controlling terminal, 24
  rows by 80 columns, and `TERM` is `xterm-256color`. It passes output on as
  written, echoes nothing that is typed and drops no output on an interrupt. So
  the harness reads every byte the command wrote, on either stream, in order.
- **Keys.** Ctrl-C is typed once the command has shown its first bytes. The long
  page is larger than any terminal buffer in use, so the command is still
  printing it then. A command that has ended by then takes no key, and its
  output is compared as it is.
- **Compared.** The bytes shown and the exit code, where a command ended by a
  signal has the negated signal number. In the `interrupt` case the length of
  the page's head that a side shows turns on timing, so each output is compared
  from where it leaves the page's text. A side that opens its own text with the
  page's next byte would hide that byte. Inside the long line the next byte is a
  dot, which no interrupt text starts with. A line end, which one may start
  with, follows only the page's first lines and its last dot.
- **Each run by itself.** A run that ends with a code its case does not allow is
  an `unexpected` difference, as in the matrix. A run that does not leave its
  identity record is an `identity` difference; for the dispatcher's run that is
  the identity rule of Dispatcher, for exactly one call.

| Aspect       | Meaning                                         | Detail        |
| ------------ | ----------------------------------------------- | ------------- |
| `output`     | the bytes the two runs showed differ            | empty         |
| `exit`       | the exit codes differ                           | empty         |
| `unexpected` | a run ended with an exit code its case forbids  | the exit code |
| `identity`   | a run did not leave the identity record it owes | empty         |

A difference names the table as `terminal`, the run (`dispatcher`, or the v1
side's name) and the case.

## Replay

v1's core tests call the engine in process, and assert on what a call returns,
raises, writes and reports. The replay takes those tests as a source of inputs.
It runs them unchanged under v1 with a recorder, and then runs every call they
made again as a command line, on the tree the call found, on each side. Two
sides that agree on every byte there agree on everything the tests assert, if
the command line does what the test's call did. So the replay has two verdicts
for each call: the sides are compared by the rules of Comparison and Caches, and
v1's side is held to the call (Fidelity, below).

- **Where.** The tests are the folders `tests/test_core` and `tests/test_cli` of
  the oracle checkout, run as the CLI suite runs its tests, with every call of
  `wiki` sent to v1: a record is what v1 did in the test.
- **Sides.** With no second engine the replay runs every call on v1 twice, under
  `PYTHONHASHSEED=1` and `PYTHONHASHSEED=2`, and checks itself (Controls). With
  one it runs the calls of the ported commands (CLI Suite, Ported Commands) on
  v1, on the engine, and on the engine with `WIKI_THREADS=1`.
- **Nothing registered.** Between two builds of v1 a call that a classifier
  excuses is a `registered` finding. Against a second engine it is listed.

### Recorder

The recorder is a pytest plugin (`parity/plugins`). It stands in for seven
methods of the engine class (`update`, `lint`, `read`, `search`, `match`, `map`
and `new`) and for the module the CLI tests' helper starts commands with.

- **Outermost calls.** A call of a recorded method is one record. What that
  method calls in turn is part of it, and so is a call another thread makes
  while it runs.
- **Command line.** A call is written as the arguments of its command in the
  output mode that prints the most: `update --full`, `lint --json`,
  `match --lines`, `search --json`. A value of the test goes into an option as
  `--option=value`, and the positionals follow `--`, so a value that starts with
  a dash is still a value. The wiki is named with `--path`. A command of the
  helper is recorded with its own arguments.
- **Clock.** Each call runs under a fixed clock, and the record holds its
  instant as `WIKI_FREEZE_TIME`. The recorder sets the variable for a call whose
  test left it unset. A test that puts its own clock method on the engine keeps
  it: the recorder listens to the stamps it returns, and the instant is the one
  that renders as that stamp under the wiki's zone and format. For a command of
  the helper the recorder takes the notice line of its own clock out of the
  stderr the test reads, and the record keeps the stream as the command wrote
  it. A command whose test set the variable runs as the test started it, notice
  and all.
- **Blank clock.** An engine reads a blank `WIKI_FREEZE_TIME` as unset. For a
  call in process the recorder does the same, and sets the variable. A helper
  command under a blank value is a test of that reading: it runs with the clock
  running and prints no notice, and it is unformed, because a replay would write
  the stamps of its own moment.
- **Trees.** The trees of a call are top entries of the temporary folder of its
  pytest process: every entry the test has made or written by then, and every
  entry the call names in its arguments, its working directory, its wiki root or
  a variable of its environment. Each process runs under a home of its own in
  that folder, so the home is one of them. The recorder copies the trees before
  the call and after it, with library calls: a test may leave no `cp` on `PATH`.
  A copy keeps modes and modification times. A folder or a file its owner cannot
  read is opened for the copy and closed again; the record lists its true mode.
- **Environment.** The record holds the whole environment of the call and its
  working directory.
- **Kinds.** For each test the recorder counts the notice kinds the engine
  emitted in process and the issue kinds `lint` returned. Each record of an
  engine call holds the kind of each notice it emitted and of each issue it
  returned. The recorder also lists the kinds the engine has: one notice kind
  for each notice hook of the engine class, and the issue kinds its source
  names. An issue's kind is a keyword where the issue is made, so an issue made
  with a kind that is no string literal ends the run.
- **Shared fixtures.** A fixture of a wider scope than one test is set up inside
  the first test that asks for it, and which test that is turns on how the tests
  are spread over the processes. A call such a fixture makes is recorded under
  the fixture's name, and each test lists the shared fixtures it asks for that
  made a call.
- **Commands with no record.** The recorder sees a command of the console script
  only when the helper's own `subprocess.run` starts it. The merge driver that
  git starts during a test's `git merge`, and a command a test starts by itself,
  reach a side with no record, and the replay does not hold them: the CLI suite
  does, by the tests' own assertions. The dispatcher counts every command of a
  test, and the result file lists each test that ran more commands than the
  recorder holds for it (`unrecorded`). A helper that does not hold the module
  it starts commands with as `subprocess` ends the run before a test. A whole
  recording that holds no helper command, though its tests ran commands, is an
  `unrecorded` finding.

A call has no command line, and is `unformed`, in these cases:

- An argument is no string or integer where the command takes one, or the call
  gives a parameter that no option of the command states.
- `read` is asked for a unit it has no option for.
- The test's clock gave two stamps, or a stamp that no instant renders as.
- The test left the clock running for a helper command (Blank clock, above).
- The call ran before the temporary folder was made, or it names the whole
  temporary folder.
- The wiki of an engine call, or the working directory of a helper command, lies
  outside the temporary folder: no copy holds that path.
- The helper did not capture a stream, or the command did not end.
- A tree cannot be copied.

The replay adds two cases: a command that cannot be started on the trees, and a
record whose temporary folder lies outside the folder of the recording. The
replay removes that folder and places the trees there, so it takes no path
outside its own work from a record.

### Playing a Call

- **Place.** For each side the trees of the call are cloned from the copy made
  before the call to their own paths, and the temporary folder of the call's
  process holds nothing else. Sealed paths get their true modes back. The side
  runs the command under the recorded environment and working directory, with
  its own variables set over them.
- **Order.** The calls of one pytest process may name the same paths, so they
  are replayed one after the other. The calls of two processes share no path and
  run side by side.
- **Compared.** The scans cover the temporary folder of the process. Paths at or
  below a `.git` folder are left out: a copy leaves each index stale, and each
  side refreshes it as it goes. So the replay does not hold what a command
  writes there, such as the merge driver that `init` registers in the
  repository's config. The CLI suite does: its tests of `init` and `config`
  assert on the config. The cache rule is applied to every wiki whose cache
  folder a side holds before or after the command, and to the wiki of an engine
  call.
- **Exit codes and notice.** A replayed call brings no list of allowed exit
  codes and no notice rule of its own. Fidelity holds v1's side to the test, and
  the comparison holds the other sides to v1: a side prints the clock notice
  exactly when v1's command did.
- **Read-only.** `lint`, `read`, `match`, `map`, `search` and `update --check`
  are held to the read-only rule of Comparison.

### Fidelity

A replay that runs another scenario than the test's call can still compare equal
on both sides, and it then says nothing about the test. So v1's command line
must do what the call did:

| The call                 | v1's command line                                                                                                              |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| raised an error          | exits 2, and its stderr ends with `Error: ` and the error's text                                                               |
| `update` returned a list | exits 0 and prints the count of the list; under `--check` it exits 1 for a list that is not empty, and names the list in order |
| `lint` returned issues   | exits 1 for a list that is not empty, and the issues of its JSON document have the texts of the list, in order                 |
| `read` returned text     | exits 0 and prints the text                                                                                                    |
| `map` returned text      | exits 0 and prints the text; for an empty text it prints nothing, or that the wiki is empty                                    |
| `new` returned a path    | exits 0 and names the path                                                                                                     |
| `search` returned hits   | exits 0 and prints as many, or exits 1 for none                                                                                |
| `match` returned hits    | exits 0 and prints as many lines, or exits 1 for none                                                                          |
| emitted notices          | prints each notice, in the call's order: on stderr for `update` and `map`, among the notes of the document for `lint`          |
| was a helper command     | has the same exit code and the same two streams, read as text the way the helper read them                                     |

Every call is also held to its trees. The manifest the command leaves must be
the one the copy made after the call holds, and the command must write the paths
the call wrote. Cache folders and `.git` folders are left out of both. A copy
keeps a path's modification time and not its inode, so "wrote" means here: the
path was made or removed, or it is no folder and its modification time or its
content changed. Both the test's trees and the command's are read that way.

A call that breaks a row is `unfaithful`. An unformed or unfaithful call is a
finding under its test, never a skip, unless the routing table names the test.

### Routing

`parity/routing.toml` is the routing table: the tests of v1 that neither the CLI
suite nor the replay holds, each with the check that holds it in their place.

```toml
[[route]]
tests = ["tests/test_core/test_x.py::test_name"]
to = "rust"
target = ""
cite = ""
reason = "<why the replay cannot hold the tests>"
```

- **Tests.** The id of a test function routes every parameter set of it; an id
  with its parameters routes that one.
- **To.** `rust`: a test that the second engine brings holds the tests, in its
  own suite or over its repository. `probe`: a function of its probe does.
  `replay`: the replay will, once the recorder has a command line for the method
  the tests call. `dropped`: nothing does. `target` names the test or the
  function, and is empty until it exists. For a `replay` row it names the
  command the row waits for, which is one a second engine ports: any command of
  v1 but `trust`.
- **Cite.** A dropped row cites what it rests on: the id of a registry entry,
  the id of the test that replaces the dropped ones, or a part of v1 that a
  second engine does not have (`trust`, `hooks`, `frontmatter memo`,
  `loader choice`, `Python API`). A test of the CLI suite that the replay cannot
  hold cites itself: the suite holds it by its own assertions. No other row
  cites.
- **Refusals.** A table is refused when it holds a key other than `route`, a row
  that is no table, a field outside those above or of the wrong shape, a row
  with no test or no reason, a `to` outside the four, a dropped row that cites
  nothing it may cite, another row that cites, a `replay` row whose target is no
  command a second engine ports, or one test in two rows.
- **Routed calls.** The calls of a routed test are replayed and compared like
  any other. A call with no command line and an unfaithful call are no findings
  there. A difference between the sides still fails the run.
- **Registry rows.** A row that cites a registry entry routes its tests for that
  entry's difference, so it covers the calls the entry is about (the table
  below) and no other call. For an entry with no classifier, what the sides
  differ in on such a call is the entry's difference, and it is listed in the
  result file only. An entry with a classifier says call by call what it
  explains: between two builds of v1 a call it excuses under the row's own entry
  is no `registered` finding, and any other difference still fails the run.
- **Shared calls.** A call a shared fixture made belongs to no one test, so no
  row covers it: it is held like the call of a test that is not routed.
- **Unneeded rows.** Where every call is replayed, a row for a test whose calls
  all replay as their commands, with no difference, is an `unneeded` finding:
  the replay holds that test. So is a row for a test whose format calls all give
  their outcome again. A row that cites a registry entry is needed while a call
  of its test is one the entry is about, whether or not two builds of v1 differ
  there.

| Entry | A call the entry is about                                                     |
| ----- | ----------------------------------------------------------------------------- |
| D1    | v1's command ends in a library's text behind a wiki-written prefix            |
| D3    | v1 ran the command on input outside the supported set (Supported Sets, below) |
| D6    | the call's trees hold a hook file, or the command is `trust`                  |
| D10   | the command is `install`                                                      |

No call is about another entry, so a row that cites one covers nothing.

### Format Calls

The recorder also records every call the format tests make to a public function
of `wiki.core.format`, whoever makes it: the test, or the engine under it. The
format tests are those of `tests/test_core/test_format.py` and the two tests of
`tests/test_core/test_update.py` that call a format function and nothing else
the recorder records. The outermost call counts. A record holds the function,
the arguments and the outcome as JSON, how often the call was made, and the
tests that made it. Equal calls are one record.

- **JSON form.** A string, a number, a boolean, `null` and a list are
  themselves. A tuple, a set and a mapping are objects marked with the key `~`,
  each with its `items`; a set lists its members in the order of their JSON
  text. An outcome is `returned` with the value, or `raised` with the error's
  type and text. A value with no such form, in an argument or in what the
  function returned, makes the call a `format` finding unless every test that
  made it is routed. Such a call is not made again.
- **Made again.** With no second engine the replay makes every recorded call
  again, in a fresh interpreter of v1's side, under each of the two hash seeds
  (`parity/plugins/parity_values.py`). A call that gives another outcome there
  than in its test turns on something its arguments do not hold, and is a
  `format` finding unless every test that made it is routed.

### Controls

With no second engine, and no `--test`, a replay ends with two controls on its
own recording. Each doctors the replay and must be reported.

| Control | Doctored                                                                                                              | Must show as                        |
| ------- | --------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| `argv`  | the argument mapping: every `update` call that found pending changes under `--check` runs without the option          | every one of those calls unfaithful |
| `side`  | one side: a wrapper changes one thing behind the first faithful call that may write, in one replay for each row below | exactly the differences of that row |

| The wrapper                                                              | Must show as                      |
| ------------------------------------------------------------------------ | --------------------------------- |
| changes one byte of stdout                                               | `stdout`                          |
| writes one more file                                                     | `tree` and `touched` on that file |
| changes one byte of a file the command leaves alone, and keeps its times | `tree` on that file               |
| changes the mode of that file                                            | `tree` on that file               |
| writes that file again with the same bytes                               | `touched` on that file            |

A control that doctors no call, or that the replay does not report in full, is a
`control` finding.

### Supported Sets

A second engine supports a fixed set of regex constructs, of timestamp
directives and of settings values, and refuses input outside it (Registry,
Classifiers). The replay confirms the set against the calls v1's tests record:
for each call it replays, it asks the judge of v1's side about the inputs of D3.

- **Inputs.** Every `.wiki/settings.json` the call's trees hold before the
  command, the value of `--settings` of an `init` command, and the pattern of a
  `match` command. A command line does not say which wiki it resolves to, and a
  wiki need not have a cache folder yet, so every settings file of the trees
  counts: a call beside a wiki with such settings is listed too.
- **Outside.** A call that v1 ran, with exit code 0 or 1, on input the judge
  calls outside the set is listed in the result file (`outside`) with the
  judge's reasons. A command that v1 refuses by itself is not listed: no engine
  runs it.
- **Finding.** Each such call is an `unsupported` finding, unless the routing
  table drops its test under D3. So a test that depends on input outside the set
  is either named in the table or fails the replay, and a change to the set
  shows at once in which tests it reaches.
- **Not judged.** A test that records no call gives the replay no input to
  judge, so the set is not confirmed against it. The tests of the naming and
  timestamp settings that the routing table sends to `probe` are of this kind.

### Homes

With no second engine, and no `--test`, a replay ends with an account of v1's
tests: each has one home, the part of the harness that holds a second engine to
it.

- **Tests.** The tests are those pytest collects from the root of the oracle
  checkout under v1's own settings: every test file and every doctest. The
  harness's own tests on v1's branch (`tests/test_parity`) are left out. v1's
  settings leave out the one test that downloads from the network: it is not
  collected and has no home here, and runs once for each release candidate, on
  each side.
- **Homes.** A test takes the first home that holds it.

| Home     | Holds a test when                                                                                   |
| -------- | --------------------------------------------------------------------------------------------------- |
| `routed` | the routing table names it                                                                          |
| `suite`  | it is a test of the CLI suite that ran a command, by itself or through a shared fixture it asks for |
| `replay` | a call of its own replays as its command                                                            |
| `format` | it made a format call that has a JSON form, and every format call it made gives its outcome again   |

- **Homeless.** A test with none of these is a `homeless` finding. It runs v1's
  Python code and nothing the harness can run on a second engine, so a row of
  the routing table has to say what holds it.
- **Skipped.** A test that v1 skips on the host, before it shows a home, is
  listed as skipped and is no finding: it shows its home on a host that runs it.
- **The table.** The account holds the routing table to the tests in turn. A row
  that names a test v1 does not have is an `absent` finding. A dropped row that
  cites a test must cite one the CLI suite holds, or it is a `cite` finding. A
  `replay` row whose command is a ported command is a `waiting` finding: the
  recorder was to have a command line for the tests' method by then.
- **Counts.** The run prints, and the result file records, how many tests have
  each home, how many the table sends to each place and how many the host
  skipped.

### Kinds

`parity/kinds.toml` is the kind list. It gives every notice kind and every issue
kind of v1 one home: a test of v1, or `perturbation` for a kind that the
perturbation puts into the trees it changes.

```toml
[notices]
link_add = "tests/test_core/test_x.py::test_name"

[issues]
broken_link = "perturbation"
```

- **Refusals.** A list is refused when it holds a key other than the two tables,
  a table of another shape, or a home that is neither `perturbation` nor a test
  id.
- **Every kind.** With no second engine, and no `--test`, the replay holds the
  list to the kinds the recorder found in the engine. A kind of the engine that
  the list lacks, and a kind of the list that the engine lacks, are `kind`
  findings.
- **A test for a home.** The replay holds the kind when a call of the test
  replays as its command and the command shows the kind. A `lint` command shows
  the kinds its JSON document names, for its issues and its notes. Any other
  command shows a notice kind when it prints, on stderr, the text of a notice of
  that kind which the call emitted in its test. A home that shows nothing of its
  kind is a `kind` finding.
- **A routed home.** A kind that only a routed test meets has that test for a
  home, if the table sends the test to `rust` and the test met the kind in a
  recorded call: the test of the second engine that takes the test's place holds
  the kind. A home routed anywhere else is a `kind` finding.
- **Perturbation.** The replay does not judge a kind whose home is the
  perturbation. The perturbation's own run answers for it (Perturbation, Kinds).
- **Counts.** The run prints, and the result file records, how many kinds of
  each family have each sort of home.

A run passes when it has no difference and no finding:

| Finding                                   | Means                                                                     |
| ----------------------------------------- | ------------------------------------------------------------------------- |
| `failed`, `error`, `identity`, `untraced` | The tests did not run under the recorder as v1 runs them (CLI Suite).     |
| `unformed`                                | No command line states a call of the test.                                |
| `unfaithful`                              | v1's command line does not do what a call of the test did.                |
| `registered`                              | A registry entry excused a call between two builds of v1.                 |
| `unsupported`                             | v1 ran a call on input outside the supported set.                         |
| `unneeded`                                | The routing table names a test the replay holds.                          |
| `homeless`                                | A test of v1 has no home.                                                 |
| `absent`, `cite`, `waiting`               | A row of the routing table is not borne out by v1's tests.                |
| `kind`                                    | The kind list lacks a kind, or a home does not show its kind.             |
| `unrecorded`                              | The tests ran commands, and a whole recording holds none of the helper's. |
| `format`                                  | A format call has no JSON form, or gives another outcome when made again. |
| `control`                                 | A doctored replay was not reported.                                       |

A test that is skipped, or fails as its mark expects, records no call. The
result file lists it with its outcome, and it is no finding of the replay.

## Gates

A gate is the harness run whole on the corpora: its layers, one after the other,
each timed. `python -m parity gate` runs one, and `parity/gate.py` names its
layers. A layer is one harness command, or that command once for each corpus,
and every command of a gate is a step: a process of its own, with a work folder,
an output folder and a log below the gate's.

- **Full gate.** It first checks the harness with v1 alone: the self-check and
  the registry check, the replay of v1's tests with its controls, the fixture
  families, the perturbation in both modes, and the fixed sample of the merge
  history with no cache. With an engine it then compares v1 with the engine on
  the corpora, on the fixture families and on the perturbation in both modes. It
  ends with the whole merge history: v1 alone on every merge the cache does not
  hold, which stores them, then the engine on every merge.
- **Daily gate.** The comparison with the engine on fresh snapshots, without the
  loop of fresh seeds, and the merges made since the last gate: the engine on
  every merge the cache does not hold, against v1 as it runs there, and then v1
  alone on those merges, which stores them.
- **No engine.** Without `--engine` a gate has no layer that compares two
  engines, and says so. A full gate is then the check of the harness and the
  fill of the cache, and a daily gate the fill alone.
- **Oracle.** Every gate starts with `oracle check`, so a gate fails while this
  branch is not level with v1. The check reads the remote-tracking references:
  fetch first.
- **Loop.** `--loop LABEL` (repeatable) names the corpora whose wikis the loop
  of fresh seeds runs on, `--fresh N` seeds each (default 200). The first of
  them also takes the registry check, which one corpus proves; with none, the
  first corpus of the gate takes it and no loop runs.
- **Order.** The layers run one after the other, so the time of a layer is its
  own. The steps of a layer run side by side. `--jobs N` bounds how many steps
  run at once, and is handed to the commands that run several trees or merges at
  once.
- **Times.** For each step the gate records the exit code, the seconds on the
  clock, and the processor seconds of the command with every process it waited
  for, which are the sides it ran. For each layer it records the seconds from
  its first step's start to its last step's end, the sum of its steps' processor
  seconds, their quotient (how many processors the layer kept busy) and the
  host's load average when the layer started and when it ended. Other work on
  the host shows in the load, stretches the seconds and can add processor
  seconds too; time a gate on a host that does nothing else.
- **Result.** `<out>/gate.json` holds the gate, the engine, the corpora, when it
  started, the host and one row for each layer with its steps and their
  arguments. It is written again as each layer ends, so a gate that is cut short
  leaves the layers it ran. A step writes its results to `<out>/<layer>/`, or
  `<out>/<layer>/<corpus>/` where the layer has a step for each corpus, and what
  it prints to a file of that name with `.txt`.
- **Exit code.** Every layer runs, whatever the one before it showed. A layer
  ends 0 when every step exited 0, 1 when a step exited 1 and none ended any
  other way, and 2 otherwise: a step that a signal ended is an error. The gate
  exits with the highest of its layers' codes. It exits 2 with a message, before
  a step runs: on a corpora or sides file of the wrong shape, on an unknown
  corpus or side, on a corpus named twice, on a `--loop` corpus the gate does
  not run on, on a count below one, on a work folder that exists, and on a Rust
  side whose checkout differs from its commit (Sides, Uncommitted edits). A gate
  that passed removes its work folder; one that did not leaves the trees its
  steps kept.

| Layer                   | Part         | Gate                  | Command                                                             |
| ----------------------- | ------------ | --------------------- | ------------------------------------------------------------------- |
| `oracle`                | `oracle`     | both                  | `oracle check`                                                      |
| `selfcheck`             | `self-check` | full                  | `selfcheck --side oracle`, a step for each corpus                   |
| `registry`              | `self-check` | full                  | `registry --side oracle` on one corpus                              |
| `replay`                | `self-check` | full                  | `replay`                                                            |
| `fixtures`              | `self-check` | full                  | `fixtures`                                                          |
| `perturb-corpus`        | `self-check` | full                  | `perturb corpus`, a step for each corpus                            |
| `perturb-loop`          | `self-check` | full, with `--loop`   | `perturb loop --corpora` on the loop's corpora, with `--fresh`      |
| `merges-sample`         | `self-check` | full                  | `merges --sample`, with no cache                                    |
| `matrix`                | `corpora`    | both, with an engine  | `matrix --a oracle --b ENGINE --registered`, a step for each corpus |
| `fixtures-engine`       | `corpora`    | both, with an engine  | `fixtures --engine`                                                 |
| `perturb-corpus-engine` | `corpora`    | both, with an engine  | `perturb corpus --engine`, a step for each corpus                   |
| `perturb-loop-engine`   | `corpora`    | full, with both       | `perturb loop --engine --corpora` on the loop's corpora             |
| `merges-engine`         | `history`    | daily, with an engine | `merges --engine --cache --new`, before `merges`                    |
| `merges`                | `history`    | both                  | `merges --cache`                                                    |
| `merges-engine`         | `history`    | full, with an engine  | `merges --engine --cache`, after `merges`                           |

The cache of the merge history is keyed on the path a merge ran at. Every step
of a gate that replays merges is therefore handed one folder, `--at <work>/at`,
so the engine's layer reads what the layer of v1 alone stored, and the daily
gate's `--new` names only the merges no gate has stored. Every gate of a host
takes the same `--work`, or its merges run at other paths and it finds no entry.

## Container

macOS keeps no two names that differ only in case and no name that is no valid
UTF-8, and link verdicts turn on the file system. The container is a Linux on a
host that is none: the Ubuntu release of the CI runners, not their image.
`python -m parity container` builds its image from `parity/Dockerfile` and runs
one command in a fresh container.

- **Image.** The Ubuntu release of the CI runners, with that release's own git,
  uv, gawk, mawk and the system zone files, and a user of its own, because v1's
  tests of unreadable files need a user that is not root. The git version and
  the processor architecture are the image's and the host's, not the runners':
  every result file records the git it ran. It holds no file of the repository,
  so it is built with no context. Its tag is a digest of the Dockerfile: a
  changed Dockerfile builds a new image. The base image and the packages are
  named by release, not by digest, so two hosts may hold other builds under one
  tag.
- **Clone.** A container reads the repository of the checkout the harness lies
  in, and never writes it. It clones that repository to its own file system,
  takes the remote-tracking branches as the host holds them, and checks out one
  commit: `HEAD` of that checkout, or the commit `--commit` names. So the clone
  holds what is committed, and an edit that is not committed does not reach it.
- **File system.** The clone, the oracle checkout that `oracle ensure` makes
  beside it, and every work folder below `/work` lie on the container's own file
  system, which keeps case and bytes. Nothing a side runs on is mounted from the
  host.
- **Environment.** The container installs the harness's dependency group, then
  becomes the command, which runs from the clone's root with the streams of the
  caller. The command builds the oracle with `oracle ensure`, as CI does.
- **Kept.** One volume, `wiki-parity-cache`, holds what uv downloads, so a
  second container starts fast. `--out DIR` gives the container a folder of the
  host as `/out`. Everything else is gone when the command ends.

## Contracts

The tool reaches a host as a package: by a pin of a release or of a commit of
the repository, under a distribution name that is looked up, with a version that
`wiki --version` prints, and as the `wiki` an installer puts beside the
interpreter. `python -m parity contracts` holds a built wheel and sdist to that.
It compares no two engines: the checkout the harness lies in states what the
files must be.

- **Package.** The wheel and the sdist are the files a build of this checkout
  made, the wheel for this host. The checkout states the version, in
  `Cargo.toml` and in Cargo's spelling, and the commit, which is `HEAD`. A wheel
  built over an edit that is not committed carries the commit all the same, so
  the run prints and records the paths that differ from `HEAD` among those a
  build reads (Sides, Uncommitted edits).
- **Scratch host.** Every installer and every `wiki` of a run sees one
  environment and inherits nothing else: a new home below the work folder;
  `PYTHONUSERBASE`, `PYENV_ROOT`, `PIPX_HOME`, `PIPX_BIN_DIR`, `CARGO_HOME`,
  `RUSTUP_HOME`, `UV_CACHE_DIR`, `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` below that
  home; an empty global git config; `UV_NO_CONFIG`, so uv reads no settings
  file, neither of a folder above the one it runs in nor of the system; and a
  `PATH` of the system's folders and one folder that holds links to the caller's
  uv and git and nothing else. So no install reaches an interpreter, a cache or
  a toolchain of the user's.
- **No Rust.** The scratch host holds no `cargo`, and a run is refused when a
  system folder holds one. A build there is the build of a host with no Rust:
  the build backend fetches the toolchain the checkout pins, so it needs the
  network. A build with a `cargo` at hand would prove nothing of such a host.
- **Venvs.** A check installs into venvs and folders of its own, below a folder
  of the work folder that carries its name, or into the folder of the scratch
  home that its installer keeps for a user, which no other check installs into.
  So each check stands alone. Every venv is made from the interpreter version of
  the `oracle` side, which uv fetches into the scratch home, and a check that
  installs into no venv runs the pip of that interpreter.
- **Working `wiki`.** A check that installs a `wiki` holds it to three things.
  `wiki --version` prints the version and a newline on stdout and nothing on
  stderr, and exits 0. `wiki install --project`, run in a new folder, exits 0
  and leaves in `.claude/skills` and in `.agents/skills` the files that
  `wiki/skills` holds at the commit, each byte for byte, and no other file. And
  both commands leave the identity record the check expects (Sides, Rust side):
  the engine, the version, and the build id the check names.
- **Findings.** A check that does not hold is a finding, one line for each thing
  it found. An installer that fails inside a check is that check's finding, with
  the end of what it printed, and so is a `wiki` that does not start: the run
  goes on to the next check. A step that is no part of a check, a venv or the
  build of v1's wheel, ends the run with an error when it fails. What each
  command of an installer printed is written to `<work>/contracts/logs/` as the
  command ends, so a run that ends in an error leaves it there.

| Check     | Holds                                                                                                                                                                                                                                                                                                                                                                                           |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`    | The wheel's metadata names the distribution `plasma-wiki`, and the interpreter of a venv that holds the wheel finds `plasma-wiki` at the version the wheel states.                                                                                                                                                                                                                              |
| `version` | `wiki --version` prints the bare version. Read as a Python version, that text is the version the wheel states: the binary prints Cargo's spelling (`2.0.0-dev.1`, `2.0.0-rc.1`), and an installer records the one Python normalizes it to (`2.0.0.dev1`, `2.0.0rc1`).                                                                                                                           |
| `native`  | The wheel holds `wiki` among its scripts, the `wiki` of a venv is that file byte for byte, and the file is no script: it does not start with `#!`.                                                                                                                                                                                                                                              |
| `venv`    | The `wiki` of a venv that holds the wheel is a working one, with the commit as its build id.                                                                                                                                                                                                                                                                                                    |
| `symlink` | So is that `wiki`, run through a symbolic link in another folder.                                                                                                                                                                                                                                                                                                                               |
| `space`   | So is the `wiki` of a venv whose path holds a space.                                                                                                                                                                                                                                                                                                                                            |
| `pip`     | So is the `wiki` of a venv that Python's own `venv` module makes, installed by the pip of that venv.                                                                                                                                                                                                                                                                                            |
| `user`    | So is the `wiki` that `pip install --user` puts below the user's base folder, for the interpreter outside every venv.                                                                                                                                                                                                                                                                           |
| `target`  | So is the `wiki` that `pip install --target` puts below a folder that is no environment.                                                                                                                                                                                                                                                                                                        |
| `pipx`    | So is the `wiki` that pipx links into its folder of commands. pipx runs from a venv that holds exactly the packages `constraints/pipx.txt` pins.                                                                                                                                                                                                                                                |
| `tool`    | So is the `wiki` that `uv tool install` links into its folder of commands.                                                                                                                                                                                                                                                                                                                      |
| `run`     | So is the `wiki` that `uv run --with` runs by its name, from an environment uv keeps for the one command. The check runs uv over the interpreter outside every venv, because over an environment that holds a `wiki` of its own uv runs that one and not the wheel's.                                                                                                                           |
| `sdist`   | An install of the sdist builds a working `wiki` whose build id is empty: the build runs outside every checkout.                                                                                                                                                                                                                                                                                 |
| `upgrade` | Once with uv and once with the venv's own pip: v1's wheel is installed, then the wheel over it, then v1's wheel over that. After the first and the third install `wiki --version` prints v1's version and exits 0, and after the second the `wiki` is a working one. v1's wheel is built from the oracle commit as git holds it, and installed under the constraints file of the `oracle` side. |
| `lock`    | A project names `plasma-wiki` as its one dependency, from the repository of the checkout at the commit. `uv lock` records that commit as the source, `uv sync --locked` builds it, and the `wiki` of the project's venv is a working one with the commit as its build id.                                                                                                                       |
