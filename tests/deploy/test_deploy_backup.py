import os
import stat
import time
from datetime import UTC, datetime

import pytest
from deploy_harness import ROOT

pytestmark = pytest.mark.unit

SCRIPT = ROOT / "deploy/vm/backup.sh"
POSTGRES = [
    {"cmd": "docker", "match": r"label=com\.docker\.compose\.service=postgres$", "stdout": "pg1\n"},
    {"cmd": "docker", "match": r"^exec pg1 ", "stdout": "PGDUMP"},
]
MONGO = [
    {"cmd": "docker", "match": r"label=com\.docker\.compose\.service=mongo$", "stdout": "mg1\n"},
    {"cmd": "docker", "match": r"^exec mg1 ", "stdout": "MONGOARCHIVE"},
]


def run_backup(sandbox, backups):
    return sandbox.run(SCRIPT, BACKUP_DIR=str(backups))


def today(backups):
    return backups / datetime.now(UTC).strftime("%Y-%m-%d")


def test_writes_both_dumps_readable_only_by_root(sandbox, tmp_path):
    sandbox.rules = POSTGRES + MONGO
    backups = tmp_path / "backups"

    result = run_backup(sandbox, backups)

    assert result.returncode == 0, result.stderr
    day = today(backups)
    assert (day / "postgres.dump").read_text() == "PGDUMP"
    assert (day / "mongo.archive.gz").read_text() == "MONGOARCHIVE"
    for name in ("postgres.dump", "mongo.archive.gz"):
        assert stat.S_IMODE((day / name).stat().st_mode) == 0o600
    execs = [c["args"] for c in sandbox.calls("docker") if c["args"][0] == "exec"]
    assert "pg_dump" in execs[0][-1] and "-Fc" in execs[0][-1]
    assert "mongodump" in execs[1][-1] and "--archive" in execs[1][-1]


def test_old_backups_are_removed_and_recent_ones_kept(sandbox, tmp_path):
    sandbox.rules = POSTGRES + MONGO
    backups = tmp_path / "backups"
    old, recent = backups / "2026-01-01", backups / "2026-01-20"
    for path, days in ((old, 10), (recent, 3)):
        path.mkdir(parents=True)
        then = time.time() - days * 86400
        os.utime(path, (then, then))

    assert run_backup(sandbox, backups).returncode == 0

    assert not old.exists()
    assert recent.exists()


def test_missing_container_fails_but_the_other_dump_is_written(sandbox, tmp_path):
    sandbox.rules = POSTGRES  # mongo is not running
    backups = tmp_path / "backups"

    result = run_backup(sandbox, backups)

    assert result.returncode == 1
    assert "mongo is not running" in result.stderr
    assert (today(backups) / "postgres.dump").exists()
    assert sorted(p.name for p in today(backups).iterdir()) == ["postgres.dump"]


def test_failed_dump_leaves_no_partial_file(sandbox, tmp_path):
    sandbox.rules = [
        {"cmd": "docker", "match": r"^exec pg1 ", "exit": 1},
        *POSTGRES,
        *MONGO,
    ]
    backups = tmp_path / "backups"

    result = run_backup(sandbox, backups)

    assert result.returncode == 1
    assert sorted(p.name for p in today(backups).iterdir()) == ["mongo.archive.gz"]


def test_timer_runs_the_installed_script_nightly():
    service = (ROOT / "deploy/systemd/mcpwr-backup.service").read_text()
    timer = (ROOT / "deploy/systemd/mcpwr-backup.timer").read_text()

    assert "ExecStart=/opt/mcpwr/agent/backup.sh" in service
    assert "OnCalendar=*-*-* 03:00:00" in timer
    assert "Persistent=true" in timer
