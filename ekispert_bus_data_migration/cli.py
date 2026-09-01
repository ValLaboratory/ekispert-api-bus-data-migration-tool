from __future__ import annotations

import argparse
import os
import sys
import time

from . import csvio, mapping
from .config import load_config, missing_profile_fields
from .discover import discover_mapping
from .ekispert import Client, redact
from .migrate.common import (
    Common,
    DefaultCandidateCount,
    StatusFailed,
    maxCandidateCount,
)
from .migrate.serialize import SerializeInput, serialize
from .migrate.station import StationInput, station
from .migrate.teiki import TeikiInput, teiki
from .outputs import output_serialize, output_station, output_teiki
from .ui import (
    Reporter,
    print_closing,
    prompt_access_key,
    warn_duplicate_ids,
    warn_duplicate_mapping,
)


def usage():
    sys.stderr.write(
        "バスデータ移行ツール\n"
        "\n"
        "使い方:\n"
        "  python3 -m ekispert_bus_data_migration 移行したいデータ.csv\n"
        "  python3 -m ekispert_bus_data_migration バス停.csv 経路.csv 定期.csv   （複数まとめて渡せます）\n"
        "\n"
        "  対応表は同じフォルダーにあれば自動で見つけます。\n"
        "  複数見つかった場合は、--mapping で使用する対応表を指定してください。\n"
        "  APIを使う機能では、再編ごとの移行プロファイルを --config で指定します。\n"
        "  アクセスキーは必要になった時点で画面から入力できます。\n"
        "\n"
        "オプション:\n"
        "  --mapping     新旧バス停対応表CSV（省略時は同じフォルダーから自動検出）\n"
        "  --config      移行プロファイル(JSON)。接続先URL・適用開始日を指定します\n"
        "  --candidates  提示する移行先候補の件数（デフォルト%d、最大%d）\n"
        % (DefaultCandidateCount, maxCandidateCount)
    )


def parse_args(args):
    parser = argparse.ArgumentParser(
        prog="python3 -m ekispert_bus_data_migration", add_help=False, allow_abbrev=False
    )
    parser.add_argument("inputs", nargs="*")
    parser.add_argument(
        "--mapping",
        dest="mapping",
        default="",
        help="新旧バス停対応表CSV (省略時は同じフォルダーから自動検出)",
    )
    parser.add_argument(
        "--config",
        dest="config",
        default="",
        help="移行プロファイル(JSON)。APIを使う機能では必須",
    )
    parser.add_argument(
        "--candidates",
        dest="candidates",
        type=int,
        default=DefaultCandidateCount,
        help="提示する移行先候補の件数 (最大20)",
    )
    return parser.parse_args(args)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    args = list(argv)
    for a in args:
        if a in ("-h", "--help", "help"):
            usage()
            return 0
    try:
        return cmd_run(args)
    except KeyboardInterrupt:
        print("\n中断しました。", file=sys.stderr)
        return 130
    except SystemExit as e:
        if e.code not in (0, None):
            print("\nオプションの指定が正しくありません。", file=sys.stderr)
            print("使い方を見るには --help を付けて実行してください。", file=sys.stderr)
        return e.code if isinstance(e.code, int) else 2
    except Exception as e:
        print("\nエラー:", redact(str(e)), file=sys.stderr)
        return 1


