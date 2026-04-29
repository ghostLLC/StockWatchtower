from __future__ import annotations

import json
from pathlib import Path

import pytest

from config_loader import BASE_DIR, CONFIG_DIR, load_json, save_json


class TestLoadJson:
    def test_reads_temp_json(self, tmp_path: Path) -> None:
        data = {"key": "value", "num": 42}
        fpath = tmp_path / "test.json"
        fpath.write_text(json.dumps(data), encoding="utf-8")
        assert load_json(fpath) == data

    def test_reads_empty_dict(self, tmp_path: Path) -> None:
        fpath = tmp_path / "empty.json"
        fpath.write_text("{}", encoding="utf-8")
        assert load_json(fpath) == {}


class TestSaveJson:
    def test_writes_and_round_trips(self, tmp_path: Path) -> None:
        data = {"a": 1, "b": [2, 3], "c": {"nested": True}}
        fpath = tmp_path / "saved.json"
        save_json(fpath, data)
        assert fpath.exists()
        loaded = json.loads(fpath.read_text(encoding="utf-8"))
        assert loaded == data

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        data = {"hello": "world"}
        fpath = tmp_path / "sub" / "dir" / "cfg.json"
        save_json(fpath, data)
        assert fpath.exists()
        assert load_json(fpath) == data


class TestBaseDir:
    def test_base_dir_is_path(self) -> None:
        assert isinstance(BASE_DIR, Path)

    def test_base_dir_contains_src(self) -> None:
        assert (BASE_DIR / "src").is_dir()

    def test_config_dir_is_path(self) -> None:
        assert isinstance(CONFIG_DIR, Path)

    def test_config_dir_under_base_dir(self) -> None:
        assert CONFIG_DIR.parent == BASE_DIR
        assert CONFIG_DIR.name == "config"
