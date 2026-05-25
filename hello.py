import re

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TOKEN_URL = "https://easymap.moi.gov.tw/R02/pages/setToken.jsp"
DETAIL_URL = "https://easymap.moi.gov.tw/R02/LandDesc_ajax_detail"

TOKEN_PATTERN = re.compile(
    r'name="token"\s+value="([^"]+)"'
)


def fetch_token(session: requests.Session) -> str:
    resp = session.post(TOKEN_URL, verify=False)
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
) -> dict:
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
    resp = session.post(DETAIL_URL, data=payload, verify=False)
    print(resp.text)
    resp.raise_for_status()
    return resp.json()


def main():
    result = fetch_land_detail(
        city_code="F",
        town_code="05",
        office="FG",
        sect_no="1768",
        land_no="46",
    )
    print(result)


if __name__ == "__main__":
    main()
