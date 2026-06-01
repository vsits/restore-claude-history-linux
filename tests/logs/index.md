# Spotlight harness run log

One row per run. Raw numbers are in the linked `.tsv`. Edit "One-line takeaway" by hand after each run — that column is the whole point of this index.

Filename pattern: `YYYY-MM-DDTHH-MM_<label>.tsv`.

| When | Run | Drive state | One-line takeaway | File |
|---|---|---|---|---|
| 2026-05-29 11:00 | Baseline A (idle, v1) | unplugged | mds=1, mdworker_shared=2→5 drift, CGPDFService=0 — system genuinely quiet | _(pre-logging; numbers in chat history)_ |
| 2026-06-01 10:17 | Baseline A (idle, v2) | **plugged, unmounted** | mds spiked to 24.1%; mdworker_shared ramped 2→10; drive presence alone wakes Spotlight | [2026-06-01T10-17_baseline-A-idle-test.tsv](2026-06-01T10-17_baseline-A-idle-test.tsv) |

## Open questions parked here

- **CGPDFService swarms on TM-drive connect** (8–10 of them, observed in chat 2026-06-01). They quiet during `backupd`, swarm back after. Our script may not be the trigger we thought it was — the drive being plugged in is. Worth measuring directly: idle run with drive plugged but no backup running.
- Is mds's 24% in the v2 idle a one-time burst (post-connect indexing of a newly visible volume) or sustained? Re-run with `--post-unmount-window` extended, or just a longer idle, to find out.