def cmd_run(args):
    ns = parse_args(args)
    config = load_config(ns.config)
    input_paths = list(ns.inputs)

    if not input_paths:
        usage()
        print("\n移行したいデータのCSVファイルを指定してください。", file=sys.stderr)
        print("例: python3 -m ekispert_bus_data_migration 移行したいデータ.csv", file=sys.stderr)
        return 2

    for p in input_paths:
        if not os.path.exists(p):
            raise RuntimeError("入力ファイルが見つかりません: %s" % p)

    print("=== バスデータ移行ツール ===\n", file=sys.stderr)

    mapping_path = discover_mapping(ns.mapping, input_paths[0])
    table = mapping.load(mapping_path)

    files = []
    file_labels = []
    for p in input_paths:
        inp = csvio.read_input(p)
        name = os.path.basename(p)
        try:
            inp.require_any("old_code", "old_name", "serialize_data", "detail_route")
        except RuntimeError as e:
            raise RuntimeError("%s: %s" % (name, e))
        files.append((name, inp))
        file_labels.append("%s(%d件)" % (name, len(inp.rows)))

    all_rows = [r for _, inp in files for r in inp.rows]

    station_rows = filter_rows(all_rows, "old_code", "old_name")
    serialize_rows = filter_rows(all_rows, "serialize_data")

    # 定期経路文字列移行に必要な列の不足は、他のファイルの行まで止めないようファイル単位で判定する。
    teiki_rows = []
    teiki_skipped = []
    teiki_all = []
    for name, inp in files:
        rows = filter_rows(inp.rows, "detail_route")
        if not rows:
            continue
        teiki_all.extend(rows)
        m = missing_columns(inp, "origin", "destination", "date")
        if m:
            teiki_skipped.append((name, m, len(rows)))
        else:
            teiki_rows.extend(rows)

    # どの機能にも振り分けられなかった行。黙って捨てると行が消えたことに気づけない。
    classified = {id(r) for r in station_rows + serialize_rows + teiki_all}
    incomplete_rows = [r for r in all_rows if id(r) not in classified and has_any_value(r)]

    # 結果CSVを入力順にするため、バス停データ変換の対象行と未入力行を入力の並びで1本にまとめる。
    station_output_ids = {id(r) for r in station_rows} | {id(r) for r in incomplete_rows}
    station_output_rows = [r for r in all_rows if id(r) in station_output_ids]

    warn_duplicate_ids(all_rows)

    has_station = bool(station_output_rows)
    has_serialize = bool(serialize_rows)
    has_teiki = bool(teiki_rows)
    serialize_profile_missing = missing_profile_fields(config, serialized_route=True) if has_serialize else []
    teiki_profile_missing = missing_profile_fields(config, detail_route=True) if has_teiki else []

    kinds = []
    if station_rows:
        kinds.append("バス停コード・名称(%d件)" % len(station_rows))
    if has_serialize:
        kinds.append("経路シリアライズデータ(%d件)" % len(serialize_rows))
    if has_teiki:
        kinds.append("定期経路文字列(%d件)" % len(teiki_rows))
    if incomplete_rows:
        kinds.append("データ未入力(%d件)" % len(incomplete_rows))
    if not kinds:
        kinds.append("なし")
    print("入力ファイル : %s" % " ".join(file_labels), file=sys.stderr)
    print("対応表       : %s" % os.path.basename(mapping_path), file=sys.stderr)
    warn_duplicate_mapping(table)
    print("検出データ   : %s" % " / ".join(kinds), file=sys.stderr)
    if has_serialize or has_teiki or config.profile_id:
        print("移行プロファイル: %s" % (config.profile_id or "未指定"), file=sys.stderr)

    access_key = os.environ.get("EKISPERT_ACCESS_KEY", "")
    need_api = (has_serialize and not serialize_profile_missing) or (has_teiki and not teiki_profile_missing)
    if need_api and access_key == "":
        access_key = prompt_access_key("（入力せずEnterを押すと、アクセスキーが不要な範囲だけ処理します）")

    common = build_common(ns, table, access_key, config)
    out_dir = os.path.dirname(input_paths[0]) or "."

    produced = []
    skipped = []

    if has_station:
        label = "バス停コード・名称"
        print("\n%s" % label, file=sys.stderr)
        p = prepare_output(os.path.join(out_dir, output_station))
        r = run_station(table, station_output_rows, p)
        print("  結果: %s" % r.status_line(), file=sys.stderr)
        record_result(label, p, r, produced, skipped)

    if has_serialize:
        label = "経路シリアライズデータ"
        print("\n%s" % label, file=sys.stderr)
        if not blocked(label, serialize_rows, serialize_profile_missing, access_key, skipped):
            p = prepare_output(os.path.join(out_dir, output_serialize))
            r = run_serialize(common, serialize_rows, p, access_key)
            print(
                "  結果: %s （CSVは%d行。1つの入力につき候補ごとに1行）" % (r.status_line(), r.written),
                file=sys.stderr,
            )
            record_result(label, p, r, produced, skipped)

    if has_teiki or teiki_skipped:
        label = "定期経路文字列"
        print("\n%s" % label, file=sys.stderr)
        for name, m, count in teiki_skipped:
            print(
                "  %s に %s 列が不足しているため、この%d件は処理していません" % (name, ", ".join(m), count),
                file=sys.stderr,
            )
            skipped.append("%s の%s(%d件)を処理していません" % (name, label, count))
        if has_teiki and not blocked(label, teiki_rows, teiki_profile_missing, access_key, skipped):
            p = prepare_output(os.path.join(out_dir, output_teiki))
            r = run_teiki(common, teiki_rows, p, access_key)
            print("  結果: %s" % r.status_line(), file=sys.stderr)
            record_result(label, p, r, produced, skipped)

    print_closing(produced, skipped)
    return 1 if skipped else 0


