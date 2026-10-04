"""Tests for the shared target-configuration spec."""

from __future__ import annotations

import pytest

from tracebench_corpus import TargetConfiguration, find_target_config, load_target_configs


def test_loads_testdata_matrix(target_config_spec) -> None:
    assert [config.name for config in target_config_spec] == [
        "claude-code-sonnet-high",
        "claude-code-sonnet-none",
        "opencode-gpt-medium",
        "codex-gpt-none",
    ]
    high = find_target_config(target_config_spec, "claude-code-sonnet-high")
    assert high.harness == "claude-code"
    assert high.model == "claude-sonnet-4-6"
    assert high.version is None
    assert high.thinking == "high"
    assert high.to_dict() == {
        "name": "claude-code-sonnet-high",
        "harness": "claude-code",
        "model": "claude-sonnet-4-6",
        "version": None,
        "thinking": "high",
    }
    pinned = find_target_config(target_config_spec, "opencode-gpt-medium")
    assert pinned.version == "1.18.34"


def test_to_dict_stubs_absent_axes_as_null() -> None:
    configuration = TargetConfiguration(name="unknown-level", harness="codex", model="gpt-5.2")
    assert configuration.to_dict() == {
        "name": "unknown-level",
        "harness": "codex",
        "model": "gpt-5.2",
        "version": None,
        "thinking": None,
    }


def test_find_unknown_configuration_raises(target_config_spec) -> None:
    with pytest.raises(ValueError, match="unknown target configuration"):
        find_target_config(target_config_spec, "nope")


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        ("target_configurations:\n  - name: a\n    thinking: extreme\n", "outside"),
        ("target_configurations:\n  - name: a\n  - name: a\n", "duplicate"),
        ("target_configurations:\n  - name: a\n    temperature: 0.2\n", "unknown keys"),
        ("target_configurations:\n  - name: a\n    harness: [x]\n", "must be a string"),
        ("target_configurations:\n  - name: a\n    version: [x]\n", "must be a string"),
        ("target_configurations:\n  - just-a-string\n", "not a mapping"),
        ("target_configurations: []\n", "non-empty"),
        ("target_configurations:\n  - harness: claude-code\n", "non-empty name"),
    ],
)
def test_spec_validation(tmp_path, spec: str, match: str) -> None:
    path = tmp_path / "spec.yaml"
    path.write_text(spec)
    with pytest.raises(ValueError, match=match):
        load_target_configs(path)
