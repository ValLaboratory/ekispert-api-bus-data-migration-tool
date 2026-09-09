import json
import os

from ekispert_bus_data_migration import mapping
from ekispert_bus_data_migration.ekispert import Client, parse_course, parse_route
from ekispert_bus_data_migration.migrate.common import (
    ChangedNo,
    ChangedYes,
    Common,
    StatusAmbiguous,
    StatusCandidate,
    StatusFailed,
    StatusNotTarget,
)
from ekispert_bus_data_migration.migrate.serialize import (
    SerializeInput,
    line_key,
    route_summary,
    same_lines,
    same_stops,
    serialize,
    switch_indices,
    switch_targets,
)
from ekispert_bus_data_migration.migrate.teiki import (
    RouteTypeRoute,
    build_teiki_route,
    parse_route_type,
    split_teiki_route,
    valid_teiki_route,
)

EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")

OLD_EDIT_RESPONSE = """{
  "ResultSet": {
    "Course": {
      "dataType": "onTimetable",
      "Route": {
        "Point": [
          {"Station": {"code": "841234", "Name": "みどり町／サンプルバス※旧"}},
          {"Station": {"code": "22361", "Name": "中央駅"}}
        ],
        "Line": [
          {
            "Name": "サンプルバス※旧・系統１",
            "direction": "Down",
            "DepartureState": {"Datetime": {"text": "2026-08-02T00:38:00+09:00"}}
          }
        ]
      }
    }
  }
}"""

TWO_CANDIDATES = """{"ResultSet":{"Course":[
    {"SerializeData":"CAND_1","Route":{"Point":[{"Station":{"code":"1514600","Name":"みどり町／サンプルバス"}},{"Station":{"code":"22361","Name":"中央駅"}}],
     "Line":[{"Name":"サンプルバス・系統１","direction":"Down"}]}},
    {"SerializeData":"CAND_2","Route":{"Point":[{"Station":{"code":"1514600","Name":"みどり町／サンプルバス"}},{"Station":{"code":"22361","Name":"中央駅"}}],
     "Line":[{"Name":"サンプルバス・系統２","direction":"Down"}]}}
]}}"""

NEW_SEARCH_RESPONSE = """{
  "ResultSet": {
    "Course": {
      "SerializeData": "NEW_SERIALIZED_DATA",
      "Route": {
        "Point": [
          {"Station": {"code": "1514600", "Name": "みどり町／サンプルバス"}},
          {"Station": {"code": "22361", "Name": "中央駅"}}
        ],
        "Line": [{"Name": "路線", "direction": "Down", "DepartureState": {"Datetime": {"text": "2026-09-01T08:00:00+09:00"}}}]
      }
    }
  }
}"""


def verify_response(line):
    return (
        '{"ResultSet":{"Course":{"Route":{'
        '"Point":[{"Station":{"code":"1514600","Name":"みどり町／サンプルバス"}},'
        '{"Station":{"code":"22361","Name":"中央駅"}}],'
        '"Line":[{"Name":"サンプルバス・' + line + '","direction":"Down"}]}}}}'
    )


# 運賃・所要時間・経由駅の差分を確認するためのレスポンス。
# 旧経路: みどり町(841234) → 中央駅(22361)、系統１、190円、25分。
OLD_EDIT_WITH_PRICE = """{
  "ResultSet": {
    "Course": {
      "dataType": "onTimetable",
      "Price": [{"kind": "FareSummary", "Oneway": "190"}],
      "Route": {
        "timeOnBoard": "20",
        "timeWalk": "5",
        "Point": [
          {"Station": {"code": "841234", "Name": "みどり町／サンプルバス※旧"}},
          {"Station": {"code": "22361", "Name": "中央駅"}}
        ],
        "Line": [
          {
            "Name": "サンプルバス※旧・系統１",
            "direction": "Down",
            "DepartureState": {"Datetime": {"text": "2026-08-02T00:38:00+09:00"}}
          }
        ]
      }
    }
  }
}"""

THREE_CANDIDATES = """{"ResultSet":{"Course":[
    {"SerializeData":"CAND_SAME"},
    {"SerializeData":"CAND_FARE"},
    {"SerializeData":"CAND_VIA"}
]}}"""

SAME_POINTS = [("1514600", "みどり町／サンプルバス"), ("22361", "中央駅")]
VIA_POINTS = [("1514600", "みどり町／サンプルバス"), ("1514700", "さくら競技場／サンプルバス")]


