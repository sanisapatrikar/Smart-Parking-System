"""
vision.py — Licence plate recognition for the Smart Parking System.

Public API
----------
scan_plate() -> Optional[str]
    Capture a frame from the Raspberry Pi CSI Camera Module, pre-process it
    with OpenCV, and extract a licence plate string using EasyOCR.
    Returns the plate string on success, or None if recognition fails.
    There is no QR-code fallback; callers must handle a None return value.

Pipeline
--------
1. capture_frame()     — Picamera2 CSI capture → BGR numpy array
2. preprocess_frame()  — BGR → grayscale → bilateral filter → adaptive threshold
3. extract_plate()     — EasyOCR readtext → confidence filter → regex preference
"""

import logging
import re
import time
from typing import Optional

import cv2
import easyocr
import numpy as np
from picamera2 import Picamera2

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Camera capture resolution
_CAPTURE_WIDTH = 1280
_CAPTURE_HEIGHT = 720

# EasyOCR: discard detections whose confidence is below this threshold
_OCR_CONFIDENCE_THRESHOLD = 0.3

# Regex for an Indian vehicle registration plate.
# Matches formats such as: MH12AB1234  KA05XY9988  DL1CAB1234  TN09AX0001
_PLATE_RE = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{1,4}$")

# ---------------------------------------------------------------------------
# Lazy-initialised EasyOCR Reader (downloading the model on first call)
# ---------------------------------------------------------------------------
_reader: Optional[easyocr.Reader] = None


def _get_reader() -> easyocr.Reader:
    """Return the shared EasyOCR Reader, creating it on first call."""
    global _reader
    if _reader is None:
        logger.info("Initialising EasyOCR Reader — this may take a moment on first run…")
        _reader = easyocr.Reader(["en"], gpu=False)
        logger.info("EasyOCR Reader ready")
    return _reader


# ---------------------------------------------------------------------------
# Frame capture
# ---------------------------------------------------------------------------

def capture_frame() -> Optional[np.ndarray]:
    """
    Capture a single still frame from the CSI Camera Module.

    Returns a BGR numpy array suitable for OpenCV processing, or None if
    the capture fails for any reason (camera not connected, driver error, …).
    """
    cam: Optional[Picamera2] = None
    try:
        cam = Picamera2()
        config = cam.create_still_configuration(
            main={"size": (_CAPTURE_WIDTH, _CAPTURE_HEIGHT), "format": "RGB888"}
        )
        cam.configure(config)
        cam.start()
        # Allow auto-exposure and auto-white-balance to converge
        time.sleep(0.5)
        frame_rgb = cam.capture_array()
        # Convert RGB (Picamera2 native) → BGR (OpenCV convention)
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        logger.debug(
            "Frame captured: %d×%d", frame_bgr.shape[1], frame_bgr.shape[0]
        )
        return frame_bgr
    except Exception as exc:
        logger.error("capture_frame failed: %s", exc)
        return None
    finally:
        if cam is not None:
            try:
                cam.stop()
                cam.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# OpenCV pre-processing
# ---------------------------------------------------------------------------

def preprocess_frame(frame: np.ndarray) -> np.ndarray:
    """
    Apply an OpenCV pre-processing pipeline to the raw BGR frame.

    Steps
    -----
    1. Grayscale conversion    — reduces colour noise and shrinks the data
                                  that OCR must process.
    2. Bilateral filter        — smooths uniform regions (background) while
                                  preserving sharp edges (characters).
    3. Adaptive Gaussian       — produces a clean binary image that handles
       thresholding              uneven lighting, shadows, and reflections.

    Returns a single-channel uint8 binary image.
    """
    # Step 1: grayscale
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Step 2: bilateral filter
    #   d=11      — diameter of the pixel neighbourhood
    #   sigmaColor=17 — filter sigma in colour space (lower = less blurring)
    #   sigmaSpace=17 — filter sigma in coordinate space
    blurred = cv2.bilateralFilter(gray, d=11, sigmaColor=17, sigmaSpace=17)

    # Step 3: adaptive Gaussian threshold
    #   blockSize=11 — size of neighbourhood area used to compute threshold
    #   C=2          — constant subtracted from the mean
    thresh = cv2.adaptiveThreshold(
        blurred,
        maxValue=255,
        adaptiveMethod=cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        thresholdType=cv2.THRESH_BINARY,
        blockSize=11,
        C=2,
    )
    return thresh


# ---------------------------------------------------------------------------
# Plate extraction
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Strip all whitespace and convert to upper-case."""
    return re.sub(r"\s+", "", text).upper()


def extract_plate(frame: np.ndarray) -> Optional[str]:
    """
    Run the full recognition pipeline on a raw BGR frame.

    1. Pre-process the frame with OpenCV (grayscale + adaptive threshold).
    2. Pass the processed image to EasyOCR.
    3. Discard detections below ``_OCR_CONFIDENCE_THRESHOLD``.
    4. Among surviving detections, prefer tokens that match the Indian
       plate regex.  If multiple match, pick the highest-confidence one.
    5. If no regex match is found, return the highest-confidence token
       as a best-effort result (useful for non-standard plates).
    6. Return None if no usable result can be produced.

    Parameters
    ----------
    frame : np.ndarray
        Raw BGR image from ``capture_frame()``.

    Returns
    -------
    str or None
        Upper-case plate string, or None on failure.
    """
    processed = preprocess_frame(frame)

    reader = _get_reader()
    try:
        results = reader.readtext(processed, detail=1, paragraph=False)
    except Exception as exc:
        logger.error("EasyOCR readtext raised an exception: %s", exc)
        return None

    if not results:
        logger.debug("EasyOCR returned no detections")
        return None

    # Filter by confidence
    confident = [
        (_normalise(text), conf)
        for _bbox, text, conf in results
        if conf >= _OCR_CONFIDENCE_THRESHOLD
    ]

    if not confident:
        logger.debug(
            "All %d OCR detection(s) fell below confidence threshold %.2f",
            len(results),
            _OCR_CONFIDENCE_THRESHOLD,
        )
        return None

    # Prefer detections that match the plate regex
    plate_matches = [
        (text, conf) for text, conf in confident if _PLATE_RE.match(text)
    ]

    if plate_matches:
        best_text, best_conf = max(plate_matches, key=lambda x: x[1])
        logger.info(
            "Plate detected: '%s' (confidence %.2f)", best_text, best_conf
        )
        return best_text

    # No regex match — fall back to the highest-confidence raw token
    best_text, best_conf = max(confident, key=lambda x: x[1])
    if best_text:
        logger.info(
            "No plate-pattern match; best OCR token: '%s' (confidence %.2f)",
            best_text,
            best_conf,
        )
        return best_text

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_plate() -> Optional[str]:
    """
    Capture a frame and attempt to extract a licence plate string.

    This is the single entry point used by ``main.py``.

    Returns
    -------
    str
        Upper-case plate identifier (e.g. ``"MH12AB1234"``).
    None
        If the camera capture fails or OCR produces no usable result.
        The caller is responsible for handling this case (e.g. by asking
        the driver to reposition or by logging the failed attempt).
    """
    frame = capture_frame()
    if frame is None:
        logger.warning("scan_plate: frame capture failed — returning None")
        return None

    plate = extract_plate(frame)
    if plate is None:
        logger.warning("scan_plate: OCR produced no usable result — returning None")
    return plate
