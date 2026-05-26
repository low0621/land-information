import csv
import io

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Project, Zoning
from app.schemas import ParcelQuery, ParcelResponse, ProjectResponse, ZoningUploadResponse
from app.seed import init_db

ZONING_CSV_HEADER_MAP = {
    "縣市": "county",
    "行政區": "district",
    "都市計畫地區": "urban_plan",
    "使用分區": "land_use_zone",
    "建蔽率": "building_coverage",
    "容積率": "floor_area_ratio",
}
ZONING_CSV_COLUMNS = list(ZONING_CSV_HEADER_MAP.keys())

app = FastAPI(title="Land Information Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get(
    "/api/projects/{uuid}",
    response_model=ProjectResponse,
    summary="取得專案資料",
    description=(
        "依 UUID 取得對應的專案資料。\n\n"
        "**Path 參數**：\n"
        "- `uuid` (str)：專案唯一識別碼，由前端產生或從先前建立流程取得\n\n"
        "**成功回應 `200`**：\n"
        "- `uuid` (str)：與 path 相同\n"
        "- `data` (object)：該專案內容，JSON 結構由建立專案時決定（未強制 schema）\n\n"
        "**錯誤回應**：\n"
        "- `401`：找不到對應 UUID 的專案（預留未來作為認證失敗用途）"
    ),
)
def get_project(uuid: str, db: Session = Depends(get_db)) -> ProjectResponse:
    project = db.query(Project).filter(Project.uuid == uuid).first()
    if project is None:
        raise HTTPException(status_code=401, detail="project not found")
    return ProjectResponse(uuid=project.uuid, data=project.data)


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
        "- `house_price`（房價）、`area`（面積）、`announced_value`（公告現值）：目前為假資料，回傳前會乘上 `share`\n\n"
        "**錯誤回應**：\n"
        "- `404`：找不到符合條件的使用分區資料\n"
        "- `422`：payload 欄位缺漏或型別錯誤（FastAPI 預設驗證）"
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

    # 房價 / 面積 / 公告現值 先用假資料，再依持分計算
    fake_parcel = {
        "house_price": 32_500_000.0,
        "area": 132.5,
        "announced_value": 18_700_000.0,
    }
    scaled = {k: v * payload.share for k, v in fake_parcel.items()}

    return ParcelResponse(
        building_coverage=zoning.building_coverage,
        floor_area_ratio=zoning.floor_area_ratio,
        **scaled,
    )


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
        raise HTTPException(status_code=400, detail="CSV 編碼需為 UTF-8")

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
            building_coverage = float((row["建蔽率"] or "").strip().rstrip("%")) * 0.01
            floor_area_ratio = float((row["容積率"] or "").strip().rstrip("%")) * 0.01
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


# 靜態網站掛在 "/"，要放在所有 API 路由之後
app.mount("/", StaticFiles(directory="static", html=True), name="static")
