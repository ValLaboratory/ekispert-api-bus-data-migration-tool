"""新旧バス停対応表の読込・変換。"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from .csvio import _BOM_CHAR, read_utf8_file

column_candidates = {
    "old_code": ["旧コード", "旧バス停コード", "old_code"],
    "new_code": ["新コード", "新バス停コード", "new_code"],
    "old_name": ["旧バス停名(フル)", "旧バス停名（フル）", "旧バス停名", "old_name"],
    "new_name": ["新バス停名(フル)", "新バス停名（フル）", "新バス停名", "new_name"],
}


@dataclass
class Entry:
    old_code: str = ""
    new_code: str = ""
    old_name: str = ""
    new_name: str = ""


class Table:
    def __init__(self):
        self._by_old_code = {}
        self._by_old_name = {}
        self._dup_old_code = {}

    def duplicate_old_codes(self):
        """移行先が一意でない旧コードの一覧を返す。読み込み時の警告に使う。"""
        return sorted(self._dup_old_code)

    def add(self, entry):
        """対応表の1行を取り込む。重複と統合の扱いはこの表の不変条件のためここへ閉じる。"""
        prev = self._by_old_code.get(entry.old_code)
        if prev is None:
            self._by_old_code[entry.old_code] = entry
        elif prev.new_code != entry.new_code:
            self._dup_old_code.setdefault(entry.old_code, [prev]).append(entry)
        if entry.old_name == "":
            return
        entries = self._by_old_name.setdefault(entry.old_name, [])
        if all(x.new_code != entry.new_code for x in entries):
            entries.append(entry)

    def is_ambiguous_code(self, code):
        """旧コードの移行先が一意に決まらない場合、その候補を返す（一意なら空）。"""
        return self._dup_old_code.get(code, [])

    def ambiguous_codes(self, codes):
        """移行先が一意に決まらない旧コードを、与えられた順で重複なく返す。"""
        out = []
        for c in codes:
            if c in self._dup_old_code and c not in out:
                out.append(c)
        return out

    def contains_any_old_code(self, codes):
        """対応表の旧バス停コードが含まれるか（名称のみ変更でコード据え置きも対象）。"""
        return any(c in self._by_old_code for c in codes)

    def contains_unconverted_old_code(self, codes):
        """変換されるべきだったのに変換されていない旧バス停コードが含まれるか。"""
        for c in codes:
            e = self._by_old_code.get(c)
            if e is not None and e.new_code != e.old_code:
                return True
        return False

    def by_old_code(self, code):
        """旧コードに対応する行を返す。対応表に無ければ None。"""
        return self._by_old_code.get(code)

    def by_old_name(self, name):
        """旧名称に対応する行の候補を返す。対応表に無ければ空。"""
        return self._by_old_name.get(name, [])

    def convert_code(self, code):
        e = self._by_old_code.get(code)
        if e is not None:
            return e.new_code
        return code

    def convert_point(self, value):
        value = value.strip()
        e = self._by_old_code.get(value)
        if e is not None:
            if value in self._dup_old_code:
                return value, False
            return e.new_code, True
        entries = self._by_old_name.get(value, [])
        if len(entries) == 1:
            return entries[0].new_code, True
        if len(entries) > 1:
            return value, False
        return value, True

    def convert_via_list(self, via_list):
        parts = []
        ambiguous = []
        for p in via_list.split(":"):
            converted, ok = self.convert_point(p)
            parts.append(converted)
            if not ok:
                key = p.strip()
                entries = self._dup_old_code.get(key) or self._by_old_name.get(key, [])
                ambiguous.append(
                    "%s の候補: %s" % (key, " / ".join("%s:%s" % (e.new_code, e.new_name) for e in entries))
                )
        return ":".join(parts), ambiguous


def load(path):
    return parse(read_utf8_file(path, "対応表"))


def _cell(rec, i):
    return rec[i].strip() if i < len(rec) else ""


def parse(text):
    if text.startswith(_BOM_CHAR):
        text = text[1:]
    reader = csv.reader(io.StringIO(text), skipinitialspace=True)
    try:
        header = next(reader)
    except StopIteration:
        raise RuntimeError("対応表のヘッダーを読めません")
    idx = {}
    for i, name in enumerate(header):
        idx[name.strip().lower()] = i

    col_idx = {}
    for col, candidates in column_candidates.items():
        col_idx[col] = _resolve_column(idx, candidates)

    t = Table()
    for rec in reader:
        if not rec:
            continue
        old_code = _cell(rec, col_idx["old_code"])
        new_code = _cell(rec, col_idx["new_code"])
        if old_code == "" or new_code == "":
            # 対応先未定（新コード空欄）の行は変換対象外として除外する
            continue
        e = Entry(
            old_code=old_code,
            new_code=new_code,
            old_name=_cell(rec, col_idx["old_name"]),
            new_name=_cell(rec, col_idx["new_name"]),
        )
        t.add(e)
    return t


def _resolve_column(idx, candidates):
    for c in candidates:
        if c.lower() in idx:
            return idx[c.lower()]
    raise RuntimeError("対応表に列 %s のいずれかが見つかりません" % "/".join(candidates))
