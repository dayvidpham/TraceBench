"""Loader and bundle materializer for the TraceBench Peasant PR corpus."""

from .corpus import Bundle, Corpus, Trace, load_corpus
from .golden import GoldenSuiteError, glob_to_regex, materialize_golden_tests
from .oracle import build_oracle, write_oracle
from .repository_spec import DEFAULT_REPOSITORY_SPECS, RepositorySpec, find_repository_spec, load_repository_specs
from .skeleton import MAX_BODY_CHARS, Skeleton, build_skeleton, task_slug
from .verifier import grade, parse_go_test_json
from .target_config import THINKING_LEVELS, TargetConfiguration, find_target_config, load_target_configs
from .test_manifest import build_test_manifest
from .worktree import WorktreeError, materialize_worktree
from .task import (
    DEFAULT_TEST_PATTERNS,
    GENERATED_ENTRIES,
    TaskBuilder,
    TaskPayload,
    clear_generated,
    load_pr_index,
    payload_test_patterns,
    repo_family,
)

__all__ = [
    "Bundle",
    "Corpus",
    "DEFAULT_REPOSITORY_SPECS",
    "DEFAULT_TEST_PATTERNS",
    "GENERATED_ENTRIES",
    "GoldenSuiteError",
    "MAX_BODY_CHARS",
    "RepositorySpec",
    "Skeleton",
    "THINKING_LEVELS",
    "TargetConfiguration",
    "TaskBuilder",
    "TaskPayload",
    "Trace",
    "WorktreeError",
    "build_oracle",
    "build_skeleton",
    "build_test_manifest",
    "clear_generated",
    "find_repository_spec",
    "find_target_config",
    "glob_to_regex",
    "grade",
    "load_corpus",
    "load_pr_index",
    "load_repository_specs",
    "load_target_configs",
    "materialize_golden_tests",
    "materialize_worktree",
    "parse_go_test_json",
    "payload_test_patterns",
    "repo_family",
    "task_slug",
    "write_oracle",
]
