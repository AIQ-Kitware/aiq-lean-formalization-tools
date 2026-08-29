#!/usr/bin/env bash
# Put a Lean build cache on local disk and bind-mount it over the repo's .lake.
#
# WHY: this repo is shared with the host over virtiofs. lake does an enormous
# number of small reads and writes into .lake, and on virtiofs that is slow
# enough to dominate a build. The fix is to keep .lake on the VM's own ext4
# disk and mount it over the repo's .lake.
#
# WHY A BIND MOUNT AND NOT A SYMLINK: a symlink would be a real file in the
# shared tree, so the host would see it too, and it would dangle there --
# breaking host-side `lake build`, editors and tooling. A bind mount changes
# nothing on disk. The host's own .lake stays exactly where it was, underneath
# the mount, untouched and still usable by the host. Only this VM sees the
# substitution.
#
# CONSEQUENCE WORTH KNOWING: the two caches are then independent. Builds run in
# the VM do not warm the host's cache, and vice versa. That is the price of
# letting both sides build at once without fighting over the same oleans.
#
# The mount does not survive a reboot. Re-apply everything with `--all`.
#
# Usage:
#   setup-lake-cache.sh [REPO...]      set up (default: git root of cwd)
#   setup-lake-cache.sh --status       report, change nothing
#   setup-lake-cache.sh --unmount      remove the mount; host .lake reappears
#   setup-lake-cache.sh --all          re-apply every cache recorded under the
#                                      cache root (use after a reboot)
# Options:
#   --cache-dir DIR    use DIR as the cache instead of deriving one
#   --cache-root DIR   root for derived caches (default /var/cache/lake)
#   --force            proceed despite "already local" or an owner mismatch
set -euo pipefail

CACHE_ROOT=${LAKE_CACHE_ROOT:-/var/cache/lake}
MODE=setup
CACHE_DIR=
FORCE=0
REPOS=()

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*"; }

usage() { sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; $d'; }

while [ $# -gt 0 ]; do
  case $1 in
    --status)     MODE=status ;;
    --unmount|--umount) MODE=unmount ;;
    --all)        MODE=all ;;
    --force)      FORCE=1 ;;
    --cache-dir)  CACHE_DIR=${2:?--cache-dir needs a path}; shift ;;
    --cache-root) CACHE_ROOT=${2:?--cache-root needs a path}; shift ;;
    -h|--help)    usage; exit 0 ;;
    -*)           die "unknown option: $1 (try --help)" ;;
    *)            REPOS+=("$1") ;;
  esac
  shift
done

# fstypes that are local block storage: a cache here is the whole point.
is_local_fs() {
  case $1 in ext4|ext3|xfs|btrfs|f2fs|zfs) return 0 ;; *) return 1 ;; esac
}

fstype_of() { findmnt -no FSTYPE --target "$1"; }

# Identity of a repo is its device:inode, not its path: the same tree is often
# reachable at several mount paths, and those must share one cache rather than
# silently building two.
tree_id() { stat -c '%d:%i' "$1"; }

resolve_repo() {
  local start=${1:-$PWD} top
  [ -d "$start" ] || die "not a directory: $start"
  if top=$(git -C "$start" rev-parse --show-toplevel 2>/dev/null); then
    realpath "$top"
  else
    realpath "$start"
  fi
}

