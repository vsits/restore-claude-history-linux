"""Layer 1: Timeshift backend is_available() + discover() using tempdir trees."""

from __future__ import annotations

import json

from backends.timeshift import TimeshiftBackend


def _config(tmp_path, btrfs_mode="false"):
    cfg = tmp_path / "timeshift.json"
    cfg.write_text(json.dumps({"btrfs_mode": btrfs_mode, "snapshot_count": "3"}))
    return cfg


def _backend(tmp_path, base, btrfs_mode="false", runtime_root=None):
    return TimeshiftBackend(config_path=_config(tmp_path, btrfs_mode),
                            snapshot_bases=(base,),
                            runtime_root=runtime_root or (tmp_path / "no-runtime"))


# -------- is_available --------


def test_is_available_true_when_config_parses(tmp_path):
    b = TimeshiftBackend(config_path=_config(tmp_path), snapshot_bases=())
    assert b.is_available() is True


def test_is_available_false_when_config_missing(tmp_path):
    b = TimeshiftBackend(config_path=tmp_path / "nope.json", snapshot_bases=())
    assert b.is_available() is False


def test_is_available_false_on_invalid_json(tmp_path):
    cfg = tmp_path / "timeshift.json"
    cfg.write_text("{not valid json")
    b = TimeshiftBackend(config_path=cfg, snapshot_bases=())
    assert b.is_available() is False


# -------- discover --------


def test_discover_rsync_snapshots(tmp_path):
    base = tmp_path / "snapshots"
    for ts in ("2026-05-28_00-00-01", "2026-05-28_12-00-01"):
        (base / ts / "localhost").mkdir(parents=True)
    snaps = _backend(tmp_path, base).discover()
    assert {str(s.data_root) for s in snaps} == {
        str(base / "2026-05-28_00-00-01" / "localhost"),
        str(base / "2026-05-28_12-00-01" / "localhost"),
    }
    assert all(s.needs_mount is False for s in snaps)
    assert {s.name for s in snaps} == {
        "2026-05-28_00-00-01/localhost", "2026-05-28_12-00-01/localhost"}


def test_discover_btrfs_emits_both_subvols(tmp_path):
    # Round-1 HIGH: a Timeshift BTRFS snapshot has @ AND @home; Timeshift must
    # claim both so auto-mode dedup prunes both Btrfs sibling subvolumes.
    base = tmp_path / "snapshots"
    ts = base / "2026-05-28_00-00-01"
    (ts / "@home").mkdir(parents=True)
    (ts / "@").mkdir(parents=True)
    snaps = _backend(tmp_path, base, btrfs_mode="true").discover()
    assert {str(s.data_root) for s in snaps} == {str(ts / "@home"), str(ts / "@")}


def test_discover_btrfs_single_root_subvol(tmp_path):
    base = tmp_path / "snapshots"
    ts = base / "2026-05-28_00-00-01"
    (ts / "@").mkdir(parents=True)
    snaps = _backend(tmp_path, base, btrfs_mode="true").discover()
    assert [str(s.data_root) for s in snaps] == [str(ts / "@")]


def test_discover_fallback_to_timestamp_dir(tmp_path):
    base = tmp_path / "snapshots"
    ts = base / "2026-05-28_00-00-01"
    ts.mkdir(parents=True)  # no localhost/@home/@ subdir
    snaps = _backend(tmp_path, base).discover()
    assert [str(s.data_root) for s in snaps] == [str(ts)]


def test_discover_ignores_non_dir_entries(tmp_path):
    base = tmp_path / "snapshots"
    (base / "2026-05-28_00-00-01" / "localhost").mkdir(parents=True)
    (base / "info.json").write_text("{}")  # stray file in base
    snaps = _backend(tmp_path, base).discover()
    assert len(snaps) == 1


def test_discover_dedups_by_realpath(tmp_path):
    # Two bases, one a symlink to the other -> same snapshot, one entry.
    real = tmp_path / "snapshots"
    (real / "2026-05-28_00-00-01" / "localhost").mkdir(parents=True)
    link = tmp_path / "snapshots-link"
    link.symlink_to(real)
    b = TimeshiftBackend(config_path=_config(tmp_path), snapshot_bases=(real, link),
                         runtime_root=tmp_path / "no-runtime")
    assert len(b.discover()) == 1


def test_discover_runtime_rsync_mount(tmp_path):
    # Snapshots exposed only via the non-PID runtime mount (RSYNC).
    runtime = tmp_path / "run-timeshift"
    snaps_dir = runtime / "backup" / "timeshift" / "snapshots"
    (snaps_dir / "2026-05-28_00-00-01" / "localhost").mkdir(parents=True)
    b = TimeshiftBackend(config_path=_config(tmp_path), snapshot_bases=(),
                         runtime_root=runtime)
    assert [str(s.data_root) for s in b.discover()] == [
        str(snaps_dir / "2026-05-28_00-00-01" / "localhost")]


def test_discover_runtime_btrfs_pid_mount(tmp_path):
    # Snapshots exposed only via the PID runtime mount (BTRFS).
    runtime = tmp_path / "run-timeshift"
    snaps_dir = runtime / "1234" / "backup" / "timeshift-btrfs" / "snapshots"
    ts = snaps_dir / "2026-05-28_00-00-01"
    (ts / "@home").mkdir(parents=True)
    b = TimeshiftBackend(config_path=_config(tmp_path, btrfs_mode="true"),
                         snapshot_bases=(), runtime_root=runtime)
    assert [str(s.data_root) for s in b.discover()] == [str(ts / "@home")]


def test_discover_empty_without_config(tmp_path):
    base = tmp_path / "snapshots"
    (base / "2026-05-28_00-00-01" / "localhost").mkdir(parents=True)
    b = TimeshiftBackend(config_path=tmp_path / "nope.json", snapshot_bases=(base,))
    assert b.discover() == []


def test_discover_empty_when_base_missing(tmp_path):
    b = _backend(tmp_path, tmp_path / "does-not-exist")
    assert b.discover() == []


# -------- _parse_created_at (info.json + dir-name fallback) --------


def test_parse_created_at_prefers_info_json_epoch(tmp_path):
    """info.json's `created` epoch is the authoritative UTC source."""
    import json as _json
    from datetime import datetime, timezone

    from backends.timeshift import _parse_created_at

    ts_dir = tmp_path / "2026-06-02_17-10-26"
    ts_dir.mkdir()
    # 1780425026 = 2026-06-02 18:30:26 UTC — deliberately different from dir name
    # so we can prove info.json wins.
    (ts_dir / "info.json").write_text(_json.dumps({"created": "1780425026"}))
    out = _parse_created_at(ts_dir)
    assert out == datetime(2026, 6, 2, 18, 30, 26, tzinfo=timezone.utc)


def test_parse_created_at_falls_back_to_dir_name(tmp_path):
    """Without info.json, dir name parses as local time (warned)."""
    ts_dir = tmp_path / "2026-06-02_17-10-26"
    ts_dir.mkdir()
    from backends.timeshift import _parse_created_at
    out = _parse_created_at(ts_dir)
    assert out is not None
    # We can't assert the exact UTC without knowing the host TZ; assert it
    # parsed and is timezone-aware (the fallback path goes through
    # astimezone(utc) so the result is UTC-aware).
    assert out.tzinfo is not None


def test_parse_created_at_none_when_both_fail(tmp_path):
    """Dir name doesn't match the format and no info.json — return None."""
    ts_dir = tmp_path / "garbage-name"
    ts_dir.mkdir()
    from backends.timeshift import _parse_created_at
    assert _parse_created_at(ts_dir) is None
