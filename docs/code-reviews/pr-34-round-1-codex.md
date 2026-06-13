Codex review:

# Review: PR #34 rename restore_claude_history.py -> restore_claude_code.py

Date: 2026-06-13
Reviewed: PR #34 at b42159f607ad76dd2f31822220f15203fe69e577
Round: 1
Label applied: changes-requested

## What Is Correct
- `git diff --name-status origin/main...HEAD` matches the directive's Section 2 scope: the 13 implementation-target files, the `restore_claude_history.py` -> `restore_claude_code.py` rename, plus the follow-up `.gitignore` cleanup. No extra implementation files were touched.
- The allowlist check is correct. `git ls-files '*.py' '*.md' '*.sh' | xargs rg -n "restore_claude_history"` returns hits only in `docs/directives/rcb-v1.2-rename-restore-claude-code-2026-06-12.md` and prior review artifacts under `docs/code-reviews/`.
- The renamed script is internally coherent: the docstring name is updated at `restore_claude_code.py:3`, `__version__` is `1.2.0` at `restore_claude_code.py:19`, and `--version` reports `%(prog)s 1.2.0` via `argparse` at `restore_claude_code.py:455`. Local spot-check: `python3 restore_claude_code.py --version` prints `restore_claude_code.py 1.2.0`.
- The cleanup follow-up is scoped correctly. Commit `b42159f` only deletes tracked `qemu.stderr` and adds `qemu.stderr` to `.gitignore:9`.
- Local verification succeeded: `python3 -m pytest -x -q` passes 93/93 tests.

## Blockers
- `README.md:3` still advertises `v1.1.0` in the repo's opening status line. This PR updates the script version to `1.2.0`, targets tag `linux/v1.2.0`, and is being validated on the basis that no stale version strings remain. Leaving the top-level README on `v1.1.0` keeps a user-facing stale version marker in the branch, so the versioning part of the rename/release sweep is not complete.

## What Needs Attention
None.

## Bloat / Non-Functional
None.

## Recommendations
- Update `README.md:3` to either say `v1.2.0` or remove the release number from that sentence entirely if the intent is to describe capability rather than the current release.
- After that edit, rerun the repo-wide version-string grep used in this review (`git ls-files | xargs rg -n "1\\.1\\.0|1\\.2\\.0|v1\\.1\\.0|v1\\.2\\.0"`) to confirm only intentional historical references remain.

## Bottom Line
The rename itself is disciplined and mechanically correct: the directive's 13-file target list was honored, the new entrypoint name is coherent inside the script, the old filename survives only in the directive/review allowlist, and the `qemu.stderr` cleanup commit is narrowly scoped. I am not approving this round because the branch still contains one stale user-facing `v1.1.0` reference in `README.md:3`.
