"""定期経路文字列（assignDetailRoute / assignRoute）の移行。"""

from __future__ import annotations

from dataclasses import dataclass

from .common import (
    ChangedNo,
    ChangedYes,
    StatusAmbiguous,
    StatusConverted,
    StatusNotTarget,
    StatusNoUpdate,
    failed,
    format_candidates,
    line_key,
    route_summary,
    summarize_names,
    valid_date,
)

RouteTypeDetail = "assignDetailRoute"
RouteTypeRoute = "assignRoute"

_FormatHint = {
    RouteTypeDetail: "バス停:路線:方向:…:バス停",
    RouteTypeRoute: "バス停:路線:…:バス停",
}


@dataclass
class TeikiInput:
    id: str = ""
    origin: str = ""
    destination: str = ""
    date: str = ""
    time: str = ""
    route_type: str = ""
    detail_route: str = ""


@dataclass
class TeikiResult:
    id: str = ""
    status: str = ""
    detail: str = ""
    new_detail_route: str = ""
    route_changed: str = ""
    old_route: str = ""
    new_route: str = ""


def teiki(c, inp):
    res = TeikiResult(id=inp.id)

    if c.config.target_data_start_date == "":
        res.status, res.detail = failed("移行プロファイルに target_data_start_date が設定されていません")
        return res

    if not valid_date(inp.date):
        res.status, res.detail = failed(
            "date は YYYYMMDD 形式の実在する日付で指定してください: %r" % inp.date
        )
        return res

    if inp.date < c.config.target_data_start_date:
        res.status, res.detail = failed(
            "dateは移行プロファイルの適用開始日(%s以降)を指定してください" % c.config.target_data_start_date
        )
        return res

    empty = [name for name in ("origin", "destination") if getattr(inp, name).strip() == ""]
    if empty:
        res.status, res.detail = failed(
            "%s が空です。定期区間の出発・到着駅を指定してください" % "・".join(empty)
        )
        return res

    kind, ok = parse_route_type(inp.route_type)
    if not ok:
        res.status, res.detail = failed(
            "route_type は %s（方向あり）か %s（方向なし）を指定してください: %r"
            % (RouteTypeDetail, RouteTypeRoute, inp.route_type)
        )
        return res

    if not valid_teiki_route(inp.detail_route, kind):
        res.status, res.detail = failed(
            "定期経路文字列が %s の形式（%s）になっていません。"
            "方向の有無が route_type と異なる場合は route_type を指定してください" % (kind, _FormatHint[kind])
        )
        return res

    stop_names, line_names, directions = split_teiki_route(inp.detail_route, kind)
    res.old_route = summarize_names(stop_names, line_names)

    if not any(c.table.by_old_name(name) for name in stop_names):
        res.status = StatusNotTarget
        res.detail = "定期経路文字列に対応表の旧バス停名が含まれないため移行対象外"
        return res

    try:
        current = c.target_client.search_course_extreme(
            {
                "viaList": inp.origin + ":" + inp.destination,
                "date": inp.date,
                "time": inp.time,
                "searchType": "departure",
                kind: inp.detail_route,
                "addAssignStatus": "true",
                "answerCount": "1",
            }
        )
    except Exception as e:
        res.status, res.detail = failed("割り当て確認に失敗: " + str(e))
        return res

    st = current.assign_status
    if st.code == "0" and st.require_update == "0":
        res.status = StatusNoUpdate
        res.detail = "定期経路は正常に割り当てられ、更新不要と判定されました"
        return res

    converted, notes, ambiguous = convert_stop_names(stop_names, c.table)
    if ambiguous:
        res.status = StatusAmbiguous
        res.detail = "同名バス停があり移行先を一意に決められないため自動変換しません。" + " / ".join(
            ambiguous
        )
        return res

    # 同名の別バス停を避けるため、バス停名ではなく新バス停コードを使う。
    via_parts = []
    for sc in converted:
        if sc.code != "":
            via_parts.append(sc.code)
        else:
            via_parts.append(sc.name)
    via_list = ":".join(via_parts)

    try:
        researched = c.target_client.search_course_extreme(
            {
                "viaList": via_list,
                "date": inp.date,
                "searchType": "plain",
                "answerCount": "1",
            }
        )
    except Exception as e:
        res.status, res.detail = failed("平均探索に失敗: " + str(e))
        return res

    res.new_route = route_summary(researched.route)
    stops_same = same_stops(converted, researched.route)
    lines_same = same_lines(line_names, directions, researched.route.lines(), kind)
    res.route_changed = ChangedNo if (stops_same and lines_same) else ChangedYes
    if not stops_same:
        notes.append("経由バス停・駅が変わりました")
    if not lines_same:
        notes.append("平均路線名・方向が変わりました")

    try:
        new_detail_route = build_teiki_route(researched.route, kind)
    except RuntimeError as e:
        res.status, res.detail = failed(str(e))
        return res

    try:
        applied = c.target_client.search_course_extreme(
            {
                "viaList": via_list,
                "date": inp.date,
                "searchType": "plain",
                kind: new_detail_route,
                "addAssignStatus": "true",
                "answerCount": "1",
            }
        )
    except Exception as e:
        res.status, res.detail = failed("新定期経路文字列の動作確認に失敗: " + str(e))
        return res
    applied_st = applied.assign_status
    if applied_st.code != "0" or applied_st.require_update != "0":
        res.status, res.detail = failed(
            "新定期経路文字列の割り当てを確認できませんでした "
            "(AssignStatus.code=%s, requireUpdate=%s)" % (applied_st.code, applied_st.require_update)
        )
        return res

    res.status = StatusConverted
    res.new_detail_route = new_detail_route
    if notes:
        res.detail = "新しい定期経路文字列の動作確認まで完了。要確認: " + " / ".join(notes)
    else:
        res.detail = "新しい定期経路文字列の動作確認まで完了"
    return res


