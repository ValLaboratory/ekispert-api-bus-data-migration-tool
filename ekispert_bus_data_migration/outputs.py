from __future__ import annotations

import os

output_station = "変換結果-バスデータ.csv"
output_serialize = "移行先経路候補-経路シリアライズデータ.csv"
output_teiki = "変換結果-定期経路文字列.csv"

output_files = frozenset({output_station, output_serialize, output_teiki})


def is_output_file(name):
    """現行の結果ファイルと、再実行時に退避した結果ファイルを判定する。"""
    if name in output_files:
        return True
    return any(
        name.startswith(os.path.splitext(output)[0] + "-") and name.lower().endswith(".csv")
        for output in output_files
    )
