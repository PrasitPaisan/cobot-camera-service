"""
Loads the ampoule-detection model (best.pt, a single-class YOLO model) once,
lazily, and classifies a single full-resolution BGR trigger frame as
OK (ampoule detected) or NG (not detected) for the PLC result handshake.

preload() runs the model once up front (load + a throwaway inference) so the
*first* real trigger after process start isn't the one that eats the ~5s
model-load + first-inference warmup cost -- call it during startup, before
capture.run() begins watching for triggers.
"""

import logging

import cv2
import numpy as np

from app.config import settings

logger = logging.getLogger("camera_service.vision")

_model = None


def _load_model():
    global _model
    if _model is None:
        from ultralytics import YOLO

        _model = YOLO(str(settings.VISION_MODEL_PATH))
        logger.info(
            "Loaded vision model %s, classes=%s", settings.VISION_MODEL_PATH, _model.names
        )
    return _model


def preload() -> None:
    model = _load_model()
    blank = np.zeros((64, 64, 3), dtype=np.uint8)
    model.predict(source=blank, conf=settings.VISION_CONF_THRESHOLD, verbose=False)
    logger.info("Vision model warmed up")


def detect_ampoule(bgr: np.ndarray) -> bool:
    """True if an ampoule is detected at or above VISION_CONF_THRESHOLD."""
    model = _load_model()
    results = model.predict(source=bgr, conf=settings.VISION_CONF_THRESHOLD, verbose=False)
    return len(results[0].boxes) > 0


def detect_and_annotate(bgr: np.ndarray) -> tuple[bool, np.ndarray]:
    """Same judgment as detect_ampoule(), plus a copy of bgr with boxes drawn on it."""
    model = _load_model()
    results = model.predict(source=bgr, conf=settings.VISION_CONF_THRESHOLD, verbose=False)
    result = results[0]

    annotated = bgr.copy()
    for box in result.boxes:
        class_name = model.names[int(box.cls[0])]
        confidence = float(box.conf[0])
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        logger.debug("Detection: %s conf=%.2f box=(%d,%d,%d,%d)", class_name, confidence, x1, y1, x2, y2)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
        label = f"{class_name} {confidence:.2f}"
        cv2.putText(annotated, label, (x1, max(y1 - 10, 0)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    found = len(result.boxes) > 0
    if not found:
        logger.debug("Detection: no ampoule found (conf threshold %.2f)", settings.VISION_CONF_THRESHOLD)
    return found, annotated
