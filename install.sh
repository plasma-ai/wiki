#!/usr/bin/env bash
set -euo pipefail

# A thin `uv sync` wrapper: installs the project (editable) plus requested
# extras and dependency groups. With no environment active, uv creates and
# manages a local `.venv`; with one active (e.g. pyenv), it syncs into that
# env pinned to the env's own interpreter (--python) so it never recreates it.

ALL_EXTRAS=false
INSTALL_EXTRAS=()
INSTALL_GROUPS=()

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

usage() {
    cat <<USAGE
Usage: $(basename "$0") [options]

Install the project (editable) plus requested extras and dependency groups.

Options:
    --all-extras         Install all optional dependency extras
    --extras=<extras>    Comma-separated optional dependency extras
    --groups=<groups>    Comma-separated dependency groups (e.g. test,lint)
    --help|-h            Show this help message
USAGE
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all-extras)
            ALL_EXTRAS=true
            shift
            ;;
        --extras | --extras=*)
            if [[ "$1" == *=* ]]; then
                VALUE="${1#*=}"
                shift
            else
                [[ -z "${2:-}" ]] && {
                    echo "Error: --extras requires an argument" >&2
                    usage 1
                }
                VALUE="$2"
                shift 2
            fi
            IFS=',' read -ra ITEMS <<<"$VALUE"
            if [[ ${#ITEMS[@]} -gt 0 ]]; then
                for ITEM in "${ITEMS[@]}"; do
                    [[ -n "$ITEM" ]] && INSTALL_EXTRAS+=("$ITEM")
                done
            fi
            ;;
        --groups | --groups=*)
            if [[ "$1" == *=* ]]; then
                VALUE="${1#*=}"
                shift
            else
                [[ -z "${2:-}" ]] && {
                    echo "Error: --groups requires an argument" >&2
                    usage 1
                }
                VALUE="$2"
                shift 2
            fi
            IFS=',' read -ra ITEMS <<<"$VALUE"
            if [[ ${#ITEMS[@]} -gt 0 ]]; then
                for ITEM in "${ITEMS[@]}"; do
                    [[ -n "$ITEM" ]] && INSTALL_GROUPS+=("$ITEM")
                done
            fi
            ;;
        --help | -h)
            usage
            ;;
        *)
            echo "Error: unknown argument: $1" >&2
            usage 1
            ;;
    esac
done

if [[ "$ALL_EXTRAS" == true ]] && [[ ${#INSTALL_EXTRAS[@]} -gt 0 ]]; then
    echo "Error: --all-extras and --extras are mutually exclusive" >&2
    exit 1
fi

if ! command -v uv &>/dev/null; then
    echo "Error: uv is required (brew install uv)" >&2
    exit 1
fi

# detect an active environment (e.g. pyenv) and its interpreter;
# the extras/groups flags below are shared by both install paths
ARGS=()
VENV_ACTIVE=false
VENV_PYTHON=""
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    VENV_ACTIVE=true
    VENV_PYTHON="$VIRTUAL_ENV/bin/python"
elif [[ -n "${PYENV_VIRTUAL_ENV:-}" ]]; then
    VENV_ACTIVE=true
    VENV_PYTHON="$PYENV_VIRTUAL_ENV/bin/python"
fi
if [[ "$ALL_EXTRAS" == true ]]; then
    ARGS+=(--all-extras)
fi
if [[ ${#INSTALL_EXTRAS[@]} -gt 0 ]]; then
    for EXTRA in "${INSTALL_EXTRAS[@]}"; do
        ARGS+=(--extra "$EXTRA")
    done
fi
if [[ ${#INSTALL_GROUPS[@]} -gt 0 ]]; then
    for GROUP in "${INSTALL_GROUPS[@]}"; do
        ARGS+=(--group "$GROUP")
    done
fi

# with an env active, sync into it pinned to the env's own interpreter
# so uv installs in place instead of recreating it on a version mismatch
# (a genuinely incompatible interpreter errors instead); otherwise let uv
# create and manage a local .venv
if [[ "$VENV_ACTIVE" == true ]]; then
    if [[ ${#ARGS[@]} -gt 0 ]]; then
        uv sync --active --python "$VENV_PYTHON" "${ARGS[@]}"
    else
        uv sync --active --python "$VENV_PYTHON"
    fi
else
    if [[ ${#ARGS[@]} -gt 0 ]]; then
        uv sync "${ARGS[@]}"
    else
        uv sync
    fi
    # activate the freshly-created .venv so the pre-commit step can find it
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# install and enable pre-commit (so the hooks can't be skipped)
uv pip install pre-commit
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    pre-commit install
fi
