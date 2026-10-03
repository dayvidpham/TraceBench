"""CLI: snapshot --repo PATH --cutoff-type {date,pr} ... --out DIR."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from snapshot.api import materialize_snapshot, snapshot_repo, write_snapshot
from snapshot.cutoff import Cutoff
from snapshot.peasant import BinaryPeasantClient


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="TraceBench repo snapshot (issue #2)")
    p.add_argument("--repo", required=True, help="path to any git repo")
    p.add_argument("--cutoff-type", required=True, choices=["date", "pr"])
    p.add_argument("--cutoff-date", default=None, help="ISO date (date cutoff)")
    p.add_argument("--pr", type=int, default=None, help="PR number (pr cutoff)")
    p.add_argument("--trace-dir", default=None)
    p.add_argument("--peasant-bin", default=None, help="path to peasant binary")
    p.add_argument(
        "--peasant-argv",
        default=None,
        help="override template, '{pr}' = number, '|' separated",
    )
    p.add_argument("--expected-peasant-version", default=None)
    p.add_argument("--pr-start-override", default=None)
    p.add_argument("--out", default=None, help="write history.json (+ repo/ if --materialize)")
    p.add_argument("--materialize", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cutoff_type == "date" and not args.cutoff_date:
        print("--cutoff-date is required for date cutoffs", file=sys.stderr)
        return 2
    if args.cutoff_type == "pr" and args.pr is None:
        print("--pr is required for pr cutoffs", file=sys.stderr)
        return 2

    cutoff = (
        Cutoff.by_date(args.cutoff_date)
        if args.cutoff_type == "date"
        else Cutoff.by_pr(args.pr)
    )
    peasant = None
    if args.cutoff_type == "pr" and args.pr_start_override is None:
        template = args.peasant_argv.split("|") if args.peasant_argv else None
        peasant = BinaryPeasantClient(
            binary=args.peasant_bin or "peasant",
            expected_version=args.expected_peasant_version,
            pr_show_argv=template,
        )
        # Fail fast on version skew when pinned.
        if args.expected_peasant_version:
            peasant.version()

    snap = snapshot_repo(
        args.repo,
        cutoff,
        trace_dir=args.trace_dir,
        peasant=peasant,
        pr_start_override=args.pr_start_override,
    )
    if args.out:
        out = Path(args.out)
        if args.materialize:
            materialize_snapshot(args.repo, snap, out, trace_root=args.trace_dir)
        else:
            write_snapshot(snap, out)
        print(str(out))
    else:
        payload = snap.to_dict()
        payload["manifest_sha256"] = snap.manifest_hash()
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
