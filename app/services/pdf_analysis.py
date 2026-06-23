import os

from openai import OpenAI

from app.schemas import PdfAnalysisResponse

# 支援 structured output 的模型；可用環境變數覆寫
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4")

ANALYSIS_PROMPT = (
    "這是一份台灣的土地登記謄本／權狀 PDF，可能包含多筆地號。"
    "請逐頁檢視整份文件，將每一筆地號都抽出成一筆資料放入 items，不要遺漏。"
    "每筆需抽取：行政區、地段名稱、地號、所有權人、權利範圍（持分）、前次移轉現值。"
    "地段名稱請抓段／小段的名稱（例如「信義段一小段」），不是代碼；"
    "權利範圍若為分數（例如 1/4）請換算為小數（0.25）；"
    "前次移轉現值請去除逗號與單位，只保留純數字。"
    "若某欄位在文件中找不到，字串欄位回傳空字串、數值欄位回傳 0。"
)

# 模組層級單例，避免每次請求都重建連線池
_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        # OpenAI() 會自動讀取環境變數 OPENAI_API_KEY
        _client = OpenAI()
    return _client


def analyze_pdf(content: bytes, filename: str) -> PdfAnalysisResponse:
    """將 PDF 內容送到 OpenAI 解析，回傳結構化結果。

    流程：Files API 上傳 → Responses API 以 input_file 帶入並要求 structured
    output → 解析完刪除暫存檔。此函式為同步阻塞，請在 threadpool 中呼叫。
    """
    client = _get_client()

    uploaded = client.files.create(
        file=(filename, content, "application/pdf"),
        purpose="user_data",
    )
    try:
        response = client.responses.parse(
            model=OPENAI_MODEL,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_file", "file_id": uploaded.id},
                        {"type": "input_text", "text": ANALYSIS_PROMPT},
                    ],
                }
            ],
            text_format=PdfAnalysisResponse,
        )
    finally:
        # 暫存檔分析完即清掉，避免在 OpenAI 端累積
        try:
            client.files.delete(uploaded.id)
        except Exception as e:
            print("openai file cleanup failed: ", e)

    result = response.output_parsed
    if result is None:
        raise RuntimeError("OpenAI 未回傳可解析的結構化結果")
    return result
