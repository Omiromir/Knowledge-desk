"""Run Cyrillic OCR on rendered PDF pages in the local process."""

import logging
from contextlib import closing, contextmanager
from functools import lru_cache
from threading import Lock
from time import monotonic

from .summarizer import SummaryError

logger = logging.getLogger(__name__)
_ocr_lock = Lock()


@lru_cache(maxsize=1)
def engine():
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

    return RapidOCR(params={
        "Rec.lang_type": LangRec.CYRILLIC,
        "Rec.ocr_version": OCRVersion.PPOCRV5,
        "Rec.model_type": ModelType.MOBILE,
        "EngineConfig.onnxruntime.intra_op_num_threads": 2,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    })


@contextmanager
def open_scan(pdf: bytes):
    # PDFium calls and the OCR engine share one slot, including between PDF pages.
    if not _ocr_lock.acquire(blocking=False):
        raise SummaryError("upload_ocr_busy", "Wait for the current scan to finish.")
    try:
        from pypdfium2 import PdfDocument

        with closing(PdfDocument(pdf)) as document:
            deadline = monotonic() + 120

            def recognize(page_index: int) -> str:
                if monotonic() >= deadline:
                    raise SummaryError("upload_ocr_timeout", "Split the scan into smaller files.")
                with closing(document[page_index]) as page:
                    scale = min(3.5, 2600 / max(page.get_size()))
                    with closing(page.render(scale=scale, grayscale=True)) as bitmap:
                        with bitmap.to_pil() as image:
                            try:
                                result = engine()(image)
                            except Exception as exc:
                                logger.warning("Local OCR failed: %s", type(exc).__name__)
                                raise SummaryError(
                                    "upload_ocr_unavailable", "Could not run the local OCR model.",
                                ) from None
                if monotonic() >= deadline:
                    raise SummaryError("upload_ocr_timeout", "Split the scan into smaller files.")
                return "\n".join(result.txts or ())

            yield recognize
    finally:
        _ocr_lock.release()
