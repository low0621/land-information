import csv
import io
import os
import uuid as uuid_lib
from contextlib import asynccontextmanager
from fractions import Fraction

import anyio.to_thread
import xlrd
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.crypto import decrypt_file_bytes, decrypt_json
from app.database import get_db
from app.models import PriceIndex, Project, Zoning
from app.schemas import (
    HousePriceQuery,
    HousePriceResponse,
    LandTaxQuery,
    LandTaxResponse,
    ParcelQuery,
    ParcelResponse,
    PdfAnalysisResponse,
    PriceIndexUploadResponse,
    ProjectCreate,
    ProjectDelete,
    ProjectResponse,
    ProjectUpdate,
    ZoningUploadResponse,
)
from app.seed import init_db
from app.services.easymap import fetch_land_detail, fetch_land_geo, resolve_codes
from app.services.etax import fetch_etax_calculate
from app.services.mortgage import fetch_house_price
from app.services.pdf_analysis import analyze_pdf

ZONING_CSV_HEADER_MAP = {
    "縣市": "county",
    "行政區": "district",
    "都市計畫地區": "urban_plan",
    "使用分區": "land_use_zone",
    "建蔽率": "building_coverage",
    "容積率": "floor_area_ratio",
}
ZONING_CSV_COLUMNS = list(ZONING_CSV_HEADER_MAP.keys())

@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001 — FastAPI 規定的簽名
    # startup
    init_db()
    # 擴大 anyio threadpool: FastAPI 把同步 def handler 丟到這跑, 預設只有 40 條,
    # 上游 (easymap / etax / mortgage) 慢或卡住時容易吃滿
    threadpool_size = int(os.environ.get("THREADPOOL_SIZE", "200"))
    anyio.to_thread.current_default_thread_limiter().total_tokens = threadpool_size
    yield
    # shutdown (目前無需特別處理)


app = FastAPI(title="Land Information Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get(
    "/api/users/{user_id}/projects",
    response_model=list[ProjectResponse],
    summary="取得使用者的所有專案",
    description=(
        "依使用者 `user_id` 撈出該使用者擁有的所有專案。\n\n"
        "**Path 參數**：\n"
        "- `user_id` (str)：使用者 id\n\n"
        "**成功回應 `200`**：list of `{ pid, user_id, data }`，使用者沒有任何專案時回傳空陣列"
    ),
)
def list_user_projects(
    user_id: str,
    db: Session = Depends(get_db),
) -> list[ProjectResponse]:
    projects = db.query(Project).filter(Project.user_id == user_id).all()
    return [
        ProjectResponse(pid=p.pid, user_id=p.user_id, data=p.data)
        for p in projects
    ]


@app.post(
    "/api/projects",
    response_model=ProjectResponse,
    status_code=201,
    summary="新增專案",
    description=(
        "建立一筆新的專案，將 JSON 內容整包存入 `projects` 表。\n\n"
        "**Request body 參數**：\n"
        "- `user_id` (str)：使用者 id\n"
        "- `data` (object)：專案內容，任意 JSON 物件\n\n"
        "**回應**：\n"
        "- `pid` (str)：由後端產生的專案 id（uuid4）\n"
        "- `user_id` (str)：與 payload 相同\n"
        "- `data` (object)：與 payload 相同\n\n"
        "**錯誤回應**：\n"
        "- `422`：payload 欄位缺漏或型別錯誤"
    ),
)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
) -> ProjectResponse:
    # data / data_enc 擇一：有加密就先解密還原成原始 JSON
    if payload.data_enc is not None:
        try:
            data = decrypt_json(payload.data_enc)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"data_enc 解密失敗: {e}")
    elif payload.data is not None:
        data = payload.data
    else:
        raise HTTPException(status_code=422, detail="必須提供 data 或 data_enc 其一")

    pid = str(uuid_lib.uuid4())
    project = Project(pid=pid, user_id=payload.user_id, data=data)
    db.add(project)
    db.commit()
    db.refresh(project)
    return ProjectResponse(pid=project.pid, user_id=project.user_id, data=project.data)


