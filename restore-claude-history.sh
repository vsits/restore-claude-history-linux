#!/bin/bash
#
# restore-claude-history.sh
#
# Restore Claude Code chat transcripts (~/.claude/projects/<project>/*.jsonl)
# from macOS Time Machine APFS snapshots. For each (project, filename) pair
# across all snapshots, picks the largest version (JSONLs are append-only)
# and copies it back, preserving mtime and stripping the inherited TM ACL.
#
# macOS + APFS Time Machine only. Bash 3.2 compatible (ships with macOS).
# Requires Full Disk Access for the terminal/IDE running this.
#
# See NOTES.md in this repo for background.

set -u

# -------- defaults --------
DRY_RUN=0
VERBOSE=0
INCLUDE_MEMORY=0
ONLY_PROJECT=""

CLAUDE_DIR="$HOME/.claude/projects"
TMP_PREFIX="/tmp/tm-claude-restore"
INDEX_FILE=""
MOUNTS_FILE=""

# -------- usage --------
usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Restore deleted Claude Code chat transcripts from Time Machine APFS snapshots.

Options:
  --dry-run             Show what would be restored; copy nothing.
  --project <name>      Limit to one encoded project dir
                        (e.g. -Users-you-projects-foo). Default: all projects.
  --include-memory      Also restore <project>/memory/ subdirs.
  --verbose             Log every file decision, not just the summary.
  -h, --help            Show this help.

Requires: macOS, APFS Time Machine drive mounted, Full Disk Access.
EOF
}

