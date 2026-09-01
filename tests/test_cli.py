"""CLI層（入力の振り分け・移行プロファイル・出力ファイル）の確認。"""

import json
import os
from types import SimpleNamespace

import pytest

from ekispert_bus_data_migration import mapping
from ekispert_bus_data_migration.cli import cmd_run, parse_args, prepare_output, run_serialize
from ekispert_bus_data_migration.discover import discover_mapping, looks_like_mapping
from ekispert_bus_data_migration.outputs import output_station
from ekispert_bus_data_migration.ui import Reporter, _is_terminal, progressInterval

MAPPING_CSV = (
    "旧コード,旧バス停名(フル),新コード,新バス停名(フル)\n"
    "841234,みどり町／サンプルバス※旧,1514600,みどり町／サンプルバス\n"
)


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def read_rows(path):
    from ekispert_bus_data_migration import csvio

    return csvio.read_input(path).rows


def write_profile(tmp_path, **overrides):
    data = {
        "profile_id": "test-migration",
        "source_api_base_url": "https://source.example.com",
        "target_api_base_url": "https://target.example.com",
        "target_data_start_date": "20300115",
    }
    data.update(overrides)
    return write(tmp_path, "profile.json", json.dumps(data))


def test_result_csv_is_not_mistaken_for_mapping(tmp_path):
    """結果CSVは old_code / new_code 列を持つが、対応表として使ってはならない。

    中身は変換できた行だけの部分集合で old_name も空のため、対応表として
    読み込むと黙って誤った移行結果になる。
    """
    p = write(
        tmp_path,
        output_station,
        "id,status,detail,old_code,old_name,new_code,new_name\n"
        "001,変換済み,新コードへ変換しました,841234,,1514600,みどり町／サンプルバス\n",
    )
    assert not looks_like_mapping(p)


def test_backed_up_result_csv_is_not_mistaken_for_mapping(tmp_path):
    """タイムスタンプ付きで退避した結果CSVも、対応表候補から除外する。"""
    p = write(
        tmp_path,
        "変換結果-バスデータ-20300115-120000.csv",
        "id,status,detail,old_code,old_name,new_code,new_name\n"
        "001,変換済み,新コードへ変換しました,841234,旧名,1514600,新名\n",
    )
    assert not looks_like_mapping(p)


def test_mapping_requires_name_columns_too(tmp_path):
    """コード2列だけのCSVを対応表と取り違えない（parse も名称列を要求するため）。"""
    p = write(tmp_path, "codes.csv", "old_code,new_code\n841234,1514600\n")
    assert not looks_like_mapping(p)
    full = write(tmp_path, "mapping.csv", MAPPING_CSV)
    assert looks_like_mapping(full)


def test_multiple_mapping_candidates_require_explicit_option(tmp_path, monkeypatch):
    """対応表候補が複数ある場合は、誤った対応表を自動選択しない。"""
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    inp = write(input_dir, "in.csv", "id,old_code\n001,841234\n")
    write(input_dir, "mapping-input.csv", MAPPING_CSV)
    write(tmp_path, "mapping-current.csv", MAPPING_CSV)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError) as exc_info:
        discover_mapping("", inp)

    message = str(exc_info.value)
    assert "複数見つかった" in message
    assert "mapping-input.csv" in message
    assert "mapping-current.csv" in message
    assert "--mapping" in message


def test_row_without_any_migration_data_is_reported(tmp_path):
    """データが空の行を黙って捨てない（入力N件＝結果N件を保つ）。

    結果は入力順で出す。未入力行だけ先頭へ寄せると、利用者が入力と結果を
    行単位で突き合わせられない（`id` 未指定なら突き合わせる手がかりも無い）。
    """
    write(tmp_path, "mapping.csv", MAPPING_CSV)
    inp = write(tmp_path, "in.csv", "id,old_code\n001,841234\n002,\n003,841234\n")
    rc = cmd_run([inp, "--mapping", str(tmp_path / "mapping.csv")])

    rows = read_rows(str(tmp_path / output_station))
    assert [r["id"] for r in rows] == ["001", "002", "003"]
    assert [r["status"] for r in rows] == ["変換済み", "エラー", "変換済み"]
    assert rc == 1, "処理できなかった行があるため終了コードは0以外"


