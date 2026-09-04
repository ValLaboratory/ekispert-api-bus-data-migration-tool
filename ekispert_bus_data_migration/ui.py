from __future__ import annotations

import getpass
import os
import sys

from .migrate.common import StatusAmbiguous, StatusFailed

progressInterval = 20


class Progress:
    def __init__(self, total):
        self.total = total
        self.done = 0
        self._line_len = 0

    def advance(self):
        if self.total <= 0:
            return
        self.done += 1
        text = "  [%d/%d] 処理中" % (self.done, self.total)
        if _stderr_is_terminal():
            sys.stderr.write("\r" + text)
            sys.stderr.flush()
            self._line_len = len(text)
        elif self.done == self.total or self.done % progressInterval == 0:
            print(text, file=sys.stderr)

    def clear(self):
        if self._line_len == 0:
            return
        sys.stderr.write("\r" + " " * self._line_len + "\r")
        sys.stderr.flush()
        self._line_len = 0


class Reporter:
    def __init__(self, total=0):
        self.counts = {}
        self.written = 0
        self.progress = Progress(total)

    def row(self, status, id_, detail):
        self.counts[status] = self.counts.get(status, 0) + 1
        if needs_attention(status):
            self.progress.clear()
            print("  [%s] %s: %s" % (status, id_, detail), file=sys.stderr)
        self.progress.advance()

    def finish(self):
        self.progress.clear()

    def status_line(self):
        order = [
            "変換済み",
            "移行先の候補",
            "対応表に該当なし",
            "要確認（同名バス停あり）",
            "更新不要",
            "エラー",
        ]
        parts = []
        for s in order:
            if s in self.counts:
                parts.append("%s:%d" % (s, self.counts[s]))
        return ", ".join(parts)


def needs_attention(status):
    return status == StatusFailed or status == StatusAmbiguous


def warn_duplicate_mapping(table, summary=None):
    dups = table.duplicate_old_codes()
    if not dups:
        return
    shown = ", ".join(dups[:5])
    more = "" if len(dups) <= 5 else " ほか%d件" % (len(dups) - 5)
    _warn(
        "警告         : 対応表に移行先が複数ある旧コードがあります(%s%s)。該当データは要確認になります。"
        % (shown, more),
        summary,
    )


def _warn(text, summary):
    print(text, file=sys.stderr)
    if summary is not None:
        summary.note(text)


def warn_duplicate_ids(rows, summary=None):
    seen = {}
    for r in rows:
        v = r.get("id", "").strip()
        if v == "":
            continue
        seen[v] = seen.get(v, 0) + 1
    dups = sorted(k for k, n in seen.items() if n > 1)
    if not dups:
        return
    shown = ", ".join(dups[:5])
    more = "" if len(dups) <= 5 else " ほか%d件" % (len(dups) - 5)
    _warn(
        "警告         : id が重複しています(%s%s)。結果を元データへ突き合わせる際にご注意ください。"
        % (shown, more),
        summary,
    )


class Summary:
    def __init__(self):
        self.lines = []

    def line(self, text=""):
        print(text, file=sys.stderr)
        self.lines.append(text)

    def note(self, text):
        self.lines.append(text)

    def mark(self):
        return len(self.lines)

    def insert(self, at, texts):
        self.lines[at:at] = list(texts)

    def text(self):
        return "\n".join(self.lines).strip("\n") + "\n"


def print_closing(produced, skipped=(), summary=None):
    out = summary.line if summary is not None else (lambda t="": print(t, file=sys.stderr))
    out("\n=== 処理が終わりました ===\n")
    if produced:
        out("作成したファイル:")
        for p in produced:
            out("  %s" % os.path.basename(p))
    else:
        out("作成したファイルはありません。")
    if skipped:
        out("\n要対応:")
        for m in skipped:
            out("  %s" % m)
    print("\n※ 本ツールは利用者のシステムへの反映は行いません。", file=sys.stderr)


def prompt_access_key(hint):
    if not stdin_is_terminal():
        return ""
    print("\n「駅すぱあと API」のアクセスキーが必要です。", file=sys.stderr)
    if hint != "":
        print(hint, file=sys.stderr)
    print("（入力内容は画面に表示されません）", file=sys.stderr)
    try:
        return getpass.getpass("アクセスキー: ", stream=sys.stderr).strip()
    except EOFError:
        return ""


def stdin_is_terminal():
    return _is_terminal(sys.stdin)


def _stderr_is_terminal():
    return _is_terminal(sys.stderr)


def _is_terminal(stream):
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False
