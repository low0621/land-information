from typing import Any

from pydantic import BaseModel, Field


class ProjectResponse(BaseModel):
    pid: str
    user_id: str
    data: dict[str, Any]


class ProjectCreate(BaseModel):
    user_id: str = Field(..., description="使用者 id")
    data: dict[str, Any] = Field(..., description="專案資料（任意 JSON 物件）")


class ProjectUpdate(BaseModel):
    user_id: str = Field(..., description="使用者 id（驗證擁有者）")
    pid: str = Field(..., description="專案 id")
    data: dict[str, Any] = Field(..., description="更新後的專案資料（會整包覆蓋）")


class ProjectDelete(BaseModel):
    user_id: str = Field(..., description="使用者 id（驗證擁有者）")
    pid: str = Field(..., description="要刪除的專案 id")


class ParcelQuery(BaseModel):
    county: str = Field(..., description="縣市")
    district: str = Field(..., description="行政區")
    urban_plan: str = Field(..., description="都市計劃區")
    land_use_zone: str = Field(..., description="使用分區")
    section_no: str = Field(..., description="地段號")
    land_no: str = Field(..., description="地號")
    owner: str = Field(..., description="所有權人")
    share: float = Field(..., gt=0, le=1, description="持分（0 < share ≤ 1）")


class ParcelResponse(BaseModel):
    building_coverage: float
    floor_area_ratio: float
    area: float
    announced_value: float


class HousePriceQuery(BaseModel):
    county: str = Field(..., description="縣市")
    district: str = Field(..., description="行政區")
    section_no: str = Field(..., description="地段號")
    land_no: str = Field(..., description="地號")
    total_floors: int = Field(..., gt=0, description="樓層總高度（總樓層數）")


class HousePriceResponse(BaseModel):
    unit_price: float


class ZoningUploadResponse(BaseModel):
    inserted: int
    updated: int
    skipped: int
    errors: list[str]


class PriceIndexUploadResponse(BaseModel):
    inserted: int
    updated: int
    skipped: int
    errors: list[str]


class LandTaxQuery(BaseModel):
    year: int = Field(..., description="計算年度（民國／西元依 etax 規格）")
    month: int = Field(..., ge=1, le=12, description="計算月份")
    curr_val: str = Field(..., description="現值")
    orig_val: str = Field(..., description="原規定地價")
    area: str = Field(..., description="土地面積")
    land_type: str = Field(..., description="土地類別代碼")
    share: str = Field(..., description="持分（float 字串），例如 `0.5`、`0.25`")


class LandTaxResponse(BaseModel):
    param5: int
