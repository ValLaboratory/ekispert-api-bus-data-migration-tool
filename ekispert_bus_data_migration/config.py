"""移行プロファイルの読み込みと検証。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse


@dataclass
class Config:
    profile_id: str = ""
    source_api_base_url: str = ""
    target_api_base_url: str = ""
    target_data_start_date: str = ""


_profile_keys = frozenset(Config.__dataclass_fields__)


def load_config(path=None):
    """JSON形式の移行プロファイルを読み込む。"""
    if not path:
        return Config()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise RuntimeError("移行プロファイルを開けません: %s" % e) from None
    except json.JSONDecodeError as e:
        raise RuntimeError("移行プロファイルのパースに失敗しました: %s" % e) from None
    if not isinstance(data, dict):
        raise TypeError("移行プロファイルはJSONオブジェクトで指定してください")

    normalized = {}
    for key, value in data.items():
        if key not in _profile_keys:
            raise RuntimeError("移行プロファイルに不明なキーがあります: %s" % key)
        if not isinstance(value, str):
            raise TypeError("移行プロファイルの %s は文字列で指定してください" % key)
        normalized[key] = value.strip()

    cfg = Config(**normalized)
    validate_config_values(cfg)
    return cfg


def validate_config_values(config):
    """設定済みの値が安全に利用できる形式か確認する。"""
    if config.target_data_start_date:
        try:
            if not re.fullmatch(r"\d{8}", config.target_data_start_date):
                raise ValueError
            date(
                int(config.target_data_start_date[0:4]),
                int(config.target_data_start_date[4:6]),
                int(config.target_data_start_date[6:8]),
            )
        except (TypeError, ValueError):
            raise RuntimeError(
                "target_data_start_date は実在する日付を YYYYMMDD 形式で指定してください"
            ) from None

    for key in ("source_api_base_url", "target_api_base_url"):
        value = getattr(config, key)
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise RuntimeError("%s は http:// または https:// から始まるURLで指定してください" % key)


def missing_profile_fields(config, serialized_route=False, detail_route=False):
    """指定された機能に必要だが未設定の移行プロファイル項目を返す。"""
    required = set()
    if serialized_route or detail_route:
        required.add("profile_id")
        required.add("target_api_base_url")
    if serialized_route:
        required.add("source_api_base_url")
    if detail_route:
        required.add("target_data_start_date")
    return sorted(key for key in required if not getattr(config, key))
