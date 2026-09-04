from ekispert_bus_data_migration import mapping

sample = (
    "旧コード,新コード,旧バス停名(フル),新バス停名(フル)\n"
    "841234,1514600,みどり町／サンプルバス※旧,みどり町／サンプルバス\n"
    "841235,1514601,こもれび橋／サンプルバス※旧,こもれび橋／サンプルバス\n"
)


def load_test_table():
    return mapping.parse(sample)


def test_contains_any_old_code():
    tb = load_test_table()
    assert tb.contains_any_old_code(["999", "841234"])
    assert not tb.contains_any_old_code(["999", "1514600"])


def test_contains_unconverted_old_code():
    s = (
        "旧コード,新コード,旧バス停名(フル),新バス停名(フル)\n"
        "841234,1514600,みどり町／サンプルバス※旧,みどり町／サンプルバス\n"
        "1513754,1513754,みなと公園前／サンプルバス※旧,みなと公園前／サンプルバス\n"
    )
    tb = mapping.parse(s)

    assert tb.contains_unconverted_old_code(["841234", "22361"])
    assert not tb.contains_unconverted_old_code(["1514600", "22361"])
    assert not tb.contains_unconverted_old_code(["1513754", "22361"])
    assert tb.contains_any_old_code(["1513754"])


def test_convert_via_list():
    tb = load_test_table()
    assert tb.convert_via_list("841234:22361") == ("1514600:22361", [])


def test_convert_via_list_converts_names_too():
    """viaList を名称で保持している場合も新コードへ変換する。

    コードだけを見ていると無変換のまま再探索が"成功"し、誤った経路が
    移行先候補になる。
    """
    tb = load_test_table()
    got, ambiguous = tb.convert_via_list("みどり町／サンプルバス※旧:中央駅")
    assert got == "1514600:中央駅"
    assert ambiguous == []


def test_convert_via_list_reports_ambiguous_names():
    tb = mapping.parse(sample + "849999,1514777,こもれび橋／サンプルバス※旧,こもれび橋北／サンプルバス\n")
    got, ambiguous = tb.convert_via_list("こもれび橋／サンプルバス※旧:22361")
    # 決められない要素は変換せず、そのまま報告する
    assert got == "こもれび橋／サンプルバス※旧:22361"
    assert len(ambiguous) == 1
    assert "1514601" in ambiguous[0] and "1514777" in ambiguous[0]


def test_by_old_name_ambiguous():
    tb = mapping.parse(sample + ("849999,1514777,こもれび橋／サンプルバス※旧,こもれび橋北／サンプルバス\n"))
    entries = tb.by_old_name("こもれび橋／サンプルバス※旧")
    assert len(entries) == 2


real_distributed_sample = "\ufeff" + (
    "旧会社,新会社,変更内容,旧コード,旧バス停名(フル),新コード,新バス停名(フル),距離(m),旧緯度,旧経度,新緯度,新経度,都道府県,よみ\n"
    "サンプルバス※旧,サンプルバス,駅コード変更,840901,ひかり病院／サンプルバス※旧,1513759,ひかり病院／サンプルバス,0.0,35.701336,140.002174,35.701336,140.002174,東京都,ひかりびょういん\n"
    "サンプルバス※旧,サンプルバス,駅コード変更、バス停名変更,840906,あおば丘／サンプルバス※旧,1513766,あおば丘北／サンプルバス,0.0,35.698872,140.052419,35.698872,140.052419,東京都,あおばおか\n"
)


def test_parse_real_distributed_format():
    tb = mapping.parse(real_distributed_sample)
    e = tb.by_old_code("840901")
    assert e is not None
    assert e.new_code == "1513759" and e.new_name == "ひかり病院／サンプルバス"
    e2 = tb.by_old_code("840906")
    assert e2 is not None and e2.old_name == "あおば丘／サンプルバス※旧"


def test_by_old_name_treats_same_destination_as_unique():
    """同名の行が並んでも、移行先が同じなら一意として扱う。

    複数の旧バス停が1つの新バス停へ統合された場合に該当する。ここで候補として
    数えると、移行先が決まっているデータまで要確認へ倒してしまう。
    """
    tb = mapping.parse(sample + "849999,1514601,こもれび橋／サンプルバス※旧,こもれび橋／サンプルバス\n")
    entries = tb.by_old_name("こもれび橋／サンプルバス※旧")
    assert len(entries) == 1
    assert entries[0].new_code == "1514601"