def priced_course(points, line, fare, on_board):
    """動作確認(course/edit)のレスポンス。運賃・所要時間・駅を指定して組み立てる。"""
    point_json = ",".join('{"Station":{"code":"%s","Name":"%s"}}' % (c, n) for c, n in points)
    return (
        '{"ResultSet":{"Course":{'
        '"Price":[{"kind":"FareSummary","Oneway":"%s"}],'
        '"Route":{"timeOnBoard":"%s","timeWalk":"5",'
        '"Point":[%s],'
        '"Line":[{"Name":"%s","direction":"Down"}]}}}}' % (fare, on_board, point_json, line)
    )


NORMAL_AND_IC = (
    ("1", "普通乗車券", "Fare", "190"),
    ("2", "ICカード乗車券", "FareICCard", "180"),
)
NORMAL_ONLY = (("1", "普通乗車券", "Fare", "190"),)


def fare_prices(summary, selected_index, options):
    entries = ['{"kind":"FareSummary","Oneway":"%s"}' % summary]
    for index, name, type_, oneway in options:
        entries.append(
            '{"kind":"Fare","index":"%s","fromLineIndex":"1","toLineIndex":"1",'
            '"selected":"%s","Oneway":"%s","Name":"%s","Type":"%s"}'
            % (index, "true" if index == selected_index else "false", oneway, name, type_)
        )
    return ",".join(entries)


def old_edit_with_fare_options(selected_index, summary, options=NORMAL_AND_IC):
    return (
        '{"ResultSet":{"Course":{"dataType":"onTimetable","Price":[%s],'
        '"Route":{"timeOnBoard":"20","timeWalk":"5",'
        '"Point":[{"Station":{"code":"841234","Name":"みどり町／サンプルバス※旧"}},'
        '{"Station":{"code":"22361","Name":"中央駅"}}],'
        '"Line":[{"Name":"サンプルバス※旧・系統１","direction":"Down",'
        '"DepartureState":{"Datetime":{"text":"2026-08-02T00:38:00+09:00"}}}]}}}}'
        % fare_prices(summary, selected_index, options)
    )


def new_edit_with_fare_options(selected_index, summary, options=NORMAL_AND_IC):
    return (
        '{"ResultSet":{"Course":{"Price":[%s],'
        '"Route":{"timeOnBoard":"20","timeWalk":"5",'
        '"Point":[{"Station":{"code":"1514600","Name":"みどり町／サンプルバス"}},'
        '{"Station":{"code":"22361","Name":"中央駅"}}],'
        '"Line":[{"Name":"サンプルバス・系統１","direction":"Down"}]}}}}'
        % fare_prices(summary, selected_index, options)
    )


def must_table():
    return mapping.load(os.path.join(EXAMPLES_DIR, "mapping.csv"))


def switch_case(start_server, old_body, new_body, recalculated=None):
    requests = []

    def target_handler(path, query):
        requests.append((path, query))
        if path == "/v1/json/search/course/extreme":
            return 200, NEW_SEARCH_RESPONSE
        if path == "/v1/json/course/edit":
            return 200, new_body
        if path == "/v1/json/course/recalculate":
            return 200, recalculated
        return 500, "{}"

    c = Common(table=must_table(), candidate_count=1)
    c.source_client = Client(start_server(lambda path, query: (200, old_body)), "test-key")
    c.target_client = Client(start_server(target_handler), "test-key")

    res = serialize(c, SerializeInput(id="route-switch", serialize_data="OLD_SERIALIZED_DATA"))
    assert res.status == StatusCandidate, res.detail
    assert len(res.candidates) == 1
    return res.candidates[0], requests


def test_serialize_returns_candidates(start_server):
    old_requests = []

    def source_handler(path, query):
        old_requests.append((path, query))
        return 200, OLD_EDIT_RESPONSE

    source_url = start_server(source_handler)

    new_requests = []

    def target_handler(path, query):
        new_requests.append((path, query))
        if path == "/v1/json/search/course/extreme":
            return 200, TWO_CANDIDATES
        if path == "/v1/json/course/edit":
            sd = query.get("serializeData")
            line = "系統１" if sd == "CAND_1" else "系統２"
            return 200, verify_response(line)
        return 500, "{}"

    target_url = start_server(target_handler)

    c = Common(table=must_table(), candidate_count=2)
    c.source_client = Client(source_url, "test-key")
    c.target_client = Client(target_url, "test-key")

    res = serialize(
        c,
        SerializeInput(id="route-001", serialize_data="OLD_SERIALIZED_DATA"),
    )

    assert res.status == StatusCandidate, res.detail
    assert len(res.candidates) == 2

    # 旧版 course/edit に checkEngineVersion=false が渡る
    assert old_requests[0][0] == "/v1/json/course/edit"
    assert old_requests[0][1].get("checkEngineVersion") == "false"

    search_reqs = [q for p, q in new_requests if p == "/v1/json/search/course/extreme"]
    assert len(search_reqs) == 1
    q = search_reqs[0]
    assert q.get("viaList") == "1514600:22361"
    assert q.get("date") == "20260802"
    assert q.get("time") == "0038"
    assert q.get("searchType") == "departure"
    assert q.get("answerCount") == "2"

    verified = [q.get("serializeData") for p, q in new_requests if p == "/v1/json/course/edit"]
    assert "CAND_1" in verified and "CAND_2" in verified

    assert res.candidates[0].no == 1
    assert res.candidates[0].new_serialize_data == "CAND_1"
    assert res.candidates[0].route_changed == ChangedNo
    assert res.candidates[1].route_changed == ChangedYes


