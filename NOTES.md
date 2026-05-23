# Notes

Design rationale and lessons learned from building `restore_claude_history.py`. The README covers usage; this is for anyone who wants to understand *why* the script makes the choices it does — including future-you, six months from now, wondering "why am I parsing this in such a weird way?"

## Prevention vs. restoration

Chats disappear for more than one reason. The documented one is `cleanupPeriodDays` in `~/.claude/settings.json` — a positive integer (default 30) that Claude Code reads on startup, then deletes any JSONL older than that. The default is too aggressive, the setting isn't exposed in the UI, and there's no warning before deletion. Beyond that, there are user reports of the setting being ignored after app updates, and more generally: Anthropic ships updates that touch your local files, and any future update could introduce new ways for chats to go missing.

So there are two layers.

**Prevention** is one line in `~/.claude/settings.json`:
```json
"cleanupPeriodDays": 36500
```
~100 years; no documented upper bound. Set this on every machine. It defangs the documented cleanup, but it doesn't protect you from app updates that ignore the setting, future changes in cleanup behavior, or anything else Anthropic decides to do to your local files down the road.

**Restoration** is [`restore_claude_history.py`](restore_claude_history.py). It assumes the worst has already happened and pulls your chats back out of Time Machine. It's what catches you when prevention fails — which, given the track record, is a "when" not an "if."

## What we verified by hand, before scripting

Before writing any code, we worked through a real recovery in a Claude Code session. The script automates exactly this sequence:

1. **List APFS snapshots on the TM volume.** `diskutil apfs listSnapshots /dev/diskNsM` gives names like `com.apple.TimeMachine.2026-04-24-205237.backup`.

2. **Mount each snapshot.** macOS only auto-mounts one snapshot at a time via `/Volumes/.timemachine/<UUID>/...`. To access more, mount yourself:
   ```
   mkdir -p /tmp/tm-<label>
   mount_apfs -s com.apple.TimeMachine.<timestamp>.backup /dev/diskNsM /tmp/tm-<label>
   ```
   Read-only, no sudo needed if you have Full Disk Access.

3. **Find the Claude project dir inside each snapshot.** Path is `<mount>/<timestamp>.backup/Data/Users/<user>/.claude/projects/<encoded-project>/`. The leading `Data/` is the APFS data-volume firmlink — don't omit it.

4. **For each JSONL filename, pick the largest version across all snapshots.** JSONLs are append-only logs. Bigger file = longer conversation = more complete.

5. **Copy with mtime preservation, and re-stamp after any re-copy.** `cp -p` keeps the snapshot's original mtime — otherwise VS Code's "Recent chats" picker sorts restored files as "just now." Important gotcha: if a file fails on the first copy (e.g. ACL conflict) and you re-copy after stripping the ACL, mtime preservation doesn't always survive the second pass. Explicitly re-stamp.

6. **Strip ACLs after copy.** TM snapshot files carry an inherited ACL (`group:everyone deny write,delete,append,writeattr,writeextattr,chown`). This sticks to the copy and blocks future overwrites. `chmod -N <file>` removes the ACL. `chmod u+w <file>` ensures the user write bit.

7. **Restore subdirectories too.** `~/.claude/projects/<project>/<session-uuid>/subagents/` contains subagent transcripts. Use `cp -R`.

8. **Unmount everything on exit.** Use a trap (or `try/finally` in Python) so cleanup runs even on Ctrl-C or error.

## Gotchas we learned the hard way

These are the things the script is silently working around. Document them here so they don't get re-discovered.

- **Spotlight indexes APFS Time Machine volumes the moment they mount, and you cannot turn it off.** `mdutil -i off` reports success but the index restarts. `.metadata_never_index` marker files do nothing. Apple's Spotlight Privacy UI refuses to add TM volumes. Result: high CPU (CGPDFService, mds_stores, mdworker_shared) for as long as the drive is mounted. Mitigation: **be fast** — mount, restore, unmount, eject.

- **macOS sometimes pre-mounts snapshots at `/Volumes/.timemachine/<UUID>/<ts>.backup/`.** Trying `mount_apfs` on those fails with `Resource busy`. The script detects existing mounts and uses them directly rather than trying to remount.

- **The auto-mount path has a doubled-`.backup` layout** (`<mp>/<ts>.backup/<ts>.backup/Data/...`) different from what `mount_apfs` produces yourself (`<mp>/<ts>.backup/Data/...`). The script probes both.

- **`cp -p` fails with "Permission denied" when the destination already exists** with the read-only ACL inherited from a previous copy. Strip the ACL on the destination first (`chmod -N`).

- **`tmutil` has no mount/unmount verbs.** It lists snapshots, restores files (limited), but does not let you mount one on demand. `mount_apfs` is the lower-level escape hatch.

- **`diskutil info <dev>` includes snapshot names** in its output. If you grep that output for "Time Machine" to identify the TM volume, you'll match the internal disk too (which has local TM snapshots). Use `tmutil destinationinfo` instead — it's purpose-built.

- **`diskutil apfs listSnapshots` formats output as an ASCII tree** with leading pipe characters. A naive `^\s*Name:` regex only matches the *last* block (which uses spaces, not pipes, for its prefix). Match `Name:` anywhere on the line, not just after whitespace.

- **`os.getlogin()` can return `root`** in non-TTY contexts (sudo, nested shells, some CI). `getpass.getuser()` reads `LOGNAME`/`USER` env vars and is more reliable.

- **argparse rejects values that start with `-`** because it thinks they're flags. Encoded Claude project names all start with `-`. The script pre-rewrites `--project FOO` → `--project=FOO` in argv before argparse sees it.

- **macOS ships bash 3.2** (frozen for licensing reasons since 2006). No associative arrays. We tried writing this in bash first; the resulting code was readable only via `sort | awk` pipeline tricks and a `trap` cleanup that turned out to be buggy. Python made all of this go away.

## Related GitHub issues

To fill in once we start linking this repo from those threads. Search `anthropics/claude-code` for "history" "deleted" "cleanupPeriodDays" "lost chats".

## Origin

The original recovery happened on 2026-05-23 from inside `~/projects/young-ladys-primer`. ~26 lost JSONLs recovered from 4 TM snapshots dating March 11 through April 24, 2026. The full transcript of that session lives at `~/.claude/projects/-Users-garrettstone-projects-young-ladys-primer/a2144d30-9891-47ea-810d-9a124d6b7497.jsonl`. The script in this repo was extracted from what we learned doing that by hand.
