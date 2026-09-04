"""CSV入出力。入力・対応表・出力CSVはいずれも UTF-8 を前提とする。"""

from __future__ import annotations

import csv
import io

Encoding = "UTF-8"

_BOM_BYTES = b"\xef\xbb\xbf"
_BOM_CHAR = "\ufeff"


class Input:
    """入力CSVを読み込んだ結果。列名の検証に使うためヘッダーも保持する。"""

    def __init__(self, header, rows):
        self.header = header
        self.rows = rows

    def has(self, col):
        return col in self.header

    def require_any(self, *cols):
        if any(self.has(c) for c in cols):
            return
        raise RuntimeError(
            "入力CSVに %s のいずれかの列が必要です（実際の列: %s）"
            % (" または ".join(cols), ", ".join(self.header))
        )


def read_utf8_file(path, label):
    """CSVファイルを読み、先頭のBOMを除いたUTF-8のテキストを返す。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise RuntimeError("%sを開けません: %s" % (label, e))
    data = data.removeprefix(_BOM_BYTES)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise RuntimeError(
            "%sの文字コードが %s ではありません（Shift_JIS の可能性があります）: %s\n"
            "  本ツールは %s のCSVを読み込みます。Excel をお使いの場合は\n"
            "  「名前を付けて保存」で「CSV UTF-8 (コンマ区切り)(*.csv)」を選び、保存し直してください。"
            % (label, Encoding, path, Encoding)
        ) from None


def read_input(path):
    """入力CSVを読み込む。列名は小文字化・前後空白の除去を行う。"""
    text = read_utf8_file(path, "入力ファイル")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise RuntimeError("入力CSVのヘッダーを読めません")
    header = [h.strip().lower() for h in header]

    rows = []
    for rec in reader:
        # 列ずれした入力を正常に処理したように見せないため、末尾の空欄以外は止める。
        overflow = [v.strip() for v in rec[len(header) :] if v.strip() != ""]
        if overflow:
            raise RuntimeError(
                "入力CSVの%d行目に、ヘッダーの列数(%d)を超える値があります: %s\n"
                '  値にコンマを含む場合は " で囲んでください。'
                % (reader.line_num, len(header), ", ".join(overflow))
            )
        row = {}
        for i, v in enumerate(rec):
            if i < len(header):
                row[header[i]] = v.strip()
        rows.append(row)
    return Input(header, rows)


class Writer:
    """日本語版Excelで開けるUTF-8 BOM付きの結果CSVを書き出す。"""

    def __init__(self, path, header):
        try:
            self._file = open(path, "w", encoding="utf-8", newline="")  # noqa: SIM115 - ファイルはWriterの生存期間中開いたままにする
        except OSError as e:
            raise RuntimeError("出力ファイルを作成できません: %s" % e)
        self.header = list(header)
        self._file.write(_BOM_CHAR)
        self._writer = csv.writer(self._file, lineterminator="\n")
        self._write(self.header)

    def _write(self, row):
        self._writer.writerow(row)

    def write_row(self, values):
        """列名→値の辞書をヘッダーの並びで書き出す。指定しなかった列は空欄とする。"""
        unknown = [k for k in values if k not in self.header]
        if unknown:
            raise RuntimeError("結果CSVのヘッダーに無い列を指定しています: %s" % ", ".join(unknown))
        self._writer.writerow([values.get(c, "") for c in self.header])

    def close(self):
        self._file.close()