def test_serialize_reports_fare_and_stop_changes(start_server):
    """経路が同一でも運賃だけ変わる場合と、経由駅だけ変わる場合を区別して出力する。"""

    def source_handler(path, query):
        return 200, OLD_EDIT_WITH_PRICE

    source_url = start_server(source_handler)

    line = "サンプルバス・系統１"
    bodies = {
        # 経路も運賃も変わらない
        "CAND_SAME": priced_course(SAME_POINTS, line, "190", "20"),
        # 経路は同じだが運賃だけ変わる
        "CAND_FARE": priced_course(SAME_POINTS, line, "250", "22"),
        # 路線は同じだが経由駅が変わる
        "CAND_VIA": priced_course(VIA_POINTS, line, "190", "20"),
    }

    def target_handler(path, query):
        if path == "/v1/json/search/course/extreme":
            return 200, THREE_CANDIDATES
        if path == "/v1/json/course/edit":
            return 200, bodies[query.get("serializeData")]
        return 500, "{}"

    target_url = start_server(target_handler)

    c = Common(table=must_table(), candidate_count=3)
    c.source_client = Client(source_url, "test-key")
    c.target_client = Client(target_url, "test-key")

    res = serialize(c, SerializeInput(id="route-002", serialize_data="OLD_SERIALIZED_DATA"))

    assert res.status == StatusCandidate, res.detail
    assert len(res.candidates) == 3

    same, fare, via = res.candidates

    assert same.route_changed == ChangedNo
    assert same.fare_changed == ChangedNo
    assert (same.old_fare, same.new_fare) == ("190", "190")
    assert (same.old_time, same.new_time) == ("25", "25")
    assert "差なし" in same.detail

    # 経路が変わらなくても運賃の変化を取りこぼさない
    assert fare.route_changed == ChangedNo
    assert fare.fare_changed == ChangedYes
    assert (fare.old_fare, fare.new_fare) == ("190", "250")
    assert (fare.old_time, fare.new_time) == ("25", "27")
    assert "運賃・料金が変わりました (190円 → 250円)" in fare.detail

    # 経由駅だけの変化を「利用路線が変わりました」と取り違えない
    assert via.route_changed == ChangedYes
    assert via.fare_changed == ChangedNo
    assert "経由バス停・駅が変わりました" in via.detail
    assert "利用路線が変わりました" not in via.detail


def test_serialize_reproduces_old_switch_state(start_server):
    recalculated = '{"ResultSet":{"Course":{"SerializeData":"NEW_SERIALIZED_DATA_IC","Price":[%s]}}}' % (
        fare_prices("180", "2", NORMAL_AND_IC)
    )
    cand, requests = switch_case(
        start_server,
        old_edit_with_fare_options("2", "180"),
        new_edit_with_fare_options("1", "190"),
        recalculated,
    )

    recalcs = [q for p, q in requests if p == "/v1/json/course/recalculate"]
    assert len(recalcs) == 1
    assert recalcs[0].get("serializeData") == "NEW_SERIALIZED_DATA"
    assert recalcs[0].get("fareIndex") == "2"
    assert recalcs[0].get("checkEngineVersion") == "true"

    assert cand.new_serialize_data == "NEW_SERIALIZED_DATA_IC"
    assert (cand.old_fare, cand.new_fare) == ("180", "180")
    assert cand.fare_changed == ChangedNo
    assert "旧経路の切替状態（金額種別）を再現しました" in cand.detail