@app.put(
    "/api/projects",
    response_model=ProjectResponse,
    summary="更新專案",
    description=(
        "依 `pid` 找到專案後，整包覆寫 `data`。需提供 `user_id` 驗證擁有者。\n\n"
        "**Request body 參數**：\n"
        "- `user_id` (str)：使用者 id（驗證擁有者）\n"
        "- `pid` (str)：要更新的專案 id\n"
        "- `data` (object)：更新後的專案內容，會整包覆蓋原本的 `data`\n\n"
        "**錯誤回應**：\n"
        "- `404`：找不到對應 `pid` 的專案\n"
        "- `403`：`user_id` 與專案擁有者不符\n"
        "- `422`：payload 欄位缺漏或型別錯誤"
    ),
)
def update_project(
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
) -> ProjectResponse:
    # data / data_enc 擇一：有加密就先解密還原成原始 JSON
    if payload.data_enc is not None:
        try:
            data = decrypt_json(payload.data_enc)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"data_enc 解密失敗: {e}")
    elif payload.data is not None:
        data = payload.data
    else:
        raise HTTPException(status_code=422, detail="必須提供 data 或 data_enc 其一")

    project = db.query(Project).filter(Project.pid == payload.pid).first()
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if project.user_id != payload.user_id:
        raise HTTPException(
            status_code=403, detail="user_id does not match project owner")

    project.data = data
    db.commit()
    db.refresh(project)
    return ProjectResponse(pid=project.pid, user_id=project.user_id, data=project.data)


@app.delete(
    "/api/projects",
    status_code=204,
    summary="刪除專案",
    description=(
        "依 `pid` 刪除專案，需提供 `user_id` 驗證擁有者。\n\n"
        "**Request body 參數**：\n"
        "- `user_id` (str)：使用者 id（驗證擁有者）\n"
        "- `pid` (str)：要刪除的專案 id\n\n"
        "**成功回應**：`204` No Content\n\n"
        "**錯誤回應**：\n"
        "- `404`：找不到對應 `pid` 的專案\n"
        "- `403`：`user_id` 與專案擁有者不符\n"
        "- `422`：payload 欄位缺漏或型別錯誤"
    ),
)
def delete_project(
    payload: ProjectDelete,
    db: Session = Depends(get_db),
) -> None:
    project = db.query(Project).filter(Project.pid == payload.pid).first()
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if project.user_id != payload.user_id:
        raise HTTPException(
            status_code=403, detail="user_id does not match project owner")

    db.delete(project)
    db.commit()
    return None


