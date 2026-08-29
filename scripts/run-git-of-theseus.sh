#!/usr/bin/env bash
# Render Git-of-Theseus repository-history statistics for this checkout.
#
# Outputs are written to ./git-of-theseus/ by default (gitignored). Override
# with GIT_OF_THESEUS_OUTDIR, GIT_OF_THESEUS_INTERVAL_SECONDS, or
# GIT_OF_THESEUS_PROCS when a different view is useful.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

OUTDIR="${GIT_OF_THESEUS_OUTDIR:-$ROOT/git-of-theseus}"
INTERVAL_SECONDS="${GIT_OF_THESEUS_INTERVAL_SECONDS:-86400}"
PROCS="${GIT_OF_THESEUS_PROCS:-4}"

run_git_of_theseus() {
    local command_name="$1"
    shift
    if command -v "$command_name" >/dev/null 2>&1; then
        "$command_name" "$@"
        return
    fi
    if command -v uv >/dev/null 2>&1; then
        uv tool run --from git-of-theseus "$command_name" "$@"
        return
    fi
    echo "Required command not found: $command_name" >&2
    echo "Install git-of-theseus or uv, then rerun this script." >&2
    exit 1
}

mkdir -p "$OUTDIR"

echo "Analyzing repository history..." >&2
run_git_of_theseus git-of-theseus-analyze . \
    --interval "$INTERVAL_SECONDS" \
    --procs "$PROCS" \
    --ignore-whitespace \
    --outdir "$OUTDIR"

echo "Rendering repository-history plots..." >&2
run_git_of_theseus git-of-theseus-line-plot \
    "$OUTDIR/authors.json" \
    --outfile "$OUTDIR/authors-line.png"
run_git_of_theseus git-of-theseus-line-plot \
    "$OUTDIR/authors.json" \
    --normalize \
    --outfile "$OUTDIR/authors-line-norm.png"
run_git_of_theseus git-of-theseus-stack-plot \
    "$OUTDIR/cohorts.json" \
    --outfile "$OUTDIR/cohorts-stack.png"
run_git_of_theseus git-of-theseus-stack-plot \
    "$OUTDIR/authors.json" \
    --outfile "$OUTDIR/authors-stack.png"
run_git_of_theseus git-of-theseus-stack-plot \
    "$OUTDIR/authors.json" \
    --normalize \
    --outfile "$OUTDIR/authors-stack-norm.png"
run_git_of_theseus git-of-theseus-stack-plot \
    "$OUTDIR/exts.json" \
    --outfile "$OUTDIR/ext-stack.png"
run_git_of_theseus git-of-theseus-survival-plot \
    "$OUTDIR/survival.json" \
    --outfile "$OUTDIR/survival.png"

echo "Git-of-Theseus outputs: $OUTDIR" >&2
