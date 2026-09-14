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
    teiki_changed: str = ""
    old_teiki1: str = ""
    new_teiki1: str = ""
    old_teiki3: str = ""
    new_teiki3: str = ""
    old_teiki6: str = ""
    new_teiki6: str = ""


@dataclass
class SerializeResult:
    id: str = ""
    status: str = ""
    detail: str = ""
    candidates: list = field(default_factory=list)


@dataclass
class Switch:
    course: object = None  # 切替後の経路。切替が不要だった場合・再現できなかった場合は None
    labels: list = field(default_factory=list)  # 再現した切替の種類
    notes: list = field(default_factory=list)  # 再現できなかった理由
    price_failed: bool = False  # 金額種別・料金種別をそろえられなかった
    pass_failed: bool = False  # 定期券種別・車両種別をそろえられなかった


price_switches = (
    ("Fare", "fareIndex", "金額種別"),
    ("Charge", "chargeIndex", "料金種別"),
)
TeikiKinds = ("Teiki1", "Teiki3", "Teiki6")

pass_switches = (
    ("nikukanteiki", "passClassIndex", "定期券種別"),
    ("bycorporation", "passClassIndex", "定期券種別"),
    ("vehicle", "vehicleIndex", "定期券の車両種別"),
)


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
            res.detail = "旧経路の駅の移行先が対応表に複数あり一意に決められません。" + " / ".join(ambiguous)
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
        notes.append("旧経路の駅の移行先が対応表に複数あるため経路の変化を判定できません")
    else:
        if not stops_same:
            notes.append("経由バス停・駅が変わりました")
        cand.route_changed = ChangedNo if (stops_same and lines_same) else ChangedYes
    if not lines_same:
        notes.append("利用路線が変わりました")

    switch = reproduce_selection(c, old_course, new_course, found.serialize_data)
    priced = new_course
    if switch.course is not None:
        priced = switch.course
        cand.new_serialize_data = switch.course.serialize_data
    notes.extend(switch.notes)

    # 経路が同一でも金額だけ変わる場合を拾えるよう、経路とは独立したフラグにする。
    old_fare, ok1 = old_course.fare_total()
    new_fare, ok2 = priced.fare_total()
    if ok1 and ok2:
        cand.old_fare = str(old_fare)
        cand.new_fare = str(new_fare)
        if not switch.price_failed:
            if old_fare != new_fare:
                cand.fare_changed = ChangedYes
                notes.append("運賃・料金が変わりました (%d円 → %d円)" % (old_fare, new_fare))
            else:
                cand.fare_changed = ChangedNo
    teiki = []
    for kind in TeikiKinds:
        old_teiki, ok3 = old_course.teiki_total(kind)
        new_teiki, ok4 = priced.teiki_total(kind)
        if not (ok3 and ok4):
            continue
        setattr(cand, "old_" + kind.lower(), str(old_teiki))
        setattr(cand, "new_" + kind.lower(), str(new_teiki))
        teiki.append((kind, old_teiki, new_teiki))
    if teiki and not switch.pass_failed:
        moved = [(k, a, b) for k, a, b in teiki if a != b]
        cand.teiki_changed = ChangedYes if moved else ChangedNo
        if moved:
            notes.append(
                "定期代が変わりました (%s)" % " / ".join("%s: %d円 → %d円" % (k, a, b) for k, a, b in moved)
            )

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
    if switch.labels:
        cand.detail += "。旧経路の切替状態（%s）を再現しました" % "・".join(switch.labels)
    return cand


def reproduce_selection(c, old_course, new_course, serialize_data):
    sw = Switch()
    selections = {}
    for group, param, label, old_entries, new_entries in switch_targets(old_course, new_course):
        indices, notes = switch_indices(old_entries, new_entries, label)
        sw.notes.extend(notes)
        if notes:
            if group == "price":
                sw.price_failed = True
            else:
                sw.pass_failed = True
        if not indices:
            continue
        selections.setdefault(param, []).extend(indices)
        if label not in sw.labels:
            sw.labels.append(label)
    if not selections:
        return sw

    try:
        switched = c.target_client.course_recalculate(serialize_data, selections, True)
    except Exception as e:
        sw.labels = []
        sw.notes.append("切替(course/recalculate)に失敗: " + str(e))
        sw.price_failed = True
        sw.pass_failed = True
        return sw
    sw.course = switched
    return sw


def switch_targets(old_course, new_course):
    targets = []
    for kind, param, label in price_switches:
        targets.append(
            (
                "price",
                param,
                label,
                [p for p in old_course.price if p.kind == kind],
                [p for p in new_course.price if p.kind == kind],
            )
        )
    for kind, param, label in pass_switches:
        targets.append(
            (
                "pass",
                param,
                label,
                [s for s in old_course.pass_status if s.kind == kind],
                [s for s in new_course.pass_status if s.kind == kind],
            )
        )
    return targets


def switch_indices(old_entries, new_entries, label):
    old_sections = group_sections(old_entries)
    if not old_sections:
        return [], []
    if all(len(section) <= 1 for section in old_sections):
        return [], []
    new_sections = group_sections(new_entries)
    if len(old_sections) != len(new_sections):
        return [], ["%sの区間数が新旧で異なるため切替状態を再現できません" % label]

    indices = []
    notes = []
    for old_section, new_section in zip(old_sections, new_sections):
        want = selected_option(old_section)
        if want is None or option_key(want) == "":
            continue
        current = selected_option(new_section)
        if current is not None and option_key(current) == option_key(want):
            continue
        same = [e for e in new_section if option_key(e) == option_key(want)]
        if not same:
            notes.append("旧経路の%s「%s」が新経路にありません" % (label, option_name(want)))
            continue
        match = next((e for e in same if e.index), None)
        if match is None:
            notes.append(
                "%s「%s」は切替インデックスを取得できないため再現できません" % (label, option_name(want))
            )
            continue
        indices.append(match.index)
    return indices, notes


def group_sections(entries):
    order = []
    groups = {}
    for e in entries:
        key = (e.from_line_index, e.to_line_index)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(e)
    return [groups[key] for key in order]


def selected_option(section):
    for e in section:
        if e.selected == "true":
            return e
    return None


def option_key(entry):
    return entry.type or entry.name


def option_name(entry):
    return entry.name or entry.type


def ambiguous_point_notes(table, codes):
    """移行先が一意に決まらない駅コードと、その候補の説明を返す（一意なら空）。"""
    return [
        "%s の候補: %s" % (code, format_candidates(table.is_ambiguous_code(code)))
        for code in table.ambiguous_codes(codes)
    ]


def same_stops(table, old_route, new_route):
    """旧経路の駅を対応表で変換した結果が新経路の駅と一致するか（コードで比較）。"""
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
        raise RuntimeError("経路の駅が2未満のため発着駅を取得できません")
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