@app.post(
    "/api/parcels",
    response_model=ParcelResponse,
    summary="查詢地號相關資訊",
    description=(
        "依縣市 / 行政區 / 都市計畫地區 / 使用分區 / 地段號 / 地號 / 所有權人 / 持分查詢土地資訊。\n\n"
        "**Request body 參數**：\n"
        "- `county` (str)：縣市，例如「臺北市」\n"
        "- `district` (str)：行政區，例如「大安區」\n"
        "- `urban_plan` (str)：都市計畫地區名稱，例如「臺北市都市計畫」\n"
        "- `land_use_zone` (str)：使用分區，例如「第三種住宅區」、「商業區」\n"
        "- `section_no` (str)：地段號（段／小段代碼），用於定位地籍\n"
        "- `land_no` (str)：地號，與地段號合併後可唯一指向一筆地籍\n"
        "- `owner` (str)：所有權人姓名；目前僅收進 payload，預留未來權狀／歷史紀錄查詢使用\n"
        "- `share` (float, 0 < share ≤ 1)：持分比例。`1` 表示完全持有，`0.5` 表示一半\n\n"
        "**處理邏輯**：\n"
        "- `building_coverage`（建蔽率）、`floor_area_ratio`（容積率）：由 `zoning` 表以 (county, district, urban_plan, land_use_zone) 四欄完全比對撈出\n"
        "- `area`（面積）、`announced_value`（公告現值）：透過 `easymap.moi.gov.tw` 爬取後解析數值\n"
        "- 以上兩項回傳前皆會乘上 `share`\n"
        "- 房價估值已獨立為 `POST /api/house-price`，本端點不再回傳\n\n"
        "**錯誤回應**：\n"
        "- `404`：找不到符合條件的使用分區資料\n"
        "- `422`：payload 欄位缺漏或型別錯誤（FastAPI 預設驗證）\n"
        "- `502`：呼叫 easymap 失敗或解析失敗"
    ),
)
def query_parcel(payload: ParcelQuery, db: Session = Depends(get_db)) -> ParcelResponse:
    zoning = (
        db.query(Zoning)
        .filter(
            Zoning.county == payload.county,
            Zoning.district == payload.district,
            Zoning.urban_plan == payload.urban_plan,
            Zoning.land_use_zone == payload.land_use_zone,
        )
        .first()
    )
    if zoning is None:
        raise HTTPException(status_code=404, detail="zoning not found")

    # 面積 / 公告現值：透過 easymap 爬蟲取得
    try:
        city_code, town_code, office, section_no = resolve_codes(
            county=payload.county,
            district=payload.district,
            section_no=payload.section_no,
        )
        detail = fetch_land_detail(
            city_code=city_code,
            town_code=town_code,
            office=office,
            sect_no=section_no,
            land_no=payload.land_no,
        )
        area = detail["面積"]
        announced_value = detail["公告現值"]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"easymap fetch failed: {e}")

    return ParcelResponse(
        building_coverage=zoning.building_coverage,
        floor_area_ratio=zoning.floor_area_ratio,
        area=area * payload.share,
        announced_value=announced_value * payload.share,
    )


