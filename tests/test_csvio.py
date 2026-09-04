import os

from ekispert_bus_data_migration import csvio

jp_old_name = "みどり町／サンプルバス※旧"
jp_new_name = "みどり町／サンプルバス"


def write_file(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_read_input_strips_bom(tmp_path):
    p = write_file(tmp_path, "in.csv", "\ufeffid,old_name\nst-003," + jp_old_name + "\n")
    inp = csvio.read_input(p)
    assert inp.header[0] == "id"
    assert inp.has("id")
    assert inp.rows[0]["old_name"] == jp_old_name


def test_read_input_without_bom(tmp_path):
    p = write_file(tmp_path, "in.csv", "id,old_name\nst-003," + jp_old_name + "\n")
    inp = csvio.read_input(p)
    assert inp.header[0] == "id"
    assert inp.rows[0]["old_name"] == jp_old_name


def test_write_read_round_trip_japanese(tmp_path):
    out = str(tmp_path / "out.csv")
    w = csvio.Writer(out, ["id", "old_name", "new_name"])
    w.write_row({"id": "st-003", "old_name": jp_old_name, "new_name": jp_new_name})
    w.close()

    raw = (tmp_path / "out.csv").read_bytes()
    assert jp_old_name.encode("utf-8") in raw
    inp = csvio.read_input(out)
    assert inp.rows[0]["old_name"] == jp_old_name
    assert inp.rows[0]["new_name"] == jp_new_name


def test_japanese_file_name_round_trip(tmp_path):
    name = "変換結果-バスデータ.csv"
    out = str(tmp_path / name)
    w = csvio.Writer(out, ["id", "new_name"])
    w.write_row({"id": "st-003", "new_name": jp_new_name})
    w.close()

    entries = os.listdir(str(tmp_path))
    assert entries == [name]
    inp = csvio.read_input(out)
    assert inp.rows[0]["new_name"] == jp_new_name


def test_read_input_rejects_shift_jis(tmp_path):
    sjis = bytes([0x8E, 0x73, 0x90, 0xEC, 0x90, 0x5E, 0x8A, 0xD4])
    content = b"id,old_name\nst-003," + sjis + b"\n"
    p = tmp_path / "sjis.csv"
    p.write_bytes(content)

    try:
        csvio.read_input(str(p))
    except RuntimeError as e:
        msg = str(e)
        for want in ("Shift_JIS", csvio.Encoding, "sjis.csv"):
            assert want in msg
    else:
        raise AssertionError("Shift_JIS の入力がエラーになっていません")


def test_read_input_accepts_valid_utf8(tmp_path):
    p = write_file(tmp_path, "utf8.csv", "id,old_name\nst-003," + jp_old_name + "\n")
    csvio.read_input(p)


def test_writer_adds_utf8_bom(tmp_path):
    out = str(tmp_path / "out.csv")
    w = csvio.Writer(out, ["id", "new_name"])
    w.write_row({"id": "st-003", "new_name": jp_new_name})
    w.close()

    raw = (tmp_path / "out.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert not raw[3:].startswith(b"\xef\xbb\xbf")


def test_read_input_rejects_extra_values(tmp_path):
    """ヘッダーより多い値を黙って捨てない。

    値に含まれるコンマのクォート漏れで列がずれた入力を、正常に処理したように
    見せてしまうため。
    """
    p = write_file(tmp_path, "in.csv", "id,old_code\n001,841234,余分\n")
    try:
        csvio.read_input(p)
    except RuntimeError as e:
        assert "2行目" in str(e) and "余分" in str(e)
    else:
        raise AssertionError("列数を超える値をエラーにしていない")


def test_read_input_allows_trailing_empty_values(tmp_path):
    """末尾の空欄（Excelが付ける余分なコンマ）は無害なため受け入れる。"""
    p = write_file(tmp_path, "in.csv", "id,old_code\n001,841234,,\n")
    rows = csvio.read_input(p).rows
    assert rows[0]["old_code"] == "841234"


def test_writer_maps_values_by_column_name(tmp_path):
    """値は位置ではなく列名で対応付ける。指定しなかった列は空欄になる。"""
    path = str(tmp_path / "out.csv")
    w = csvio.Writer(path, ["id", "status", "detail", "new_code"])
    try:
        w.write_row({"new_code": "1514600", "id": "001", "status": "変換済み"})
    finally:
        w.close()
    rows = csvio.read_input(path).rows
    assert rows[0] == {"id": "001", "status": "変換済み", "detail": "", "new_code": "1514600"}


def test_writer_rejects_unknown_column(tmp_path):
    """ヘッダーに無い列を書こうとしたら、ずれたまま出力せずに止める。"""
    path = str(tmp_path / "out.csv")
    w = csvio.Writer(path, ["id", "status"])
    try:
        w.write_row({"id": "001", "typo_column": "x"})
    except RuntimeError as e:
        assert "typo_column" in str(e)
    else:
        raise AssertionError("未定義の列をエラーにしていない")
    finally:
        w.close()
