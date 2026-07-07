"""全國土地使用分區資料查詢系統 (luz.nlma.gov.tw) 服務。

作為 easymap 的備援來源，最終目標同樣是取得 面積 / 公告現值 / 公告地價。
使用者提供「縣市 / 鄉鎮 / 地段 / 地號」中文名稱，查詢流程：

    縣市名 ──get_counties──▶ COUNTY 代號
    COUNTY ─get_towns────▶ TOWN 代號 (依鄉鎮名比對)
    TOWN ──get_sections──▶ LANDSEC 代號 (依地段名比對)
    LANDSEC + 地號 ─search_cada─▶ 面積 / 公告現值 / 公告地價

query_land_value() 一次串完整條流程。
"""

import re
import time
from functools import lru_cache

import requests
import urllib3
from bs4 import BeautifulSoup

# 對齊 easymap: 只回傳這三個欄位, 讓 luz 能直接當備援
TARGET_FIELDS = ("面積", "公告現值", "公告地價")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://luz.nlma.gov.tw/web"
INDEX_URL = f"{BASE_URL}/"
FORM_URL = f"{BASE_URL}/ws_form.ashx"
DATA_URL = f"{BASE_URL}/ws_data.ashx"

# 這站所有查詢都固定 FUNC=0101 面板
FUNC = "0101"

# 首頁 inline script 內嵌的 session token: const M_CONFIG = {"Token":"..."}
TOKEN_PATTERN = re.compile(r'"Token"\s*:\s*"([^"]+)"')
# LANDSEC2 的 NAME 前綴代碼, 例如 "[0021]龍泉段一小段" -> "龍泉段一小段"
SEC_NAME_PREFIX = re.compile(r"^\[\d+\]")

# (連線, 讀取) 秒; 對齊 easymap
HTTP_TIMEOUT = (5, 30)

# SEARCHCADA 限流: 連續查詢會回 "請5秒後再進行查詢"; 偵測到就等待重試
RATE_LIMIT_HINT = "請5秒後再進行查詢"
RATE_LIMIT_WAIT = 5.5
RATE_LIMIT_RETRIES = 2

# ws_form.ashx / ws_data.ashx 都用 jQuery $.get/$.post 呼叫,
# 後端會檢查 XMLHttpRequest header, 少了就回空字串。
AJAX_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": INDEX_URL,
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


def _new_session() -> requests.Session:
    """建立 session 並訪問首頁, 取得 ws_*.ashx 必要的 ASP.NET_SessionId cookie。"""
    session = requests.Session()
    session.headers.update(AJAX_HEADERS)
    session.get(INDEX_URL, verify=False, timeout=HTTP_TIMEOUT)
    return session