@app.post(
    "/api/house-price",
    response_model=HousePriceResponse,
    summary="查詢地號單價估值",
    description=(
        "依縣市 / 行政區 / 地段號 / 地號，呼叫內部 miaogu 估價服務取得單價。\n\n"
        "**Request body 參數**：\n"
        "- `county` (str)：縣市\n"
        "- `district` (str)：行政區\n"
        "- `section_no` (str)：地段號（段代碼）\n"
        "- `land_no` (str)：地號\n"
        "- `total_floors` (int, >0)：樓層總高度（總樓層數），送進 miaogu 的 `STORY`；`FLOOR` 取 `total_floors // 2`\n\n"
        "**處理邏輯**：\n"
        "- 先以 easymap 取得地號 WGS84 座標，轉成 TWD97\n"
        "- 反向地理編碼取得門牌字串\n"
        "- 以上資訊送 miaogu，回傳 response 中的 `unit_price`\n\n"
        "**錯誤回應**：\n"
        "- `400`：縣市 / 行政區 / 地段號對不到 easymap 資料\n"
        "- `502`：easymap 或 miaogu 服務呼叫失敗"
    ),
)
def query_house_price(payload: HousePriceQuery) -> HousePriceResponse:
    try:
        _city_code, _town_code, office, section_no = resolve_codes(
            county=payload.county,
            district=payload.district,
            section_no=payload.section_no,
        )
        geo = fetch_land_geo(
            office=office,
            sect_no=section_no,
            land_no=payload.land_no,
        )
    except ValueError as e:
        print(e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print("easymap error: ", e)
        raise HTTPException(
            status_code=502, detail=f"easymap fetch failed: {e}")

    try:
        price_result = fetch_house_price(
            twd_x=geo["twd_x"],
            twd_y=geo["twd_y"],
            county=payload.county,
            district=payload.district,
            address=geo.get("address", "") or "",
            total_floors=payload.total_floors,
        )
    except Exception as e:
        print("mortgage error: ", e)
        raise HTTPException(
            status_code=502, detail=f"house price service failed: {e}")

    return HousePriceResponse(unit_price=float(price_result["unit_price"]))


@app.post(
    "/api/land-tax",
    response_model=LandTaxResponse,
    summary="試算土地稅",
    description=(
        "呼叫 etax 試算 API（`5101`）計算土地稅。前端提供所有條件，物價指數 `priceIdx` 由後端從 `price_index` 表依 `year`/`month` 查表取得。\n\n"
        "**Request body 參數**：\n"
        "- `year` (int)：計算年度\n"
        "- `month` (int, 1-12)：計算月份\n"
        "- `curr_val` (str)：現值\n"
        "- `orig_val` (str)：原規定地價\n"
        "- `area` (str)：土地面積\n"
        "- `land_type` (str)：土地類別代碼\n"
        "- `share` (str)：持分（float 字串），例如 `0.5`、`0.25`\n\n"
        "**處理邏輯**：\n"
        "- 將 `share` 轉成 `Fraction` 後（限制分母最大 10）取 `numerator` / `denominator` 送 etax\n"
        "- 回傳 etax response 中的 `result.param5`\n\n"
        "**錯誤回應**：\n"
        "- `400`：`share` 無法解析成數字、≤ 0，或 `year`/`month` 在 `price_index` 表查不到\n"
        "- `502`：etax 服務呼叫失敗或回傳格式不符"
    ),
)
def query_land_tax(
    payload: LandTaxQuery,
    db: Session = Depends(get_db),
) -> LandTaxResponse:
    try:
        share_value = float(payload.share)
    except ValueError:
        print(payload.share)
        raise HTTPException(
            status_code=400, detail=f"invalid share: {payload.share!r}")
    if share_value <= 0:
        raise HTTPException(status_code=400, detail="share must be > 0")

    frac = Fraction(share_value).limit_denominator(10)
    numerator, denominator = str(frac.numerator), str(frac.denominator)

    price_index = (
        db.query(PriceIndex)
        .filter(PriceIndex.year == payload.year, PriceIndex.month == payload.month)
        .first()
    )
    if price_index is None:
        print(payload.year, payload.month)
        raise HTTPException(
            status_code=400,
            detail=f"查詢年月超出範圍：{payload.year}/{payload.month} 無物價指數資料",
        )

    try:
        result = fetch_etax_calculate(
            year=payload.year,
            month=payload.month,
            curr_val=payload.curr_val,
            orig_val=payload.orig_val,
            area=payload.area,
            land_type=payload.land_type,
            numerator=numerator,
            denominator=denominator,
            price_idx=str(price_index.price_idx),
        )
    except Exception as e:
        print("etax error: ", e)
        raise HTTPException(status_code=502, detail=f"etax fetch failed: {e}")

    try:
        print(result)
        raw = result["result"]["param5"]
    except (KeyError, TypeError) as e:
        print("etax response error: ", e)
        raise HTTPException(
            status_code=502, detail=f"unexpected etax response: {e}")

    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        print("no digits in param5: ", raw)
        raise HTTPException(
            status_code=502, detail=f"no digits in param5: {raw!r}")

    return LandTaxResponse(param5=int(digits))


@app.post(
    "/api/zoning/upload",
    response_model=ZoningUploadResponse,
    summary="上傳 zoning CSV 並更新資料庫",
    description=(
        "上傳一份 CSV 檔，將 `zoning` 資料寫入資料庫。\n\n"
        "**Form 參數**：\n"
        "- `file` (UploadFile)：UTF-8 編碼的 `.csv` 檔，欄位定義如下\n\n"
        "**CSV 欄位（header 為中文，順序不限）**：\n"
        "- `縣市` (str) → 對應 DB `county`\n"
        "- `行政區` (str) → 對應 DB `district`\n"
        "- `都市計畫地區` (str) → 對應 DB `urban_plan`\n"
        "- `使用分區` (str) → 對應 DB `land_use_zone`\n"
        "- `建蔽率` (str/float) → 對應 DB `building_coverage`；可帶 `%`（如 `30%`），存入前會除以 100 轉為小數（`0.3`）\n"
        "- `容積率` (str/float) → 對應 DB `floor_area_ratio`；可帶 `%`（如 `225%`），存入前會除以 100 轉為小數（`2.25`）\n\n"
        "**行為**：\n"
        "- 以 (縣市, 行政區, 都市計畫地區, 使用分區) 為唯一鍵\n"
        "- 已存在則更新建蔽率／容積率，不存在則新增\n"
        "- 單行格式錯誤會被記錄到 `errors` 並跳過，不影響其他行\n\n"
        "**回應欄位**：\n"
        "- `inserted` (int)：新增筆數\n"
        "- `updated` (int)：更新筆數\n"
        "- `skipped` (int)：略過筆數（格式錯誤）\n"
        "- `errors` (list[str])：每個略過行的錯誤訊息，含行號\n\n"
        "**錯誤回應**：\n"
        "- `400`：副檔名非 `.csv`、檔案編碼非 UTF-8，或 CSV header 欄位不完整"
    ),
)
async def upload_zoning_csv(
    file: UploadFile = File(..., description="zoning CSV 檔"),
    db: Session = Depends(get_db),
) -> ZoningUploadResponse:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="請上傳 .csv 檔")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("big5")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="CSV 編碼需為 UTF-8 或 Big5")

    reader = csv.DictReader(io.StringIO(text))
    print(reader.fieldnames)
    if reader.fieldnames is None or not set(ZONING_CSV_COLUMNS).issubset(reader.fieldnames):
        raise HTTPException(
            status_code=400,
            detail=f"CSV header 必須包含欄位：{', '.join(ZONING_CSV_COLUMNS)}",
        )

    inserted = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    for line_no, row in enumerate(reader, start=2):  # start=2: 第 1 行是 header
        try:
            county = (row["縣市"] or "").strip()
            district = (row["行政區"] or "").strip()
            urban_plan = (row["都市計畫地區"] or "").strip()
            land_use_zone = (row["使用分區"] or "").strip()
            if not all([county, district, urban_plan, land_use_zone]):
                raise ValueError("key 欄位不可為空")
            building_coverage = float(
                (row["建蔽率"] or "").strip().rstrip("%")) * 0.01
            floor_area_ratio = float(
                (row["容積率"] or "").strip().rstrip("%")) * 0.01
        except (KeyError, ValueError, TypeError) as e:
            skipped += 1
            errors.append(f"line {line_no}: {e}")
            continue

        existing = (
            db.query(Zoning)
            .filter(
                Zoning.county == county,
                Zoning.district == district,
                Zoning.urban_plan == urban_plan,
                Zoning.land_use_zone == land_use_zone,
            )
            .first()
        )
        if existing is None:
            db.add(Zoning(
                county=county,
                district=district,
                urban_plan=urban_plan,
                land_use_zone=land_use_zone,
                building_coverage=building_coverage,
                floor_area_ratio=floor_area_ratio,
            ))
            inserted += 1
        else:
            existing.building_coverage = building_coverage
            existing.floor_area_ratio = floor_area_ratio
            updated += 1

    db.commit()
    return ZoningUploadResponse(
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        errors=errors,
    )


