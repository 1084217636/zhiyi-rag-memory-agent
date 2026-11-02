"""文档解析：把各类文件的二进制内容提取为纯文本。

支持 PDF / Word(docx) / Markdown / 纯文本 / HTML / CSV / Excel(xlsx)。
"""
import io
import csv

import chardet

from app.core.exceptions import BizError

SUPPORTED_EXTS = {".pdf", ".docx", ".md", ".markdown", ".txt", ".html", ".htm", ".csv", ".xlsx"}
MAX_SPREADSHEET_ROWS = 10_000
MAX_SPREADSHEET_CELLS = 100_000


def _decode_text(content: bytes) -> str:
    detected = chardet.detect(content)
    encoding = detected.get("encoding") or "utf-8"
    try:
        return content.decode(encoding, errors="ignore")
    except (LookupError, UnicodeDecodeError):
        return content.decode("utf-8", errors="ignore")


def decode_text(content: bytes) -> str:
    """对外暴露的文本解码：保留原始文本内容（不做 markdown→纯文本转换），用于预览。"""
    return _decode_text(content)


def _parse_pdf(content: bytes) -> str:
    import fitz  # pymupdf

    text_parts: list[str] = []
    with fitz.open(stream=content, filetype="pdf") as doc:
        for number, page in enumerate(doc, 1):
            text_parts.append(f"\f第 {number} 页\n{page.get_text()}")
    return "\n".join(text_parts)


def _parse_docx(content: bytes) -> str:
    from docx import Document as DocxDocument

    doc = DocxDocument(io.BytesIO(content))
    parts: list[str] = []
    for item in doc.iter_inner_content():
        if hasattr(item, "rows"):
            for row in item.rows:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
        elif item.text.strip():
            prefix = "# " if item.style and item.style.name.startswith("Heading") else ""
            parts.append(prefix + item.text.strip())
    return "\n".join(parts)


def _parse_markdown(content: bytes) -> str:
    # Markdown is useful as retrieval text: headings and table row boundaries
    # are more valuable than stripping syntax into one long paragraph.
    return _decode_text(content)


def _html_to_text(soup) -> str:
    from bs4 import NavigableString

    for tag in soup(["script", "style"]):
        tag.decompose()
    for row in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        row.replace_with(NavigableString("\n" + " | ".join(cells) + "\n"))
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        heading.replace_with(NavigableString("\n# " + heading.get_text(" ", strip=True) + "\n"))
    for tag in soup.find_all(["p", "div", "li", "br"]):
        tag.insert_after(NavigableString("\n"))
    return "\n".join(line.strip() for line in soup.get_text().splitlines() if line.strip())


def _parse_html(content: bytes) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_decode_text(content), "html.parser")
    return _html_to_text(soup)


def _table_rows(rows, sheet_name: str) -> str:
    out: list[str] = []
    headers: list[str] = []
    cells_seen = 0
    for row_number, values in enumerate(rows, 1):
        if row_number > MAX_SPREADSHEET_ROWS:
            raise BizError("表格行数超过解析上限", code=3007)
        cells_seen += len(values)
        if cells_seen > MAX_SPREADSHEET_CELLS:
            raise BizError("表格单元格数超过解析上限", code=3007)
        cleaned = [str(value).strip().replace("\n", " ") if value is not None else "" for value in values]
        if row_number == 1:
            headers = [value or f"列{index}" for index, value in enumerate(cleaned, 1)]
            continue
        fields = [f"{headers[index] if index < len(headers) else f'列{index + 1}'}: {value}"
                  for index, value in enumerate(cleaned) if value]
        if fields:
            out.append(f"工作表 {sheet_name} 第 {row_number} 行 | " + " | ".join(fields))
    return "\n".join(out)


def _parse_csv(content: bytes) -> str:
    rows = csv.reader(io.StringIO(_decode_text(content)))
    return _table_rows(rows, "CSV")


def _parse_xlsx(content: bytes) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        return "\n".join(_table_rows(sheet.values, sheet.title) for sheet in workbook.worksheets)
    finally:
        workbook.close()


def parse_document(file_ext: str, content: bytes) -> str:
    """按扩展名解析文件二进制，返回纯文本。"""
    ext = file_ext.lower()
    if not ext.startswith("."):
        ext = f".{ext}"
    if ext == ".pdf":
        return _parse_pdf(content)
    if ext == ".docx":
        return _parse_docx(content)
    if ext in (".md", ".markdown"):
        return _parse_markdown(content)
    if ext in (".html", ".htm"):
        return _parse_html(content)
    if ext == ".txt":
        return _decode_text(content)
    if ext == ".csv":
        return _parse_csv(content)
    if ext == ".xlsx":
        return _parse_xlsx(content)
    raise BizError(f"不支持的文件类型: {ext}", code=3001)
