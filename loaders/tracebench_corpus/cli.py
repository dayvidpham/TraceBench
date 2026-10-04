"""Command line interface for the TraceBench corpus loader."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .adaptation import Adaptation, find_adaptation, load_adaptations
from .corpus import Corpus, load_corpus
from .skeleton import build_skeleton
from .task import TaskBuilder, load_pr_index

_SPLITS = ("train", "val", "test")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tracebench-corpus",
        description="Load the TraceBench Peasant PR corpus and materialize per-PR bundles.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--corpus",
        default=os.environ.get("TRACEBENCH_CORPUS"),
        help="local dump directory (defaults to $TRACEBENCH_CORPUS)",
    )
    source.add_argument("--repo", help="HuggingFace dataset id, e.g. dayvidpham/TraceBench")
    parser.add_argument("--revision", default="main", help="HuggingFace revision (default: main)")
    parser.add_argument("--cache-dir", default=None, help="HuggingFace cache directory")

    commands = parser.add_subparsers(dest="command", required=True)
    list_parser = commands.add_parser("list", help="list sampled pull requests")
    list_parser.add_argument("--split", choices=_SPLITS, default=None)
    bundle_parser = commands.add_parser("bundle", help="materialize one pull request bundle")
    bundle_parser.add_argument("pr", help="pull request id, e.g. peasant-labs/peasant#343")
    bundle_parser.add_argument("--dest", required=True, help="destination directory")
    all_parser = commands.add_parser("bundle-all", help="materialize every pull request")
    all_parser.add_argument("--dest", required=True, help="destination directory")
    all_parser.add_argument("--split", choices=_SPLITS, default=None)
    task_parser = commands.add_parser("task", help="assemble a task payload for one pull request")
    task_parser.add_argument("pr", help="pull request id, e.g. peasant-labs/peasant#343")
    task_parser.add_argument("--dest", required=True, help="destination directory")
    task_parser.add_argument(
        "--index",
        default=None,
        help="corpus/index/merged_prs.json; supplies merge commits and merged dates",
    )
    task_parser.add_argument(
        "--repo-dir",
        default=None,
        help="local clone used to resolve the develop boundary by commit ancestry",
    )
    task_parser.add_argument(
        "--adaptations",
        default=None,
        help="adaptation spec (YAML or JSON) with harness/model/thinking configurations",
    )
    task_parser.add_argument(
        "--adaptation",
        default=None,
        help="adaptation name from --adaptations; keeps only matching prior sessions",
    )
    task_parser.add_argument(
        "--force",
        action="store_true",
        help="rebuild tool-owned entries in a non-empty destination",
    )
    skeleton_parser = commands.add_parser("skeleton", help="generate a Harbor task skeleton")
    skeleton_parser.add_argument("--payload", required=True, help="task payload directory")
    skeleton_parser.add_argument("--dest", required=True, help="destination task directory")
    skeleton_parser.add_argument("--org", default="tracebench", help="Harbor task namespace")
    skeleton_parser.add_argument(
        "--base-image",
        default="tracebench/peasant-base:latest",
        help="shared base image referenced by task.toml",
    )
    skeleton_parser.add_argument(
        "--workdir", default="/workdir", help="container workdir for uploaded task data"
    )
    skeleton_parser.add_argument("--task-version", default="1.0.0", help="task version")
    skeleton_parser.add_argument(
        "--force",
        action="store_true",
        help="rebuild tool-owned entries in a non-empty destination",
    )

    args = parser.parse_args(argv)
    if args.command == "skeleton":
        return _skeleton(args)
    try:
        corpus = load_corpus(
            path=args.corpus,
            repo=args.repo,
            revision=args.revision,
            cache_dir=args.cache_dir,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"tracebench-corpus: {exc}", file=sys.stderr)
        return 2

    if args.command == "list":
        return _list(corpus, args.split)
    if args.command == "bundle":
        return _bundle(corpus, args.pr, Path(args.dest))
    if args.command == "task":
        return _task(corpus, args)
    return _bundle_all(corpus, Path(args.dest), args.split)


def _list(corpus: Corpus, split: str | None) -> int:
    for pr in sorted(corpus.prs(), key=lambda record: record["id"]):
        if split is not None and pr.get("split") != split:
            continue
        sessions = len(corpus.sessions_for_pr(pr["id"]))
        print(f"{pr.get('split', '?'):5} {pr['id']:55} {sessions:4} sessions  {pr.get('title', '')}")
    return 0


def _bundle(corpus: Corpus, pr_id: str, dest: Path) -> int:
    try:
        path = corpus.materialize(pr_id, dest)
    except KeyError as exc:
        print(f"tracebench-corpus: {exc}", file=sys.stderr)
        return 2
    bundle = corpus.pr_bundle(pr_id)
    print(
        f"wrote {path}: {len(bundle.traces)} traces, {len(bundle.transcripts)} transcripts"
        + (f", {len(bundle.missing_sessions)} missing" if bundle.missing_sessions else "")
    )
    return 0


def _bundle_all(corpus: Corpus, dest: Path, split: str | None) -> int:
    written = corpus.materialize_all(dest, split=split)
    print(f"wrote {len(written)} bundles into {dest}")
    return 0


def _task(corpus: Corpus, args: argparse.Namespace) -> int:
    try:
        index = load_pr_index(args.index) if args.index else None
        adaptation = None
        if args.adaptation or args.adaptations:
            if not (args.adaptation and args.adaptations):
                raise ValueError("--adaptation and --adaptations must be used together")
            adaptation = find_adaptation(load_adaptations(args.adaptations), args.adaptation)
        builder = TaskBuilder(corpus, pr_index=index, repo_dir=args.repo_dir)
        payload = builder.build(args.pr, Path(args.dest), force=args.force, adaptation=adaptation)
    except (KeyError, ValueError, OSError) as exc:
        print(f"tracebench-corpus: {exc}", file=sys.stderr)
        return 2
    print(
        f"wrote {payload.path}: {payload.prior_traces} prior traces from "
        f"{payload.prior_pull_requests} pull requests ({payload.prior_sessions} sessions, "
        f"cutoff basis {payload.cutoff_basis} at {payload.cutoff_time})"
    )
    if payload.adaptation:
        print(f"  adapted to {payload.adaptation}; {payload.excluded_by_adaptation} sessions filtered out")
    if payload.sessions_past_cutoff or payload.missing_sessions:
        print(
            f"  excluded {payload.sessions_past_cutoff} sessions past the cutoff; "
            f"{payload.missing_sessions} sessions lack a transcript or metadata"
        )
    return 0


def _skeleton(args: argparse.Namespace) -> int:
    try:
        skeleton = build_skeleton(
            args.payload,
            args.dest,
            org=args.org,
            base_image=args.base_image,
            workdir=args.workdir,
            task_version=args.task_version,
            force=args.force,
        )
    except (ValueError, OSError) as exc:
        print(f"tracebench-corpus: {exc}", file=sys.stderr)
        return 2
    print(
        f"wrote {skeleton.path}: Harbor task {skeleton.task_name} "
        f"with {skeleton.golden_tests} golden test files"
    )
    return 0