@app.post(
    "/api/price-index/upload",
    response_model=PriceIndexUploadResponse,
    summary="上傳物價指數 xls 並更新資料庫",
    description=(
        "上傳消費者物價指數 xls 檔，將每個 (民國年, 月份) 的指數寫入 `price_index` 表。\n\n"
        "**Form 參數**：\n"
        "- `file` (UploadFile)：`.xls` 檔\n\n"
        "**xls 結構**：\n"
        "- 第一欄為民國年，直接以民國年存入 DB（不轉西元）\n"
        "- 第 2~13 欄依序為 1 月～12 月的指數\n"
        "- 第 14 欄（累計平均）會被忽略\n"
        "- 首列若是 header 會自動跳過（首欄無法解析成整數即視為 header）\n\n"
        "**行為**：\n"
        "- 以 (year, month) 為唯一鍵；已存在則更新、否則新增\n"
        "- 任何儲存格解析失敗會計入 `skipped` 並記錄到 `errors`，不影響其他格"
    ),
)
async def upload_price_index(
    file: UploadFile = File(..., description="物價指數 xls 檔（或 AES 加密後的二進位）"),
    encrypted: str | None = Form(None, description="設為 1 表示 file 是 AES 加密的 xls"),
    db: Session = Depends(get_db),
) -> PriceIndexUploadResponse:
    raw = await file.read()
    if encrypted:
        # file 是 AES-GCM 加密的 xls 位元組 (iv ‖ ciphertext+tag)，先解密還原
        try:
            raw = decrypt_file_bytes(raw)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"檔案解密失敗: {e}")
    elif not file.filename or not file.filename.lower().endswith(".xls"):
        print(file.filename)
        raise HTTPException(status_code=400, detail="請上傳 .xls 檔")

    try:
        wb = xlrd.open_workbook(file_contents=raw)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"無法解析 xls: {e}")

    ws = wb.sheet_by_index(0)
    inserted = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    for row_no in range(ws.nrows):
        row = ws.row_values(row_no)
        if not row or row[0] in (None, ""):
            continue
        try:
            year = int(row[0])
        except (ValueError, TypeError):
            continue  # header / 非資料列

        for month in range(1, 13):
            if month >= len(row):
                break
            cell = row[month]
            if cell is None or cell == "":
                continue
            try:
                price_idx = float(cell)
            except (ValueError, TypeError) as e:
                skipped += 1
                errors.append(f"row {row_no + 1} month {month}: {e}")
                continue

            existing = (
                db.query(PriceIndex)
                .filter(PriceIndex.year == year, PriceIndex.month == month)
                .first()
            )
            if existing is None:
                db.add(PriceIndex(year=year, month=month, price_idx=price_idx))
                inserted += 1
            else:
                existing.price_idx = price_idx
                updated += 1

    db.commit()
    return PriceIndexUploadResponse(
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        errors=errors,
    )


