from ekispert_bus_data_migration import ekispert

course_with_price = {
    "dataType": "onTimetable",
    "Route": {
        "timeOnBoard": "9",
        "timeWalk": "14",
        "timeOther": "3",
        "Point": [
            {"Station": {"code": "1", "Name": "A"}},
            {"Station": {"code": "2", "Name": "B"}},
        ],
        "Line": [{"Name": "路線", "direction": "Down"}],
    },
    "Price": [
        {
            "kind": "Fare",
            "index": "1",
            "selected": "true",
            "Oneway": "170",
            "Round": "340",
        },
        {"kind": "FareSummary", "Oneway": "170", "Round": "340"},
        {"kind": "Charge", "selected": "true", "Oneway": "530", "Round": "1060"},
        {"kind": "ChargeSummary", "Oneway": "530", "Round": "1060"},
        {"kind": "Teiki1Summary", "Oneway": "6800", "Round": "6800"},
    ],
}


def must_course(obj):
    return ekispert.parse_course(obj)


def test_fare_total_uses_summary_and_sums_fare_and_charge():
    c = must_course(course_with_price)
    got, ok = c.fare_total()
    assert ok
    assert got == 700


def test_fare_total_falls_back_to_selected_sections():
    c = must_course(
        {
            "Price": [
                {"kind": "Fare", "selected": "true", "Oneway": "100"},
                {"kind": "Fare", "selected": "true", "Oneway": "250"},
                {"kind": "Fare", "selected": "false", "Oneway": "9999"},
            ]
        }
    )
    got, ok = c.fare_total()
    assert ok
    assert got == 350


def test_teiki_total_uses_summary_only():
    c = must_course(
        {
            "Price": [
                {"kind": "Teiki1Summary", "Oneway": "8550"},
                {"kind": "Teiki1", "selected": "true", "Oneway": "5000"},
                {"kind": "Teiki1", "selected": "true", "Oneway": "5000"},
                {"kind": "Teiki3Summary", "Oneway": "24370"},
            ]
        }
    )
    assert c.teiki_total("Teiki1") == (8550, True)
    assert c.teiki_total("Teiki3") == (24370, True)


def test_teiki_total_without_summary_is_not_available():
    c = must_course({"Price": [{"kind": "Teiki6", "selected": "true", "Oneway": "47710"}]})
    _, ok = c.teiki_total("Teiki6")
    assert not ok


def test_fare_total_absent():
    c = must_course({"Route": {"Point": []}})
    _, ok = c.fare_total()
    assert not ok


def test_price_accepts_single_object():
    c = must_course({"Price": {"kind": "FareSummary", "Oneway": "210"}})
    got, ok = c.fare_total()
    assert ok and got == 210


def test_route_total_minutes():
    c = must_course(course_with_price)
    got, ok = c.route.total_minutes()
    assert ok
    assert got == 26

    empty = must_course({"Route": {"Point": []}})
    _, ok = empty.route.total_minutes()
    assert not ok


def test_course_accepts_single_object():
    # ResultSet/Course が配列でなくオブジェクトで返るケース（course/edit は単一）
    obj = {"ResultSet": {"Course": {"SerializeData": "X"}}}
    resp = ekispert.parse_response(obj)
    assert len(resp.result_set.course) == 1
    assert resp.result_set.course[0].serialize_data == "X"


def test_parse_station_reads_capitalized_name():
    """バス停名は Station/Name（先頭大文字）から読む。

    実APIのレスポンスは Name だが、モックを小文字の name で書くとテストは通り、
    実データでのみ名称が空になる。定期経路文字列移行はこの名称から文字列を組み立てるため、
    空になると組み立て結果が壊れる。退行しやすい箇所のため固定する。
    """
    course = ekispert.parse_course(
        {
            "Route": {
                "Point": [{"Station": {"code": "1514600", "Name": "みどり町／サンプルバス"}}],
                "Line": [],
            }
        }
    )
    st = course.route.points()[0].station_first()
    assert st.code == "1514600"
    assert st.name == "みどり町／サンプルバス"