def test_serialize_outputs_teiki_when_summary_is_available(start_server):

    def edit(code, name, teiki1, teiki3, old_side=False):
        head = '"dataType":"onTimetable",' if old_side else '"SerializeData":"NEW_SERIALIZED_DATA",'
        dep = ',"DepartureState":{"Datetime":{"text":"2026-08-02T00:38:00+09:00"}}' if old_side else ""
        prices = (
            '{"kind":"FareSummary","Oneway":"190"},'
            '{"kind":"Fare","index":"1","selected":"true","Oneway":"190",'
            '"fromLineIndex":"1","toLineIndex":"1","Type":"Fare"},'
            '{"kind":"Teiki1Summary","Oneway":"%s"},'
            '{"kind":"Teiki3Summary","Oneway":"%s"}' % (teiki1, teiki3)
        )
        return (
            '{"ResultSet":{"Course":{%s"Price":[%s],'
            '"Route":{"timeOnBoard":"20","timeWalk":"5",'
            '"Point":[{"Station":{"code":"%s","Name":"%s"}},'
            '{"Station":{"code":"22361","Name":"中央駅"}}],'
            '"Line":[{"Name":"サンプルバス・系統１","direction":"Down"%s}]}}}}'
            % (head, prices, code, name, dep)
        )

    def target_handler(path, query):
        if path == "/v1/json/search/course/extreme":
            return 200, NEW_SEARCH_RESPONSE
        return 200, edit("1514600", "みどり町／サンプルバス", "9000", "25000")

    c = Common(table=must_table(), candidate_count=1)
    old_body = edit("841234", "みどり町／サンプルバス※旧", "8550", "25000", old_side=True)
    c.source_client = Client(start_server(lambda path, query: (200, old_body)), "k")
    c.target_client = Client(start_server(target_handler), "k")

    res = serialize(c, SerializeInput(id="route-teiki", serialize_data="OLD_SERIALIZED_DATA"))

    assert res.candidates, res.detail
    cand = res.candidates[0]
    assert (cand.old_teiki1, cand.new_teiki1) == ("8550", "9000")
    assert (cand.old_teiki3, cand.new_teiki3) == ("25000", "25000")
    assert (cand.old_teiki6, cand.new_teiki6) == ("", "")
    assert cand.teiki_changed == ChangedYes
    assert "定期代が変わりました (Teiki1: 8550円 → 9000円)" in cand.detail


def test_serialize_reports_switch_option_missing(start_server):
    cand, requests = switch_case(
        start_server,
        old_edit_with_fare_options("2", "180"),
        new_edit_with_fare_options("1", "190", NORMAL_ONLY),
    )

    assert [p for p, q in requests if p == "/v1/json/course/recalculate"] == []
    assert cand.new_serialize_data == "NEW_SERIALIZED_DATA"
    assert (cand.old_fare, cand.new_fare) == ("180", "190")
    assert cand.fare_changed == ""
    assert "旧経路の金額種別「ICカード乗車券」が新経路にありません" in cand.detail
    assert "運賃・料金が変わりました" not in cand.detail


def test_serialize_skips_switch_when_selection_matches(start_server):
    cand, requests = switch_case(
        start_server,
        old_edit_with_fare_options("1", "190"),
        new_edit_with_fare_options("1", "190"),
    )

    assert [p for p, q in requests if p == "/v1/json/course/recalculate"] == []
    assert cand.fare_changed == ChangedNo
    assert "切替状態" not in cand.detail


def test_serialize_reports_failed_switch(start_server):

    def target_handler(path, query):
        if path == "/v1/json/search/course/extreme":
            return 200, NEW_SEARCH_RESPONSE
        if path == "/v1/json/course/edit":
            return 200, new_edit_with_fare_options("1", "190")
        return 400, "bad request"

    c = Common(table=must_table(), candidate_count=1)
    old_body = old_edit_with_fare_options("2", "180")
    c.source_client = Client(start_server(lambda path, query: (200, old_body)), "test-key")
    c.target_client = Client(start_server(target_handler), "test-key")

    res = serialize(c, SerializeInput(id="route-switch", serialize_data="OLD_SERIALIZED_DATA"))

    assert res.status == StatusCandidate, res.detail
    cand = res.candidates[0]
    assert cand.status == StatusCandidate
    assert cand.new_serialize_data == "NEW_SERIALIZED_DATA"
    assert cand.fare_changed == ""
    assert "切替(course/recalculate)に失敗" in cand.detail