def blocked(label, rows, profile_missing, access_key, skipped):
    """APIを使う機能を実行できない場合、理由を表示して True を返す。"""
    if profile_missing:
        print(
            "  移行プロファイルに必要な設定がありません: %s" % ", ".join(profile_missing),
            file=sys.stderr,
        )
    elif access_key == "":
        print("  アクセスキーが無いため実行できませんでした", file=sys.stderr)
    else:
        return False
    skipped.append("%s(%d件)を処理していません" % (label, len(rows)))
    return True


def record_result(label, path, rep, produced, skipped):
    produced.append(path)
    if rep.counts.get(StatusFailed):
        skipped.append("%sにエラーが%d件あります" % (label, rep.counts[StatusFailed]))


def build_common(ns, table, access_key, config):
    if ns.candidates <= 0 or ns.candidates > maxCandidateCount:
        effective = DefaultCandidateCount if ns.candidates <= 0 else maxCandidateCount
        print(
            "警告         : --candidates %d は指定できる範囲(1〜%d)外のため %d 件として実行します"
            % (ns.candidates, maxCandidateCount, effective),
            file=sys.stderr,
        )
    common = Common(table=table, candidate_count=ns.candidates, config=config)
    if access_key != "":
        if config.source_api_base_url:
            common.source_client = Client(config.source_api_base_url, access_key)
        if config.target_api_base_url:
            common.target_client = Client(config.target_api_base_url, access_key)
    return common


# ---- 各機能の処理本体 ----
def build_input(cls, row):
    """入力行を入力dataclassへ詰め替える。CSVの列名とフィールド名は一致させてある。"""
    return cls(**{name: row.get(name, "") for name in cls.__dataclass_fields__})


def input_columns(inp):
    """入力dataclassの値を、結果CSVへ書き戻すための列名→値にする（`id` は別に扱う）。"""
    return {name: value for name, value in vars(inp).items() if name != "id"}


def run_station(table, rows, out_path):
    header = ["id", "status", "detail", "old_code", "old_name", "new_code", "new_name"]
    w = csvio.Writer(out_path, header)
    try:
        rep = Reporter()
        for row in rows:
            inp = build_input(StationInput, row)
            # 呼び出し側でどの機能にも振り分けられなかった行。移行対象のデータが1つも無い。
            if inp.old_code == "" and inp.old_name == "":
                detail = (
                    "移行対象のデータ(old_code / old_name / serialize_data / detail_route)がいずれも空です"
                )
                rep.row(StatusFailed, inp.id, detail)
                w.write_row({"id": inp.id, "status": StatusFailed, "detail": detail})
                continue
            res = station(table, inp)
            rep.row(res.status, inp.id, res.detail)
            w.write_row(
                dict(
                    input_columns(inp),
                    id=res.id,
                    status=res.status,
                    detail=res.detail,
                    new_code=res.new_code,
                    new_name=res.new_name,
                )
            )
    finally:
        w.close()
    return rep


