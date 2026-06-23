"""App package 初始化。

在任何子模組讀取環境變數之前先載入本地 .env（若存在）。
正式環境（Azure）沒有 .env，load_dotenv 會直接 no-op，env 由 App Settings 提供。
"""

from dotenv import load_dotenv

load_dotenv()
