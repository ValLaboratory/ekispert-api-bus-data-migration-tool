"""定期経路文字列の移行方式を確認する。"""

import json

import pytest

from ekispert_bus_data_migration import mapping
from ekispert_bus_data_migration.config import Config
from ekispert_bus_data_migration.ekispert import Client
from ekispert_bus_data_migration.migrate.common import (
    ChangedNo,
    ChangedYes,
    Common,
    StatusAmbiguous,
    StatusConverted,
    StatusFailed,
    StatusNotTarget,
    StatusNoUpdate,
)
from ekispert_bus_data_migration.migrate.teiki import RouteTypeRoute, TeikiInput, teiki

teiki_mapping_csv = (
    "旧コード,新コード,旧バス停名(フル),新バス停名(フル)\n"
    "841234,1514600,みどり町／サンプルバス※旧,みどり町／サンプルバス\n"
    "841235,1514601,こもれび橋／サンプルバス※旧,こもれび橋／サンプルバス\n"
    "849999,1514777,こもれび橋／サンプルバス※旧,こもれび橋北／サンプルバス\n"
)


@pytest.fixture
def table():
    return mapping.parse(teiki_mapping_csv)


def assign_param(query):
    """定期経路文字列を指定した探索か（方向あり・方向なしのどちらでも）。"""
    return "assignDetailRoute" in query or "assignRoute" in query


def route_json(names, line="サンプルバス・系統１"):
    points = [{"Station": {"Name": n}} for n in names]
    lines = [{"Name": line, "direction": "Down"} for _ in range(len(names) - 1)]
    return {"Point": points, "Line": lines}


def make_client(start_server, calls, assign_code="9", require_update="1", route_names=None):
    names = route_names or ["みどり町／サンプルバス", "中央駅"]

    def handler(path, query):
        calls.append(query)
        course = {"Route": route_json(names)}
        if assign_param(query):
            course["AssignStatus"] = {"code": assign_code, "requireUpdate": require_update}
        return 200, json.dumps({"ResultSet": {"Course": course}})

    return Client(start_server(handler), "k")


def base_input(**kw):
    kw.setdefault("id", "t1")
    kw.setdefault("origin", "22361")
    kw.setdefault("destination", "22671")
    kw.setdefault("date", "20300201")
    kw.setdefault("detail_route", "みどり町／サンプルバス※旧:サンプルバス※旧・系統１:Down:中央駅")
    return TeikiInput(**kw)


def common(table, target_data_start_date="20300115"):
    return Common(table=table, config=Config(target_data_start_date=target_data_start_date))


def test_missing_target_data_start_date_is_error(table):
    res = teiki(Common(table=table), base_input())
    assert res.status == StatusFailed
    assert "target_data_start_date" in res.detail


def test_date_before_target_data_start_date_is_error(table):
    c = common(table)
    res = teiki(c, base_input(date="20300114"))
    assert res.status == StatusFailed
    assert "20300115" in res.detail


def test_detail_route_without_mapping_name_is_not_target(table, start_server):
    """対応表の旧バス停名を含まない定期経路文字列は移行対象外。"""
    calls = []
    c = common(table)
    c.target_client = make_client(start_server, calls)
    res = teiki(c, base_input(detail_route="無関係駅A:ＪＲ総武線:Down:無関係駅B"))
    assert res.status == StatusNotTarget, res.detail
    assert calls == [], "対象外の判定は対応表だけで完結するためAPIを呼ばない"


def test_format_mismatching_route_type_is_rejected(table, start_server):
    """route_type と実際の形式が食い違う入力は、黙って誤分解せずエラーにする。"""
    calls = []
    c = common(table)
    c.target_client = make_client(start_server, calls)
    # 方向なしの文字列を既定（方向あり）のまま渡した場合
    res = teiki(c, base_input(detail_route="みどり町／サンプルバス※旧:サンプルバス※旧・系統１:中央駅"))
    assert res.status == StatusFailed, res.detail
    assert "assignDetailRoute" in res.detail
    assert calls == []


