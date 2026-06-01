# QEMU end-to-end test harness — plan (pre-release)

**Date:** 2026-06-01
**Status:** draft (held for execution before v1 release; not gating any merge)
**Author:** vsits-restore-claude-builder (RCB)
**Track:** chore (post-Phase-3, pre-v1.0.0 tag)

## Goal

Validate `BackendName.discover()` and the restore loop against **real** ZFS / Btrfs / Timeshift on real kernels — Layer 3 of the directive's test approach, automated. Run as a release gate, not a per-PR gate (cost discipline).

## Precondition

This plan is **executed after Phase 2 and Phase 3 have merged to `main`**. It depends on the Layer 3 test files (`tests/integration/test_btrfs_real.py`, `tests/integration/test_timeshift_real.py`) being present on the execution branch. The plan PR itself only lands the document; the harness implementation does not begin until the listed preconditions hold.

## Why this, why now

The v1 directive's Phase 1 explicitly rejected Docker fixtures because the kernel-dependent semantics (ZFS module, Btrfs subvol mounts, `.zfs/snapshot` visibility) collapse to command-parsing checks under unprivileged containers — coverage we already get from the Layer 1 subprocess mocks. The fix prescribed was Layer 3: opt-in tests on real hosts (`tests/integration/`). Phase 1 shipped `test_zfs_real.py`; Phase 2 added `test_btrfs_real.py`; Phase 3 added `test_timeshift_real.py`. They self-skip without the right env, so today they pass-by-not-running everywhere except a host that already has the backend installed — which is none of our hosts.

QEMU/KVM with stock cloud-init images gives us **real** kernels with each backend natively supported, run unprivileged inside the VM, host kept clean. This is exactly what the directive asked for; only the orchestration was missing.

When this plan executes, the Phase 2 and Phase 3 Layer 3 tests will already be on `main` — this plan does not add or modify those tests, it only orchestrates running them on a real kernel.

## Non-Functional Requirements

- **Size/complexity budget** — small. One `tests/e2e/` dir, three `cloud-init` user-data files, a `run.sh` wrapper, a `Makefile` target. ~300 LOC total target; flag at ~600.
- **Threat model** — runs entirely on the developer's machine or a CI runner. VMs are throwaway; no host-state mutation; no credentials handled. SSH key for VM access generated per-run, discarded on teardown.
- **Maintainability constraints** — no new abstraction over QEMU/libvirt; just shell + cloud-init. If the surface grows past "fetch image, boot, run pytest over SSH, destroy" we're over-engineering.
- **Performance/reliability** — total run target ≤ 15 min for all three backends locally (most of it kernel boot + apt install). Idempotent: rerun without cleanup gives the same result.
- **Load-bearing? — NO.** Test infra. Doesn't touch production code paths, the backend contracts, or shipped artifacts. Standard Lead + Codex review.

## Approach (per backend)

For each of zfs / btrfs / timeshift:

1. **Base image** — official Ubuntu 24.04 cloud image (v1 locks all three backends to Ubuntu; see Out of scope), fetched once into a local cache (`~/.cache/rcb-e2e/`). qcow2, ~600 MB.
2. **cloud-init user-data** per backend, installing the snapshot tool, creating a tiny test pool/subvolume/config, taking a snapshot containing `.claude/projects/<proj>/*.jsonl` fixtures.
3. **Boot ephemerally** with `qemu-system-x86_64 -snapshot` (writes discarded on exit) or libvirt transient domain. SSH in via a per-run ed25519 keypair on a forwarded port.
4. **Run pytest inside the VM** against the existing Layer 3 tests, with the right `RCB_*_TEST_*` env var pointing at the prepared pool/mount/config.
5. **Tear down** — process exit destroys the VM; no shared state survives.

The Layer 3 tests already exist; the harness only orchestrates *running* them on a real kernel.

## Per-backend env

| Backend | Image | Snapshot tool install | Layer 3 env var |
|---|---|---|---|
| ZFS | Ubuntu 24.04 cloud | `apt install zfsutils-linux` | `RCB_ZFS_TEST_DATASET=rcbtest/home` (created in cloud-init) |
| Btrfs | Ubuntu 24.04 cloud (root on ext4, scratch loopback for btrfs) | `apt install btrfs-progs` | `RCB_BTRFS_TEST_MOUNT=/mnt/rcbbtrfs` |
| Timeshift | Ubuntu 24.04 cloud | `apt install timeshift`; pre-seed RSYNC config; take a real snapshot with `timeshift --create` non-interactively | `RCB_TIMESHIFT_TEST_BASE=/timeshift/snapshots` + `RCB_TIMESHIFT_TEST_CONFIG=/etc/timeshift/timeshift.json` |

## File layout

```
tests/e2e/
├── README.md             # how to run, prereqs (qemu-system-x86_64, cloud-image-utils)
├── Makefile              # `make e2e` (all), `make e2e-zfs`, etc.
├── run.sh                # fetch image -> generate seed.iso -> boot -> ssh-pytest -> teardown
├── lib/
│   └── ssh-wait.sh       # poll for SSH up, bounded retry
├── zfs/
│   └── user-data.yaml    # cloud-init: install zfs, create test pool, take snapshot
├── btrfs/
│   └── user-data.yaml    # cloud-init: btrfs-progs, loop fs, subvol + snapshot
└── timeshift/
    └── user-data.yaml    # cloud-init: timeshift install, RSYNC config, real snapshot via `timeshift --create`
```

