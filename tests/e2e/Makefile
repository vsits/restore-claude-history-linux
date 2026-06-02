# Layer 3 end-to-end harness (QEMU + KVM). See README.md / docs/plans/qemu-e2e-plan.md.

SHELL := /usr/bin/env bash
HERE  := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

.PHONY: e2e e2e-zfs e2e-btrfs e2e-timeshift help

help:
	@echo "Targets:"
	@echo "  e2e            run all three backends serially"
	@echo "  e2e-zfs        run just ZFS"
	@echo "  e2e-btrfs      run just Btrfs"
	@echo "  e2e-timeshift  run just Timeshift"
	@echo
	@echo "Overrides:"
	@echo "  RCB_E2E_CACHE         image cache root (default ~/.cache/rcb-e2e)"
	@echo "  RCB_E2E_SCRATCH_ROOT  scratch root, POSIX fs (default /tmp)"
	@echo "  SSH_PORT              host port forwarded to VM SSH (default: auto-pick)"
	@echo "  SSH_WAIT_SECS         max wait for guest SSH (default 300)"
	@echo "  VM_MEM, VM_CPUS       VM allocation (default 4G, 2)"

e2e: e2e-zfs e2e-btrfs e2e-timeshift

e2e-zfs:
	@$(HERE)/run.sh zfs

e2e-btrfs:
	@$(HERE)/run.sh btrfs

e2e-timeshift:
	@$(HERE)/run.sh timeshift