# Prefer an existing cache that already claims this tree. Without this, a repo
# reachable at two mount paths derives two cache directories from two basenames
# and builds itself twice -- which is the exact situation this box is in, with
# the tree visible at both /home/... and /mnt/shared/repository-copy
cache_dir_for() {
  local repo=$1 id m
  [ -n "$CACHE_DIR" ] && { printf '%s\n' "$CACHE_DIR"; return; }
  id=$(tree_id "$repo")
  shopt -s nullglob dotglob
  for m in "$CACHE_ROOT"/*/owner; do
    if [ "$(sed -n 's/^id=//p' "$m" | head -1)" = "$id" ]; then
      dirname "$m"; return
    fi
  done
  printf '%s/%s\n' "$CACHE_ROOT" "$(basename "$repo")"
}

# The marker records which tree a cache belongs to, so a second repo with the
# same basename cannot quietly adopt another repo's oleans. It also lists every
# path the tree has been mounted at, which is what --all replays.
marker_of() { printf '%s/owner\n' "$1"; }

# Refuse to hand one tree's oleans to another, then remember every path this
# tree is reachable at -- the same repo is often mounted twice, and both paths
# must share the one cache rather than silently building two.
record_marker() {
  local repo=$1 cdir=$2 id=$3 marker owner_id
  marker=$(marker_of "$cdir")
  if [ -f "$marker" ]; then
    owner_id=$(sed -n 's/^id=//p' "$marker" | head -1)
    if [ -n "$owner_id" ] && [ "$owner_id" != "$id" ] && [ "$FORCE" -eq 0 ]; then
      die "cache $cdir belongs to another tree (id $owner_id, this is $id); use --cache-dir or --force"
    fi
  else
    printf 'id=%s\n' "$id" > "$marker"
  fi
  grep -qxF "path=$repo" "$marker" 2>/dev/null || printf 'path=%s\n' "$repo" >> "$marker"
}

report() {
  local repo=$1 dst=$1/.lake src
  printf '%s\n' "$repo"
  printf '  repo fs   : %s\n' "$(fstype_of "$repo")"
  if [ -L "$dst" ]; then
    printf '  .lake     : SYMLINK -> %s (should be a real directory)\n' "$(readlink "$dst")"
  elif [ ! -e "$dst" ]; then
    printf '  .lake     : absent\n'
  elif mountpoint -q "$dst"; then
    src=$(findmnt -no SOURCE --target "$dst")
    printf '  .lake     : bind-mounted\n'
    printf '  backed by : %s (%s)\n' "$src" "$(fstype_of "$dst")"
  else
    printf '  .lake     : plain directory on %s -- NOT cached\n' "$(fstype_of "$dst")"
  fi
}

do_unmount() {
  local repo=$1 dst=$1/.lake
  mountpoint -q "$dst" || { note "not mounted: $dst"; return 0; }
  sudo umount "$dst"
  note "unmounted $dst (the host's .lake is visible again)"
}

do_setup() {
  local repo=$1 dst=$1/.lake src marker id
  src=$(cache_dir_for "$repo")/.lake
  marker=$(marker_of "$(cache_dir_for "$repo")")
  id=$(tree_id "$repo")

  [ -L "$dst" ] && die "$dst is a symlink; remove it first (this script replaces that pattern)"

  # findmnt prints a bind mount as `/dev/vda1[/var/cache/lake/foo/.lake]`, so
  # compare against the bracketed subpath rather than the whole string.
  if mountpoint -q "$dst"; then
    local cur; cur=$(findmnt -no SOURCE --target "$dst")
    case $cur in
      *"[$src]"|"$src")
        # Record the marker even here: a cache mounted by hand (or by an older
        # script) is invisible to --all until its owner file exists, and the
        # first thing anyone wants after a reboot is --all.
        record_marker "$repo" "$(cache_dir_for "$repo")" "$id"
        note "already set up: $dst <- $cur"; return 0 ;;
      *) die "$dst is already mounted from $cur, not $src; --unmount first" ;;
    esac
  fi

  if is_local_fs "$(fstype_of "$repo")" && [ "$FORCE" -eq 0 ]; then
    die "$repo is already on $(fstype_of "$repo"); a cache mount buys nothing (use --force to override)"
  fi

  # A cache on the same slow filesystem as the repo would be pointless.
  sudo mkdir -p "$src"
  sudo chown "$(id -u):$(id -g)" "$(cache_dir_for "$repo")" "$src"
  if [ "$(fstype_of "$src")" = "$(fstype_of "$repo")" ] && [ "$FORCE" -eq 0 ]; then
    die "cache would live on $(fstype_of "$src"), the same filesystem as the repo; pick --cache-root on local disk"
  fi

  record_marker "$repo" "$(cache_dir_for "$repo")" "$id"

  # The mount point must exist. If the repo has no .lake yet we create an empty
  # one; the host will simply see an empty directory until it builds.
  [ -d "$dst" ] || mkdir -p "$dst"

  sudo mount --bind "$src" "$dst"
  mountpoint -q "$dst" || die "mount reported success but $dst is not a mountpoint"
  note "bind-mounted $src -> $dst  ($(fstype_of "$dst") over $(fstype_of "$repo"))"
}

do_all() {
  local found=0 m id repo
  shopt -s nullglob dotglob
  for m in "$CACHE_ROOT"/*/owner; do
    found=1
    while IFS= read -r repo; do
      [ -d "$repo" ] || { note "skip (gone): $repo"; continue; }
      CACHE_DIR=$(dirname "$m")
      do_setup "$repo"
      CACHE_DIR=
    done < <(sed -n 's/^path=//p' "$m")
  done
  [ "$found" -eq 1 ] || note "no caches recorded under $CACHE_ROOT"
}

if [ "$MODE" = all ]; then
  do_all
  exit 0
fi

[ ${#REPOS[@]} -gt 0 ] || REPOS=("$PWD")

for r in "${REPOS[@]}"; do
  repo=$(resolve_repo "$r")
  case $MODE in
    status)  report "$repo" ;;
    unmount) do_unmount "$repo" ;;
    setup)   do_setup "$repo" ;;
  esac
done
