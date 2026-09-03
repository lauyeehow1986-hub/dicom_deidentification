"""Encapsulated-PDF de-identification (rasterize + OCR-redact + flatten).

A DICOM-embedded PDF (EncapsulatedDocument, 0042,0011) can carry PHI in text and
in images. Rather than deleting it wholesale, we render each page to a raster
(pypdfium2), OCR-redact PHI regions with the shared scanner, and rebuild a
flattened image-only PDF. Flattening removes the text layer entirely, so any
hidden/selectable text PHI is gone by construction.

Licence-clean and air-gap friendly: pypdfium2 ships a self-contained wheel
(Apache-2.0 / BSD-3), no system binary. OCR still needs the bundled Tesseract;
when it is absent the caller falls back to removal.
"""
from __future__ import annotations

import io

import numpy as np

from . import pixels as _pixels

ENCAPSULATED_PDF_SOP = "1.2.840.10008.5.1.4.1.1.104.1"


def is_encapsulated_pdf(ds) -> bool:
    """True when ``ds`` carries an embedded PDF (by SOP Class or MIME type)."""
    if str(getattr(ds, "SOPClassUID", "")) == ENCAPSULATED_PDF_SOP:
        return "EncapsulatedDocument" in ds
    mime = str(getattr(ds, "MIMETypeOfEncapsulatedDocument", "")).lower()
    return mime == "application/pdf" and "EncapsulatedDocument" in ds


def render_pdf_pages(pdf_bytes: bytes, dpi: int = 150) -> list:
    """Render every page to an RGB uint8 ndarray at ``dpi``."""
    import pypdfium2 as pdfium

    scale = float(dpi) / 72.0
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=scale)
            pil = bitmap.to_pil().convert("RGB")
            pages.append(np.asarray(pil, dtype=np.uint8))
        return pages
    finally:
        pdf.close()


def flatten_to_pdf(pages: list) -> bytes:
    """Rebuild a flattened, image-only PDF (no text layer) from page rasters."""
    from PIL import Image

    imgs = [Image.fromarray(np.ascontiguousarray(p)).convert("RGB") for p in pages]
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def pdf_text(pdf_bytes: bytes) -> str:
    """Extract the PDF's text layer (empty for a flattened image PDF)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        parts = []
        for i in range(len(pdf)):
            tp = pdf[i].get_textpage()
            parts.append(tp.get_text_bounded())
        return "\n".join(parts)
    finally:
        pdf.close()


def redact_pdf_bytes(pdf_bytes: bytes, scanner, dpi: int = 150):
    """Render -> OCR-redact PHI boxes per page -> flatten. Returns (bytes, info).

    Caller must ensure OCR is available; this raises if it is not (no silent
    keep of an un-scanned PDF).
    """
    ok, note = _pixels._ocr_available()
    if not ok:
        raise RuntimeError(note)
    pages = render_pdf_pages(pdf_bytes, dpi=dpi)
    total_boxes = 0
    redacted = []
    for arr in pages:
        gray = _pixels._frame_to_uint8(arr)
        boxes = _pixels.image_phi_boxes(gray, scanner)
        total_boxes += len(boxes)
        # apply_boxes wants a frame axis (N,H,W[,C]); wrap this single page.
        stacked = arr[np.newaxis, ...]
        stacked = _pixels.apply_boxes(stacked, boxes, fill=0)
        redacted.append(stacked[0])
    return flatten_to_pdf(redacted), {"pages": len(pages), "boxes": total_boxes}


def redact_encapsulated_pdf(ds, scanner, dpi: int = 150) -> dict:
    """De-identify the embedded PDF in ``ds`` in place.

    When OCR is available: rasterize + redact + flatten, replacing
    EncapsulatedDocument with the flattened bytes. When OCR is absent: remove the
    document (never keep an un-scanned PDF). Never raises for the missing-binary
    case — that is the fallback, not an error.
    """
    ok, note = _pixels._ocr_available()
    if not ok:
        for kw in ("EncapsulatedDocument", "MIMETypeOfEncapsulatedDocument",
                   "EncapsulatedDocumentLength"):
            if kw in ds:
                del ds[kw]
        return {"mode": "removed_fallback", "note": note}
    src = bytes(ds.EncapsulatedDocument)
    flat, info = redact_pdf_bytes(src, scanner, dpi=dpi)
    if len(flat) % 2 == 1:
        flat += b"\x00"  # DICOM OB values are even-length
    ds.EncapsulatedDocument = flat
    if "EncapsulatedDocumentLength" in ds:
        ds.EncapsulatedDocumentLength = len(flat)
    return {"mode": "rasterize_redact", **info}
