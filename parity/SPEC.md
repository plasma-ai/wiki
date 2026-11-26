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

# run a command in the Linux container
python -m parity container [--commit COMMIT] [--out DIR] COMMAND [ARG ...]
```

A side name is `oracle` or `campaign`, the two v1 sides that
`parity/oracle.toml` names (Oracle, below), or a side that the `SIDES` file
declares. `SIDES` may not declare a side under the name of a v1 side.

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
rules refuse, or a registry file that breaks a rule of Registry. An error of the
harness itself prints a Python traceback and exits 2 as well, so exit 1 always
means that a result is not the expected one.

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
argv, variables, identity, reported version and description, the status of every
registry entry, the sample, for every stage how many commands of each run ended
with each exit code, which searches found nothing and which commands a registry
entry excused, and for every case the exit code, the size and sha256 of both
streams, and the touched count on every run. `matrix` saves the streams of a
case under `<out>/<label>/<stage>/` when they differ, an excused case included,
and `selfcheck` and `registry` save those of a failed check under
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
work folder; on a corpus the harness cannot snapshot; on a seed list or a kind
list that breaks a rule; on a kind list that disagrees with the operators
(Perturbation, Kinds); on `--fresh` with `--seed`, or with a count below one;
and on `--family` or `--every` with `--corpora`. They make `<work>/perturb`,
which must not exist beforehand, and remove it when the run passed. A corpus
gets a result file `<out>/<label>.json` in the form `matrix` writes, with the
seed, what the perturbation did, the kinds each of its trees showed and the
findings; the streams of a case that differs are saved under
`<out>/<label>/<stage>/`. A tree of a loop gets `<out>/<label>.json` with one
row for each seed (differences, findings, excused commands, seconds), and for
each seed that failed the whole result as `<out>/<label>/<seed>.json`, with the
streams under `<out>/<label>/<seed>/<stage>/`. `<out>/perturb.json` records the
run: the mode, the seeds, the trees, how many failed, and the findings of the
whole run. A corpus that failed keeps its perturbed snapshot under
`<work>/perturb/runs/<label>/<seed>/`, because its checkout may move on; a tree
of a loop is written again from its seed.

`container` runs one command in the Linux container (Container, below) and exits
with that command's exit code. It exits 2 with a message when it is given no
command, when `--commit` names no commit, and when Docker is not installed or
its daemon does not answer.

The sections below are the rules of the harness, one part each: the oracle,
sides, snapshots, comparison, caches, the matrix, the fixture families, the
perturbation, the registry, the self-check, the CLI suite, the replay, with the
account a whole replay gives of v1's tests and kinds, and the container.

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
- **Sides.** v1 has two sides, and both run the oracle commit. `oracle` defines
  parity. `campaign` is the commit as the consumers of 1.9.x run it: their
  interpreter version and their package versions. Each side has a venv in the
  checkout, `.venv/<side>`: a uv-managed interpreter of the named version;
  exactly the packages its constraints file pins; and the checkout itself
  installed in place, so the side imports `wiki` from the checkout.
- **Interpreter build.** The named version is the same CPython release on every
  host, but not the same build: uv installs the build its own release lists and
  keeps one it has already installed. The build and the SQLite inside it can
  therefore differ between two hosts. Nothing names them, and the result file
  records both for each v1 side.
- **Pins.** A constraints file pins every package of its venv, one
  `name==version` per line. `oracle.toml` names what a package pin cannot: the
  interpreter, its Unicode tables, and the libyaml inside PyYAML's C loader.
  Both sides pin the PyYAML built on libyaml 0.2.5, the loader build the
  consumers run, so the loader is no difference between the two sides.
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
  interpreter is made of; `judge.py`, which answers the classifiers; and
  `tap.py`, which names the kinds a tree shows), never an import of `parity`.
  The pytest plugins of the CLI suite and of the replay are loaded there by
  names of their own (`parity_suite`, `parity_record`), from a folder that holds
  the harness's plugins and nothing else.

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
- **Where they run.** `base` and `removed` depend on the tree alone and run on
  every pull request. `level` changes when v1 gains a commit, so it runs where a
  person or an agent reads it: at the start of a work session and at each gate.

## Sides

A side has a name, an argv prefix, variables of its own, a `notice` flag, an
optional `describe` command, an optional `judge` command, an optional `tap`
command, and an optional identity. The harness appends the wiki arguments to the
prefix. The environment is built from nothing:

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
  runs `parity/facts.py`, its `judge` command `parity/judge.py`, and its `tap`
  command `parity/tap.py` (Perturbation, Kinds). Only a v1 side has a tap.
- **Identity.** A side with an identity gets `WIKI_TRACE=<file>` on every
  command, a fresh file outside the slot. Before it runs the command, the side
  appends one line of JSON to the file: `engine`, `version` and `build`. For a
  v1 side the launcher writes it, with the engine `python`, the package's
  version and the commit its checkout's `HEAD` holds, and takes the variable out
  of the command's environment. A command that leaves anything but the expected
  record, once, is an `identity` difference: the side is not the engine it
  names.

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
  commit and no checkout in a clone. Its one git write is the index refresh.
- **Cold stages.** A stage marked cold removes `<root>/.wiki/cache` from its
  clone before the first command.

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
  difference and the run does not pass; another `--seed` draws another sample.

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

The corpora and the fixture bases are converged: `update` writes almost nothing
there and `lint` reports almost nothing. A perturbation changes a tree by a
seed, the way hands, tools and merges change a wiki, so that the read-only
commands have something to report and `update` something to take in.
`python -m parity perturb` perturbs trees and runs each as a corpus. The
operators are in `parity/perturb.py`. A perturbation runs no engine: it edits
files, and what the engines make of the edits is what the run compares.

- **Seed.** One seed, one tree and one path for the wiki to stand at give one
  perturbation, byte for byte. Each operator draws from a generator of its own,
  seeded with the seed and the operator's name, so a new operator moves no
  other's draws. The seed is also the seed of the matrix sample.
- **Every operator, every tree.** Each operator applies once or twice on every
  tree; the seed draws how often and where. The one operator that needs a
  repository is left out on a tree without one.
- **What an operator draws.** A page or an index as the engine left it: a page
  with a closed block that carries a `name`, and an index with such a block, a
  delimiter and a row. The operators that rewrite a block, a body under a kept
  stamp or a row each take a file that no other of them has taken. Those that
  add a link to a body or a row to an index draw among all, and their changes
  add up. A tree that runs short gets new pages and folders, in the form the
  engine writes under its default settings. Dot folders, symlinks, a wiki
  declared inside the wiki and names that open with a dash are never drawn.
- **Names.** A name the perturbation makes is `perturbed` and a number, which
  the default naming policy and one that asks for identifiers both take. The two
  invalid names end in `[`, which is link grammar and so denied by every policy.
- **Order.** A folder is renamed first, before anything else is drawn. An index
  is cut short next, and no later operator adds to its folder: an index that was
  cut takes no row, so a file added beside it would show less than its operator
  names.
- **Times.** Every file the perturbation writes gets one of two fixed
  modification times: long before any stamp a wiki holds, so the file reads as
  stamped after its last edit, or long after, so it reads as edited since. The
  seed draws which, and a file left late stays late. The first `dirty-edit` of a
  tree is late.
- **Three trees.** The operators without a stage leave the tree `edited`. Two
  more trees come from it, each by one staged operator: `unindexed`, where every
  index is deleted and `update` builds each again, and `conflicted`, where a
  page and an index end in merge conflict markers and `update` refuses. A stage
  of the perturbation makes its change on every tree it runs on, before the
  first command, and every file of such a tree is hashed at the first scan.
- **Corpus mode.** `perturb corpus` snapshots a corpus, perturbs the snapshot
  and runs the stages of the matrix (Matrix) on the edited tree, then the stages
  `unindexed` and `conflicted`. It runs once for each corpus, under a fresh seed
  that the result file records, or under the seed `--seed` names.
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
  up, each once, with at least one. A fresh seed is a whole number below 2^32.
- **Sides.** With no `--engine` both sides are v1, under `PYTHONHASHSEED=1` and
  `PYTHONHASHSEED=2`, and a command the registry excuses fails the run. With
  one, the runs are v1, the engine, and the engine with `WIKI_THREADS=1`, and
  what the registry excuses passes.

| Stage        | Tree                                         | Commands                                                                                                                                                                                                                                                                                    |
| ------------ | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `edited`     | the perturbed snapshot, in a loop            | `lint --json`, `lint`, `update --check --full`, `map`, a `search --json` and a `match --lines` for the word the perturbation's own text holds, a `search --tag` for a word and the tag of a page it tagged, a `read` of a page it edited, `update --full`, `update --check`, `lint --json`. |
| `unindexed`  | the perturbed snapshot without any index     | `lint --json`, `update --check --full`, `update --full`, `update --check`, `lint --json`, `map`.                                                                                                                                                                                            |
| `conflicted` | the perturbed snapshot with conflict markers | `lint --json`, `update --check` and `update`, which both must exit 2, `map`.                                                                                                                                                                                                                |

| Operator            | Change                                                                                                                          | Notice kinds it names    | Issue kinds it names     |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------- | ------------------------ | ------------------------ |
| `rename-folder`     | renames an indexed folder below the root, with all it holds                                                                     | `link_add`               | `requires_update`        |
| `truncate-index`    | cuts an index short, inside its block or before it                                                                              | `index_truncated`        | `truncated_index`        |
| `dirty-edit`        | adds a paragraph to a page and leaves its stamp; where git tracks the page and the edit is late, the kinds are named            | `restamp`                | `requires_update`        |
| `strip-frontmatter` | takes the block off a page                                                                                                      | `page_adopt`             | `bare_page`              |
| `blank-stamps`      | leaves both stamps of a page with no value                                                                                      |                          | `requires_update`        |
| `shuffle-keys`      | puts the fields of a page's block in another order                                                                              |                          | `requires_update`        |
| `add-tags`          | gives a page two tags, one of them the perturbation's own, so a search by tag finds a page                                      |                          |                          |
| `missing-desc`      | puts the placeholder `...` in place of a page's desc                                                                            | `desc_missing`           |                          |
| `long-desc`         | gives a page a desc of more than 500 characters                                                                                 | `desc_long`              |                          |
| `bare-file`         | adds a page with no block                                                                                                       | `page_adopt`, `link_add` | `bare_page`              |
| `crlf-file`         | adds a page whose lines end in CR LF                                                                                            | `crlf_notice`            |                          |
| `other-file`        | adds a file that is no page                                                                                                     | `link_add`               |                          |
| `stale-link`        | adds a link to a page that does not exist to a body                                                                             | `link_stale`             |                          |
| `directory-link`    | adds a link that names a folder, not its index, to a body                                                                       |                          | `directory_link`         |
| `absolute-link`     | adds a link to a page by its absolute path, as the wiki stands when the commands run, to a body                                 |                          | `absolute_link`          |
| `outside-link`      | adds a link that leaves the wiki to a body                                                                                      |                          | `outside_link`           |
| `region-marker`     | adds the end of a region that never started to a body                                                                           |                          | `region_marker`          |
| `invalid-page`      | adds a page whose name ends in `[`                                                                                              | `name_skip`              | `invalid_page_name`      |
| `invalid-folder`    | adds a folder whose name ends in `[`, with a page inside                                                                        | `name_skip`              | `invalid_folder_name`    |
| `drift-row`         | changes the desc of a row of an index: a row whose page has a desc of its own, which the engine writes over the row's           | `desc_overwrite`         | `requires_update`        |
| `drop-row`          | takes a row out of an index: a row whose entry stands, so the engine adds it again                                              | `link_add`               | `requires_update`        |
| `broken-row`        | adds a row for a page that does not exist to an index                                                                           | `link_prune`             | `broken_link`            |
| `symlink-row`       | adds a symlink to a page, and a row for it                                                                                      | `symlink_skip`           | `symlink_link_target`    |
| `excluded-row`      | adds a page, a row for it, and an exclude pattern that names the page to the wiki's settings, which it makes if absent          | `exclude_skip`           | `excluded_link_target`   |
| `gitignored-row`    | adds a page, a row for it, and a pattern that names the page to the `.gitignore` at the top of the checkout; needs a repository | `gitignore_skip`         | `gitignored_link_target` |
| `delete-indexes`    | deletes every index; staged, on `unindexed`                                                                                     | `index_create`           | `missing_index`          |
| `conflict-markers`  | ends a page and an index in the lines a merge leaves around a conflict; staged, on `conflicted`                                 |                          | `conflict_markers`       |

### Kinds

A perturbation is held to what its operators name: a change that does not show
its kind compares less than the table says.

- **Tap.** A command line prints the text of a notice and not its kind. So the
  tap of the reference side (`parity/tap.py`) runs `lint` and then `update` on
  the wiki inside v1's interpreter, as the command line runs them, and prints
  the kind of each issue and of each notice they met, and the text of the error
  if `update` refused. It runs once on each of the three trees: a fresh clone of
  the snapshot at the path every side runs at, changed as the tree's stage
  changes it. The tap writes to that clone, and no run is compared on it.
- **Named kinds.** Each change names the kinds it put into its tree, where the
  table says it does. A named kind that the tap does not meet on that tree is a
  `kind` finding, and the run fails: the operator did not do what it names.
- **One home.** The kinds the operators name are exactly the kinds whose home in
  `parity/kinds.toml` is `perturbation`. A run exits 2 on a list that gives a
  kind that home and no operator names it, and on an operator that names a kind
  whose home is a test.
- **Whole run.** A run that names no tree (no `--corpus`, no `--family`) also
  holds the kind list to what its trees showed: a kind whose home is the
  perturbation and that no tree of the run shows is a `kind` finding.
- **Limits.** A wiki whose settings make a stale link an issue shows
  `link_stale` as an issue, and the notice the operator names is then a finding.
  No corpus and no small tree has such settings.

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
  from v1. Two builds of v1 have nothing registered between them: the campaign
  side against the oracle, or one release against the next. A `matrix` run
  therefore fails on a command the registry excuses, and lists it as
  `registered`, unless `--registered` says that side `b` is a second engine. The
  self-check and its doctored checks run one engine and fail the same way.
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
  error line (D1) waits for a `_merge` command in the harness, which runs none.
  A pattern past the compiled-size limits, and nesting past the depth limit
  (D3), are limits of the second engine's regex library, and the judge's walk
  does not hold them.
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