def test_input_without_id_is_processed(tmp_path):
    """id は結果との対応付け用であり、移行処理には使用しないため省略できる。"""
    write(tmp_path, "mapping.csv", MAPPING_CSV)
    inp = write(tmp_path, "in.csv", "old_code\n841234\n")
    rc = cmd_run([inp, "--mapping", str(tmp_path / "mapping.csv")])

    rows = read_rows(str(tmp_path / output_station))
    assert len(rows) == 1
    assert rows[0]["id"] == ""
    assert rows[0]["new_code"] == "1514600"
    assert rc == 0


def test_blank_line_is_not_reported_as_error(tmp_path):
    write(tmp_path, "mapping.csv", MAPPING_CSV)
    inp = write(tmp_path, "in.csv", "id,old_code\n001,841234\n\n")
    cmd_run([inp, "--mapping", str(tmp_path / "mapping.csv")])
    rows = read_rows(str(tmp_path / output_station))
    assert [r["id"] for r in rows] == ["001"]


def test_existing_result_file_is_backed_up(tmp_path):
    """再実行で前回の結果を失わないよう、既存の結果ファイルは退避する。"""
    write(tmp_path, "mapping.csv", MAPPING_CSV)
    inp = write(tmp_path, "in.csv", "id,old_code\n001,841234\n")
    args = [inp, "--mapping", str(tmp_path / "mapping.csv")]
    cmd_run(args)
    cmd_run(args)

    backups = [n for n in os.listdir(str(tmp_path)) if n.startswith("変換結果-バスデータ-")]
    assert len(backups) == 1, os.listdir(str(tmp_path))


def test_prepare_output_returns_path_when_absent(tmp_path):
    p = str(tmp_path / "new.csv")
    assert prepare_output(p) == p


def test_teiki_column_shortage_does_not_block_other_files(tmp_path, monkeypatch):
    """列が欠けているのはそのファイルの問題。他ファイルの行まで止めない。"""
    write(tmp_path, "mapping.csv", MAPPING_CSV)
    ng = write(tmp_path, "ng.csv", "id,detail_route\nt1,A:L:Down:B\n")
    ok = write(
        tmp_path,
        "ok.csv",
        "id,origin,destination,date,detail_route\nt2,1,2,20300114,A:L:Down:B\n",
    )
    profile = write_profile(tmp_path)
    monkeypatch.setenv("EKISPERT_ACCESS_KEY", "DUMMY")
    rc = cmd_run(
        [
            ng,
            ok,
            "--mapping",
            str(tmp_path / "mapping.csv"),
            "--config",
            profile,
        ]
    )

    # ok.csv の行は処理され結果CSVに出る（date が適用開始日前なのでエラー行になる）
    rows = read_rows(str(tmp_path / "変換結果-定期経路文字列.csv"))
    assert [r["id"] for r in rows] == ["t2"]
    assert rc == 1, "ng.csv を処理できていないため終了コードは0以外"


def test_api_feature_without_profile_is_skipped(tmp_path):
    write(tmp_path, "mapping.csv", MAPPING_CSV)
    inp = write(tmp_path, "in.csv", "id,serialize_data\ns1,OLD\n")
    rc = cmd_run([inp, "--mapping", str(tmp_path / "mapping.csv")])

    assert rc == 1
    assert not (tmp_path / "移行先経路候補-経路シリアライズデータ.csv").exists()


def test_serialize_output_has_only_current_input_columns(tmp_path, monkeypatch):
    """廃止した入力項目を結果CSVへ残さず、再実行用の列を現行仕様にそろえる。"""

    def fake_serialize(common, inp):
        return SimpleNamespace(
            id=inp.id,
            status="対応表に該当なし",
            detail="",
            candidates=[],
        )

    monkeypatch.setattr("ekispert_bus_data_migration.cli.serialize", fake_serialize)
    out = str(tmp_path / "out.csv")
    run_serialize(None, [{"id": "s1", "serialize_data": "OLD"}], out)

    from ekispert_bus_data_migration import csvio

    result = csvio.read_input(out)
    assert result.header == [
        "id",
        "candidate_no",
        "status",
        "detail",
        "serialize_data",
        "via_list",
        "date",
        "time",
        "search_type",
        "condition_detail",
        "new_serialize_data",
        "route_changed",
        "old_route",
        "new_route",
        "fare_changed",
        "old_fare",
        "new_fare",
        "old_time_min",
        "new_time_min",
    ]


@pytest.mark.parametrize(
    "option",
    [
        # オプションは -- 形に統一している。単一ダッシュ形は受け付けない。
        "-mapping",
        "-config",
        "-candidates",
        "-help",
        "-source-api-base-url",
        "-target-api-base-url",
        "-old-base-url",
        "-new-base-url",
        "-access-key",
        "-verbose",
    ],
)
def test_unsupported_options_are_rejected(option):
    with pytest.raises(SystemExit):
        parse_args([option, "https://example.com"])


