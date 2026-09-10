"""Text recognition over a binarized reward-name crop.

Tesseract sits behind a tiny protocol so an alternative engine (RapidOCR,
PaddleOCR) can be dropped in if accuracy on Warframe's stylised font stalls.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

# Item names are letters, spaces and the occasional ampersand or hyphen.
WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz &-'"

#: Tesseract reads small text badly; scale every crop up to this cap height.
TARGET_TEXT_HEIGHT = 44
BORDER = 12


class Reader(Protocol):
    def read(self, image: np.ndarray) -> OcrResult: ...


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float


def preprocess(image: np.ndarray) -> np.ndarray:
    """Upscale a binary crop and pad it, which is what Tesseract wants."""
    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    height = image.shape[0]
    if height > 0 and height < TARGET_TEXT_HEIGHT:
        factor = TARGET_TEXT_HEIGHT / height
        image = cv2.resize(
            image,
            (max(1, int(image.shape[1] * factor)), TARGET_TEXT_HEIGHT),
            interpolation=cv2.INTER_CUBIC,
        )
        # Cubic interpolation reintroduces grey; snap back to bi-level.
        _, image = cv2.threshold(image, 127, 255, cv2.THRESH_BINARY)
    return cv2.copyMakeBorder(
        image, BORDER, BORDER, BORDER, BORDER, cv2.BORDER_CONSTANT, value=255
    )


class TesseractReader:
    """Reads a single line of text using Tesseract in single-line mode."""

    def __init__(self, *, language: str = "eng", psm: int = 7) -> None:
        self.language = language
        self.psm = psm

    @staticmethod
    def available() -> bool:
        return shutil.which("tesseract") is not None

    @property
    def config(self) -> str:
        return f"--psm {self.psm} -c tessedit_char_whitelist=\"{WHITELIST}\""

    def read(self, image: np.ndarray) -> OcrResult:
        import pytesseract

        prepared = preprocess(image)
        data = pytesseract.image_to_data(
            prepared,
            lang=self.language,
            config=self.config,
            output_type=pytesseract.Output.DICT,
        )
        words: list[str] = []
        confidences: list[float] = []
        for word, confidence in zip(data.get("text", []), data.get("conf", []), strict=False):
            word = word.strip()
            if not word:
                continue
            words.append(word)
            try:
                value = float(confidence)
            except (TypeError, ValueError):
                continue
            if value >= 0:
                confidences.append(value)
        return OcrResult(
            text=" ".join(words),
            confidence=float(np.mean(confidences)) / 100.0 if confidences else 0.0,
        )


def default_reader() -> Reader:
    return TesseractReader()
