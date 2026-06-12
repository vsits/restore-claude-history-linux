Codex review: APPROVE for PR #33.

# Review: PR #33 — docs: v1.2 port directive (script rename) + boundary tightening

Date: 2026-06-12
Reviewed: `docs/directives/rcb-v1.2-rename-restore-claude-code-2026-06-12.md`, PR #33 description at `59dd6771fb99a9c1b515d5b4c46845e3cfff606e`
Round: 3
Label applied: approved-by-codex-agent

## Findings

| Severity | Path | Summary |
|---|---|---|
| Low | PR #33 description | The PR-body NFR still says `rename + 20 references`, while the directive now correctly pins the historical survey at 22 grep line matches across the 13 implementation-target files. This no longer blocks the directive itself, but the description should be synced so the required inline PR summary does not drift from the reviewed artifact. |

## What Is Correct

- At `87417e8`, the Section 2 file list does sum to 22 grep line matches across 13 implementation-target files, and the full tracked-file survey sums to 31 line matches across 15 files once `restore_claude_history.py` and the v1.2 directive are included. I re-ran both counts.
- The directive no longer pins a live self-count. The two places that previously hard-coded `8` now explicitly leave the directive's own historical-name mentions unpinned, which is the right fix because the live branch has already grown to 11 matching lines after review prose and artifacts.
- Keying the 22/13 figure to historical head `87417e8` makes the size budget stable across future review rounds and future Codex artifacts. New review text can grow without invalidating the implementation-target survey.
- No new directive-side inconsistencies were introduced by `59dd677`.

## Blockers

None.

## What Needs Attention

- If you want the PR's discoverability surface fully aligned before merge, update the PR description's maintainability bullet from `20` to `22` references.

## Bloat / Non-Functional

None.

## Recommendations

- Keep future exact count claims keyed either to an immutable survey point (as done here with `87417e8`) or to a fixed enumerated file list, not to live review-grown document text.

## Bottom Line

The remaining round-2 HIGH is resolved. The directive's count story is now internally consistent on the reviewed surface and stable against future review-driven growth. I am approving the PR; the only remaining note is a non-blocking stale number in the PR description.
