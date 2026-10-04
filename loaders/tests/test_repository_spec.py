"""Tests for the repository adaptation spec."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tracebench_corpus import RepositorySpec, find_repository_spec, load_repository_specs

FIXTURES = yaml.safe_load((Path(__file__).parent / "testdata" / "repository_specs.yaml").read_text())


def test_invalid_fixture_names_are_present() -> None:
    names = {case["name"] for case in FIXTURES["invalid_specs"]}
    assert set(FIXTURES["required_names"]) <= names


def test_loads_valid_spec(tmp_path) -> None:
    path = tmp_path / "spec.yaml"
    path.write_text(FIXTURES["valid_spec"])
    assert [spec.to_dict() for spec in load_repository_specs(path)] == FIXTURES["expected"]


def test_default_is_go_peasant() -> None:
    specs = load_repository_specs()
    assert [spec.to_dict() for spec in specs] == FIXTURES["default_expected"]
    assert find_repository_spec(specs, "peasant-labs/peasant-prerelease-archive").framework == "go"


def test_first_match_wins() -> None:
    first = RepositorySpec("a/b", "go", "first test", "first build")
    second = RepositorySpec("a/b", "go", "second test", "second build")
    assert find_repository_spec([first, second], "a/b") is first


def test_unknown_repo_lists_known_matches() -> None:
    with pytest.raises(ValueError, match=r"'x/y'.*known matches: peasant-labs/peasant, peasant-labs/peasant-prerelease-archive"):
        find_repository_spec(load_repository_specs(), "x/y")


def test_unreadable_path_names_path(tmp_path) -> None:
    with pytest.raises(ValueError, match="cannot read repository spec"):
        load_repository_specs(tmp_path / "missing.yaml")


@pytest.mark.parametrize("case", FIXTURES["invalid_specs"], ids=lambda case: case["name"])
def test_spec_validation(tmp_path, case) -> None:
    path = tmp_path / "spec.yaml"
    path.write_text(case["spec"])
    with pytest.raises(ValueError, match=case["error"]) as info:
        load_repository_specs(path)
    assert str(path) in str(info.value)
