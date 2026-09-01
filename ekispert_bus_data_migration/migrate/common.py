"""各移行機能の共通ロジック。"""

from __future__ import annotations

import re
from datetime import date as _date

from ..config import Config

StatusConverted = "変換済み"
StatusCandidate = "移行先の候補"
StatusNotTarget = "対応表に該当なし"
StatusNoUpdate = "更新不要"
StatusAmbiguous = "要確認（同名バス停あり）"
StatusFailed = "エラー"

ChangedYes = "変化あり"
ChangedNo = "変化なし"

DefaultCandidateCount = 3
maxCandidateCount = 20
minSearchCount = 5


class Common:
    def __init__(self, table, candidate_count=DefaultCandidateCount, config=None):
        self.table = table
        self.candidate_count = candidate_count
        self.config = config if config is not None else Config()
        self.source_client = None
        self.target_client = None

    def candidate_count_for_api(self):
        n = self.candidate_count
        if n <= 0:
            n = DefaultCandidateCount
        n = min(n, maxCandidateCount)
        return n

    def search_count_for_api(self):
        n = self.candidate_count_for_api()
        n = max(n, minSearchCount)
        n = min(n, maxCandidateCount)
        return n


def failed(detail):
    return StatusFailed, detail


def valid_date(text):
    """`YYYYMMDD` 形式で実在する日付かを返す。"""
    if not re.fullmatch(r"\d{8}", text or ""):
        return False
    try:
        _date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return False
    return True


def format_candidates(entries):
    """同名バス停が複数ある場合に、候補の新コード・新名称を「新コード:新名称」形式で列挙する。"""
    return " / ".join("%s:%s" % (e.new_code, e.new_name) for e in entries)


def route_summary(route):
    """経路を「地点名 →[路線名]→ 地点名」形式の1行にする。"""
    return summarize_names(
        [p.station_first().name for p in route.points()],
        [ln.name for ln in route.lines()],
    )


def summarize_names(stop_names, line_names):
    """地点名・路線名の並びを「地点名 →[路線名]→ 地点名」形式の1行にする。"""
    b = []
    for i, name in enumerate(stop_names):
        if i > 0:
            ln = line_names[i - 1] if i - 1 < len(line_names) else ""
            b.append(" →[" + ln + "]→ ")
        b.append(name)
    return "".join(b)


def line_key(name):
    """路線名の比較キー。系統名（「・」以降）を返し、「・」が無ければ名称全体を返す。"""
    i = name.rfind("・")
    if i >= 0:
        return name[i + 1 :]
    return name
