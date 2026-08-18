"""不經 LLM 的謄本解析：直接讀 PDF 文字層，再用規則比對欄位。

適用對象是地政事務所核發、帶文字層的電子謄本（第一／二類）。輸出與
`pdf_analysis.analyze_pdf()` 完全相同的 `PdfAnalysisResponse`，兩者可互換。

掃描影像式 PDF 沒有文字層，會擲出 `PdfTextLayerMissing`，呼叫端可據此退回 LLM 版本。

謄本大致長相（欄位以全形空白補齊，故解析前一律先把空白壓掉）：

    大安區延平段二小段 0591-0000地號
    ***  土地標示部  ***
    登記日期：民國065年05月28日           登記原因：地籍圖重測
    ***  土地所有權部  ***
    （0001）登記次序：0007
      所有權人：永豐商業銀行股份有限公司
    權利範圍：*********4分之1*********
    前次移轉現值或原規定地價：
    096年01月     **106,000.0元/平方公尺
"""

import io
import re
import unicodedata

from app.schemas import PdfAnalysisResponse, PdfAnalysisResult


class PdfTextLayerMissing(RuntimeError):
    """PDF 取不到有意義的文字層（多半是掃描影像檔）。"""


# --- 版面 / 章節 -------------------------------------------------------------

# 標題行：段小段 + 地號，且整行沒有欄位標籤（用來排除
# 「其他登記事項：重測前：太平段○小段○○地號」這種內文）
_LAND_HEADER_RE = re.compile(
    r"(?P<body>[一-鿿\w]*?段(?:[一-鿿\w]*?小段)?)"
    r"(?P<no>\d{1,5}(?:-\d{1,5})?)地號$"
)

_PART_OWNERSHIP = "ownership"
_PART_OTHER = "other"

# 「土地所有權部」「建物所有權部」；他項權利部裡的義務人不能當所有權人
_PART_MARKERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"他項權利部"), _PART_OTHER),
    (re.compile(r"所有權部"), _PART_OWNERSHIP),
    (re.compile(r"標示部"), _PART_OTHER),
]

# --- 欄位 --------------------------------------------------------------------

# 只認「所有權人」；祭祀公業等會另列「管理者」，那不是權利主體
_OWNER_RE = re.compile(r"所有權人[:：]")
_SHARE_RE = re.compile(r"權利範圍[:：]")
_PREV_RE = re.compile(r"前次移轉現值(?:或原規定地價)?[:：]")
_SEQ_RE = re.compile(r"登記次序[:：]")

# 同一行可能被空白分成兩欄（例：登記日期:… 登記原因:…），取值時切到下一個標籤前
_NEXT_LABEL_RE = re.compile(r"[一-鿿]{2,8}[:：]")

_ROC_YM_RE = re.compile(r"(\d{2,3})年(\d{1,2})月")
_MONEY_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*元")
_FRACTION_CN_RE = re.compile(r"(\d+)分之(\d+)")
_FRACTION_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def _compact(line: str) -> str:
    """NFKC 正規化（全形→半形）後，把所有空白與謄本補齊用的 `*` 去掉。

    謄本用空白把「住    址」這種標籤撐開、用 `*` 把數值補到固定寬度，
    壓掉之後同一份文件不同排版才能用同一組 regex 命中。
    """
    return re.sub(r"[\s*]+", "", unicodedata.normalize("NFKC", line))


def _value_after(line: str, label: re.Pattern[str]) -> str | None:
    """取出 `label` 標籤後面的值；若同行還有下一個標籤就切掉。"""
    m = label.search(line)
    if m is None:
        return None
    rest = line[m.end() :]
    cut = _NEXT_LABEL_RE.search(rest)
    if cut is not None:
        rest = rest[: cut.start()]
    return rest.strip()


def _split_district_section(body: str) -> tuple[str, str]:
    """把「臺北市大安區延平段二小段」拆成 ("大安區", "延平段二小段")。

    取最後一個行政區尾字（區/鄉/鎮/市），再往前回推到縣市邊界，
    才不會把「臺北市」的市或整串前綴誤當成行政區。
    """
    head = body.split("段", 1)[0]
    tail_idx = max((i for i, ch in enumerate(head) if ch in "區鄉鎮市"), default=-1)
    if tail_idx < 0:
        return "", body
    end = tail_idx + 1
    start = 0
    for j in range(end - 2, -1, -1):
        if head[j] in "縣市":
            start = j + 1
            break
    return head[start:end], body[end:]


def _parse_share(raw: str) -> float | None:
    """權利範圍轉小數。支援「全部」「4分之1」「1/4」與純小數。"""
    s = re.sub(r"[\s*]", "", raw)
    if not s:
        return None
    if "全部" in s:
        return 1.0
    m = _FRACTION_CN_RE.search(s)
    if m:  # 中文是「分母分之分子」
        den, num = int(m.group(1)), int(m.group(2))
        return num / den if den else None
    m = _FRACTION_RE.search(s)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        return num / den if den else None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def _parse_money(text: str) -> float | None:
    m = _MONEY_RE.search(text)
    if m is None:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