def test_duplicate_old_code_is_reported_as_ambiguous():
    """1つの旧コードに複数の移行先がある対応表を、後勝ちで黙って採用しない。"""
    from ekispert_bus_data_migration.migrate.common import StatusAmbiguous
    from ekispert_bus_data_migration.migrate.station import StationInput, station

    t = mapping.parse("old_code,new_code,old_name,new_name\n800001,900001,A旧,A新1\n800001,900002,A旧,A新2\n")
    assert t.duplicate_old_codes() == ["800001"]
    res = station(t, StationInput(id="1", old_code="800001"))
    assert res.status == StatusAmbiguous
    assert "900001" in res.detail and "900002" in res.detail


def test_no_input_file_is_not_success():
    """入力を指定していない実行を成功にしない。

    0 を返すと、自動実行では「使い方を表示しただけ」を成功と受け取り、
    移行していないことに気づけない。
    """
    assert cmd_run([]) == 2


def test_backup_does_not_overwrite_earlier_backup(tmp_path, monkeypatch):
    """同じ秒に再実行しても、前回退避した結果を上書きしない。

    利用者は結果CSVに採用候補の印を付けるため、退避が消えると作業が失われる。
    """
    monkeypatch.setattr("ekispert_bus_data_migration.cli.time.strftime", lambda _fmt: "20300115-120000")
    path = str(tmp_path / output_station)

    write(tmp_path, output_station, "1回目\n")
    prepare_output(path)
    write(tmp_path, output_station, "2回目\n")
    prepare_output(path)

    backups = sorted(p.name for p in tmp_path.glob("変換結果-バスデータ-*.csv"))
    assert backups == [
        "変換結果-バスデータ-20300115-120000-2.csv",
        "変換結果-バスデータ-20300115-120000.csv",
    ]
    assert (tmp_path / "変換結果-バスデータ-20300115-120000.csv").read_text(encoding="utf-8") == "1回目\n"


def test_progress_rewrites_one_line_on_terminal(monkeypatch, capsys):
    """端末では同じ行を書き換え、要確認の行と混ざらないよう消してから出す。"""
    monkeypatch.setattr("ekispert_bus_data_migration.ui._stderr_is_terminal", lambda: True)
    rep = Reporter(3)
    rep.row("変換済み", "T-1", "")
    rep.row("エラー", "T-2", "理由")
    rep.row("変換済み", "T-3", "")
    rep.finish()

    err = capsys.readouterr().err
    assert "\r  [1/3] 処理中" in err
    assert "\r  [3/3] 処理中" in err
    # エラー行の直前で進捗行を消しているか（空白で上書きして行頭へ戻す）
    assert "\r" + " " * len("  [1/3] 処理中") + "\r  [エラー] T-2: 理由" in err
    assert err.endswith("\r" + " " * len("  [3/3] 処理中") + "\r")


def test_progress_is_thinned_out_when_not_a_terminal(monkeypatch, capsys):
    """ログへのリダイレクトでは行を書き換えられないため、間引いて出す。"""
    monkeypatch.setattr("ekispert_bus_data_migration.ui._stderr_is_terminal", lambda: False)
    rep = Reporter(progressInterval + 1)
    for i in range(progressInterval + 1):
        rep.row("変換済み", "T-%d" % i, "")
    rep.finish()

    lines = [ln for ln in capsys.readouterr().err.splitlines() if "処理中" in ln]
    assert lines == [
        "  [%d/%d] 処理中" % (progressInterval, progressInterval + 1),
        "  [%d/%d] 処理中" % (progressInterval + 1, progressInterval + 1),
    ]


def test_offline_conversion_shows_no_progress(capsys):
    """オフラインで即座に終わるバス停データ変換では進捗を出さない。"""
    rep = Reporter()
    rep.row("変換済み", "A1-1", "")
    rep.finish()
    assert "処理中" not in capsys.readouterr().err


def test_is_terminal_checks_the_given_stream():
    """判定対象は渡されたストリーム。

    stdin の判定で stderr を見てしまうと、標準入力が端末でない自動実行でも
    アクセスキーの入力を待って止まる。
    """

    class Fake:
        def __init__(self, atty):
            self._atty = atty

        def isatty(self):
            return self._atty

    assert _is_terminal(Fake(True))
    assert not _is_terminal(Fake(False))
    # isatty を持たないストリームに差し替わっても落とさない
    assert not _is_terminal(object())
