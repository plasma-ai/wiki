# wiki

[![build](https://github.com/plasma-ai/wiki/actions/workflows/build.yaml/badge.svg)](https://github.com/plasma-ai/wiki/actions/workflows/build.yaml)
[![docs](https://github.com/plasma-ai/wiki/actions/workflows/docs.yaml/badge.svg)](https://github.com/plasma-ai/wiki/actions/workflows/docs.yaml)
[![lint](https://github.com/plasma-ai/wiki/actions/workflows/lint.yaml/badge.svg)](https://github.com/plasma-ai/wiki/actions/workflows/lint.yaml)
[![tests](https://github.com/plasma-ai/wiki/actions/workflows/tests.yaml/badge.svg)](https://github.com/plasma-ai/wiki/actions/workflows/tests.yaml)
[![codecov](https://codecov.io/gh/plasma-ai/wiki/branch/main/graph/badge.svg?token=D8LJA7CZ2K)](https://codecov.io/gh/plasma-ai/wiki)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)

Indexed knowledge bases with command-line tools for agents.

---

**Source**: [https://github.com/plasma-ai/wiki](https://github.com/plasma-ai/wiki)

**Package**: [https://pypi.org/project/plasma-wiki/](https://pypi.org/project/plasma-wiki/)

**Documentation**: [https://docs.plasma.ai/wiki](https://docs.plasma.ai/wiki)

---

## Installation

Install the `wiki` CLI from PyPI:

```bash
pipx install plasma-wiki
```

(`pip install plasma-wiki` or `uv tool install plasma-wiki` work too.)

### Skill

Install the `/wiki` skill for your agent via the
plugin marketplace (Claude Code and Codex):

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

Basic usage:

```python
import wiki
```

## Development

### Install

Run `install.sh` in the package root. With no environment active it
creates and uses a local `.venv`; with one active (e.g. pyenv) it syncs
into that:

```bash
./install.sh --all-extras --groups=test,lint,type
```

Run tools with `uv run <command>`, or activate the environment first
(`source .venv/bin/activate`). Run `./install.sh --help` for all
options.

Alternatively, run
`uv sync --all-extras --group test --group lint --group type` and
`uv run pre-commit install` to set up the environment manually.

Installing a dependency as editable (e.g. a sibling package) is left to
the caller: `uv pip install --editable <path>`.

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