# -------- arg parsing --------
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --verbose) VERBOSE=1 ;;
    --include-memory) INCLUDE_MEMORY=1 ;;
    --project)
      shift
      [ $# -eq 0 ] && { echo "error: --project needs a value" >&2; exit 2; }
      ONLY_PROJECT="$1"
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

log()  { echo "$*"; }
vlog() { [ $VERBOSE -eq 1 ] && echo "$*"; return 0; }
die()  { echo "error: $*" >&2; exit 1; }

# -------- cleanup trap --------
cleanup() {
  local rc=$?
  if [ -n "${MOUNTS_FILE:-}" ] && [ -f "$MOUNTS_FILE" ]; then
    while IFS= read -r mp; do
      [ -z "$mp" ] && continue
      if mount | grep -q " on $mp "; then
        diskutil unmount "$mp" >/dev/null 2>&1 || diskutil unmount force "$mp" >/dev/null 2>&1 || true
      fi
      [ -d "$mp" ] && rmdir "$mp" 2>/dev/null || true
    done < "$MOUNTS_FILE"
    rm -f "$MOUNTS_FILE"
  fi
  [ -n "${INDEX_FILE:-}" ] && rm -f "$INDEX_FILE"
  exit $rc
}
trap cleanup EXIT INT TERM

# -------- preflight --------
[ "$(uname)" = "Darwin" ] || die "macOS only."
command -v diskutil   >/dev/null || die "diskutil not found."
command -v mount_apfs >/dev/null || die "mount_apfs not found."

# -------- find the TM disk device --------
# Look for an APFS volume on a disk that has a Time Machine role.
# Strategy: ask diskutil for all APFS volumes, then check each for the
# TimeMachine role via `diskutil info`.
find_tm_device() {
  local dev
  # All APFS data volumes currently visible.
  for dev in $(diskutil list | awk '/APFS Volume/ {print $NF}' | grep -E '^disk[0-9]+s[0-9]+$'); do
    if diskutil info "/dev/$dev" 2>/dev/null | grep -qi "Time Machine"; then
      echo "$dev"
      return 0
    fi
  done
  return 1
}

TM_DEV="$(find_tm_device || true)"
[ -n "$TM_DEV" ] || die "No Time Machine APFS volume detected. Plug in your TM drive and try again."
log "Time Machine volume: /dev/$TM_DEV"

# -------- list snapshots --------
SNAPSHOTS="$(diskutil apfs listSnapshots "/dev/$TM_DEV" 2>/dev/null | awk -F': *' '/Name:/ {print $2}')"
[ -n "$SNAPSHOTS" ] || die "No APFS snapshots found on /dev/$TM_DEV."

SNAP_COUNT=$(printf '%s\n' "$SNAPSHOTS" | wc -l | tr -d ' ')
log "Found $SNAP_COUNT snapshots."

# -------- mount each snapshot --------
MOUNTS_FILE="$(mktemp -t tm-claude-mounts)"
INDEX_FILE="$(mktemp -t tm-claude-index)"

# Format of $INDEX_FILE lines:
#   <size>\t<project>\t<filename>\t<absolute-src-path>
# Tab separated; project & filename should not contain tabs.

USER_SHORT="$(id -un)"

i=0
while IFS= read -r snap; do
  [ -z "$snap" ] && continue
  i=$((i + 1))
  label=$(echo "$snap" | sed -E 's/^com\.apple\.TimeMachine\.//; s/\.backup$//')
  mp="${TMP_PREFIX}-${i}-${label}"
  mkdir -p "$mp" || die "cannot mkdir $mp"
  echo "$mp" >> "$MOUNTS_FILE"

  vlog "Mounting $snap at $mp"
  if ! mount_apfs -s "$snap" "/dev/$TM_DEV" "$mp" 2>/dev/null; then
    log "  warn: failed to mount $snap, skipping"
    continue
  fi

  # Inside the mount, the backup data lives at <mp>/<timestamp>.backup/Data/...
  # Locate the projects dir.
  backup_root=""
  for d in "$mp"/*.backup; do
    [ -d "$d" ] || continue
    backup_root="$d"
    break
  done
  [ -n "$backup_root" ] || { vlog "  no *.backup dir under $mp"; continue; }

  projects_root="$backup_root/Data/Users/$USER_SHORT/.claude/projects"
  if [ ! -d "$projects_root" ]; then
    vlog "  no projects dir at $projects_root"
    continue
  fi

  # Walk projects.
  for proj_path in "$projects_root"/*; do
    [ -d "$proj_path" ] || continue
    proj_name=$(basename "$proj_path")
    if [ -n "$ONLY_PROJECT" ] && [ "$proj_name" != "$ONLY_PROJECT" ]; then
      continue
    fi
    # Index top-level JSONLs.
    for f in "$proj_path"/*.jsonl; do
      [ -e "$f" ] || continue
      fname=$(basename "$f")
      size=$(stat -f%z "$f" 2>/dev/null || echo 0)
      printf '%s\t%s\t%s\t%s\n' "$size" "$proj_name" "$fname" "$f" >> "$INDEX_FILE"
    done
  done
done <<EOF
$SNAPSHOTS
EOF

[ -s "$INDEX_FILE" ] || die "No Claude JSONL files found in any snapshot for user '$USER_SHORT'."

# -------- pick largest version per (project, filename) --------
# Sort by project, filename, then size descending. First row per group wins.
PICK_FILE="$(mktemp -t tm-claude-pick)"
sort -t $'\t' -k2,2 -k3,3 -k1,1nr "$INDEX_FILE" \
  | awk -F'\t' 'BEGIN{OFS="\t"} {key=$2 SUBSEP $3; if (!(key in seen)) {seen[key]=1; print}}' \
  > "$PICK_FILE"

PICK_COUNT=$(wc -l < "$PICK_FILE" | tr -d ' ')
log "Indexed $PICK_COUNT unique (project, jsonl) pairs across snapshots."

# -------- restore loop --------
restored=0
skipped=0
bytes=0

while IFS=$'\t' read -r src_size proj_name fname src_path; do
  dest_dir="$CLAUDE_DIR/$proj_name"
  dest="$dest_dir/$fname"

  if [ -e "$dest" ]; then
    dst_size=$(stat -f%z "$dest" 2>/dev/null || echo 0)
    if [ "$dst_size" -ge "$src_size" ]; then
      vlog "skip  $proj_name/$fname (on-disk $dst_size >= snapshot $src_size)"
      skipped=$((skipped + 1))
      continue
    fi
  fi

  if [ $DRY_RUN -eq 1 ]; then
    log "would restore $proj_name/$fname ($src_size bytes) from $src_path"
    restored=$((restored + 1))
    bytes=$((bytes + src_size))
    continue
  fi

  mkdir -p "$dest_dir" || { log "  fail: mkdir $dest_dir"; continue; }
  if [ -e "$dest" ]; then
    chmod -N "$dest" 2>/dev/null || true
    chmod u+w "$dest" 2>/dev/null || true
  fi
  if ! cp "$src_path" "$dest" 2>/dev/null; then
    log "  fail: cp $src_path -> $dest"
    continue
  fi
  touch -r "$src_path" "$dest" 2>/dev/null || true
  chmod -N "$dest" 2>/dev/null || true
  chmod u+w "$dest" 2>/dev/null || true

  vlog "restore $proj_name/$fname ($src_size bytes)"
  restored=$((restored + 1))
  bytes=$((bytes + src_size))
done < "$PICK_FILE"

# -------- subagent subdirectories --------
# For each project, copy any session-uuid/subagents dirs that don't already
# exist on disk. We iterate snapshots in newest-first order so the freshest
# version of a subagents dir wins.
copy_subdir_tree() {
  local src="$1"
  local dst="$2"
  [ -d "$src" ] || return 0
  if [ -e "$dst" ]; then
    return 0
  fi
  if [ $DRY_RUN -eq 1 ]; then
    log "would restore subdir $dst (from $src)"
    return 0
  fi
  cp -R "$src" "$dst" 2>/dev/null || { log "  fail: cp -R $src -> $dst"; return 1; }
  find "$dst" -exec chmod -N {} \; 2>/dev/null
  find "$dst" -exec chmod u+w {} \; 2>/dev/null
  vlog "restore subdir $dst"
}

# Build a newest-first list of mounted snapshot project roots.
# Mount labels embed the timestamp, so reverse-sorting the mount list
# (which we wrote in mount order) by label works.
SORTED_MOUNTS="$(sort -r "$MOUNTS_FILE")"

while IFS= read -r mp; do
  [ -z "$mp" ] && continue
  backup_root=""
  for d in "$mp"/*.backup; do
    [ -d "$d" ] || continue
    backup_root="$d"
    break
  done
  [ -n "$backup_root" ] || continue
  projects_root="$backup_root/Data/Users/$USER_SHORT/.claude/projects"
  [ -d "$projects_root" ] || continue

  for proj_path in "$projects_root"/*; do
    [ -d "$proj_path" ] || continue
    proj_name=$(basename "$proj_path")
    if [ -n "$ONLY_PROJECT" ] && [ "$proj_name" != "$ONLY_PROJECT" ]; then
      continue
    fi
    dest_proj="$CLAUDE_DIR/$proj_name"
    mkdir -p "$dest_proj" 2>/dev/null || true

    # Per-session subagents/ subdirectories.
    for sess in "$proj_path"/*/; do
      [ -d "$sess" ] || continue
      sub_name=$(basename "$sess")
      # Skip the memory dir here; handled below.
      [ "$sub_name" = "memory" ] && continue
      copy_subdir_tree "$sess" "$dest_proj/$sub_name"
    done

    # Optionally restore memory/.
    if [ $INCLUDE_MEMORY -eq 1 ] && [ -d "$proj_path/memory" ]; then
      copy_subdir_tree "$proj_path/memory" "$dest_proj/memory"
    fi
  done
done <<EOF
$SORTED_MOUNTS
EOF

# -------- summary --------
echo
if [ $DRY_RUN -eq 1 ]; then
  log "DRY RUN: would restore $restored file(s), $bytes byte(s). Skipped $skipped already-current."
else
  log "Restored $restored file(s), $bytes byte(s). Skipped $skipped already-current."
fi
log "Done."
