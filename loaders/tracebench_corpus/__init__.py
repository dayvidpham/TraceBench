"""Loader and bundle materializer for the TraceBench Peasant PR corpus."""

from .adaptation import Adaptation, find_adaptation, load_adaptations, session_thinking, thinking_matches
from .corpus import Bundle, Corpus, Trace, load_corpus
from .skeleton import Skeleton, build_skeleton, find_path_pattern, task_slug
from .task import (
    DEFAULT_TEST_PATTERNS,
    GENERATED_ENTRIES,
    TaskBuilder,
    TaskPayload,
    clear_generated,
    load_pr_index,
    repo_family,
)

__all__ = [
    "Adaptation",
    "Bundle",
    "Corpus",
    "DEFAULT_TEST_PATTERNS",
    "GENERATED_ENTRIES",
    "Skeleton",
    "TaskBuilder",
    "TaskPayload",
    "Trace",
    "build_skeleton",
    "clear_generated",
    "find_adaptation",
    "find_path_pattern",
    "load_adaptations",
    "load_corpus",
    "load_pr_index",
    "repo_family",
    "session_thinking",
    "task_slug",
    "thinking_matches",
]