def parse_route_type(value):
    value = value.strip()
    if value == "":
        return RouteTypeDetail, True
    if value in (RouteTypeDetail, RouteTypeRoute):
        return value, True
    return RouteTypeDetail, False


def _step(kind):
    """定期経路文字列の1区間あたりの要素数（方向ありは3、方向なしは2）。"""
    return 3 if kind == RouteTypeDetail else 2


def valid_teiki_route(detail_route, kind=RouteTypeDetail):
    """指定した形式の定期経路文字列になっているかを返す。"""
    if detail_route == "":
        return False
    step = _step(kind)
    n = len(detail_route.split(":"))
    return n >= step + 1 and n % step == 1


def split_teiki_route(detail_route, kind=RouteTypeDetail):
    """定期経路文字列をバス停名・路線名・方向に分解する。方向なしでは方向は空になる。"""
    step = _step(kind)
    stop_names = []
    line_names = []
    directions = []
    for i, tk in enumerate(detail_route.split(":")):
        if i % step == 0:
            stop_names.append(tk)
        elif i % step == 1:
            line_names.append(tk)
        else:
            directions.append(tk)
    return stop_names, line_names, directions


@dataclass
class StopConversion:
    name: str = ""
    code: str = ""


def convert_stop_names(names, table):
    """バス停名を対応表の新名称・新コードへ変換する。

    第3戻り値は一意に決められなかった名称の説明。空でなければ変換結果を採用しない。
    """
    out = []
    notes = []
    ambiguous = []
    for name in names:
        entries = table.by_old_name(name)
        if not entries:
            out.append(StopConversion(name=name))
            notes.append("対応表に存在しない名称のため変換していません: %s" % name)
        elif len(entries) > 1:
            out.append(StopConversion(name=entries[0].new_name, code=entries[0].new_code))
            ambiguous.append("%s の候補: %s" % (name, format_candidates(entries)))
        else:
            out.append(StopConversion(name=entries[0].new_name, code=entries[0].new_code))
    return out, notes, ambiguous


def same_stops(converted, new_route):
    """対応表で変換した駅と、平均探索で得た経路の駅が一致するかを返す。"""
    points = new_route.points()
    if len(converted) != len(points):
        return False
    for i, sc in enumerate(converted):
        st = points[i].station_first()
        if sc.code != "" and st.code != "":
            if sc.code != st.code:
                return False
        elif sc.name != st.name:
            return False
    return True


def same_lines(old_names, old_directions, new_lines, kind=RouteTypeDetail):
    """元の定期経路と平均探索結果の路線が同一かを返す。"""
    if len(old_names) != len(new_lines):
        return False
    for i, name in enumerate(old_names):
        if line_key(name) != line_key(new_lines[i].name):
            return False
        if kind == RouteTypeDetail:
            old_dir = old_directions[i] if i < len(old_directions) else ""
            if old_dir != new_lines[i].direction:
                return False
    return True


def build_teiki_route(route, kind=RouteTypeDetail):
    points = route.points()
    lines = route.lines()
    if len(points) < 2 or len(lines) != len(points) - 1:
        raise RuntimeError("探索結果から定期経路文字列を組み立てられませんでした")
    parts = []
    for i, ln in enumerate(lines):
        parts.append(points[i].station_first().name)
        parts.append(ln.name)
        if kind == RouteTypeDetail:
            parts.append(ln.direction)
    parts.append(points[-1].station_first().name)
    return ":".join(parts)