def test_switch_targets_covers_price_and_pass_status():
    old = parse_course(
        {
            "Price": [
                {"kind": "Charge", "index": "1", "selected": "false", "Name": "自由席", "Type": "Free"},
                {"kind": "Charge", "index": "2", "selected": "true", "Name": "グリーン", "Type": "Green"},
            ],
            "PassStatus": [
                {"kind": "vehicle", "index": "1", "selected": "false", "Name": "普通車"},
                {"kind": "vehicle", "index": "2", "selected": "true", "Name": "グリーン車"},
            ],
        }
    )
    new = parse_course(
        {
            "Price": [
                {"kind": "Charge", "index": "1", "selected": "true", "Name": "自由席", "Type": "Free"},
                {"kind": "Charge", "index": "2", "selected": "false", "Name": "グリーン", "Type": "Green"},
            ],
            "PassStatus": [
                {"kind": "vehicle", "index": "1", "selected": "true", "Name": "普通車"},
                {"kind": "vehicle", "index": "2", "selected": "false", "Name": "グリーン車"},
            ],
        }
    )
    picked = {}
    groups = {}
    for group, param, label, old_entries, new_entries in switch_targets(old, new):
        indices, notes = switch_indices(old_entries, new_entries, label)
        assert notes == []
        if indices:
            picked[param] = indices
            groups[param] = group
    assert picked == {"chargeIndex": ["2"], "vehicleIndex": ["2"]}
    assert groups == {"chargeIndex": "price", "vehicleIndex": "pass"}


def test_switch_indices_maps_sections_in_order():

    def prices(selected_by_section):
        entries = []
        for section, selected in enumerate(selected_by_section, start=1):
            for index, type_ in (("1", "Fare"), ("2", "FareICCard")):
                entries.append(
                    {
                        "kind": "Fare",
                        "index": "%d%s" % (section, index),
                        "fromLineIndex": str(section),
                        "toLineIndex": str(section),
                        "selected": "true" if index == selected else "false",
                        "Type": type_,
                    }
                )
        return parse_course({"Price": entries}).price

    indices, notes = switch_indices(prices(["2", "1"]), prices(["1", "1"]), "金額種別")
    assert (indices, notes) == (["12"], [])


def test_switch_indices_gives_up_when_section_count_differs():

    def prices(sections):
        entries = []
        for section in range(1, sections + 1):
            for i, type_ in enumerate(("Fare", "FareICCard"), start=1):
                entries.append(
                    {
                        "kind": "Fare",
                        "index": "%d%d" % (section, i),
                        "fromLineIndex": str(section),
                        "toLineIndex": str(section),
                        "selected": "true" if type_ == "FareICCard" else "false",
                        "Type": type_,
                    }
                )
        return parse_course({"Price": entries}).price

    indices, notes = switch_indices(prices(1), prices(2), "金額種別")
    assert indices == []
    assert notes == ["金額種別の区間数が新旧で異なるため切替状態を再現できません"]


def test_switch_indices_skips_when_the_old_route_has_no_alternatives():

    def prices(sections):
        entries = [
            {
                "kind": "Fare",
                "index": str(section),
                "fromLineIndex": str(section),
                "toLineIndex": str(section),
                "selected": "true",
                "Type": "Fare",
            }
            for section in range(1, sections + 1)
        ]
        return parse_course({"Price": entries}).price

    assert switch_indices(prices(1), prices(4), "金額種別") == ([], [])


def test_switch_indices_uses_the_array_position_for_pass_status():

    def statuses(selected, types):
        return parse_course(
            {
                "PassStatus": [
                    {
                        "kind": "bycorporation",
                        "selected": "true" if t == selected else "false",
                        "fromLineIndex": "1",
                        "toLineIndex": "1",
                        "Type": t,
                        "Name": t,
                    }
                    for t in types
                ]
            }
        ).pass_status

    old = statuses("二区間定期", ["ＩＣ金額定期", "二区間定期"])
    indices, notes = switch_indices(old, statuses("ＩＣ金額定期", ["ＩＣ金額定期", "普通定期"]), "定期券種別")
    assert indices == []
    assert notes == ["旧経路の定期券種別「二区間定期」が新経路にありません"]

    new = statuses("ＩＣ金額定期", ["ＩＣ金額定期", "二区間定期"])
    indices, notes = switch_indices(old, new, "定期券種別")
    assert indices == ["2"]
    assert notes == []


def test_switch_indices_needs_an_index_to_switch():

    def prices(selected_type, types):
        entries = []
        for i, t in enumerate(types, start=1):
            entries.append(
                {
                    "kind": "Fare",
                    "index": "" if t == "FareICCard" else str(i),
                    "fromLineIndex": "1",
                    "toLineIndex": "1",
                    "selected": "true" if t == selected_type else "false",
                    "Type": t,
                }
            )
        return parse_course({"Price": entries}).price

    old = prices("FareICCard", ["Fare", "FareICCard"])
    new = prices("Fare", ["Fare", "FareICCard"])
    indices, notes = switch_indices(old, new, "金額種別")
    assert indices == []
    assert notes == ["金額種別「FareICCard」は切替インデックスを取得できないため再現できません"]


