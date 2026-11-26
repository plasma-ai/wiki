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

The sections below are the rules of the harness, one part each: the oracle,
sides, snapshots, comparison, caches, the matrix, the registry and the
self-check.

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
  interpreter is made of; and `judge.py`, which answers the classifiers), never
  an import of `parity`.

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
optional `describe` command, an optional `judge` command, and an optional
identity. The harness appends the wiki arguments to the prefix. The environment
is built from nothing:

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
  runs `parity/facts.py`, and its `judge` command `parity/judge.py`.
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
  the manifest unchanged, so the two rules see different things.
- **Read-only commands.** `lint`, `update --check`, `read`, `match`, `map` and
  `search` may touch nothing outside `<root>/.wiki/cache`. This is checked on
  each side by itself.
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
