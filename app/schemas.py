from typing import Any

from pydantic import BaseModel, Field


class ProjectResponse(BaseModel):
    uuid: str
    data: dict[str, Any]


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
    house_price: float
    area: float
    announced_value: float


class ZoningUploadResponse(BaseModel):
    inserted: int
    updated: int
    skipped: int
    errors: list[str]
