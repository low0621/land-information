# 台灣建設開發智腦 (Land Information Backend)

FastAPI + SQLite 後端 + 純 HTML/React (CDN) 前端，包含土地分區、公告現值、土增稅、房價估值、損益／敏感度試算等功能。

## 環境需求

- Python ≥ 3.13
- [uv](https://docs.astral.sh/uv/) (建議) 或 pip

## 啟動方式

### 使用 uv (推薦)

```bash
# 安裝相依套件 (第一次或更新 pyproject.toml 後)
uv sync

# 啟動服務
uv run uvicorn app.main:app --host 0.0.0.0 --port 8003 --reload
```

### 使用 pip / venv

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8003 --reload
```

啟動成功後：

- API: <http://localhost:8003>
- 前端介面: <http://localhost:8003/> (自動載入 `static/index.html`)
- OpenAPI Swagger 文件: <http://localhost:8003/docs>

服務以 `uvicorn` 起來，`reload=True` 已開啟，修改 `app/` 內的 Python 檔會自動重啟；修改 `static/index.html` 直接重新整理瀏覽器即可。

## 專案結構

```
.
├── main.py              # uvicorn 啟動點 (port 8003)
├── app/
│   ├── main.py          # FastAPI app + 路由
│   ├── database.py      # SQLAlchemy session
│   ├── models.py        # ORM models
│   ├── schemas.py       # Pydantic schemas
│   ├── seed.py          # 啟動時建表
│   └── services/        # easymap / etax / mortgage 外部服務
├── static/
│   └── index.html       # 前端單頁應用 (React via CDN + Tailwind)
├── land_info.db         # SQLite 資料庫
├── pyproject.toml
└── uv.lock
```

## 系統資料初始化

進到前端介面後，點 header 右上角 ⚙️ 「系統資料維護」上傳：

1. **分區基準 CSV**：縣市 / 行政區 / 都市計畫地區 / 使用分區 / 建蔽率 / 容積率
2. **物價指數 xls**：土地增值稅試算前置資料

兩者上傳一次即可，所有專案共用。