@app.post(
    "/api/pdf-analysis",
    response_model=PdfAnalysisResponse,
    summary="上傳 PDF 並以 OpenAI 解析為結構化資料",
    description=(
        "上傳一份土地登記謄本／權狀 PDF，後端送至 OpenAI 做檔案分析並回傳結構化結果。\n\n"
        "**Form 參數**：\n"
        "- `file` (UploadFile)：`.pdf` 檔\n\n"
        "**處理邏輯**：\n"
        "- 經 OpenAI Files API 上傳後，以 Responses API（structured output）抽取欄位\n"
        "- 會逐頁解析，一份 PDF 可能回傳多筆地號\n"
        "- 分析完即刪除 OpenAI 端的暫存檔\n\n"
        "**回應**：`{ items: [...] }`，每筆 `item` 欄位：\n"
        "- `district` (str)：行政區\n"
        "- `section` (str)：地段名稱\n"
        "- `land_no` (str)：地號\n"
        "- `owner` (str)：所有權人\n"
        "- `share` (float)：權利範圍（持分），小數表示\n"
        "- `prev_price` (float)：前次移轉現值\n\n"
        "**錯誤回應**：\n"
        "- `400`：副檔名非 `.pdf` 或檔案內容為空\n"
        "- `502`：OpenAI 服務呼叫或解析失敗"
    ),
)
async def analyze_pdf_endpoint(
    file: UploadFile = File(..., description="要分析的 PDF 檔"),
) -> PdfAnalysisResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="請上傳 .pdf 檔")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="檔案內容為空")

    # OpenAI SDK 為同步阻塞，丟到 threadpool 避免卡住事件迴圈
    try:
        result = await anyio.to_thread.run_sync(analyze_pdf, content, file.filename)
    except Exception as e:
        print("openai pdf analysis error: ", e)
        raise HTTPException(status_code=502, detail=f"PDF 分析失敗: {e}")

    return result


# 靜態網站掛在 "/"，要放在所有 API 路由之後
app.mount("/", StaticFiles(directory="static", html=True), name="static")
