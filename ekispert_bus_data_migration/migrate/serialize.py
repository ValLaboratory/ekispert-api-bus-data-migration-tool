"""経路シリアライズデータ（Course/SerializeData）の移行。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .common import (
    ChangedNo,
    ChangedYes,
    StatusAmbiguous,
    StatusCandidate,
    StatusFailed,
    StatusNotTarget,
    failed,
    format_candidates,
    line_key,
    route_summary,
)


@dataclass
class SerializeInput:
    id: str = ""
    serialize_data: str = ""
    via_list: str = ""
    date: str = ""
    time: str = ""
    search_type: str = ""
    condition_detail: str = ""


@dataclass
class SerializeCandidate:
    no: int = 0
    status: str = ""
    detail: str = ""
    new_serialize_data: str = ""
    route_changed: str = ""
    old_route: str = ""
    new_route: str = ""
    fare_changed: str = ""
    old_fare: str = ""
    new_fare: str = ""
    old_time: str = ""
    new_time: str = ""


@dataclass
class SerializeResult:
    id: str = ""
    status: str = ""
    detail: str = ""
    candidates: list = field(default_factory=list)


def parse_rfc3339(text):
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def serialize(c, inp):
    res = SerializeResult(id=inp.id)

    try:
        old_course = c.source_client.course_edit(inp.serialize_data, False)
    except Exception as e:
        res.status, res.detail = failed("旧版course/editに失敗: " + str(e))
        return res

    if not c.table.contains_any_old_code(old_course.route.point_codes()):
        res.status = StatusNotTarget
        res.detail = "経路中に対応表の旧バス停コードが含まれないため移行対象外"
        return res
    via_list = ""
    if inp.via_list != "":
        via_list, ambiguous = c.table.convert_via_list(inp.via_list)
        if ambiguous:
            res.status = StatusAmbiguous
            res.detail = "via_list に同名バス停があり移行先を一意に決められません。" + " / ".join(ambiguous)
            return res
    else:
        ambiguous = ambiguous_point_notes(c.table, old_course.route.point_codes())
        if ambiguous:
            res.status = StatusAmbiguous
            res.detail = "旧経路の地点の移行先が対応表に複数あり一意に決められません。" + " / ".join(
                ambiguous
            )
            return res

    try:
        if inp.via_list != "":
            courses = research_with_conditions(c, inp, via_list)
        else:
            courses = research_from_serialized(c, old_course)
    except Exception as e:
        res.status, res.detail = failed("再探索に失敗: " + str(e))
        return res

    usable = 0
    for i, course in enumerate(courses):
        cand = verify_candidate(c, old_course, course, i + 1)
        if cand.status == StatusCandidate:
            usable += 1
        res.candidates.append(cand)

    if usable == 0:
        res.status = StatusFailed
        res.detail = "動作確認に成功した移行先候補がありませんでした"
        return res
    res.status = StatusCandidate
    res.detail = "%d件の候補を提示しました。採用する経路を選択してください" % usable
    return res


def verify_candidate(c, old_course, found, no):
    cand = SerializeCandidate(no=no, status=StatusFailed)
    if found.serialize_data == "":
        cand.detail = "SerializeDataが取得できませんでした"
        return cand
    try:
        new_course = c.target_client.course_edit(found.serialize_data, True)
    except Exception as e:
        cand.detail = "動作確認(course/edit)に失敗: " + str(e)
        return cand
    if c.table.contains_unconverted_old_code(new_course.route.point_codes()):
        cand.detail = "動作確認の結果、経路に旧バス停コードが残存しています"
        return cand

    cand.status = StatusCandidate
    cand.new_serialize_data = found.serialize_data
    cand.old_route = route_summary(old_course.route)
    cand.new_route = route_summary(new_course.route)

    notes = []
    stops_same, stops_ok = same_stops(c.table, old_course.route, new_course.route)
    lines_same = same_lines(old_course.route, new_course.route)
    if not stops_ok:
        # 判定できないものを「変化なし」に倒すと、確認が必要な行を見落とす。差分列は空のままとする。
        notes.append("旧経路の地点の移行先が対応表に複数あるため経路の変化を判定できません")
    else:
        if not stops_same:
            notes.append("経由バス停・地点が変わりました")
        cand.route_changed = ChangedNo if (stops_same and lines_same) else ChangedYes
    if not lines_same:
        notes.append("利用路線が変わりました")

    # 経路が同一でも金額だけ変わる場合を拾えるよう、経路とは独立したフラグにする。
    old_fare, ok1 = old_course.fare_total()
    if ok1:
        new_fare, ok2 = new_course.fare_total()
        if ok2:
            cand.old_fare = str(old_fare)
            cand.new_fare = str(new_fare)
            if old_fare != new_fare:
                cand.fare_changed = ChangedYes
                notes.append("運賃・料金が変わりました (%d円 → %d円)" % (old_fare, new_fare))
            else:
                cand.fare_changed = ChangedNo
    old_min, ok1 = old_course.route.total_minutes()
    if ok1:
        new_min, ok2 = new_course.route.total_minutes()
        if ok2:
            cand.old_time = str(old_min)
            cand.new_time = str(new_min)

    if notes:
        cand.detail = "移行先候補。要確認: " + " / ".join(notes)
    else:
        cand.detail = "移行先候補（元の経路・運賃と実質的な差なし）"
    return cand


def ambiguous_point_notes(table, codes):
    """移行先が一意に決まらない地点コードと、その候補の説明を返す（一意なら空）。"""
    return [
        "%s の候補: %s" % (code, format_candidates(table.is_ambiguous_code(code)))
        for code in table.ambiguous_codes(codes)
    ]


def same_stops(table, old_route, new_route):
    """旧経路の地点を対応表で変換した結果が新経路の地点と一致するか（コードで比較）。"""
    old_codes = old_route.point_codes()
    new_codes = new_route.point_codes()
    if table.ambiguous_codes(old_codes):
        return False, False
    if len(old_codes) != len(new_codes):
        return False, True
    for i in range(len(old_codes)):
        if table.convert_code(old_codes[i]) != new_codes[i]:
            return False, True
    return True, True


def same_lines(old_route, new_route):
    """利用路線が同一かを返す。系統名（「・」以降）で比較する。"""
    o = old_route.lines()
    n = new_route.lines()
    if len(o) != len(n):
        return False
    for i in range(len(o)):
        if line_key(o[i].name) != line_key(n[i].name):
            return False
    return True


def research_with_conditions(c, inp, via_list):
    return c.target_client.search_course_extreme_all(
        {
            "viaList": via_list,
            "date": inp.date,
            "time": inp.time,
            "searchType": inp.search_type,
            "conditionDetail": inp.condition_detail,
            "searchCount": str(c.search_count_for_api()),
            "answerCount": str(c.candidate_count_for_api()),
        }
    )


def research_from_serialized(c, old_course):
    points = old_course.route.points()
    lines = old_course.route.lines()
    if len(points) < 2:
        raise RuntimeError("経路の地点が2未満のため発着地点を取得できません")
    codes = [c.table.convert_code(p.station_first().code) for p in points]
    via = ":".join(codes)

    if old_course.data_type == "onTimetable":
        if not lines:
            raise RuntimeError("経路の区間情報がなく実出発日時を取得できません")
        try:
            dt = parse_rfc3339(lines[0].departure_state.datetime.text)
        except ValueError as e:
            raise RuntimeError(
                "出発日時(%s)をパースできません: %s" % (lines[0].departure_state.datetime.text, e)
            )
        return c.target_client.search_course_extreme_all(
            {
                "viaList": via,
                "date": dt.strftime("%Y%m%d"),
                "time": dt.strftime("%H%M"),
                "searchType": "departure",
                "sort": "ekispert",
                "searchCount": str(c.search_count_for_api()),
                "answerCount": str(c.candidate_count_for_api()),
            }
        )
    elif old_course.data_type == "plain":
        date = ""
        for ln in lines:
            try:
                t = parse_rfc3339(ln.departure_state.datetime.text)
            except ValueError:
                continue
            date = t.strftime("%Y%m%d")
            break
        return c.target_client.search_course_extreme_all(
            {
                "viaList": via,
                "date": date,
                "searchType": "plain",
                "sort": "ekispert",
                "searchCount": str(c.search_count_for_api()),
                "answerCount": str(c.candidate_count_for_api()),
            }
        )
    else:
        raise RuntimeError("未対応のdataType: %r" % old_course.data_type)
