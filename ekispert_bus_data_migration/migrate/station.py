"""バス停コード・バス停名称の変換。"""

from __future__ import annotations

from dataclasses import dataclass

from .common import (
    StatusAmbiguous,
    StatusConverted,
    StatusNotTarget,
    failed,
    format_candidates,
)


@dataclass
class StationInput:
    id: str = ""
    old_code: str = ""
    old_name: str = ""


@dataclass
class StationResult:
    id: str = ""
    status: str = ""
    detail: str = ""
    old_code: str = ""
    old_name: str = ""
    new_code: str = ""
    new_name: str = ""


def station(table, inp):
    res = StationResult(id=inp.id, old_code=inp.old_code, old_name=inp.old_name)

    # 旧コードを保持している場合は、名称へフォールバックせず旧コードだけで照合する。
    if inp.old_code != "":
        candidates = table.is_ambiguous_code(inp.old_code)
        if candidates:
            res.status = StatusAmbiguous
            res.detail = "旧コードの移行先が対応表に複数あるため自動変換しません。要確認。候補: " + (
                format_candidates(candidates)
            )
            return res
        e = table.by_old_code(inp.old_code)
        if e is not None:
            res.status = StatusConverted
            res.new_code = e.new_code
            res.new_name = e.new_name
            res.detail = "新コードへ変換しました"
            return res
        res.status = StatusNotTarget
        res.detail = "旧コードが対応表に見つからないため自動変換しません"
        return res

    if inp.old_name == "":
        res.status, res.detail = failed("旧コード・旧名称のいずれも指定されていません")
        return res

    entries = table.by_old_name(inp.old_name)
    if not entries:
        res.status = StatusNotTarget
        res.detail = "旧名称が対応表に見つからないため自動変換しません"
    elif len(entries) > 1:
        res.status = StatusAmbiguous
        res.detail = "同名のバス停が複数あるため自動変換しません。要確認。候補: " + format_candidates(entries)
    else:
        res.status = StatusConverted
        res.new_code = entries[0].new_code
        res.new_name = entries[0].new_name
        res.detail = "新コードへ変換しました（旧バス停名で照合）"
    return res
