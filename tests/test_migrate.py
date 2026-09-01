import json
import os

from ekispert_bus_data_migration import mapping
from ekispert_bus_data_migration.ekispert import Client, parse_route
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


# 運賃・所要時間・経由地点の差分を確認するためのレスポンス。
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
    """動作確認(course/edit)のレスポンス。運賃・所要時間・地点を指定して組み立てる。"""
    point_json = ",".join('{"Station":{"code":"%s","Name":"%s"}}' % (c, n) for c, n in points)
    return (
        '{"ResultSet":{"Course":{'
        '"Price":[{"kind":"FareSummary","Oneway":"%s"}],'
        '"Route":{"timeOnBoard":"%s","timeWalk":"5",'
        '"Point":[%s],'
        '"Line":[{"Name":"%s","direction":"Down"}]}}}}' % (fare, on_board, point_json, line)
    )


def must_table():
    return mapping.load(os.path.join(EXAMPLES_DIR, "mapping.csv"))


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
    """経路が同一でも運賃だけ変わる場合と、経由地点だけ変わる場合を区別して出力する。"""

    def source_handler(path, query):
        return 200, OLD_EDIT_WITH_PRICE

    source_url = start_server(source_handler)

    line = "サンプルバス・系統１"
    bodies = {
        # 経路も運賃も変わらない
        "CAND_SAME": priced_course(SAME_POINTS, line, "190", "20"),
        # 経路は同じだが運賃だけ変わる
        "CAND_FARE": priced_course(SAME_POINTS, line, "250", "22"),
        # 路線は同じだが経由地点が変わる
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

    # 経由地点だけの変化を「利用路線が変わりました」と取り違えない
    assert via.route_changed == ChangedYes
    assert via.fare_changed == ChangedNo
    assert "経由バス停・地点が変わりました" in via.detail
    assert "利用路線が変わりました" not in via.detail


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
    # 地点1個（区間が無い）はどちらの形式でも定期経路になりえない
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
        "old_code,new_code,old_name,new_name\n"
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
    "old_code,new_code,old_name,new_name\n"
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
    """旧経路の地点の移行先が複数ある場合、先頭候補へ黙って変換して再探索しない。

    via_list を指定しない経路は旧経路の地点をそのまま再探索の入力に使うため、
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
