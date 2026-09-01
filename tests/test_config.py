import json
from pathlib import Path

import pytest

from ekispert_bus_data_migration.config import Config, load_config, missing_profile_fields


def test_load_config_defaults():
    cfg = load_config()
    assert cfg.profile_id == ""
    assert cfg.source_api_base_url == ""
    assert cfg.target_api_base_url == ""
    assert cfg.target_data_start_date == ""


def test_load_config_overrides_keys(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {
                "profile_id": "example-2027",
                "source_api_base_url": "https://source.example.com",
                "target_api_base_url": "https://target.example.com",
                "target_data_start_date": "20270101",
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(str(p))
    assert cfg.profile_id == "example-2027"
    assert cfg.source_api_base_url == "https://source.example.com"
    assert cfg.target_api_base_url == "https://target.example.com"
    assert cfg.target_data_start_date == "20270101"


@pytest.mark.parametrize("key", ["old_base_url", "new_base_url", "reorganize_date"])
def test_load_config_rejects_legacy_keys(tmp_path, key):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({key: "legacy-value"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match=key):
        load_config(str(p))


def test_load_config_rejects_unknown_keys(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"unknown_key": "x"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="unknown_key"):
        load_config(str(p))


@pytest.mark.parametrize(
    "data, message",
    [
        ({"target_data_start_date": "20270230"}, "target_data_start_date"),
        ({"source_api_base_url": "source.example.com"}, "source_api_base_url"),
        ({"target_api_base_url": 123}, "target_api_base_url"),
    ],
)
def test_load_config_rejects_invalid_values(tmp_path, data, message):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises((RuntimeError, TypeError), match=message):
        load_config(str(p))


def test_missing_profile_fields_are_function_specific():
    cfg = Config(profile_id="example", target_api_base_url="https://target.example.com")
    assert missing_profile_fields(cfg, serialized_route=True) == ["source_api_base_url"]
    assert missing_profile_fields(cfg, detail_route=True) == ["target_data_start_date"]
    assert missing_profile_fields(Config(), serialized_route=True, detail_route=True) == [
        "profile_id",
        "source_api_base_url",
        "target_api_base_url",
        "target_data_start_date",
    ]


def test_bundled_profile_has_all_api_settings():
    path = Path(__file__).parent.parent / "profiles" / "bus-data-migration-202608.json"
    cfg = load_config(str(path))
    assert cfg.profile_id == "bus-data-migration-202608"
    assert missing_profile_fields(cfg, serialized_route=True, detail_route=True) == []


def test_load_config_invalid_json(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError):
        load_config(str(p))


def test_load_config_missing_file(tmp_path):
    with pytest.raises(RuntimeError):
        load_config(str(tmp_path / "does-not-exist.json"))
