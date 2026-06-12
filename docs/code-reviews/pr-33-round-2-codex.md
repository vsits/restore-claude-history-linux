Codex review: REQUEST_CHANGES for PR #33.

# Review: PR #33 — docs: v1.2 port directive (script rename) + boundary tightening

Date: 2026-06-12
Reviewed: `docs/directives/rcb-v1.2-rename-restore-claude-code-2026-06-12.md`, `docs/code-reviews/pr-33-round-1-codex.md`, `tests/e2e/run.sh`, `tests/integration/test_zfs_real.py`, `tests/integration/test_btrfs_real.py`, `tests/integration/test_timeshift_real.py` at `1527dfd303ca69acb1a7c19b4d30809d4b519a24`
Round: 2
Label applied: needs-changes

## Findings

| Severity | Path | Summary |
|---|---|---|
| High | `docs/directives/rcb-v1.2-rename-restore-claude-code-2026-06-12.md:22`, `docs/directives/rcb-v1.2-rename-restore-claude-code-2026-06-12.md:40`, `docs/directives/rcb-v1.2-rename-restore-claude-code-2026-06-12.md:59`, `docs/directives/rcb-v1.2-rename-restore-claude-code-2026-06-12.md:99` | The round-2 rewrite fixes the file-list/allowlist structure, but the count story is still not internally consistent. I re-ran the historical grep at `87417e8` and confirmed the intended math there: 31 hits across 15 tracked files, with 22 hits across the 13 Section-2 target files after subtracting `restore_claude_history.py` and the directive's then-8 self-references. But the current directive still says the NFR budget is `~30 reference updates across the 13 files`, while Section 2 says those 13 files contain 22 hits total, and the directive now preserves 11 `restore_claude_history` hits on the live branch rather than the `8` claimed in Section 2/Validation. Current tracked-file grep at `1527dfd` returns 37 hits across 16 files because the round-1 review artifact adds 3 hits and the round-2 prose added 3 more literal mentions to this directive. The QEMU fix is correct; this remaining issue is stale exact-count text in the directive itself. |

## What Is Correct

- The historical raw-grep survey now checks out. At `87417e8`, `git ls-files '*.py' '*.md' '*.sh' | xargs rg -n "restore_claude_history"` returns 31 hits across 15 tracked files, and the Section 2 file list does sum to 22 hits across 13 implementation-target files.
- Section 3 is fixed. `tests/e2e/run.sh:273` runs backend-specific pytest targets inside the VM, not a literal script path, and `tests/e2e/` currently contains no `restore_claude_history` or `restore_claude_code` path reference to rename. Routing the effect through the three integration-test imports plus a required harness rerun is the right directive.
- The validation allowlist is materially improved. It now correctly keeps the v1 and v1.1 directives out of the survivor set and explicitly allows Codex review artifacts as append-only history.

## Blockers

1. Make the exact counts agree again, or stop pinning exact counts where the directive text itself is expected to evolve. As written, the surviving-reference count and the NFR's `~30 reference updates` line are both stale relative to the current document.

## What Needs Attention

None beyond the blocker above.

## Bloat / Non-Functional

None.

## Recommendations

- Change the NFR budget at `docs/directives/rcb-v1.2-rename-restore-claude-code-2026-06-12.md:22` to match the exact Section 2 scope: 22 reference replacements across 13 implementation-target files, with the `~40 lines of diff` figure left as the rough diff-size budget.
- At `docs/directives/rcb-v1.2-rename-restore-claude-code-2026-06-12.md:59` and `docs/directives/rcb-v1.2-rename-restore-claude-code-2026-06-12.md:99`, either update the directive-self count from `8` to the current value or remove the exact self-count entirely and keep the allowlist qualitative. The latter is safer because review rounds will keep adding literal historical-name mentions.

## Bottom Line

One of the two round-1 HIGH findings is fully resolved: the QEMU section now matches the real harness behavior. The other is much closer but not fully clean yet. The directive now has the right target-file list and the right allowlist categories, but the exact numbers inside the current document still disagree with each other and with the live branch grep surface. Tighten those last count references, then this is ready to approve.
