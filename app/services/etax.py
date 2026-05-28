import requests

ETAX_URL = "https://www.etax.nat.gov.tw/etwmain/api/functions/etw158w/calculate/5101"


def fetch_etax_calculate(
    year: int,
    month: int,
    curr_val: str,
    orig_val: str,
    area: str,
    land_type: str,
    numerator: str,
    denominator: str,
    price_idx: str,
) -> dict:
    payload = {
        "year": year,
        "month": month,
        "currVal": curr_val,
        "origVal": orig_val,
        "priceIdx": price_idx,
        "area": area,
        "landType": land_type,
        "numerator": numerator,
        "denominator": denominator,
    }
    resp = requests.post(ETAX_URL, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()
