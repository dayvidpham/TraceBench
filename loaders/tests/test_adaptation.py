"""Tests for the shared adaptation spec and matching."""

from __future__ import annotations

import pytest

from conftest import TESTDATA
from tracebench_corpus import (
    find_adaptation,
    load_adaptations,
    session_thinking,
    thinking_matches,
)


def test_loads_testdata_matrix(adaptation_spec) -> None:
    assert [adaptation.name for adaptation in adaptation_spec] == [
        "claude-code-sonnet-high",
        "claude-code-sonnet-none",
        "opencode-gpt-medium",
        "codex-gpt-none",
    ]
    high = find_adaptation(adaptation_spec, "claude-code-sonnet-high")
    assert high.harness == "claude-code"
    assert high.model == "claude-sonnet-4-6"
    assert high.thinking == "high"
    assert high.to_dict() == {
        "name": "claude-code-sonnet-high",
        "harness": "claude-code",
        "model": "claude-sonnet-4-6",
        "thinking": "high",
    }


def test_find_unknown_adaptation_raises(adaptation_spec) -> None:
    with pytest.raises(ValueError, match="unknown adaptation"):
        find_adaptation(adaptation_spec, "nope")


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        ("adaptations:\n  - name: a\n    thinking: extreme\n", "outside"),
        ("adaptations:\n  - name: a\n  - name: a\n", "duplicate"),
        ("adaptations:\n  - name: a\n    temperature: 0.2\n", "unknown keys"),
        ("adaptations:\n  - name: a\n    harness: [x]\n", "must be a string"),
        ("adaptations:\n  - just-a-string\n", "not a mapping"),
        ("adaptations: []\n", "non-empty"),
        ("adaptations:\n  - harness: claude-code\n", "non-empty name"),
    ],
)
def test_spec_validation(tmp_path, spec: str, match: str) -> None:
    path = tmp_path / "spec.yaml"
    path.write_text(spec)
    with pytest.raises(ValueError, match=match):
        load_adaptations(path)


def test_matching_axes(adaptation_spec) -> None:
    high = find_adaptation(adaptation_spec, "claude-code-sonnet-high")
    none = find_adaptation(adaptation_spec, "claude-code-sonnet-none")
    opencode = find_adaptation(adaptation_spec, "opencode-gpt-medium")

    high_meta = {"harness": "claude-code", "model": "claude-sonnet-4-6", "thinkingLevel": "high"}
    none_meta = {"harness": "claude-code", "model": "claude-sonnet-4-6", "thinkingLevel": "none"}
    opencode_meta = {"harness": "opencode", "model": "gpt-5.2-codex", "thinkingLevel": "medium"}

    assert high.matches(high_meta)
    assert not high.matches(none_meta)
    assert not high.matches(opencode_meta)
    assert none.matches(none_meta)
    assert not none.matches(high_meta)
    assert opencode.matches(opencode_meta)
    assert not opencode.matches(high_meta)
    assert not high.matches(None)
    assert not high.matches({"harness": "claude-code", "model": "other", "thinkingLevel": "high"})


def test_session_thinking_derivation() -> None:
    assert session_thinking({"stats": {"thoughtTokens": 5}}) == "thinking"
    assert session_thinking({"stats": {"thoughtTokens": 0}}) == "none"
    assert session_thinking({}) == "none"
    assert session_thinking({"thinkingLevel": "xhigh"}) == "xhigh"


def test_thinking_matching_uses_declared_level_or_binary_fallback() -> None:
    assert thinking_matches("high", {"stats": {"thoughtTokens": 5}})
    assert thinking_matches("high", {"thinkingLevel": "high"})
    assert not thinking_matches("high", {"thinkingLevel": "low"})
    assert not thinking_matches("none", {"stats": {"thoughtTokens": 5}})
    assert thinking_matches("none", {})
