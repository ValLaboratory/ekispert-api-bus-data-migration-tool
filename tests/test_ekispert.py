import urllib.parse

import pytest

from ekispert_bus_data_migration.ekispert import Client, redact


def test_access_key_not_leaked_into_errors(start_server):
    secret = "SECRET-ACCESS-KEY-12345"

    # 1) 通信エラー（到達不能ホスト）
    c1 = Client("http://127.0.0.1:1", secret)
    try:
        c1.course_edit("x", False)
    except Exception as e:
        assert secret not in str(e)
    else:
        raise AssertionError("エラーになるはずです")

    # 2) HTTPエラー応答
    def handler(path, query):
        return 400, "bad request"

    url = start_server(handler)
    c2 = Client(url, secret)
    try:
        c2.course_edit("x", False)
    except Exception as e:
        assert secret not in str(e)
    else:
        raise AssertionError("エラーになるはずです")


def test_access_key_not_leaked_on_malformed_base_url():
    """ベースURLに scheme が無い場合、urllib はURL全体を含む ValueError を投げる。

    この例外は urlopen ではなく Request() の構築時に出るため、try の外に置くと
    伏せ字化を素通りして結果CSVの detail 列にキーが残る。
    """
    secret = "SECRET-ACCESS-KEY-12345"
    c = Client("api.ekispert.jp", secret)  # https:// の書き忘れ
    with pytest.raises(Exception) as ei:
        c.course_edit("x", False)
    assert secret not in str(ei.value)


def test_access_key_not_leaked_when_url_encoded():
    """`+` や `/` を含むキーはURLエンコードされるため、生値の置換だけでは漏れる。"""
    secret = "ab+cd/ef=gh"
    c = Client("api.ekispert.jp", secret)
    with pytest.raises(Exception) as ei:
        c.course_edit("x", False)
    msg = str(ei.value)
    assert secret not in msg
    assert urllib.parse.quote_plus(secret) not in msg
    assert "key=***" in msg


def test_redact_masks_key_query_without_knowing_the_value():
    """キーの実値が分からなくても、クエリの key=... は伏せ字化する。"""
    text = "https://api.ekispert.jp/v1/json/course/edit?serializeData=x&key=WHATEVER"
    assert "WHATEVER" not in redact(text)


def test_error_body_is_truncated(start_server):
    """5xx がHTMLのエラーページを返しても detail 列が壊れないよう切り詰める。"""

    def handler(path, query):
        return 400, "<html>" + ("x" * 5000) + "</html>"

    c = Client(start_server(handler), "k")
    with pytest.raises(Exception) as ei:
        c.course_edit("x", False)
    assert len(str(ei.value)) < 500
    assert "以下省略" in str(ei.value)


def test_client_keeps_engine_version_from_response(start_server):
    import json

    base = start_server(
        lambda path, query: (
            200,
            json.dumps({"ResultSet": {"engineVersion": "202008_02a", "Course": [{}]}}),
        )
    )
    c = Client(base, "key")
    c.search_course_extreme({"viaList": "1:2"})
    assert c.engine_version == "202008_02a"


def test_client_keeps_last_engine_version_when_response_omits_it(start_server):
    import json

    versions = ["202008_02a", None]

    def handler(path, query):
        v = versions.pop(0)
        rs = {"Course": [{}]}
        if v is not None:
            rs["engineVersion"] = v
        return 200, json.dumps({"ResultSet": rs})

    c = Client(start_server(handler), "key")
    c.search_course_extreme({"viaList": "1:2"})
    c.search_course_extreme({"viaList": "1:2"})
    assert c.engine_version == "202008_02a"
