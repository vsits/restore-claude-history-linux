# restore-claude-history-linux

Recover deleted Claude Code chat transcripts from Linux filesystem snapshots.

> **Status: beta.** The recovery logic is end-to-end-verified for **ZFS**, **Btrfs**, and **Timeshift** on Ubuntu 24.04 (real kernels, real snapshots, byte-equal restore), and a dogfood pass on Btrfs has confirmed a restored transcript loads and resumes in a fresh Claude Code session. Specifically untested:
> - The e2e harness exercises a single path: unflagged restore of one synthetic transcript from one snapshot. Per-flag status:
>     - `--dry-run` and `--include-memory` have full restore-loop tempdir tests (Layer 2), but never run inside the e2e harness against a real backend.
>     - `--project NAME` has a Layer 1 test of the underlying `index_projects` filter function; its CLI wiring (`Options.project` → restore loop) has no automated test.
>     - `--list-backends` has no automated test coverage at any layer.
> - Unusual home-dir layouts: encrypted home (eCryptfs / fscrypt / ZFS-native), symlinked home across filesystems, NFS-mounted home.
> - Cross-backend overlap-resolution against real backends (e.g. Timeshift-on-Btrfs deduplication) — tracked as [#13](https://github.com/cnighswonger/restore-claude-history-linux/issues/13) for v1.1.
>
> **Reading the rest of this README assuming production-grade is wrong.** Use the tool, but treat each restore as a candidate to verify by hand.

Linux port of [`garrettmoss/restore-claude-history`](https://github.com/garrettmoss/restore-claude-history) (macOS Time Machine). The recovery logic — walk every snapshot, pick the largest version of each transcript, copy it back preserving mtime — is unchanged from upstream. Only the snapshot-discovery layer is replaced, with pluggable backends for the snapshot tools Linux users actually run: **ZFS**, **Btrfs**, and **Timeshift**.

## Background

Claude Code stores chat transcripts as JSONL files under `~/.claude/projects/<encoded-cwd>/`. A cleanup job prunes them after `cleanupPeriodDays` (default: **30 days**, undocumented, no warning). If you haven't changed that setting, you've probably already lost months of conversations.

The Claude Code **CLI** cleanup path verified at the time of this writing (bundle v2.1.88) is a pure file-mtime sweep: any `*.jsonl` whose mtime is older than `now − cleanupPeriodDays` is unlinked, with no metadata-orphan check against the session store. That means raising `cleanupPeriodDays` (next section) is necessary to keep a restored transcript from being re-deleted on the next cleanup pass — but it is **not** a complete safety net. A separate orphan-style risk exists in the Claude **Desktop** session-metadata layer (tracked in [`TODO.md`](TODO.md)), and user reports describe transcripts vanishing even with the setting raised. **Keep backups in addition to both the setting and this tool.**

## Prevention first

Before anything else, add this to `~/.claude/settings.json`:

```json
"cleanupPeriodDays": 36500
```

That's ~100 years. There's no documented upper bound; the schema just wants a positive integer. Do this on every machine you use Claude Code on.

**Set this *and* keep backups — not one or the other.** The setting defangs the documented cleanup, but multiple user reports (e.g. [#41458](https://github.com/anthropics/claude-code/issues/41458)) describe chats vanishing *despite* the flag being set, most often around app updates. That's why this script exists alongside the prevention step, not instead of it.

## Recovery

This script: [`restore_claude_history.py`](restore_claude_history.py)

### Requirements

- Linux with one of:
  - a **ZFS** pool with snapshots (`zfsutils-linux`)
  - a **Btrfs** filesystem with read-only snapshots (`btrfs-progs`)
  - **Timeshift** (RSYNC or BTRFS mode) with at least one snapshot
- Python 3.10+
- `setfacl` / `getfacl` (`acl` package) — used to strip inherited ACLs on restore. Missing or no-op-on-this-fs is fine; the script skips gracefully.
- Snapshot tooling typically requires **root** for full inventory (e.g. `btrfs subvolume list -s`). Run with `sudo` if `--list-backends` shows zero snapshots despite snapshots existing.

### Quickstart

```bash
git clone https://github.com/cnighswonger/restore-claude-history-linux
cd restore-claude-history-linux

# See which backends are available and how many snapshots each found:
python3 restore_claude_history.py --list-backends

# Preview what would be restored, no changes made:
python3 restore_claude_history.py --dry-run --verbose

# Actually restore:
python3 restore_claude_history.py
```

### Flags

| Flag | What it does |
|---|---|
| `--backend {auto,zfs,btrfs,timeshift}` | Which backend to use. Default `auto` (see below). |
| `--list-backends` | Print each backend's availability + discovered snapshot count, then exit. |
| `--dry-run` | Show what would be restored, copy nothing. Always run this first. |
| `--project NAME` | Limit to one encoded project dir (e.g. `--project=-home-you-projects-foo`). Note the `=` — encoded names start with `-`. |
| `--include-memory` | Also restore `<project>/memory/` subdirs. |
| `--verbose` | Log every file decision, not just the summary. |
| `--dest DIR` | Restore into `DIR` instead of `~/.claude/projects` (for testing). |

### `--backend auto` (the default)

`auto` runs every available backend's discovery, deduplicates results, then requires exactly **one** backend to have candidates:

- **Zero backends find snapshots** → exits with "no snapshots found on any backend"; check `--list-backends` and that your snapshot tool is installed.
- **Exactly one backend finds snapshots** → uses it; logs which one.
- **Multiple backends find snapshots** → exits with an ambiguity error listing each backend, its snapshot count, and an example path. Re-run with `--backend <name>` to pick one.

This intentionally fails loud rather than guessing. On a Timeshift-on-Btrfs host, the orchestrator's overlap-resolution rule keeps the Timeshift entry and prunes the Btrfs duplicate (per-snapshot, exact path match) — so a properly-configured Timeshift host won't trigger ambiguity. Explicit `--backend <name>` bypasses dedup and returns that backend's full raw inventory.

### What it does

1. Discovers snapshots via the selected backend (see [`docs/backends.md`](docs/backends.md) for the per-backend mechanics).
2. For each snapshot, locates `.claude/projects/` under the user's home (handles snapshot-of-`/`, snapshot-of-`/home`, snapshot-of-`$HOME` layouts; follows symlinked home paths).
3. For each `(project, filename)`, picks the **largest** version across all snapshots — JSONLs are append-only, so bigger = more complete.
4. Copies it back, **preserving the original mtime** and stripping any inherited ACL via `setfacl -b`.
5. Skips files where your on-disk version is already the same size or larger — active chats are never overwritten with an older snapshot.
6. For per-session subdirs (`subagents/`, optionally `memory/`), the largest subtree wins (independent of backend-defined snapshot ordering, which isn't time-sortable).

### Resuming a restored session

Once the file is back on disk, Claude Code reads `~/.claude/projects/<encoded-cwd>/*.jsonl` directly — there's no metadata layer to repair. But `/resume` and `--continue` behave differently and the difference matters for restored sessions:

- **`/resume` — the right path for restored sessions.** Shows a picker of every session whose internal `cwd` field is the current working directory or one of its descendants. `cd` into the directory the session was originally recorded under (or any ancestor of it) before launching Claude Code. If you restore a transcript that was recorded under `/home/you/projects/foo` and launch from `/tmp`, the session is on disk but won't appear — `/tmp` isn't an ancestor of `/home/you/projects/foo`.
- **`--continue` — usually not what you want for restored sessions.** Auto-picks the session with the newest mtime in the current project dir. Because the tool preserves the original mtime (the only way for the cleanup pass to see the restored file at its true age), a restored old session is typically *older* than your active ones — `--continue` will reopen one of those instead. Use `/resume` and pick the restored session by name.
- **Keep `cleanupPeriodDays` high.** Preserved mtime means the next cleanup pass treats a restored file as if it never left, and will re-delete it after `cleanupPeriodDays` days. The "Prevention first" section above is the fix.

### Verifying it works

End-to-end test that builds synthetic snapshots in tempdirs, runs the restore loop, and checks size/mtime/ACL:

```bash
python3 tests/verify_restore.py
```

Layer 3 integration tests against real ZFS / Btrfs / Timeshift are opt-in:

```bash
# See tests/integration/README.md for per-backend setup.
pytest tests/integration/ -v
```

## See also

If your situation isn't "Linux + filesystem snapshot + JSONLs missing from disk", one of these may help. Grouped by platform.

**macOS:**
- **[garrettmoss/restore-claude-history](https://github.com/garrettmoss/restore-claude-history)** — the upstream this tool was ported from. macOS Time Machine + APFS local snapshots. Use that on macOS; the macOS and Linux trees deliberately don't merge (cross-OS confusion is a leading cause of restore failures in this problem space).
- **[DeveloperAlly/claude-code-survival-toolkit](https://github.com/DeveloperAlly/claude-code-survival-toolkit)** — broader in-app survival kit for the VS Code extension: 9 fix scripts (sidebar dropped sessions, scrambled titles, scrambled sort order, vscode `state.vscdb` snapshot/restore) plus 7 governance hooks. macOS bash; use this if your data is on disk but the extension's sidebar is broken or scrambled.

**Windows:**
- **[BasedGPT/claude-code-session-recovery](https://github.com/BasedGPT/claude-code-session-recovery)** — Windows-specific Claude Desktop metadata repair (orphan JSONLs, junction slug mismatches, missing groupings).

**Cross-platform:**
- **[ibrews/claude-session-recovery](https://github.com/ibrews/claude-session-recovery)** — your JSONLs are still on disk, but Claude Desktop's UI doesn't show them (index corruption after a crash/BSOD). Rebuilds the Desktop session index.
- **[markwoitaszek/claude-session-recovery](https://github.com/markwoitaszek/claude-session-recovery)** — Claude Desktop crashes with "There was a problem with the session" on a specific large/complex chat. Extracts the JSONL to clean Markdown so you don't lose the conversation.

## Further reading

- [`docs/backends.md`](docs/backends.md) — backend interface, per-backend mechanics, and how to add a new backend.
- [`docs/directives/rcb-v1-directive-2026-05-28.md`](docs/directives/rcb-v1-directive-2026-05-28.md) — the v1 design directive, including the `--backend auto` ambiguity semantics and overlap-resolution rules.
- [`NOTES.md`](NOTES.md) — historical context from upstream's macOS recovery work (much of the snapshot-handling and mtime-preservation reasoning carries over).

## License

[MIT](LICENSE)
