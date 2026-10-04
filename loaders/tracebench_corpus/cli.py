"""Command line interface for the TraceBench corpus loader."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .corpus import Corpus, load_corpus
from .oracle import build_oracle, payload_commits, write_oracle
from .skeleton import build_skeleton
from .target_config import find_target_config, load_target_configs
from .task import TaskBuilder, load_pr_index
from .test_manifest import build_test_manifest

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
        "--target-configs",
        default=None,
        help="target-configuration spec (YAML or JSON) with harness/model/thinking entries",
    )
    task_parser.add_argument(
        "--target-config",
        default=None,
        help="target configuration name from --target-configs; recorded in the payload",
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
    manifest_parser = commands.add_parser(
        "test-manifest", help="inventory Go tests and mark PR-added or modified cases"
    )
    manifest_parser.add_argument("--repo-dir", required=True, help="local repository clone")
    manifest_parser.add_argument("--base-commit", required=True, help="pre-PR commit")
    manifest_parser.add_argument("--merge-commit", required=True, help="merged PR commit")
    manifest_parser.add_argument("--dest", required=True, help="output JSON file")

    oracle_parser = commands.add_parser(
        "oracle", help="generate and verify the oracle patch and solve.sh for a payload"
    )
    oracle_parser.add_argument("pr", help="pull request id, e.g. peasant-labs/peasant#343")
    oracle_parser.add_argument("--repo-dir", required=True, help="local repository clone")
    oracle_parser.add_argument("--payload", required=True, help="task payload directory")
    build_source = oracle_parser.add_mutually_exclusive_group()
    build_source.add_argument(
        "--build-command", default=None, help="build command run by solve.sh after the patch"
    )
    build_source.add_argument(
        "--spec", default=None, help="repository adaptation spec supplying the build command"
    )

    args = parser.parse_args(argv)
    if args.command == "oracle":
        return _oracle(args)
    if args.command == "skeleton":
        return _skeleton(args)
    if args.command == "test-manifest":
        return _test_manifest(args)
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
        target_config = None
        if args.target_config or args.target_configs:
            if not (args.target_config and args.target_configs):
                raise ValueError("--target-config and --target-configs must be used together")
            target_config = find_target_config(
                load_target_configs(args.target_configs), args.target_config
            )
        builder = TaskBuilder(corpus, pr_index=index, repo_dir=args.repo_dir)
        payload = builder.build(
            args.pr, Path(args.dest), force=args.force, target_config=target_config
        )
    except (KeyError, ValueError, OSError) as exc:
        print(f"tracebench-corpus: {exc}", file=sys.stderr)
        return 2
    print(
        f"wrote {payload.path}: {payload.prior_traces} prior traces from "
        f"{payload.prior_pull_requests} pull requests ({payload.prior_sessions} sessions, "
        f"cutoff basis {payload.cutoff_basis} at {payload.cutoff_time})"
    )
    if payload.target_config:
        print(f"  target configuration: {payload.target_config}")
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


def _test_manifest(args: argparse.Namespace) -> int:
    try:
        manifest = build_test_manifest(args.repo_dir, args.base_commit, args.merge_commit)
        destination = Path(args.dest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(manifest, indent=2) + "\n")
    except (ValueError, OSError) as exc:
        print(f"tracebench-corpus: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {destination}: {len(manifest['suites'])} test suites")
    return 0


def _spec_build_command(spec_path: str, pr_id: str) -> str | None:
    """Build command for ``pr_id``'s repository from a repository adaptation spec."""
    try:
        from . import repository_spec
    except ImportError as exc:
        raise ValueError(
            "--spec requires the repository adaptation spec module "
            "(tracebench_corpus.repository_spec), which is not installed; "
            "pass --build-command instead"
        ) from exc
    repo = pr_id.split("#", 1)[0]
    specs = repository_spec.load_repository_specs(spec_path)
    spec = repository_spec.select_repository_spec(specs, repo)
    return getattr(spec, "build_command", None)


def _oracle(args: argparse.Namespace) -> int:
    try:
        build_command = args.build_command
        if args.spec:
            build_command = _spec_build_command(args.spec, args.pr)
        tree_commit, merge_commit = payload_commits(args.payload, args.repo_dir)
        oracle = build_oracle(args.repo_dir, tree_commit, merge_commit, build_command)
        solution = write_oracle(args.payload, oracle)
    except (KeyError, ValueError, OSError) as exc:
        print(f"tracebench-corpus: oracle for {args.pr}: {exc}", file=sys.stderr)
        return 2
    print(
        f"wrote {solution}: oracle for {args.pr} changes {len(oracle.changed_files)} files; "
        f"applied tree {oracle.applied_tree} verified"
    )
    return 0