def test_unknown_route_type_is_error(table, start_server):
    """解釈できない route_type は既定へ倒さない。形式を取り違えると別物になるため。"""
    calls = []
    c = common(table)
    c.target_client = make_client(start_server, calls)
    res = teiki(c, base_input(route_type="ほうこうなし"))
    assert res.status == StatusFailed, res.detail
    assert "route_type" in res.detail
    assert calls == []


def test_no_update_needed(table, start_server):
    calls = []
    c = common(table)
    c.target_client = make_client(start_server, calls, assign_code="0", require_update="0")
    res = teiki(c, base_input())
    assert res.status == StatusNoUpdate, res.detail
    assert len(calls) == 1, "更新不要と分かった時点で以降の探索は行わない"
    assert res.old_route == "みどり町／サンプルバス※旧 →[サンプルバス※旧・系統１]→ 中央駅"
    assert res.route_changed == "", "再探索していない以上、変化の有無は判定できない"


def test_ambiguous_stop_name_is_not_converted(table, start_server):
    """同名バス停が複数ある場合、どちらを採るか決められないため自動変換しない。"""
    calls = []
    c = common(table)
    c.target_client = make_client(start_server, calls)
    res = teiki(
        c,
        base_input(detail_route="こもれび橋／サンプルバス※旧:サンプルバス※旧・系統１:Down:中央駅"),
    )
    assert res.status == StatusAmbiguous, res.detail
    assert res.new_detail_route == "", "一意に決められない以上、定期経路文字列は組み立てない"
    assert "1514601" in res.detail and "1514777" in res.detail


def test_verification_uses_same_conditions_as_average_search(table, start_server):
    """viaList・date は再探索と同じ値、searchType=plain で確認する。"""
    calls = []
    c = common(table)
    c.target_client = make_client(start_server, calls, assign_code="0", require_update="0")
    # 事前確認だけ「更新が必要」を返し、変換・再探索へ進ませる
    first = {"done": False}

    def handler(path, query):
        calls.append(query)
        course = {"Route": route_json(["みどり町／サンプルバス", "中央駅"])}
        if "assignDetailRoute" in query:
            if not first["done"]:
                first["done"] = True
                course["AssignStatus"] = {"code": "0", "requireUpdate": "1"}
            else:
                course["AssignStatus"] = {"code": "0", "requireUpdate": "0"}
        return 200, json.dumps({"ResultSet": {"Course": course}})

    c.target_client = Client(start_server(handler), "k")
    res = teiki(c, base_input())
    assert res.status == StatusConverted, res.detail

    average, verify = calls[1], calls[2]
    assert average.get("searchType") == "plain"
    # 対応表に載る駅は新コードへ、載らない駅は名称のまま（コードを知りえないため）
    assert average.get("viaList") == "1514600:中央駅"
    # 動作確認は再探索と同じ viaList・date、かつ plain
    assert verify.get("viaList") == average.get("viaList")
    assert verify.get("date") == average.get("date")
    assert verify.get("searchType") == "plain"
    assert "time" not in verify


def test_require_update_must_be_zero(table, start_server):
    """code=0 だけでなく requireUpdate=0 も確認する。"""
    calls = []

    def handler(path, query):
        calls.append(query)
        course = {"Route": route_json(["みどり町／サンプルバス", "中央駅"])}
        if "assignDetailRoute" in query:
            course["AssignStatus"] = {"code": "0", "requireUpdate": "1"}
        return 200, json.dumps({"ResultSet": {"Course": course}})

    c = common(table)
    c.target_client = Client(start_server(handler), "k")
    res = teiki(c, base_input())
    assert res.status == StatusFailed, res.detail
    assert "requireUpdate=1" in res.detail


def two_phase_client(start_server, calls, line="サンプルバス・系統１", names=None):
    """事前確認だけ「更新が必要」を返し、変換・再探索へ進ませるクライアント。"""
    points = names or ["みどり町／サンプルバス", "中央駅"]
    first = {"done": False}

    def handler(path, query):
        calls.append(query)
        course = {"Route": route_json(points, line=line)}
        if assign_param(query):
            if not first["done"]:
                first["done"] = True
                course["AssignStatus"] = {"code": "0", "requireUpdate": "1"}
            else:
                course["AssignStatus"] = {"code": "0", "requireUpdate": "0"}
        return 200, json.dumps({"ResultSet": {"Course": course}})

    return Client(start_server(handler), "k")


