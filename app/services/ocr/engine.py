from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from app.core.config import settings
from app.core.exceptions import OCREngineError


@dataclass(frozen=True)
class OCRLine:
    """Satu baris teks hasil OCR beserta keyakinannya."""

    text: str
    confidence: float


class OCREngine(Protocol):
    """Kontrak engine OCR.

    Sengaja dibuat Protocol supaya parser & service bisa diuji tanpa
    memuat PaddleOCR, dan supaya engine bisa diganti tanpa mengubah service.
    """

    def read_image(self, image: Any) -> list[OCRLine]: ...


class PaddleOCREngine:
    """Wrapper PaddleOCR.

    Model dimuat sekali saja (lazy, thread-safe). Memuat model butuh beberapa
    detik dan ratusan MB memori — melakukannya per-request akan sangat lambat.
    """

    _lock = threading.Lock()

    def __init__(self, lang: str | None = None, enable_mkldnn: bool | None = None) -> None:
        self.lang = lang or settings.ocr_lang
        self.enable_mkldnn = (
            settings.ocr_enable_mkldnn if enable_mkldnn is None else enable_mkldnn
        )
        self._ocr: Any = None

    def _get_ocr(self) -> Any:
        if self._ocr is None:
            with self._lock:
                if self._ocr is None:
                    try:
                        from paddleocr import PaddleOCR
                    except ImportError as exc:  # pragma: no cover
                        raise OCREngineError(
                            "PaddleOCR belum terpasang. Jalankan: "
                            "pip install paddlepaddle paddleocr"
                        ) from exc
                    self._ocr = PaddleOCR(
                        lang=self.lang,
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=True,
                        # oneDNN dimatikan secara default: pada paddlepaddle 3.3
                        # + CPU tertentu ia melempar
                        # "ConvertPirAttribute2RuntimeAttribute not support"
                        # dan OCR gagal total. Nyalakan lewat OCR_ENABLE_MKLDNN
                        # bila mesin Anda memang mendukungnya (lebih cepat).
                        enable_mkldnn=self.enable_mkldnn,
                    )
        return self._ocr

    def warmup(self) -> None:
        """Paksa model dimuat sekarang (dipakai saat startup aplikasi).

        Tanpa ini, biaya load model (~beberapa detik, ratusan MB) dibayar oleh
        request OCR pertama — membuatnya terasa 'menggantung'. Memanggil ini di
        startup memindahkan biaya itu ke waktu boot, sekali saja.
        """
        self._get_ocr()

    def read_image(self, image: Any) -> list[OCRLine]:
        """Baca gambar dari MEMORI (array BGR OpenCV).

        File kini tersimpan di MinIO, bukan di disk, jadi engine tidak lagi
        menerima path — gambar sudah berupa bytes yang di-decode di service.
        """
        ocr = self._get_ocr()
        try:
            raw = ocr.predict(input=image)
        except Exception as exc:  # noqa: BLE001 — paddle melempar exception generik
            raise OCREngineError(f"PaddleOCR gagal membaca gambar: {exc}") from exc

        return self._to_lines(raw)

    @staticmethod
    def _to_lines(raw: Any) -> list[OCRLine]:
        """Normalkan output PaddleOCR menjadi daftar OCRLine.

        Bentuk output berbeda antar versi PaddleOCR, jadi keduanya ditangani:
        - v3: [{'rec_texts': [...], 'rec_scores': [...]}, ...]
        - v2: [[[box, (text, score)], ...]]
        """
        lines: list[OCRLine] = []
        if not raw:
            return lines

        for page in raw:
            # PaddleOCR v3 (OCRResult berperilaku seperti dict)
            if hasattr(page, "get") or isinstance(page, dict):
                texts = page.get("rec_texts") or []
                scores = page.get("rec_scores") or []
                for i, text in enumerate(texts):
                    score = float(scores[i]) if i < len(scores) else 0.0
                    if text and text.strip():
                        lines.append(OCRLine(text=text.strip(), confidence=score))
                continue

            # PaddleOCR v2
            if isinstance(page, list):
                for item in page:
                    try:
                        text, score = item[1]
                    except (IndexError, TypeError, ValueError):
                        continue
                    if text and str(text).strip():
                        lines.append(
                            OCRLine(text=str(text).strip(), confidence=float(score))
                        )

        return lines


@lru_cache(maxsize=1)
def get_shared_engine() -> PaddleOCREngine:
    """Engine OCR tunggal untuk seluruh proses.

    PENTING: model PaddleOCR (det + rec + cls, ratusan MB) HARUS dimuat sekali
    lalu dipakai ulang. Sebelumnya OCRService dibuat baru tiap request sehingga
    engine — dan modelnya — ikut dibuat ulang; itu yang membuat setiap OCR makan
    waktu ~1 menit (didominasi loading model, bukan inference). Dengan engine
    bersama ini, request pertama membayar biaya load, sisanya tinggal inference.
    """
    return PaddleOCREngine()
