# RCB v1.2 directive — rename `restore_claude_history.py` → `restore_claude_code.py`

Status: directive-stage
Author: vsits-restore-claude-builder[bot]
Tracking issue: TBD (link on PR open)
Upstream reference: [`e7fa576`](https://github.com/garrettmoss/restore-claude-history/commit/e7fa576) (`rename: restore_claude_history.py → restore_claude_code.py`)

## Goal

Rename the entrypoint script from `restore_claude_history.py` to `restore_claude_code.py`, and update every in-repo reference (README, NOTES, docs, tests, QEMU harness) to match. Ship as `linux/v1.2.0` with a brief deprecation note in the release body.

## Background

Upstream's `e7fa576` renamed the script as a disambiguation step ahead of adding a second tool (`restore_claude_desktop.py`). We are skipping the desktop tool itself per the boundary discipline (`AGENTS.md` — "Not a Claude Desktop tool"), but the rename is still attractive on its own merits:

- **The new name is more accurate.** What the script restores is *Claude Code transcripts*, not "Claude history" (a phrase that overlaps with web-app chat history, Claude Desktop sessions, and Anthropic API logs). "Claude Code" is the documented product name we restore for.
- **Upstream alignment lowers cherry-pick cost.** Every future upstream commit that touches the entrypoint will land cleaner against a same-named file. Path mismatches force manual three-way merges where there would otherwise be none.
- **No functional change.** Pure rename plus reference updates. Behavior, flags, and on-disk layout are unchanged.

## Non-Functional Requirements

- **Size/complexity budget:** ~25 lines of diff total — one `git mv` plus ~20 reference updates across 11 files (counts from `grep -rn "restore_claude_history" --include="*.py" --include="*.md" --include="*.sh"` at directive time). Review flags any net-new code; this PR adds none.
- **Threat model:** None — the rename does not introduce new inputs, subprocesses, or trust boundaries. The only externally-visible change is the script path users invoke, which is documented in the release notes.
- **Maintainability constraints:** No new abstractions. No back-compat shim, symlink, or wrapper script for the old filename — the deprecation note in the release body is the migration surface (see "Deprecation policy" below). Old git tags (`linux/v1.0.0`, `linux/v1.1.0`) remain valid historical references to the old filename; users on those tags retain the old path.
- **Performance/reliability:** No runtime impact.
- **Load-bearing?** **No.** Rename of a leaf script with no shared-abstraction or wire-contract impact. Backend ABC, `DiscoveredSnapshot`, and the restore loop are untouched. Standard Lead + Codex review applies; no Chris-as-required-approver gate.

## Scope

### 1. Rename the script

```bash
git mv restore_claude_history.py restore_claude_code.py
```

Old `__version__ = "1.1.0"` → `__version__ = "1.2.0"` in the renamed file. No other content changes inside the script.

### 2. Update in-repo references

Mechanical find-replace of the string `restore_claude_history` → `restore_claude_code` across:

- `README.md` (4 references — Quickstart block, script-reference link, two prose mentions)
- `NOTES.md` (3)
- `TODO.md` (2)
- `docs/backends.md` (2)
- `docs/plans/qemu-e2e-plan.md` (2)
- `tests/verify_restore.py` (2 — imports + invocation)
- `tests/test_orchestrator.py` (1 — import line)
- `tests/test_restore_loop.py` (1 — import line)
- `tests/integration/test_zfs_real.py` (1)
- `tests/integration/test_btrfs_real.py` (1)
- `tests/integration/test_timeshift_real.py` (1)
- `docs/directives/rcb-v1-directive-2026-05-28.md` (1 — historical reference; update for consistency but do not rewrite the directive's design narrative)
- `docs/directives/rcb-v1.1-sequential-mount-directive-2026-06-02.md` (1 — same)

Do NOT update historical references in:

- Issue/PR bodies on GitHub. Those are append-only historical records; rewriting them would muddle the audit trail.
- Git commit messages (we don't rewrite history).
- Release notes for `linux/v1.0.0` and `linux/v1.1.0`. Those tags still reference the old filename and should stay accurate to what shipped.

### 3. QEMU e2e harness

The harness invokes the script by path. Update the invocation in `tests/e2e/run.sh` (and any companion scripts under `tests/e2e/`) to use the new filename. Re-run the harness against all three backends before tagging `linux/v1.2.0` — same release-gate discipline as v1.1.

### 4. Release notes

`linux/v1.2.0` release body must include:

- **One-line summary:** "Entrypoint script renamed: `restore_claude_history.py` → `restore_claude_code.py`. No functional changes."
- **Migration hint:** "If you scripted against the old path (e.g. cron job, wrapper script, internal docs), update the path. The previous filename is not preserved as a symlink or shim — see the directive at `docs/directives/rcb-v1.2-rename-restore-claude-code-2026-06-12.md` for the rationale."
- **Upstream attribution:** "Tracks upstream `garrettmoss/restore-claude-history@e7fa576`."

### 5. Deprecation policy (explicit non-decision)

We do **not** ship a symlink, wrapper script, or `setup.py` console-entry shim for the old name. Reasons:

- The project's documented install flow is `git clone` + `python3 <script>`, not `pip install`. There is no package-manager surface where a console-entry alias would be discovered. A symlink at the repo root would work for cloners but not for users who scripted against the old path on a fixed checkout — they hit the rename regardless.
- A shim file at the old path that `exec`s the new one would re-introduce the disambiguation confusion the rename is trying to fix, and would have to be carried indefinitely.
- Old git tags (`linux/v1.0.0`, `linux/v1.1.0`) remain checkout-able at the old filename. Users pinned to a tag are unaffected; users tracking `main` get the rename when they pull.

## Out of scope

- **No back-port to v1.0.x / v1.1.x branches.** This is a forward-only change shipped on `linux/v1.2.0`.
- **No npm publication or `pip` packaging.** Discoverability is a separate workstream (README restructure, awesome-claude lists, GitHub topics — already in flight); the rename is independent.
- **No restore_claude_desktop.py port.** Codified as `skip` per the boundary-discipline addition to `AGENTS.md` in this same PR or a precursor PR.

## Validation

- All 93 existing tests pass after rename (the test imports are part of the reference update).
- QEMU e2e harness passes on ZFS, Btrfs, and Timeshift after rename.
- `python3 restore_claude_code.py --list-backends` runs successfully on the dogfood host.
- `grep -rn "restore_claude_history" --include="*.py" --include="*.md" --include="*.sh"` returns zero hits after the PR (excluding historical references in `docs/directives/` that pre-date the rename — those reference the historical filename intentionally and should be checked manually rather than mass-renamed if the design narrative would otherwise become confusing).

## Rollback

If a user-visible regression surfaces after `linux/v1.2.0` ships, the rollback is to ship `linux/v1.2.1` reverting the rename. The directive's "no shim" policy means there is no compatibility surface to keep; a clean revert is the rollback mechanism. Tags `linux/v1.0.0` and `linux/v1.1.0` remain valid pin points during the rollback window.
