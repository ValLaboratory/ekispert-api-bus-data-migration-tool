"""駅すぱあと API クライアントとレスポンス型。"""

from __future__ import annotations

import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

course_edit_path = "/v1/json/course/edit"
search_course_extreme_path = "/v1/json/search/course/extreme"

request_timeout = 60
max_attempts = 3
retry_base_wait = 2.0

_KEY_QUERY_RE = re.compile(r"([?&]key=)[^&\s'\"]*")

_REDACTED = "***"


def redact(text, access_key=""):
    if not text:
        return text
    text = _KEY_QUERY_RE.sub(r"\1" + _REDACTED, text)
    if access_key:
        forms = {
            access_key,
            urllib.parse.quote(access_key, safe=""),
            urllib.parse.quote_plus(access_key),
        }
        for form in sorted(forms, key=len, reverse=True):
            if form:
                text = text.replace(form, _REDACTED)
    return text


_MAX_ERROR_BODY = 300


def _summarize(text):
    text = " ".join((text or "").split())
    if len(text) <= _MAX_ERROR_BODY:
        return text
    return text[:_MAX_ERROR_BODY] + "…(以下省略)"


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


@dataclass
class Station:
    code: str = ""
    name: str = ""


@dataclass
class Point:
    station: list = field(default_factory=list)

    def station_first(self):
        return self.station[0] if self.station else Station()


@dataclass
class Datetime:
    text: str = ""


@dataclass
class State:
    datetime: Datetime = field(default_factory=Datetime)


@dataclass
class Line:
    name: str = ""
    direction: str = ""
    departure_state: State = field(default_factory=State)


@dataclass
class Route:
    point: list = field(default_factory=list)
    line: list = field(default_factory=list)
    time_on_board: str = ""
    time_walk: str = ""
    time_other: str = ""

    def points(self):
        return self.point

    def lines(self):
        return self.line

    def point_codes(self):
        return [p.station_first().code for p in self.point]

    def total_minutes(self):
        """所要時間（乗車・徒歩・その他の合計）を分で返す。取得できなければ第2戻り値が False。"""
        total = 0
        found = False
        for s in (self.time_on_board, self.time_walk, self.time_other):
            v = parse_int(s)
            if v is not None:
                total += v
                found = True
        return total, found


@dataclass
class AssignStatus:
    code: str = ""
    require_update: str = ""


@dataclass
class Price:
    kind: str = ""
    selected: str = ""
    oneway: str = ""


@dataclass
class Course:
    data_type: str = ""
    serialize_data: str = ""
    route: Route = field(default_factory=Route)
    assign_status: AssignStatus = field(default_factory=AssignStatus)
    price: list = field(default_factory=list)

    def fare_total(self):
        """片道の運賃(Fare)と料金(Charge)の合計を円で返す。取得できなければ第2戻り値が False。"""
        total = 0
        found = False
        for kind in ("Fare", "Charge"):
            v, ok = self._price_of(kind)
            if ok:
                total += v
                found = True
        return total, found

    def _price_of(self, kind):
        """指定した種別の片道金額を返す。区間合計(Summary)があればそれを使い、
        無い場合は合計計算に含まれる区間(selected=true)を合算する。"""
        for p in self.price:
            if p.kind == kind + "Summary":
                v = parse_int(p.oneway)
                if v is not None:
                    return v, True
                return 0, False
        total = 0
        found = False
        for p in self.price:
            if p.kind != kind or p.selected != "true":
                continue
            v = parse_int(p.oneway)
            if v is not None:
                total += v
                found = True
        return total, found


@dataclass
class ResultSet:
    course: list = field(default_factory=list)
    engine_version: str = ""


@dataclass
class APIResponse:
    result_set: ResultSet = field(default_factory=ResultSet)


def parse_int(s):
    s = (s or "").strip()
    if s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def parse_station(obj):
    obj = obj or {}
    return Station(code=obj.get("code") or "", name=obj.get("Name") or "")


def parse_point(obj):
    obj = obj or {}
    return Point(station=[parse_station(x) for x in _as_list(obj.get("Station"))])


def parse_state(obj):
    obj = obj or {}
    dt = obj.get("Datetime") or {}
    return State(datetime=Datetime(text=dt.get("text") or ""))


def parse_line(obj):
    obj = obj or {}
    return Line(
        name=obj.get("Name") or "",
        direction=obj.get("direction") or "",
        departure_state=parse_state(obj.get("DepartureState")),
    )