def test_serialize_all_candidates_fail(start_server):
    def source_handler(path, query):
        return 200, OLD_EDIT_RESPONSE

    source_url = start_server(source_handler)

    def target_handler(path, query):
        if path == "/v1/json/search/course/extreme":
            return 200, NEW_SEARCH_RESPONSE
        # 動作確認で旧コードが残存するケース
        return (
            200,
            '{"ResultSet":{"Course":{"Route":{"Point":[{"Station":{"code":"841234","Name":"旧"}}]}}}}',
        )

    target_url = start_server(target_handler)

    c = Common(table=must_table())
    c.source_client = Client(source_url, "test-key")
    c.target_client = Client(target_url, "test-key")

    res = serialize(c, SerializeInput(id="x", serialize_data="s"))
    assert res.status == StatusFailed, res.detail
    assert res.candidates and res.candidates[0].status == StatusFailed


def test_serialize_not_target(start_server):
    def source_handler(path, query):
        return (
            200,
            '{"ResultSet":{"Course":{"Route":{"Point":[{"Station":{"code":"11111","Name":"無関係"}}]}}}}',
        )

    source_url = start_server(source_handler)

    c = Common(table=must_table())
    c.source_client = Client(source_url, "test-key")
    c.target_client = Client("http://unused.invalid", "test-key")

    res = serialize(c, SerializeInput(id="x", serialize_data="s"))
    assert res.status == StatusNotTarget, res.detail


def test_split_teiki_route():
    stops, lines, directions = split_teiki_route("A:x1:Down:B")
    assert stops == ["A", "B"]
    assert lines == ["x1"]
    assert directions == ["Down"]


def test_split_teiki_route_without_direction():
    stops, lines, directions = split_teiki_route("A:x1:B:x2:C", RouteTypeRoute)
    assert stops == ["A", "B", "C"]
    assert lines == ["x1", "x2"]
    assert directions == []


def test_parse_route_type():
    """空欄時の既定値と正式な2形式だけを受け付ける。"""
    assert parse_route_type("") == ("assignDetailRoute", True)
    assert parse_route_type("assignDetailRoute") == ("assignDetailRoute", True)
    assert parse_route_type("assignRoute") == ("assignRoute", True)
    for value in ("AssignRoute", "route", "方向なし", "なんらか"):
        assert parse_route_type(value)[1] is False


def test_valid_teiki_route_by_type():
    """要素数だけでは両形式を判別できないため、形式ごとに検証する。"""
    assert valid_teiki_route("A:L:Down:B")
    assert not valid_teiki_route("A:L:B")
    assert valid_teiki_route("A:L:B", RouteTypeRoute)
    assert not valid_teiki_route("A:L:Down:B", RouteTypeRoute)
    # 駅1個（区間が無い）はどちらの形式でも定期経路になりえない
    assert not valid_teiki_route("A")
    assert not valid_teiki_route("A", RouteTypeRoute)


def test_build_teiki_route():
    route = parse_route(
        json.loads(
            '{"Point":[{"Station":{"Name":"みどり町／サンプルバス"}},'
            '{"Station":{"Name":"こもれび橋／サンプルバス"}}],'
            '"Line":[{"Name":"サンプルバス・系統１","direction":"Up"}]}'
        )
    )
    assert (
        build_teiki_route(route) == "みどり町／サンプルバス:サンプルバス・系統１:Up:こもれび橋／サンプルバス"
    )
    # 方向なしは方向を挟まずに組み立てる
    assert (
        build_teiki_route(route, RouteTypeRoute)
        == "みどり町／サンプルバス:サンプルバス・系統１:こもれび橋／サンプルバス"
    )


