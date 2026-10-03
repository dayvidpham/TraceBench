"""Acceptance tests for issue #2 (stdlib + pytest, no network)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from snapshot.api import snapshot_repo
from snapshot.cutoff import Cutoff
from snapshot.peasant import BinaryPeasantClient, PeasantError, StubPeasantClient
from snapshot.trace import DirTraceProvider

import pytest


def _git(repo: Path, *args: str, env: dict | None = None) -> None:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=merged,
                   capture_output=True)


def _commit(repo: Path, name: str, content: str, iso_date: str) -> None:
    (repo / name).write_text(content)
    _git(repo, "add", name)
    env = {
        "GIT_AUTHOR_DATE": iso_date,
        "GIT_COMMITTER_DATE": iso_date,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", name,
         env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", str(r)], check=True, capture_output=True)
    _commit(r, "a.txt", "v1", "2026-01-01T00:00:00+00:00")
    _commit(r, "b.txt", "v2", "2026-02-01T00:00:00+00:00")
    _commit(r, "c.txt", "v3", "2026-03-01T00:00:00+00:00")
    return r


@pytest.fixture
def tracedir(tmp_path: Path) -> Path:
    d = tmp_path / "traces"
    d.mkdir()
    early = d / "early.jsonl"
    late = d / "late.jsonl"
    early.write_text("{}\n")
    late.write_text("{}\n")
    # mtimes straddle the Feb cutoff
    t_early = datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp()
    t_late = datetime(2026, 3, 15, tzinfo=timezone.utc).timestamp()
    os.utime(early, (t_early, t_early))
    os.utime(late, (t_late, t_late))
    return d


def test_date_cutoff_excludes_later_commits_files_traces(repo, tracedir):
    snap = snapshot_repo(
        repo, Cutoff.by_date("2026-02-01T00:00:00Z"), trace_dir=tracedir
    )
    assert [c.subject for c in snap.commits] == ["a.txt", "b.txt"]
    assert [f.path for f in snap.files] == ["a.txt", "b.txt"]
    assert [t.path for t in snap.traces] == ["early.jsonl"]
    assert all(c.committer_time <= snap.cutoff_time for c in snap.commits)


def test_pr_cutoff_is_exclusive_at_start(repo):
    # PR 7 starts exactly at the b.txt commit time -> b.txt excluded.
    stub = StubPeasantClient(
        starts={7: datetime(2026, 2, 1, tzinfo=timezone.utc)}
    )
    snap = snapshot_repo(repo, Cutoff.by_pr(7), peasant=stub)
    assert [c.subject for c in snap.commits] == ["a.txt"]
    assert [f.path for f in snap.files] == ["a.txt"]


def test_pr_start_override_bypasses_binary(repo):
    snap = snapshot_repo(
        repo, Cutoff.by_pr(99), pr_start_override="2026-02-01T00:00:00Z"
    )
    assert [c.subject for c in snap.commits] == ["a.txt"]


def test_determinism_same_call_same_snapshot(repo, tracedir):
    a = snapshot_repo(repo, Cutoff.by_date("2026-03-01T00:00:00Z"),
                      trace_dir=tracedir)
    # bump late.jsonl atime only (mtime unchanged) + sleep to catch ordering bugs
    time.sleep(0.01)
    b = snapshot_repo(repo, Cutoff.by_date("2026-03-01T00:00:00Z"),
                      trace_dir=tracedir)
    assert a.to_dict() == b.to_dict()
    assert a.manifest_hash() == b.manifest_hash()


def test_binary_client_parses_json_and_checks_version(tmp_path):
    fake = tmp_path / "peasant"
    fake.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo \"peasant 1.2.3\"; exit 0; fi\n"
        "echo '{\"started_at\": \"2026-02-01T00:00:00Z\"}'\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    client = BinaryPeasantClient(binary=str(fake), expected_version="1.2.3")
    assert client.version() == "peasant 1.2.3"
    assert client.pr_start(7) == datetime(2026, 2, 1, tzinfo=timezone.utc)


def test_binary_client_version_mismatch(tmp_path):
    fake = tmp_path / "peasant"
    fake.write_text("#!/bin/sh\necho \"peasant 9.9.9\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    client = BinaryPeasantClient(binary=str(fake), expected_version="1.2.3")
    with pytest.raises(PeasantError):
        client.version()


def test_dir_provider_empty_when_missing(tmp_path):
    provider = DirTraceProvider(tmp_path / "nope")
    assert provider.list_files(datetime(2026, 5, 1, tzinfo=timezone.utc)) == []


def test_cli_e2e_date(tmp_path, repo):
    out = tmp_path / "out"
    rc = subprocess.run(
        ["python3", "-m", "snapshot.cli", "--repo", str(repo),
         "--cutoff-type", "date", "--cutoff-date", "2026-02-01T00:00:00Z",
         "--out", str(out)],
        capture_output=True, text=True,
    )
    assert rc.returncode == 0, rc.stderr
    payload = json.loads((out / "history.json").read_text())
    assert [c["subject"] for c in payload["commits"]] == ["a.txt", "b.txt"]
    assert "manifest_sha256" in payload
