#!/usr/bin/env bash
# Boot a fresh Ubuntu 24.04 cloud VM, have cloud-init install the snapshot tool
# and prepare a real snapshot, then run the matching Layer 3 test inside the VM
# and tear down. See tests/e2e/README.md and docs/plans/qemu-e2e-plan.md.
#
# Invoked by tests/e2e/Makefile; not intended to be called directly.

set -euo pipefail

BACKEND="${1:?usage: run.sh <zfs|btrfs|timeshift>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"

# ---------- config ----------
RCB_E2E_CACHE="${RCB_E2E_CACHE:-$HOME/.cache/rcb-e2e}"
RCB_E2E_SCRATCH_ROOT="${RCB_E2E_SCRATCH_ROOT:-/tmp}"
IMAGE_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
IMAGE_NAME="noble-server-cloudimg-amd64.img"
SSH_PORT="${SSH_PORT:-0}"   # 0 = pick a free one in pick_port
SSH_WAIT_SECS="${SSH_WAIT_SECS:-300}"
RUN_TIMEOUT_SECS="${RUN_TIMEOUT_SECS:-900}"
VM_MEM="${VM_MEM:-4G}"
VM_CPUS="${VM_CPUS:-2}"

# ---------- preflight ----------
preflight() {
    case "$BACKEND" in
        zfs|btrfs|timeshift) ;;
        *) echo "ERROR: unknown backend '$BACKEND' (zfs|btrfs|timeshift)" >&2; exit 2 ;;
    esac
    [ -f "$HERE/$BACKEND/user-data.yaml" ] || {
        echo "ERROR: missing $HERE/$BACKEND/user-data.yaml" >&2; exit 2;
    }
    for bin in qemu-system-x86_64 qemu-img cloud-localds curl ssh-keygen ssh; do
        command -v "$bin" >/dev/null || {
            echo "ERROR: $bin not found. See tests/e2e/README.md prereqs." >&2
            exit 2
        }
    done
    # Scratch root must be POSIX (not exfat/ntfs/vfat) — QEMU's writable overlay
    # needs sparse files and atomic rename, which exfat lacks.
    local fstype
    fstype=$(stat -f -c %T "$RCB_E2E_SCRATCH_ROOT" 2>/dev/null || echo unknown)
    case "$fstype" in
        ext2/ext3|ext4|xfs|btrfs|zfs|tmpfs|reiserfs|jfs)
            ;;
        *)
            echo "ERROR: scratch root '$RCB_E2E_SCRATCH_ROOT' is on $fstype — " >&2
            echo "       QEMU's writable overlay needs a POSIX fs. Set " >&2
            echo "       RCB_E2E_SCRATCH_ROOT=/some/ext4/path or remount /tmp." >&2
            exit 2
            ;;
    esac
}

# ---------- image cache ----------
ensure_image() {
    mkdir -p "$RCB_E2E_CACHE/images"
    local img="$RCB_E2E_CACHE/images/$IMAGE_NAME"
    if [ ! -f "$img" ]; then
        echo "[setup]  fetching $IMAGE_URL -> $img"
        curl -fL --retry 3 -o "$img.part" "$IMAGE_URL"
        mv "$img.part" "$img"
    fi
    echo "$img"
}

# ---------- per-run scratch ----------
make_scratch() {
    local d
    d=$(mktemp -d "$RCB_E2E_SCRATCH_ROOT/rcb-e2e-$BACKEND-XXXXXX")
    echo "$d"
}

