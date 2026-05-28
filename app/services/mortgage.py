from datetime import datetime

import requests

MIAOGU_URL = "http://localhost:8004/v4/mortgages/miaogu"


def fetch_house_price(
    twd_x: float,
    twd_y: float,
    county: str,
    district: str,
    address: str,
    total_floors: int,
) -> dict:
    payload = {
        "ADDRESS_X": str(twd_x),
        "ADDRESS_Y": str(twd_y),
        "AGE": "0.5",
        "BDTYPE_DETL": "1",
        "BUILDMSR": "1",
        "CERPT_VALIDDT": datetime.now().strftime("%Y-%m-%d"),
        "ChannelCode": "def0001",
        "FLOOR": str(total_floors // 2),
        "GUCITY": county,
        "GUSTRE": address,
        "GUTOWN": district,
        "SNO": "abc0001",
        "STORY": str(total_floors),
    }
    resp = requests.post(MIAOGU_URL, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()
