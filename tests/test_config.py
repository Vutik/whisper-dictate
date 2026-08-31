"""Config loading, merging and forward-compatibility."""
import json

import pytest

from whisper_dictate import config


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    return path


def test_first_run_writes_defaults(isolated):
    cfg = config.load()
    assert isolated.exists()
    assert cfg["model"] == config.DEFAULTS["model"]
    assert json.loads(isolated.read_text())["hotkey"] == cfg["hotkey"]


def test_stored_values_win(isolated):
    isolated.write_text(json.dumps({"model": "tiny", "beam_size": 1}))
    cfg = config.load()
    assert cfg["model"] == "tiny"
    assert cfg["beam_size"] == 1


def test_missing_keys_are_backfilled_and_persisted(isolated):
    isolated.write_text(json.dumps({"model": "tiny"}))
    cfg = config.load()
    assert cfg["hotkey"] == config.DEFAULTS["hotkey"]
    assert "hotkey" in json.loads(isolated.read_text())


def test_backends_section_merges_per_key(isolated):
    """A user pinning one backend must not lose the defaults for the others."""
    isolated.write_text(json.dumps({"backends": {"inject": "x11"}}))
    cfg = config.load()
    assert cfg["backends"]["inject"] == "x11"
    assert cfg["backends"]["stt"] == "auto"
    assert set(cfg["backends"]) == set(config.DEFAULTS["backends"])


def test_corrupt_file_falls_back_to_defaults(isolated):
    isolated.write_text("{ this is not json")
    cfg = config.load()
    assert cfg["model"] == config.DEFAULTS["model"]


def test_defaults_are_not_mutated_by_load(isolated):
    before = json.dumps(config.DEFAULTS, sort_keys=True)
    cfg = config.load()
    cfg["model"] = "mutated"
    cfg["backends"]["stt"] = "mutated"
    assert json.dumps(config.DEFAULTS, sort_keys=True) == before


def test_every_default_is_json_serialisable():
    json.dumps(config.DEFAULTS)