# Pick an unused TCP port in the ephemeral range (>= 10000). Avoids collisions
# with other VMs, prior failed runs, or other services on the host.
pick_port() {
    python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

# ---------- main ----------
preflight
[ "$SSH_PORT" = "0" ] && SSH_PORT=$(pick_port)
BASE_IMG=$(ensure_image)
SCRATCH=$(make_scratch)
trap 'rm -rf "$SCRATCH"' EXIT

echo "[setup]  scratch: $SCRATCH"
ssh-keygen -q -t ed25519 -N '' -f "$SCRATCH/id_ed25519"
PUBKEY=$(cat "$SCRATCH/id_ed25519.pub")

# Build cloud-init seed: merge the backend's user-data with a shared head that
# injects our SSH pubkey + base packages. The backend file owns the runcmd list
# (cloud-init's last-key-wins behavior means we can't split runcmd into two
# blocks); its first item must `git clone` the repo to /opt/rcb.
cat > "$SCRATCH/user-data" <<EOF
#cloud-config
ssh_authorized_keys:
  - $PUBKEY
package_update: true
package_upgrade: false
packages:
  - git
  - python3-pip
  - python3-pytest
EOF
# Append the backend-specific user-data (skipping its #cloud-config header).
tail -n +2 "$HERE/$BACKEND/user-data.yaml" >> "$SCRATCH/user-data"

cat > "$SCRATCH/meta-data" <<EOF
instance-id: rcb-e2e-$BACKEND-$$
local-hostname: rcb-e2e-$BACKEND
EOF

cloud-localds "$SCRATCH/seed.iso" "$SCRATCH/user-data" "$SCRATCH/meta-data"

# Writable overlay so the base image stays read-only and reusable.
qemu-img create -q -f qcow2 -F qcow2 -b "$BASE_IMG" "$SCRATCH/disk.qcow2"

# Resize so apt/zfsutils have room (the cloud image base is small).
qemu-img resize -q "$SCRATCH/disk.qcow2" 12G

# KVM if usable, else TCG.
ACCEL="kvm"
if [ ! -r /dev/kvm ] || [ ! -w /dev/kvm ]; then
    ACCEL="tcg"
    echo "[setup]  /dev/kvm not accessible — falling back to TCG (slow)"
fi

echo "[boot]   starting VM (accel=$ACCEL, mem=$VM_MEM, cpus=$VM_CPUS, ssh=127.0.0.1:$SSH_PORT)"
# -daemonize is incompatible with -nographic (which sets -serial stdio); we use
# explicit -display none + serial-to-file so the console is captured for
# debugging without tying QEMU to this script's stdio.
#
# Retry the launch up to 3 times if the host port forwarding fails: pick_port
# can race against another process grabbing the same ephemeral port between
# the python check and the qemu bind.
launch_qemu() {
    qemu-system-x86_64 \
        -name "rcb-e2e-$BACKEND" \
        -machine accel="$ACCEL" \
        $( [ "$ACCEL" = "kvm" ] && echo "-cpu host" ) \
        -smp "$VM_CPUS" \
        -m "$VM_MEM" \
        -display none \
        -serial "file:$SCRATCH/serial.log" \
        -monitor none \
        -drive "file=$SCRATCH/disk.qcow2,if=virtio,format=qcow2" \
        -drive "file=$SCRATCH/seed.iso,if=virtio,format=raw,readonly=on" \
        -netdev "user,id=net0,hostfwd=tcp::$SSH_PORT-:22" \
        -device "virtio-net-pci,netdev=net0" \
        -daemonize \
        -pidfile "$SCRATCH/qemu.pid" 2> "$SCRATCH/qemu.stderr"
}
launched=0
for attempt in 1 2 3; do
    if launch_qemu; then launched=1; break; fi
    if grep -q "Could not set up host forwarding rule" "$SCRATCH/qemu.stderr"; then
        echo "[boot]   port $SSH_PORT raced; re-picking and retrying ($attempt/3)"
        SSH_PORT=$(pick_port)
        continue
    fi
    break
done
if [ "$launched" != "1" ]; then
    echo "ERROR: qemu failed to launch:" >&2
    cat "$SCRATCH/qemu.stderr" >&2
    trap - EXIT
    echo "       scratch preserved at $SCRATCH" >&2
    exit 4
fi

QEMU_PID=$(cat "$SCRATCH/qemu.pid")
if ! kill -0 "$QEMU_PID" 2>/dev/null; then
    echo "ERROR: qemu daemonized but exited immediately:" >&2
    cat "$SCRATCH/qemu.stderr" >&2
    trap - EXIT
    echo "       scratch preserved at $SCRATCH" >&2
    exit 4
fi
trap 'kill "$QEMU_PID" 2>/dev/null || true; rm -rf "$SCRATCH"' EXIT

# Wait for SSH up + cloud-init done.
if ! "$HERE/lib/ssh-wait.sh" "$SCRATCH/id_ed25519" "$SSH_PORT" "$SSH_WAIT_SECS"; then
    echo "ERROR: serial console saved to $SCRATCH/serial.log" >&2
    trap - EXIT
    kill "$QEMU_PID" 2>/dev/null || true
    echo "       scratch preserved at $SCRATCH" >&2
    exit 3
fi

# Bound the rest of the run with RUN_TIMEOUT_SECS so a hung guest can't park
# the wrapper indefinitely. The watchdog runs in its own process group via
# `setsid` so a single `kill -- -<pgid>` from the trap takes down both the
# watchdog shell AND its `sleep` child.
#
# Round-3 corrections: a backgrounded SUBSHELL ( wait $pid && ... ) cannot
# `wait` on a non-child PID, so the kill chain would never fire and the
# advertised timeout wouldn't bound anything. We back the watchdog with a
# single setsid'd bash that owns its own `sleep`, capturing the *new
# process-group leader's* PID. We also explicitly tear it down in both
# late failure branches (cloud-init and pytest), not just the EXIT trap,
# since both branches run `trap - EXIT` before exiting.
setsid -- bash -c \
    'sleep "$1"; \
     echo "ERROR: RUN_TIMEOUT_SECS=$1 exceeded; killing run" >&2; \
     kill -TERM "$2" 2>/dev/null' \
    rcb-e2e-watchdog "$RUN_TIMEOUT_SECS" "$$" &
WATCHDOG_PGID=$!
kill_watchdog() {
    [ -n "${WATCHDOG_PGID:-}" ] || return 0
    kill -- -"$WATCHDOG_PGID" 2>/dev/null || true
    unset WATCHDOG_PGID
}
trap 'kill_watchdog; kill "$QEMU_PID" 2>/dev/null || true; rm -rf "$SCRATCH"' EXIT

SSH="ssh -i $SCRATCH/id_ed25519 -p $SSH_PORT \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=10 ubuntu@127.0.0.1"

echo "[run]    waiting for cloud-init to finish (incl. backend setup)..."
if ! $SSH "sudo cloud-init status --wait"; then
    echo "ERROR: cloud-init did not converge" >&2
    echo "       guest journalctl saved to $SCRATCH/cloud-init.log" >&2
    echo "       VM serial console saved to $SCRATCH/serial.log" >&2
    # Cloud-init runs across four services (init, init-local, config, final);
    # failures typically land in cloud-final. Grab all four and the status.
    $SSH "sudo cloud-init status --long; \
          sudo journalctl --no-pager -u cloud-init -u cloud-init-local \
                          -u cloud-config -u cloud-final" \
        > "$SCRATCH/cloud-init.log" 2>&1 || true
    # Keep the scratch dir on failure so the user can inspect both logs.
    kill_watchdog
    trap - EXIT
    kill "$QEMU_PID" 2>/dev/null || true
    echo "       scratch preserved at $SCRATCH" >&2
    exit 3
fi

# Backend-specific env var that points the Layer 3 test at the prepared
# resource. Backend already validated in preflight.
case "$BACKEND" in
    zfs)        TEST_ENV="RCB_ZFS_TEST_DATASET=rcbtest/home" ;;
    btrfs)      TEST_ENV="RCB_BTRFS_TEST_MOUNT=/mnt/rcbbtrfs" ;;
    timeshift)  TEST_ENV="RCB_TIMESHIFT_TEST_BASE=/timeshift/snapshots RCB_TIMESHIFT_TEST_CONFIG=/etc/timeshift/timeshift.json" ;;
esac

echo "[test]   pytest tests/integration/test_${BACKEND}_real.py inside VM"
if ! $SSH "cd /opt/rcb && sudo $TEST_ENV pytest tests/integration/test_${BACKEND}_real.py -v"; then
    echo "ERROR: pytest failed inside the VM" >&2
    # Capture both cloud-init logs and the test environment so we can debug
    # post-mortem (some failures are caused by missing pre-reqs, not the test).
    $SSH "sudo cloud-init status --long; \
          sudo journalctl --no-pager -u cloud-init -u cloud-init-local \
                          -u cloud-config -u cloud-final" \
        > "$SCRATCH/cloud-init.log" 2>&1 || true
    $SSH "which pytest; which python3; ls -la /opt/rcb 2>/dev/null || echo NO-REPO" \
        > "$SCRATCH/env.log" 2>&1 || true
    kill_watchdog
    trap - EXIT
    kill "$QEMU_PID" 2>/dev/null || true
    echo "       scratch preserved at $SCRATCH" >&2
    echo "       cloud-init log: $SCRATCH/cloud-init.log" >&2
    echo "       guest env:      $SCRATCH/env.log" >&2
    exit 5
fi

echo "[done]   $BACKEND e2e PASS"