def test_route_diff_ignores_rename_only():
    def parse_route_json(s):
        return parse_route(json.loads(s))

    old_route = parse_route_json(
        '{"Point":[{"Station":{"code":"841234","Name":"みどり町／サンプルバス※旧"}},'
        '{"Station":{"code":"22361","Name":"中央駅"}}],'
        '"Line":[{"Name":"サンプルバス※旧・系統１","direction":"Down"}]}'
    )
    renamed_only = parse_route_json(
        '{"Point":[{"Station":{"code":"1514600","Name":"みどり町／サンプルバス"}},'
        '{"Station":{"code":"22361","Name":"中央駅"}}],'
        '"Line":[{"Name":"サンプルバス・系統１","direction":"Down"}]}'
    )
    different_line = parse_route_json(
        '{"Point":[{"Station":{"code":"1514600","Name":"みどり町／サンプルバス"}},'
        '{"Station":{"code":"22361","Name":"中央駅"}}],'
        '"Line":[{"Name":"サンプルバス・系統２","direction":"Down"}]}'
    )
    different_stop = parse_route_json(
        '{"Point":[{"Station":{"code":"1514601","Name":"こもれび橋／サンプルバス"}},'
        '{"Station":{"code":"22361","Name":"中央駅"}}],'
        '"Line":[{"Name":"サンプルバス・系統１","direction":"Down"}]}'
    )

    tb = must_table()
    assert same_stops(tb, old_route, renamed_only) == (True, True)
    assert same_lines(old_route, renamed_only)
    assert not same_lines(old_route, different_line)
    assert same_stops(tb, old_route, different_stop) == (False, True)
    assert route_summary(renamed_only) == "みどり町／サンプルバス →[サンプルバス・系統１]→ 中央駅"


def test_line_key_falls_back_to_full_name():
    assert line_key("サンプルバス・系統１") == "系統１"
    assert line_key("ＪＲ総武線") == "ＪＲ総武線"


def test_serialize_converts_via_list_by_name(start_server):
    """via_list をバス停名称で保持している場合も新コードへ変換する。

    コードだけを見ていると無変換のまま再探索が「成功」し、誤った経路が
    移行先候補になる。
    """

    def source_handler(path, query):
        return 200, OLD_EDIT_RESPONSE

    new_requests = []

    def target_handler(path, query):
        new_requests.append((path, query))
        if path == "/v1/json/search/course/extreme":
            return 200, NEW_SEARCH_RESPONSE
        return 200, verify_response("系統１")

    c = Common(table=must_table())
    c.source_client = Client(start_server(source_handler), "test-key")
    c.target_client = Client(start_server(target_handler), "test-key")

    res = serialize(
        c,
        SerializeInput(
            id="route-001",
            serialize_data="OLD",
            via_list="みどり町／サンプルバス※旧:22361",
            date="20260801",
            time="0800",
            search_type="departure",
        ),
    )
    assert res.status == StatusCandidate, res.detail
    search = next(q for p, q in new_requests if p == "/v1/json/search/course/extreme")
    assert search.get("viaList") == "1514600:22361"
    # 元の条件に無い値をこちらから足さない
    assert "sort" not in search


def test_serialize_stops_when_via_list_name_is_ambiguous(start_server):
    def source_handler(path, query):
        return 200, OLD_EDIT_RESPONSE

    new_requests = []

    def target_handler(path, query):
        new_requests.append(path)
        return 200, NEW_SEARCH_RESPONSE

    table = mapping.parse(
        "旧コード,新コード,旧バス停名(フル),新バス停名(フル)\n"
        "841234,1514600,みどり町／サンプルバス※旧,みどり町／サンプルバス\n"
        "841235,1514601,こもれび橋／サンプルバス※旧,こもれび橋／サンプルバス\n"
        "849999,1514777,こもれび橋／サンプルバス※旧,こもれび橋北／サンプルバス\n"
    )
    c = Common(table=table)
    c.source_client = Client(start_server(source_handler), "test-key")
    c.target_client = Client(start_server(target_handler), "test-key")

    res = serialize(
        c,
        SerializeInput(
            id="route-002",
            serialize_data="OLD",
            via_list="こもれび橋／サンプルバス※旧:22361",
        ),
    )
    assert res.status == StatusAmbiguous, res.detail
    assert new_requests == [], "一意に決められない以上、再探索してはならない"
    assert "1514601" in res.detail and "1514777" in res.detail


AMBIGUOUS_MAPPING_CSV = (
    "旧コード,新コード,旧バス停名(フル),新バス停名(フル)\n"
    "841234,1514600,みどり町／サンプルバス※旧,みどり町／サンプルバス\n"
    "841500,1514900,あおば台／サンプルバス※旧,あおば台／サンプルバス\n"
    "841500,1514901,あおば台南／サンプルバス※旧,あおば台南／サンプルバス\n"
)

OLD_EDIT_VIA_AMBIGUOUS = """{
  "ResultSet": {
    "Course": {
      "dataType": "onTimetable",
      "Route": {
        "Point": [
          {"Station": {"code": "841500", "Name": "あおば台／サンプルバス※旧"}},
          {"Station": {"code": "22361", "Name": "中央駅"}}
        ],
        "Line": [
          {
            "Name": "サンプルバス※旧・系統１",
            "direction": "Down",
            "DepartureState": {"Datetime": {"text": "2026-08-02T00:38:00+09:00"}}
          }
        ]
      }
    }
  }
}"""


