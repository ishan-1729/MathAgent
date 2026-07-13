from pathlib import Path

from scripts.check_repo_skeleton import check_yaml_files, discover_yaml_files


def test_repository_yaml_is_strict_and_duplicate_free():
    assert check_yaml_files(discover_yaml_files()) == []


def test_strict_yaml_check_rejects_duplicate_mapping_keys(tmp_path: Path):
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text("receipts:\n  T1: first\n  T1: overwritten\n", encoding="utf-8")
    failures = check_yaml_files([duplicate], root=tmp_path)
    assert len(failures) == 1
    assert "duplicate key 'T1'" in failures[0]


def test_yaml_discovery_prunes_dependency_and_cache_trees(tmp_path: Path):
    visible = tmp_path / "profile.yaml"
    visible.write_text("name: visible\n", encoding="utf-8")
    hidden = tmp_path / ".venv" / "duplicate.yaml"
    hidden.parent.mkdir()
    hidden.write_text("key: first\nkey: second\n", encoding="utf-8")
    assert discover_yaml_files(tmp_path) == [visible]