def run_serialize(common, rows, out_path, access_key=""):
    header = [
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
    w = csvio.Writer(out_path, header)
    try:
        rep = Reporter(len(rows))
        for row in rows:
            inp = build_input(SerializeInput, row)
            # 入力列は候補行にも繰り返し出力する。1行だけ抜き出しても再実行の入力になるようにするため。
            input_values = input_columns(inp)
            res = serialize(common, inp)
            res.detail = redact(res.detail, access_key)
            for cd in res.candidates:
                cd.detail = redact(cd.detail, access_key)
            rep.row(res.status, inp.id, res.detail)

            if not res.candidates:
                w.write_row(dict(input_values, id=res.id, status=res.status, detail=res.detail))
                rep.written += 1
                continue
            for cd in res.candidates:
                w.write_row(
                    dict(
                        input_values,
                        id=res.id,
                        candidate_no=str(cd.no),
                        status=cd.status,
                        detail=cd.detail,
                        new_serialize_data=cd.new_serialize_data,
                        route_changed=cd.route_changed,
                        old_route=cd.old_route,
                        new_route=cd.new_route,
                        fare_changed=cd.fare_changed,
                        old_fare=cd.old_fare,
                        new_fare=cd.new_fare,
                        old_time_min=cd.old_time,
                        new_time_min=cd.new_time,
                    )
                )
                rep.written += 1
        rep.finish()
    finally:
        w.close()
    return rep


def run_teiki(common, rows, out_path, access_key=""):
    header = [
        "id",
        "status",
        "detail",
        "origin",
        "destination",
        "date",
        "time",
        "route_type",
        "detail_route",
        "new_detail_route",
        "route_changed",
        "old_route",
        "new_route",
    ]
    w = csvio.Writer(out_path, header)
    try:
        rep = Reporter(len(rows))
        for row in rows:
            inp = build_input(TeikiInput, row)
            res = teiki(common, inp)
            res.detail = redact(res.detail, access_key)
            rep.row(res.status, inp.id, res.detail)
            w.write_row(
                dict(
                    input_columns(inp),
                    id=res.id,
                    status=res.status,
                    detail=res.detail,
                    new_detail_route=res.new_detail_route,
                    route_changed=res.route_changed,
                    old_route=res.old_route,
                    new_route=res.new_route,
                )
            )
        rep.finish()
    finally:
        w.close()
    return rep


def filter_rows(rows, *cols):
    """指定した列のいずれかに値がある行だけを返す。"""
    out = []
    for r in rows:
        for c in cols:
            if r.get(c, "").strip() != "":
                out.append(r)
                break
    return out


def prepare_output(path):
    """結果ファイルを作る前に、既存の同名ファイルを退避する。

    利用者が結果CSVへ書き込んだ確認結果を、再実行で黙って失わせないため。
    """
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    # 退避名は秒までのため、同じ秒の再実行で前回の退避を上書きしないよう連番を足す。
    base = "%s-%s" % (stem, time.strftime("%Y%m%d-%H%M%S"))
    backup = base + ext
    seq = 2
    while os.path.exists(backup):
        backup = "%s-%d%s" % (base, seq, ext)
        seq += 1
    try:
        os.replace(path, backup)
    except OSError as e:
        raise RuntimeError(
            "既存の結果ファイルを退避できません: %s (%s)\n"
            "  ファイルを閉じてから再実行してください（Excelで開いたままだと退避できません）"
            % (os.path.basename(path), e)
        )
    print(
        "  既存の %s を %s に退避しました" % (os.path.basename(path), os.path.basename(backup)),
        file=sys.stderr,
    )
    return path


def has_any_value(row):
    """空行（末尾の改行など）と、値が入っている行を区別する。"""
    return any(v.strip() != "" for v in row.values())


def missing_columns(inp, *cols):
    missing = []
    for c in cols:
        if not inp.has(c):
            missing.append(c)
    return missing