No changes to `backends/` or `restore_claude_history.py`. No changes to existing `tests/integration/` Layer 3 tests — they are the workload, executed inside the VM rather than skipped on the host.

## Run model

**Local-only by default.** `make e2e` builds + boots + runs + tears down each backend serially on the developer's machine. No CI integration in v1.0 — autonomous CI gating is deferred to v1.1 if pre-release runs reveal flakiness worth catching per-PR.

### Storage layout

Two distinct stores, with the split chosen to respect the filesystems involved:

- **Image cache (read-only base qcow2 images):** persistent. Default `~/.cache/rcb-e2e/images/`, overridable via the `RCB_E2E_CACHE` env var. On hosts where the home filesystem is tight, the recommended pattern is a symlink into a roomier mount — e.g. `~/.cache/rcb-e2e` → `/mnt/<large-volume>/rcb-e2e` — matching the symlink-into-SSD convention this project already uses for other adjacent caches (`~/cc-watch/extracts` → `/mnt/ssd/cc-watch/extracts`, etc.). The `run.sh` does not care which side the bytes land on; it just opens `RCB_E2E_CACHE`.
- **Per-run scratch (writable overlay qcow2, seed ISO, SSH keypair):** `/tmp/rcb-e2e-<backend>-<pid>/`, auto-removed on exit. **MUST live on a POSIX filesystem (ext4/xfs/btrfs/zfs), not exfat**: QEMU's writable overlay relies on sparse files and atomic rename semantics that exfat does not provide reliably; runs on exfat scratch corrupt the overlay intermittently. `/tmp` on the root filesystem satisfies this on Linux out of the box; the harness's preflight check refuses to run if it detects the scratch root on a non-POSIX fs.

Nothing in the repo tree mutates between runs.

This split matters on this development host specifically: the root filesystem (`/`) is at 83% with ~50 GB free, the SSD mount (`/mnt/ssd`, exfat) has 1.6 TB free but isn't safe for the writable overlay. Image cache goes to `/mnt/ssd/rcb-e2e/`; scratch stays on the root fs's `/tmp`.

## Cost model

- **Local:** zero ongoing cost. ~2 GB total disk for cached images. ~4 GB RAM at peak (one VM at a time, 4 GB allocation).
- **DigitalOcean alternative (deferred):** considered and not chosen for v1. Same harness could target DO droplets via `doctl` for autonomous CI gating, ~$0.01–0.02/hr per droplet × runs. Cost is bounded if disciplined; failure mode (forgotten droplet) is small but real. Revisit only if local runs become inconvenient (multi-developer team) or if CI gating becomes valuable.

## Risks

| Risk | Mitigation |
|---|---|
| KVM unavailable on a dev's box (no `/dev/kvm`) | Fall back to TCG (slow but works). README documents the prereq check. |
| Cloud image upstream URL changes | Cache the image; pin a checksum; documented update procedure. |
| ZFS DKMS module build flakes in cloud-init | Use the prebuilt `zfsutils-linux` Ubuntu package (binary; no DKMS). |
| Timeshift snapshot creation is interactive | Pre-seed the config in cloud-init; create a real snapshot non-interactively via `timeshift --create --comments rcb-test`. The harness drives Layer 3 against real snapshots only — no fake snapshot trees. |
| Test timeouts during long apt installs | Bound each backend's run at 10 min; surface clear error on timeout. |
| Btrfs subvolume tests need root inside the VM | Use the VM's `root` account (it's an ephemeral VM; appropriate scope). |
| Image cache on a non-POSIX filesystem (e.g. exfat-mounted external volume) | Read-only base images are fine on exfat; the writable overlay is NOT. The preflight check refuses to run if the scratch root resolves to a non-POSIX fs, with a clear message. Image cache (`RCB_E2E_CACHE`) can still live on exfat. |

## Sequencing

Single PR, one backend per commit:

1. `tests/e2e/` scaffolding + `run.sh` + ZFS user-data + ZFS Makefile target. Verify `make e2e-zfs` passes locally.
2. Btrfs user-data + target.
3. Timeshift user-data + target.
4. README + top-level `make e2e` aggregator + brief note in `docs/directives/` revision log calling out the e2e harness landing.

Each commit is independent; PR opens after ZFS is green locally, the rest land in the same PR as follow-up commits.

## Out of scope

- CI integration (GitHub Actions, self-hosted runner, DO).
- Multi-distro coverage beyond Ubuntu. openSUSE Tumbleweed coverage and the Snapper backend are a v1.1 follow-up tracked separately; explicitly NOT in scope for the v1 harness.
- Performance benchmarking.
- Anything that touches the production code paths in `backends/` or `restore_claude_history.py`.

## Done criteria

- `make e2e` exits 0 on this host with all three backends covered.
- Each backend's existing Layer 3 test runs to completion against a real snapshot inside its VM.
- README explains prereqs, run, troubleshooting.
- Tracking note in directive revision log.

Then we tag `v1.0.0` against `main` once Phase 4 lands.