def fetch_token(session: requests.Session) -> str:
    """GET 首頁, 從 M_CONFIG 撈出當次 session token。"""
    resp = session.get(INDEX_URL, verify=False, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    match = TOKEN_PATTERN.search(resp.text)
    if not match:
        raise RuntimeError(f"luz token not found: {resp.text[:500]!r}")
    return match.group(1)


def fetch_form(func: str, session: requests.Session | None = None) -> str:
    """呼叫 ws_form.ashx?CMD=GETFORM 取得指定面板的 HTML。

    func 例如 "#0101"（都市計畫/地段查詢面板）。少了首頁建立的 session
    cookie 後端會回空字串, 故務必用帶過首頁的 session。
    """
    session = session or _new_session()
    resp = session.get(
        FORM_URL,
        params={"CMD": "GETFORM", "FUNC": func},
        verify=False,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.text


def _get_data(obj: str, extra: dict[str, str]) -> list[dict]:
    """呼叫 ws_data.ashx?CMD=GETDATA&OBJ=<obj>, 回傳 JSON list。"""
    session = _new_session()
    token = fetch_token(session)
    resp = session.post(
        DATA_URL,
        params={"CMD": "GETDATA", "OBJ": obj, "TOKEN": token},
        data=extra,
        verify=False,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("success") == "false":
        raise RuntimeError(f"luz GETDATA {obj} failed: {data.get('status')!r}")
    return data


@lru_cache(maxsize=1)
def get_counties() -> dict[str, str]:
    """縣市代號 -> 縣市名稱 (例: {"63000": "臺北市", ...})。

    取自 GETFORM #0101 面板中 id=COUNTY_0101 的 select options。
    """
    html = fetch_form(f"#{FUNC}")
    soup = BeautifulSoup(html, "html.parser")
    select = soup.find("select", id=f"COUNTY_{FUNC}")
    if select is None:
        raise RuntimeError("COUNTY_0101 select not found in GETFORM #0101 response")

    counties: dict[str, str] = {}
    for option in select.find_all("option"):
        code = (option.get("value") or "").strip()
        name = option.get_text(strip=True)
        if code:
            counties[code] = name
    if not counties:
        raise RuntimeError("no county options parsed from COUNTY_0101")
    return counties


@lru_cache(maxsize=32)
def get_towns(county_code: str) -> dict[str, str]:
    """鄉鎮名稱 -> 鄉鎮代號 (TownID)。GETDATA OBJ=TOWN。"""
    data = _get_data("TOWN", {"FUNC": FUNC, "COUNTY": county_code})
    return {t["TownName"]: t["TownID"] for t in data}


@lru_cache(maxsize=512)
def get_sections(town_id: str) -> dict[str, str]:
    """地段名稱 -> 地段代號 (LANDSEC ID, 6 碼)。GETDATA OBJ=LANDSEC2。

    NAME 原始格式為 "[0021]龍泉段一小段"，key 去掉 [代碼] 前綴以便用地段名比對。
    """
    data = _get_data("LANDSEC2", {"FUNC": FUNC, "TOWN": town_id})
    sections: dict[str, str] = {}
    for s in data:
        name = SEC_NAME_PREFIX.sub("", s["NAME"]).strip()
        sections[name] = s["ID"]
    return sections


def resolve_county(county_name: str) -> str:
    for code, name in get_counties().items():
        if name == county_name:
            return code
    raise KeyError(f"county not found: {county_name!r}")


def resolve_town(county_code: str, town_name: str) -> str:
    towns = get_towns(county_code)
    if town_name in towns:
        return towns[town_name]
    raise KeyError(f"town not found: {town_name!r} in county {county_code}")


def resolve_section(town_id: str, section_name: str) -> str:
    sections = get_sections(town_id)
    if section_name in sections:
        return sections[section_name]
    raise KeyError(f"section not found: {section_name!r} in town {town_id}")


def pad_cada_no(land_no: str) -> str:
    """將地號補成 8 碼 (母號 4 + 子號 4)。

    "1"     -> "00010000"
    "1-2"   -> "00010002"
    """
    parts = land_no.split("-")
    if len(parts) == 1:
        return parts[0].strip().zfill(4) + "0000"
    return parts[0].strip().zfill(4) + parts[1].strip().zfill(4)


def search_cada(landsec_id: str, land_no: str) -> dict:
    """CMD=SEARCHCADA, 回傳該地號的 attributes（含面積/公告現值/公告地價等）。

    VAL1 = LANDSEC 代號(6) + 補零地號(8)，總長須為 14。
    注意上游有 rate limit（連續查詢會回「請5秒後再進行查詢」），
    批次查詢時呼叫端需自行間隔 >= 5 秒。
    """
    val1 = landsec_id + pad_cada_no(land_no)
    if len(val1) != 14:
        raise ValueError(f"VAL1 must be 14 chars, got {val1!r} (len={len(val1)})")

    session = _new_session()
    token = fetch_token(session)
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        resp = session.post(
            DATA_URL,
            params={"CMD": "SEARCHCADA", "TOKEN": token},
            data={"VAL1": val1, "GTOKEN": "", "Z": "W"},
            verify=False,
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("success") == "false":
            status = str(data.get("status"))
            # 撞到限流就等待重試, 其餘失敗直接拋
            if RATE_LIMIT_HINT in status and attempt < RATE_LIMIT_RETRIES:
                time.sleep(RATE_LIMIT_WAIT)
                continue
            raise RuntimeError(f"luz SEARCHCADA failed: {status!r}")
        features = data.get("features") if isinstance(data, dict) else None
        if not features:
            raise RuntimeError(f"luz SEARCHCADA no result for VAL1={val1!r}")
        return features[0]["attributes"]
    # 迴圈結束仍未成功 = 重試用盡都還在限流
    raise RuntimeError(
        f"luz SEARCHCADA rate limited after {RATE_LIMIT_RETRIES} retries: {val1!r}"
    )


def query_land_value(
    county_name: str,
    town_name: str,
    section_name: str,
    land_no: str,
) -> dict[str, float]:
    """一次串完整條流程，回傳 {面積, 公告現值, 公告地價}（對齊 easymap 輸出）。"""
    county_code = resolve_county(county_name)
    town_id = resolve_town(county_code, town_name)
    landsec_id = resolve_section(town_id, section_name)
    attrs = search_cada(landsec_id, land_no)
    return {field: float(attrs[field]) for field in TARGET_FIELDS}
