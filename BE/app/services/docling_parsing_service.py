"""
Docling Parsing Service — document → clean Markdown.

Replaces dumb text extraction (pypdf/python-docx) with Docling's layout-aware
parsing (PDF/DOCX/PPTX/images, tables, OCR). Runs locally (MIT, no API).

Design notes for production:
  * The Docling DocumentConverter is heavy (torch + model weights) and slow to
    construct, so we build ONE lazily and reuse it (module-level singleton).
  * Docling is synchronous & CPU-bound; callers should use `parse_bytes` which
    offloads to a thread so the FastAPI event loop is never blocked.
  * Plain-text/markdown inputs skip Docling entirely (just decode).
  * The import is lazy so the rest of the app still boots if docling isn't
    installed yet — only actual parse calls require it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

# Extensions Docling should handle. Everything else we treat as decodable text.
_DOC_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".png", ".jpg", ".jpeg", ".tiff"}
_TEXT_EXTS = {".txt", ".md", ".markdown", ".text"}

_converter = None  # lazily-built Docling DocumentConverter singleton


def _get_converter():
    """Build (once) and return the Docling DocumentConverter.

    OCR is DISABLED on purpose: CVs/JDs are digital text PDFs whose text layer
    Docling reads directly. Running OCR is slower and, on some machines, the OCR
    backend errors ("Unsupported configuration: torch.PP-OCRv*"). Table structure
    stays on so skills/experience tables are still parsed.
    """
    global _converter
    if _converter is None:
        try:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
        except ImportError as e:  # pragma: no cover - depends on install
            raise RuntimeError(
                "docling is not installed. Run `pip install docling` "
                "(it pulls torch + model weights on first import)."
            ) from e
        logger.info("[Docling] Building DocumentConverter (OCR off; first call loads models)…")
        opts = PdfPipelineOptions()
        opts.do_ocr = False
        try:
            opts.do_table_structure = True
        except Exception:
            pass
        _converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
    return _converter


def _ext(filename: str | None) -> str:
    return os.path.splitext(filename or "")[1].lower()


def _parse_sync(data: bytes, filename: str | None) -> str:
    """Blocking parse. Decode text files directly; Docling for documents."""
    ext = _ext(filename)

    if ext in _TEXT_EXTS:
        return data.decode("utf-8", errors="replace").strip()

    # Unknown/empty extension but small & decodable → treat as text.
    if ext not in _DOC_EXTS:
        try:
            text = data.decode("utf-8")
            if text.strip():
                return text.strip()
        except UnicodeDecodeError:
            pass  # fall through to Docling

    converter = _get_converter()
    # Docling reads from a path; write to a temp file with the right suffix.
    suffix = ext if ext else ".pdf"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        result = converter.convert(tmp_path)
        return result.document.export_to_markdown().strip()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                logger.warning("[Docling] could not remove temp file %s", tmp_path)


async def parse_bytes(data: bytes, filename: str | None = None) -> str:
    """Async entry point — offloads the blocking parse to a worker thread.

    Returns clean Markdown. Raises on failure (caller decides how to record it).
    """
    if not data:
        raise ValueError("empty document")
    return await asyncio.to_thread(_parse_sync, data, filename)


def warm_up() -> None:
    """Optional: pre-build the converter (e.g. on startup / Cloud Run warm)."""
    try:
        _get_converter()
    except Exception as e:  # never crash startup over warm-up
        logger.warning("[Docling] warm-up skipped: %s", e)
