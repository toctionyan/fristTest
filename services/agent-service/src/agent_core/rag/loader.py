from pathlib import Path


def load_text_from_file(path: str | Path) -> str:
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix in {".txt", ".md", ".markdown", ".csv", ".json"}:
        return p.read_text(encoding="utf-8", errors="ignore")

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(p))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            raise RuntimeError(f"PDF 解析失败: {e}")

    if suffix == ".docx":
        try:
            from docx import Document
            doc = Document(str(p))
            return "\n".join(paragraph.text for paragraph in doc.paragraphs)
        except Exception as e:
            raise RuntimeError(f"DOCX 解析失败: {e}")

    return p.read_text(encoding="utf-8", errors="ignore")