def test_assign_route_is_migrated_without_direction(table, start_server):
    """定期経路文字列は assignDetailRoute だけでなく assignRoute も対象。"""
    calls = []
    c = common(table)
    c.target_client = two_phase_client(start_server, calls)
    res = teiki(
        c,
        base_input(
            route_type="assignRoute",
            detail_route="みどり町／サンプルバス※旧:サンプルバス※旧・系統１:中央駅",
        ),
    )
    assert res.status == StatusConverted, res.detail
    # 方向を挟まない形式で組み立て直す
    assert res.new_detail_route == "みどり町／サンプルバス:サンプルバス・系統１:中央駅"
    # 入力と同じパラメーターで割り当て確認する（方向ありのパラメーターは使わない）
    for q in (calls[0], calls[2]):
        assert "assignRoute" in q
        assert "assignDetailRoute" not in q


def test_assign_route_round_trips_through_split_and_build(table, start_server):
    """方向なしでも、分解した駅数ぶんの区間を保って組み立て直せる。"""
    calls = []
    c = common(table)
    c.target_client = two_phase_client(
        start_server,
        calls,
        names=["みどり町／サンプルバス", "こもれび橋／サンプルバス", "中央駅"],
    )
    res = teiki(
        c,
        base_input(
            route_type=RouteTypeRoute,
            detail_route=(
                "みどり町／サンプルバス※旧:サンプルバス※旧・系統１:中央橋:サンプルバス※旧・系統１:中央駅"
            ),
        ),
    )
    assert res.status == StatusConverted, res.detail
    assert res.new_detail_route == (
        "みどり町／サンプルバス:サンプルバス・系統１:こもれび橋／サンプルバス:サンプルバス・系統１:中央駅"
    )


def test_route_changed_is_no_when_only_renamed(table, start_server):
    """会社名だけが変わった改称は「変化なし」。全件が変化ありでは判定にならないため。"""
    calls = []
    c = common(table)
    c.target_client = two_phase_client(start_server, calls)
    res = teiki(c, base_input())
    assert res.status == StatusConverted, res.detail
    assert res.route_changed == ChangedNo
    assert res.old_route == "みどり町／サンプルバス※旧 →[サンプルバス※旧・系統１]→ 中央駅"
    assert res.new_route == "みどり町／サンプルバス →[サンプルバス・系統１]→ 中央駅"


def test_route_changed_is_yes_when_line_differs(table, start_server):
    """発着が同じでも別路線を通る経路が返りうるため、差分として出す。"""
    calls = []
    c = common(table)
    c.target_client = two_phase_client(start_server, calls, line="サンプルバス・系統２")
    res = teiki(c, base_input())
    assert res.status == StatusConverted, res.detail
    assert res.route_changed == ChangedYes
    assert "平均路線名・方向が変わりました" in res.detail


def test_malformed_date_is_reported_as_format_error(table):
    """形式の誤りを「適用開始日より前」と同じ文言で返さない。

    区別しないと、日付を直しても直らない原因を利用者が探すことになる。
    """
    c = common(table)
    res = teiki(c, base_input(date="2030/02/01"))
    assert res.status == StatusFailed
    assert "YYYYMMDD" in res.detail


def test_nonexistent_date_is_error(table):
    c = common(table)
    res = teiki(c, base_input(date="20300230"))
    assert res.status == StatusFailed
    assert "YYYYMMDD" in res.detail


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"origin": ""}, "origin"),
        ({"destination": "  "}, "destination"),
        ({"origin": "", "destination": ""}, "origin・destination"),
    ],
)
def test_empty_origin_or_destination_is_error_before_api(table, start_server, overrides, expected):
    """必須の発着駅が空の行は、APIへ送らず入力の欠落として返す。

    空のまま割り当て確認へ送るとAPIエラーになり、原因が入力の欠落だと分からない。
    """
    calls = []
    c = common(table)
    c.target_client = make_client(start_server, calls)
    res = teiki(c, base_input(**overrides))
    assert res.status == StatusFailed, res.detail
    assert expected in res.detail
    assert calls == []
