# vision.py — Licence plate recognition via OpenCV + Tesseract OCR
# Camera source: set _CAMERA_SOURCE = 0 for USB webcam, or a URL for IP Webcam.

import logging
import re
import time
from typing import Optional

import cv2
import numpy as np
import pytesseract

logger = logging.getLogger(__name__)

# Change to e.g. "http://192.168.1.42:8080/video" for an IP Webcam stream
_CAMERA_SOURCE = "http://100.64.23.17:8080/video"

# Tesseract confidence scores are 0–100 (integers)
_OCR_CONFIDENCE_THRESHOLD = 30

# Indian plate format: MH12AB1234
_PLATE_RE = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{1,4}$")

# PSM 8 = single word; whitelist keeps only plate-legal characters
_TESS_CONFIG = (
    "--psm 8 --oem 3 "
    "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", "", text).upper()


def capture_frame() -> Optional[np.ndarray]:
    cap = cv2.VideoCapture(_CAMERA_SOURCE)
    try:
        if not cap.isOpened():
            logger.error("Cannot open camera: %s", _CAMERA_SOURCE)
            return None
        time.sleep(0.5)  # let auto-exposure settle
        ret, frame = cap.read()
        if not ret or frame is None:
            logger.error("cap.read() returned no frame")
            return None
        return frame
    finally:
        cap.release()


def preprocess_frame(frame: np.ndarray) -> np.ndarray:
    gray    = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.bilateralFilter(gray, d=11, sigmaColor=17, sigmaSpace=17)
    return cv2.adaptiveThreshold(
        blurred, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
        blockSize=11, C=2,
    )


def extract_plate(frame: np.ndarray) -> Optional[str]:
    try:
        processed = preprocess_frame(frame)
        data = pytesseract.image_to_data(
            processed,
            config=_TESS_CONFIG,
            output_type=pytesseract.Output.DICT,
        )
    except Exception as exc:
        logger.error("Tesseract error: %s", exc)
        return None

    confident = []
    for text, conf in zip(data["text"], data["conf"]):
        try:
            conf_val = int(conf)
        except (ValueError, TypeError):
            logger.debug("Skipping token %r: non-integer confidence %r", text, conf)
            continue
        if conf_val >= _OCR_CONFIDENCE_THRESHOLD:
            normalised = _normalise(text)
            if normalised:
                confident.append((normalised, conf_val))

    if not confident:
        return None

    # Prefer tokens matching the Indian plate pattern
    plate_matches = [(t, c) for t, c in confident if _PLATE_RE.match(t)]
    if plate_matches:
        best, _ = max(plate_matches, key=lambda x: x[1])
        logger.info("Plate: %s", best)
        return best

    # Fall back to the highest-confidence token
    best, _ = max(confident, key=lambda x: x[1])
    if best:
        logger.info("Best OCR token: %s", best)
        return best

    return None


def scan_plate() -> Optional[str]:
    frame = capture_frame()
    if frame is None:
        return None
    return extract_plate(frame)