def test_serialize_stops_when_old_route_code_has_multiple_destinations(start_server):
    """旧経路の駅の移行先が複数ある場合、先頭候補へ黙って変換して再探索しない。

    via_list を指定しない経路は旧経路の駅をそのまま再探索の入力に使うため、
    ここで先勝ちすると誤ったバス停を通る経路が「移行先の候補」として出てしまう。
    """
    new_requests = []

    def target_handler(path, query):
        new_requests.append(path)
        return 200, NEW_SEARCH_RESPONSE

    c = Common(table=mapping.parse(AMBIGUOUS_MAPPING_CSV))
    c.source_client = Client(start_server(lambda p, q: (200, OLD_EDIT_VIA_AMBIGUOUS)), "test-key")
    c.target_client = Client(start_server(target_handler), "test-key")

    res = serialize(c, SerializeInput(id="route-003", serialize_data="OLD"))

    assert res.status == StatusAmbiguous, res.detail
    assert new_requests == [], "一意に決められない以上、再探索してはならない"
    assert "1514900" in res.detail and "1514901" in res.detail


def test_route_diff_is_blank_when_old_code_is_ambiguous():
    """判定できない差分を「変化なし」に倒さない（SPEC 3.3: 判定できない場合は空）。"""
    tb = mapping.parse(AMBIGUOUS_MAPPING_CSV)
    old_route = parse_route(
        json.loads(
            '{"Point":[{"Station":{"code":"841500","Name":"あおば台／サンプルバス※旧"}},'
            '{"Station":{"code":"22361","Name":"中央駅"}}],'
            '"Line":[{"Name":"サンプルバス※旧・系統１","direction":"Down"}]}'
        )
    )
    new_route = parse_route(
        json.loads(
            '{"Point":[{"Station":{"code":"1514900","Name":"あおば台／サンプルバス"}},'
            '{"Station":{"code":"22361","Name":"中央駅"}}],'
            '"Line":[{"Name":"サンプルバス・系統１","direction":"Down"}]}'
        )
    )
    assert same_stops(tb, old_route, new_route) == (False, False)


PASS_ONLY_FAILURE_OLD = """{"ResultSet":{"Course":{
  "dataType":"onTimetable",
  "Price":[
    {"kind":"FareSummary","Oneway":"190"},
    {"kind":"Fare","index":"1","selected":"true","Oneway":"190","fromLineIndex":"1","toLineIndex":"1","Type":"Fare"},
    {"kind":"Teiki1Summary","Oneway":"8550"}
  ],
  "PassStatus":[
    {"kind":"vehicle","selected":"false","Name":"普通","fromLineIndex":"1","toLineIndex":"1"},
    {"kind":"vehicle","selected":"true","Name":"グリーン","fromLineIndex":"1","toLineIndex":"1"}
  ],
  "Route":{"timeOnBoard":"20","timeWalk":"5",
    "Point":[{"Station":{"code":"841234","Name":"みどり町／サンプルバス※旧"}},
             {"Station":{"code":"22361","Name":"中央駅"}}],
    "Line":[{"Name":"サンプルバス※旧・系統１","direction":"Down",
             "DepartureState":{"Datetime":{"text":"2026-08-02T00:38:00+09:00"}}}]}
}}}"""

PASS_ONLY_FAILURE_NEW = """{"ResultSet":{"Course":{
  "Price":[
    {"kind":"FareSummary","Oneway":"190"},
    {"kind":"Fare","index":"1","selected":"true","Oneway":"190","fromLineIndex":"1","toLineIndex":"1","Type":"Fare"},
    {"kind":"Teiki1Summary","Oneway":"9000"}
  ],
  "Route":{"timeOnBoard":"20","timeWalk":"5",
    "Point":[{"Station":{"code":"1514600","Name":"みどり町／サンプルバス"}},
             {"Station":{"code":"22361","Name":"中央駅"}}],
    "Line":[{"Name":"サンプルバス・系統１","direction":"Down"}]}
}}}"""


def test_pass_side_failure_does_not_blank_the_fare_judgement(start_server):
    cand, requests = switch_case(start_server, PASS_ONLY_FAILURE_OLD, PASS_ONLY_FAILURE_NEW)

    assert [p for p, _ in requests if p == "/v1/json/course/recalculate"] == []

    assert (cand.old_fare, cand.new_fare) == ("190", "190")
    assert cand.fare_changed == ChangedNo
    assert (cand.old_teiki1, cand.new_teiki1) == ("8550", "9000")
    assert cand.teiki_changed == ""
    assert "定期券の車両種別の区間数が新旧で異なる" in cand.detail
