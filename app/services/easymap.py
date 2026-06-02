import re
from functools import lru_cache

import requests
import urllib3
from bs4 import BeautifulSoup

from app.utils import wgs2twd, wgs2address

TARGET_FIELDS = {"面積", "公告現值", "公告地價"}

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://easymap.moi.gov.tw/Z10Web"
TOKEN_URL = f"{BASE_URL}/layout/setToken.jsp"
DETAIL_URL = f"{BASE_URL}/LandDesc_ajax_detail"
CITY_LIST_URL = f"{BASE_URL}/City_json_getList"
TOWN_LIST_URL = f"{BASE_URL}/City_json_getTownList"
SECTION_LIST_URL = f"{BASE_URL}/City_json_getSectionList"
LOCATE_URL = f"{BASE_URL}/Land_json_locate"

TOKEN_PATTERN = re.compile(
    r'name="token"\s+value="([^"]+)"'
)
NUMBER_PATTERN = re.compile(r"[-+]?\d*\.?\d+")

# (連線, 讀取) 秒; 上游 easymap 偶爾會卡住, 加 timeout 避免拖滿 threadpool
HTTP_TIMEOUT = (5, 30)


def fetch_token(session: requests.Session) -> str:
    resp = session.post(TOKEN_URL, verify=False, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    match = TOKEN_PATTERN.search(resp.text)
    if not match:
        raise RuntimeError(f"token not found in response: {resp.text!r}")
    return match.group(1)


def fetch_land_detail(
    city_code: str,
    town_code: str,
    office: str,
    sect_no: str,
    land_no: str,
) -> dict[str, float]:
    session = requests.Session()
    token = fetch_token(session)
    payload = {
        "cityCode": city_code,
        "townCode": town_code,
        "office": office,
        "sectNo": sect_no,
        "landNo": land_no,
        "struts.token.name": "token",
        "token": token,
    }
    resp = session.post(DETAIL_URL, data=payload, verify=False, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    # 只負責地籍 HTML 資料（面積 / 公告現值 / 公告地價）；座標與地址見 fetch_land_geo
    return parse_land_detail_html(resp.text)


def parse_land_detail_html(html: str) -> dict[str, float]:
    """解析 easymap 回傳的 HTML，回傳 {欄位中文: 數值(float)}，單位字串已去除。"""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise RuntimeError("table not found in response")

    result: dict[str, float] = {}
    for row in table.find_all("tr"):
        th = row.find("th")
        td = row.find("td")
        if th is None or td is None:
            continue
        key = th.get_text(strip=True)
        if key not in TARGET_FIELDS:
            continue
        text = td.get_text(strip=True)
        match = NUMBER_PATTERN.search(text)
        if match is None:
            raise ValueError(f"cannot parse number from {key!r}: {text!r}")
        result[key] = float(match.group(0))
    return result


def _post_with_token(url: str, extra: dict[str, str] | None = None):
    session = requests.Session()
    token = fetch_token(session)
    payload: dict[str, str] = {
        "struts.token.name": "token",
        "token": token,
    }
    if extra:
        payload.update(extra)
    resp = session.post(url, data=payload, verify=False, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


@lru_cache(maxsize=1)
def get_cities() -> list[dict]:
    """[{id, name}]"""
    return _post_with_token(CITY_LIST_URL)


@lru_cache(maxsize=32)
def get_towns(city_code: str) -> list[dict]:
    """[{id, name}]"""
    return _post_with_token(TOWN_LIST_URL, {"cityCode": city_code})


@lru_cache(maxsize=512)
def get_sections(city_code: str, town_code: str) -> list[dict]:
    """[{id, name, officeCode, townCode}]"""
    return _post_with_token(
        SECTION_LIST_URL,
        {"cityCode": city_code, "townCode": town_code},
    )


def fetch_land_location(office: str, sect_no: str, land_no: str) -> tuple[float, float]:
    """回傳 (longitude, latitude)。easymap 回傳 {x, y}，x=經度、y=緯度。"""
    data = _post_with_token(
        LOCATE_URL,
        {"office": office, "sectNo": sect_no, "landNo": land_no},
    )
    return float(data["X"]), float(data["Y"])


def fetch_land_geo(office: str, sect_no: str, land_no: str) -> dict[str, float | str]:
    """抓地號中心點座標（WGS84 → TWD97）與反向地理編碼地址。

    回傳 {twd_x, twd_y, address}；房價估值（query_house_price）專用。
    """
    lng, lat = fetch_land_location(
        office=office, sect_no=sect_no, land_no=land_no)
    twd = wgs2twd(lat=lat, lng=lng)
    return {
        "twd_x": twd["x"],
        "twd_y": twd["y"],
        "address": wgs2address(lat=lat, lng=lng),
    }


def resolve_codes(county: str, district: str, section_no: str) -> tuple[str, str, str, str]:
    """中文縣市 / 行政區 / 段代碼 → (city_code, town_code, office_code)"""
    city = next((c for c in get_cities() if c["name"] == county), None)
    if city is None:
        raise ValueError(f"unknown county: {county!r}")
    city_code = city["id"]

    town = next((t for t in get_towns(city_code)
                if t["name"] == district), None)
    if town is None:
        raise ValueError(f"unknown district {district!r} in {county!r}")
    town_code = town["id"]

    section = next(
        (s for s in get_sections(city_code, town_code)
         if ((s["id"] == section_no) or (s["name"] == section_no))),
        None,
    )
    if section is None:
        raise ValueError(
            f"unknown section_no {section_no!r} in {county!r}/{district!r}"
        )
    return city_code, town_code, section["officeCode"], section["id"]


def main():
    city_code, town_code, office = resolve_codes("新北市", "三重區", "1768")
    result = fetch_land_detail(
        city_code=city_code,
        town_code=town_code,
        office=office,
        sect_no="1768",
        land_no="46",
    )
    print(result)


if __name__ == "__main__":
    main()