def test_parse_price_reads_switch_attributes():
    c = must_course(
        {
            "Price": [
                {
                    "kind": "Fare",
                    "index": "2",
                    "fromLineIndex": "1",
                    "toLineIndex": "1",
                    "selected": "true",
                    "Oneway": "180",
                    "Name": "ICカード乗車券",
                    "Type": "FareICCard",
                }
            ]
        }
    )
    p = c.price[0]
    assert (p.index, p.type, p.name) == ("2", "FareICCard", "ICカード乗車券")
    assert (p.from_line_index, p.to_line_index) == ("1", "1")


def test_parse_price_reads_pass_class_index():
    c = must_course(
        {
            "Price": [
                {
                    "kind": "Teiki1",
                    "index": "2",
                    "passClassIndex": "1",
                    "fromLineIndex": "1",
                    "toLineIndex": "1",
                    "selected": "true",
                    "Oneway": "8550",
                    "Type": "Teiki1",
                }
            ]
        }
    )
    assert c.price[0].pass_class_index == "1"


def test_parse_price_without_pass_class_index_is_empty():
    c = must_course({"Price": [{"kind": "Fare", "index": "1"}]})
    assert c.price[0].pass_class_index == ""


def test_parse_pass_status():
    c = must_course(
        {
            "PassStatus": {
                "kind": "nikukanteiki",
                "index": "1",
                "selected": "true",
                "fromLineIndex": "1",
                "toLineIndex": "2",
                "Name": "二区間定期",
            }
        }
    )
    assert len(c.pass_status) == 1
    st = c.pass_status[0]
    assert (st.kind, st.index, st.selected, st.name) == ("nikukanteiki", "1", "true", "二区間定期")
    assert (st.from_line_index, st.to_line_index) == ("1", "2")


def test_parse_without_pass_status_is_empty():
    assert must_course({"Price": []}).pass_status == []


def test_parse_reads_engine_version():
    from ekispert_bus_data_migration.ekispert import parse_response

    resp = parse_response({"ResultSet": {"engineVersion": "202008_02a", "Course": []}})
    assert resp.result_set.engine_version == "202008_02a"


def test_parse_without_engine_version_is_empty():
    from ekispert_bus_data_migration.ekispert import parse_response

    assert parse_response({"ResultSet": {"Course": []}}).result_set.engine_version == ""


def test_parse_pass_status_fills_index_from_the_array_position():
    c = must_course(
        {
            "PassStatus": [
                {"kind": "bycorporation", "Name": "ＩＣ金額定期", "fromLineIndex": "1"},
                {"kind": "vehicle", "Name": "普通", "fromLineIndex": "3"},
                {"kind": "vehicle", "Name": "グリーン", "fromLineIndex": "3"},
            ]
        }
    )
    assert [p.index for p in c.pass_status] == ["1", "2", "3"]


def test_parse_pass_status_keeps_an_explicit_index():
    c = must_course({"PassStatus": [{"kind": "vehicle", "index": "7"}]})
    assert c.pass_status[0].index == "7"


def test_pass_class_index_points_at_the_whole_pass_status_order():
    c = must_course(
        {
            "PassStatus": [
                {"kind": "bycorporation", "Name": "ＩＣ金額定期", "fromLineIndex": "1", "toLineIndex": "1"},
                {"kind": "vehicle", "Name": "普通", "fromLineIndex": "3", "toLineIndex": "3"},
                {"kind": "vehicle", "Name": "グリーン", "fromLineIndex": "3", "toLineIndex": "3"},
                {"kind": "bycorporation", "Name": "ＩＣ金額定期", "fromLineIndex": "5", "toLineIndex": "5"},
            ],
            "Price": [{"kind": "Teiki1", "index": "3", "passClassIndex": "4", "fromLineIndex": "5"}],
        }
    )
    at = {p.index: p for p in c.pass_status}
    pointed = at[c.price[0].pass_class_index]
    assert (pointed.kind, pointed.from_line_index) == ("bycorporation", "5")