def parse_route(obj):
    obj = obj or {}
    return Route(
        point=[parse_point(x) for x in _as_list(obj.get("Point"))],
        line=[parse_line(x) for x in _as_list(obj.get("Line"))],
        time_on_board=obj.get("timeOnBoard") or "",
        time_walk=obj.get("timeWalk") or "",
        time_other=obj.get("timeOther") or "",
    )


def parse_assign_status(obj):
    obj = obj or {}
    return AssignStatus(
        code=obj.get("code") or "",
        require_update=obj.get("requireUpdate") or "",
    )


def parse_price(obj):
    obj = obj or {}
    return Price(
        kind=obj.get("kind") or "",
        selected=obj.get("selected") or "",
        oneway=obj.get("Oneway") or "",
    )


def parse_course(obj):
    obj = obj or {}
    return Course(
        data_type=obj.get("dataType") or "",
        serialize_data=obj.get("SerializeData") or "",
        route=parse_route(obj.get("Route")),
        assign_status=parse_assign_status(obj.get("AssignStatus")),
        price=[parse_price(x) for x in _as_list(obj.get("Price"))],
    )


def parse_response(obj):
    rs = (obj or {}).get("ResultSet") or {}
    return APIResponse(
        result_set=ResultSet(
            course=[parse_course(x) for x in _as_list(rs.get("Course"))],
            engine_version=rs.get("engineVersion") or "",
        )
    )


class Client:
    def __init__(self, base_url, access_key):
        self.base_url = base_url
        self.access_key = access_key
        self.engine_version = ""

    def get(self, path, params):
        try:
            return self._get(path, params)
        except Exception as e:
            raise self._redact(e) from None

    def _get(self, path, params):
        v = dict(params or {})
        v["key"] = self.access_key
        req_url = self.base_url + path + "?" + urllib.parse.urlencode(v)
        endpoint = self.base_url + path

        last_err = None
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                time.sleep(retry_base_wait * (1 << (attempt - 2)))
            resp, retryable, err = self._do_once(req_url, endpoint)
            if err is None:
                if resp.result_set.engine_version != "":
                    self.engine_version = resp.result_set.engine_version
                return resp
            last_err = err
            if not retryable:
                raise err
        raise RuntimeError("%d回試行しましたが失敗しました: %s" % (max_attempts, last_err))

    def _redact(self, err):
        if err is None:
            return err
        msg = str(err)
        redacted = redact(msg, self.access_key)
        if redacted == msg:
            return err
        return RuntimeError(redacted)

    def _do_once(self, req_url, endpoint):
        try:
            req = urllib.request.Request(req_url, method="GET")
            resp = urllib.request.urlopen(req, timeout=request_timeout)
            body = resp.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            retryable = e.code == 429 or e.code >= 500
            return (
                None,
                retryable,
                RuntimeError(
                    "APIがエラーを返しました (status=%d): %s"
                    % (e.code, _summarize(body.decode("utf-8", "replace")))
                ),
            )
        except ValueError as e:
            return (
                None,
                False,
                RuntimeError("API呼び出し先のURLが不正です (%s): %s" % (endpoint, e)),
            )
        except (OSError, http.client.HTTPException) as e:
            return (
                None,
                True,
                RuntimeError("API呼び出しに失敗 (%s): %s" % (endpoint, e)),
            )
        except Exception as e:
            return (
                None,
                False,
                RuntimeError("API呼び出しに失敗 (%s): %s" % (endpoint, e)),
            )

        try:
            obj = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return None, False, RuntimeError("レスポンスのパースに失敗: %s" % e)

        result_set = obj.get("ResultSet") or {}
        errors = _as_list(result_set.get("Error"))
        if errors:
            e0 = errors[0]
            return (
                None,
                False,
                RuntimeError(
                    "APIエラー (code=%s): %s %s" % (e0.get("code"), e0.get("Text"), e0.get("Message"))
                ),
            )
        return parse_response(obj), False, None

    def course_edit(self, serialize_data, check_engine_version):
        params = {
            "serializeData": serialize_data,
            "checkEngineVersion": "true" if check_engine_version else "false",
        }
        resp = self.get(course_edit_path, params)
        if not resp.result_set.course:
            raise RuntimeError("course/editの結果が空です")
        return resp.result_set.course[0]

    def search_course_extreme_all(self, search_params):
        """探索結果の全経路を返す。返る件数は answerCount で制御する。"""
        params = {k: v for k, v in search_params.items() if v != ""}
        resp = self.get(search_course_extreme_path, params)
        if not resp.result_set.course:
            raise RuntimeError("経路探索の結果が空です")
        return resp.result_set.course

    def search_course_extreme(self, search_params):
        """探索結果の先頭1件を返す。"""
        courses = self.search_course_extreme_all(search_params)
        return courses[0]