# --- 文字層擷取 ---------------------------------------------------------------


def extract_pages(content: bytes) -> list[str]:
    """回傳每頁的原始文字。呼叫端若要 debug 可直接用。"""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    return [(page.extract_text() or "") for page in reader.pages]


# --- 主解析 ------------------------------------------------------------------


class _Pending:
    """一筆正在累積中的所有權紀錄。"""

    __slots__ = (
        "district",
        "section",
        "land_no",
        "owner",
        "share",
        "prev_price",
        "acquire_year_roc",
        "acquire_month",
    )

    def __init__(self, header: tuple[str, str, str], owner: str) -> None:
        self.district, self.section, self.land_no = header
        self.owner = owner
        self.share: float | None = None
        self.prev_price: float | None = None
        self.acquire_year_roc = 0
        self.acquire_month = 0

    def to_result(self) -> PdfAnalysisResult:
        return PdfAnalysisResult(
            district=self.district,
            section=self.section,
            land_no=self.land_no,
            owner=self.owner,
            # 找不到的欄位補 0，與 LLM 版本的行為一致
            share=self.share if self.share is not None else 0.0,
            prev_price=self.prev_price if self.prev_price is not None else 0.0,
            acquire_year_roc=self.acquire_year_roc,
            acquire_month=self.acquire_month,
        )


def parse_pages(pages: list[str]) -> PdfAnalysisResponse:
    """把已擷取的頁文字解析成結構化結果。"""
    lines = [_compact(ln) for page in pages for ln in page.splitlines()]
    lines = [ln for ln in lines if ln]

    items: list[PdfAnalysisResult] = []
    header: tuple[str, str, str] | None = None
    part: str | None = None
    pending: _Pending | None = None

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            items.append(pending.to_result())
            pending = None

    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1

        # 1) 地號標題行（每頁頁首都會重印一次，同一地號不算換筆）
        if ":" not in line:
            m = _LAND_HEADER_RE.search(line)
            if m is not None:
                district, section = _split_district_section(m.group("body"))
                new_header = (district, section, m.group("no"))
                if new_header != header:
                    flush()
                    header = new_header
                continue

        # 2) 章節切換：只有「所有權部」裡的人才是所有權人
        matched_part = next((k for p, k in _PART_MARKERS if p.search(line)), None)
        if matched_part is not None:
            flush()
            part = matched_part
            continue

        if part != _PART_OWNERSHIP or header is None:
            continue

        # 3) 新的登記次序 = 換一筆
        if _SEQ_RE.search(line):
            flush()

        # 4) 所有權人
        if _OWNER_RE.search(line):
            owner = _value_after(line, _OWNER_RE) or ""
            if owner and owner not in ("(空白)", "空白"):
                flush()
                pending = _Pending(header, owner)
            continue

        if pending is None:
            continue

        # 5) 權利範圍（「歷次取得權利範圍」是另一個欄位，不能覆寫）
        if "歷次取得權利範圍" not in line and _SHARE_RE.search(line):
            if pending.share is None:
                share = _parse_share(_value_after(line, _SHARE_RE) or "")
                if share is not None:
                    pending.share = share
            continue

        # 6) 前次移轉現值：值可能在標籤同行，也可能落在下一行
        #    （下一行長相為「096年01月 **106,000.0元/平方公尺」，沒有標籤）
        if _PREV_RE.search(line):
            if pending.prev_price is None:
                buf = _value_after(line, _PREV_RE) or ""
                j = i
                while not _MONEY_RE.search(buf) and j < len(lines) and j - i < 3:
                    nxt = lines[j]
                    if ":" in nxt or any(p.search(nxt) for p, _ in _PART_MARKERS):
                        break
                    buf += nxt
                    j += 1
                price = _parse_money(buf)
                if price is not None:
                    pending.prev_price = price
                    # 取得年月＝與前次移轉現值同一列的民國年月。buf 只涵蓋這個欄位的
                    # 範圍，所以不會誤抓到登記日期／當期申報地價那幾行的年月。
                    ym = _ROC_YM_RE.search(buf)
                    if ym is not None:
                        year, month = int(ym.group(1)), int(ym.group(2))
                        if year > 0 and 1 <= month <= 12:
                            pending.acquire_year_roc = year
                            pending.acquire_month = month
            continue

    flush()
    return PdfAnalysisResponse(items=items)


def analyze_pdf_rule_based(content: bytes, filename: str) -> PdfAnalysisResponse:
    """讀 PDF 文字層並以規則抽出欄位；簽章與 `analyze_pdf()` 相同。

    同步阻塞（pypdf 為 CPU-bound），請在 threadpool 中呼叫。
    """
    pages = extract_pages(content)
    if sum(len(_compact(p)) for p in pages) < 50:
        raise PdfTextLayerMissing(
            f"{filename} 取不到文字層（可能為掃描影像檔），請改用 LLM 解析"
        )
    return parse_pages(pages)
