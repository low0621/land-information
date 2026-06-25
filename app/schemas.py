from typing import Any

from pydantic import BaseModel, Field


class ProjectResponse(BaseModel):
    pid: str
    user_id: str
    data: dict[str, Any]


class ProjectCreate(BaseModel):
    user_id: str = Field(..., description="使用者 id")
    # data / data_enc 擇一提供：明文 JSON，或 AES-GCM 加密後的 base64 字串
    data: dict[str, Any] | None = Field(
        None, description="專案資料（任意 JSON 物件）；與 data_enc 擇一")
    data_enc: str | None = Field(
        None, description="AES-GCM 加密後的專案資料 base64；與 data 擇一，後端解密還原")


class ProjectUpdate(BaseModel):
    user_id: str = Field(..., description="使用者 id（驗證擁有者）")
    pid: str = Field(..., description="專案 id")
    # data / data_enc 擇一提供：明文 JSON，或 AES-GCM 加密後的 base64 字串
    data: dict[str, Any] | None = Field(
        None, description="更新後的專案資料（會整包覆蓋）；與 data_enc 擇一")
    data_enc: str | None = Field(
        None, description="AES-GCM 加密後的專案資料 base64；與 data 擇一，後端解密還原")


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


class PdfAnalysisResult(BaseModel):
    """土地登記謄本 / 權狀 PDF 中的單一筆地號資料。

    欄位 description 會一併送進 OpenAI，請維持精確以利抽取。
    """

    district: str = Field(..., description="行政區（例如「信義區」）")
    section: str = Field(..., description="地段名稱（例如「信義段一小段」之類的段／小段名稱，非代碼）")
    land_no: str = Field(..., description="地號")
    owner: str = Field(..., description="所有權人姓名")
    share: float = Field(
        ...,
        description="權利範圍（持分），以小數表示；謄本若為分數如 1/4 請換算為 0.25，完全持有為 1",
    )
    prev_price: float = Field(..., description="前次移轉現值（新臺幣元，純數字）")


class PdfAnalysisResponse(BaseModel):
    """PDF 解析結果；一份 PDF 可能含多筆地號，逐筆放入 items。

    同時作為 OpenAI structured output 的 root schema 與 API response model。
    （structured output 的 root 必須是 object，故以 items 包一層 list）
    """

    items: list[PdfAnalysisResult] = Field(
        ..., description="文件中所有筆地號資料，逐頁檢視不要遺漏"
    )
