"""新旧バス停対応表CSVの自動検出。"""

from __future__ import annotations

import csv
import io
import os

from . import mapping
from .outputs import is_output_file

maxHeaderChars = 64 * 1024


def discover_mapping(explicit, input_path):
    """対応表CSVの場所を決める。明示指定が無い場合は、入力CSVの
    フォルダーとカレントフォルダー（直下のサブフォルダーを含む）から探す。"""
    if explicit != "":
        return explicit
    seen = set()
    dirs = []
    for d in (os.path.dirname(input_path) or ".", "."):
        abs_ = os.path.abspath(d)
        if abs_ in seen:
            continue
        seen.add(abs_)
        dirs.append(d)

    found = []
    for d in dirs:
        found.extend(find_mapping_csv(d))
    found = unique_paths(found)

    if not found:
        raise RuntimeError(
            "新旧バス停対応表のCSVが見つかりません。\n"
            "ダウンロードしたzipを展開し、その中のCSVを入力データと同じフォルダーに置くか、\n"
            "--mapping で場所を指定してください"
        )
    if len(found) == 1:
        return found[0]
    candidates = "\n".join("  %s" % os.path.abspath(p) for p in sorted(found, key=os.path.abspath))
    raise RuntimeError(
        "新旧バス停対応表のCSVが複数見つかったため、自動で選択できません。\n"
        "候補:\n%s\n"
        "--mapping で使用する対応表を指定してください" % candidates
    )


def find_mapping_csv(dir_):
    """dir とその直下のサブフォルダーから、対応表の列を持つCSVを探す。"""
    out = []
    try:
        entries = os.listdir(dir_)
    except OSError:
        return out
    for name in entries:
        p = os.path.join(dir_, name)
        if os.path.isdir(p):
            try:
                sub = os.listdir(p)
            except OSError:
                continue
            for sname in sub:
                if os.path.isdir(os.path.join(p, sname)):
                    continue
                sp = os.path.join(p, sname)
                if looks_like_mapping(sp):
                    out.append(sp)
            continue
        if looks_like_mapping(p):
            out.append(p)
    return out


def looks_like_mapping(path):
    """CSVのヘッダー行だけを読み、対応表として必要な列を持つかを判定する。"""
    if os.path.splitext(path)[1].lower() != ".csv":
        return False
    if is_output_file(os.path.basename(path)):
        return False
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            header_line = f.readline(maxHeaderChars)
    except (OSError, UnicodeDecodeError):
        return False
    if header_line == "":
        return False
    reader = csv.reader(io.StringIO(header_line))
    try:
        header = next(reader)
    except StopIteration:
        return False
    headers = {h.strip().lower() for h in header}
    return all(name.lower() in headers for name in mapping.column_names.values())


def unique_paths(paths):
    seen = set()
    out = []
    for p in paths:
        abs_ = os.path.abspath(p)
        if abs_ in seen:
            continue
        seen.add(abs_)
        out.append(p)
    return out
