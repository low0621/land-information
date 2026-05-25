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

ZONING_CSV_COLUMNS = [
    "county",
    "district",
    "urban_plan",
    "land_use_zone",
    "building_coverage",
    "floor_area_ratio",
]

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
        "- **path 參數**：`uuid` — 專案唯一識別碼\n"
        "- **成功回應**：`200`，回傳 `{ uuid, data }`，其中 `data` 為該專案的 JSON 內容\n"
        "- **錯誤回應**：`401` — 找不到對應 UUID 的專案（預留作為未來認證失敗用途）"
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
        "依縣市 / 行政區 / 都市計劃區 / 使用分區 / 地段號 / 地號 / 所有權人 / 持分查詢土地資訊。\n\n"
        "- **建蔽率、容積率**：由 `zoning` 資料表依前四個欄位完全比對撈出\n"
        "- **房價、面積、公告現值**：由地籍便民系統爬出來，並依 `share`（持分，0 < share ≤ 1）等比例縮放後回傳\n"
        "- **所有權人 `owner`**：目前僅收進 payload，預留未來權狀／歷史紀錄查詢使用\n\n"
        "**錯誤回應**：`404` — 找不到符合條件的使用分區資料"
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
        "**CSV 欄位（header 必填，順序不限）**：\n"
        "`county, district, urban_plan, land_use_zone, building_coverage, floor_area_ratio`\n\n"
        "**行為**：\n"
        "- 以 (county, district, urban_plan, land_use_zone) 為唯一鍵\n"
        "- 已存在則更新建蔽率／容積率，不存在則新增\n"
        "- 單行格式錯誤會被記錄到 `errors` 並跳過，不影響其他行\n\n"
        "**回應**：`{ inserted, updated, skipped, errors }`"
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
            county = (row["county"] or "").strip()
            district = (row["district"] or "").strip()
            urban_plan = (row["urban_plan"] or "").strip()
            land_use_zone = (row["land_use_zone"] or "").strip()
            if not all([county, district, urban_plan, land_use_zone]):
                raise ValueError("key 欄位不可為空")
            building_coverage = float(row["building_coverage"])
            floor_area_ratio = float(row["floor_area_ratio"])
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
