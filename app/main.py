"""
Standalone LAN-side process: owns the camera and watches the PLC's
camera-trigger bit over MC protocol, judging each trigger capture OK/NG.
No live stream, no cloud push, no inbound port needed on this machine at
all -- everything here is an outbound call (PLC read/write) or a local
file write.

app/plc/trigger.py polls the trigger bit and sets snap_event on every 0 -> 1
edge. app/camera/capture.py sits idle until snap_event is set, then fires
the camera's software trigger, grabs exactly one full-resolution frame, and
calls on_trigger_frame() with it. That callback hands off to a single
background worker (_inspect_loop) that, in order: runs the frame through
app/vision/detector.py (the ampoule YOLO model), pulses the result back to
the PLC via app/plc/result.py (CAM_RESULT_OK_DEVICE / CAM_RESULT_NG_DEVICE)
-- the cobot is waiting on this, so it goes out before anything below --
and only then saves one copy of the frame, sorted into TRAINING_DATA_DIR/OK
or /NG: boxed (annotated) for OK, plain for NG. None of this ever blocks
the camera.

Run with: python main.py (from the project root)
"""

import logging
import queue
import signal
import threading
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from app.camera import capture
from app.camera.sdk_path import reexec_for_linux_runtime
from app.config import settings
from app.plc import result as plc_result
from app.plc import trigger as plc_trigger
from app.vision import detector as vision_detector

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("camera_service.main")

_stop_event = threading.Event()


def _handle_signal(signum, frame):
    logger.info("Received signal %s, shutting down...", signum)
    _stop_event.set()


def _inspect_loop(inspect_queue: "queue.Queue[tuple[str, np.ndarray]]", busy_event: threading.Event) -> None:
    training_ok_dir = Path(settings.TRAINING_DATA_DIR) / "OK"
    training_ng_dir = Path(settings.TRAINING_DATA_DIR) / "NG"
    training_ok_dir.mkdir(parents=True, exist_ok=True)
    training_ng_dir.mkdir(parents=True, exist_ok=True)

    while not _stop_event.is_set():
        try:
            ts, bgr = inspect_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        try:
            found, annotated = vision_detector.detect_and_annotate(bgr)
        except Exception:
            logger.exception("Vision detection failed, skipping PLC result for this trigger")
            # Still resume trigger polling -- otherwise a detection crash
            # would leave it paused forever on a trigger we'll never answer.
            busy_event.clear()
            continue

        # Signal the PLC first, before any disk writes -- the cobot is
        # waiting on this; the save below isn't time sensitive.
        if found:
            logger.info("Ampoule detected -> signaling OK to PLC (%s)", settings.CAM_RESULT_OK_DEVICE)
            plc_result.signal_ok()
        else:
            logger.info("No ampoule detected -> signaling NG to PLC (%s)", settings.CAM_RESULT_NG_DEVICE)
            plc_result.signal_ng()

        # Result is sent -- safe to resume watching for the next trigger now.
        busy_event.clear()

        # One save per trigger, sorted by judgment: OK gets the annotated
        # (boxed) copy, NG gets the plain frame (nothing to box).
        training_dir = training_ok_dir if found else training_ng_dir
        image_to_save = annotated if found else bgr
        training_filename = training_dir / f"trigger_{ts}.jpg"
        if not cv2.imwrite(str(training_filename), image_to_save):
            logger.warning("Failed to save training snapshot to %s", training_filename)
        else:
            logger.info("Saved training snapshot to %s", training_filename)


def main() -> None:
    # Must run before anything touches the SDK, and before any other setup --
    # on Linux this may restart the process once (see sdk_path.py) to pick up
    # LD_LIBRARY_PATH, which is only consulted by the dynamic linker at
    # process start.
    reexec_for_linux_runtime()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Unbounded: a trigger snapshot (or its inspection result) must never be
    # dropped just because the previous one hasn't been processed yet.
    inspect_queue: "queue.Queue[tuple[str, np.ndarray]]" = queue.Queue()
    snap_event = threading.Event()
    # Set by plc_trigger as soon as a trigger edge is seen, cleared by
    # _inspect_loop once the OK/NG result has been sent -- pauses trigger
    # polling for that window so it and app/plc/result.py's writes are never
    # issued to the PLC at the same time.
    busy_event = threading.Event()

    def on_trigger_frame(bgr: np.ndarray) -> None:
        ts = f"{datetime.now():%Y%m%d_%H%M%S_%f}"
        inspect_queue.put_nowait((ts, bgr))

    logger.info("Preloading vision model...")
    vision_detector.preload()

    inspect_thread = threading.Thread(target=_inspect_loop, args=(inspect_queue, busy_event), daemon=True)
    inspect_thread.start()

    trigger_thread = threading.Thread(
        target=plc_trigger.run, args=(snap_event.set, _stop_event, busy_event), daemon=True
    )
    trigger_thread.start()

    logger.info(
        "Watching PLC %s device %s for trigger snapshots (result bits OK=%s NG=%s)",
        settings.PLC_HOST,
        settings.CAM_TRIG_DEVICE,
        settings.CAM_RESULT_OK_DEVICE,
        settings.CAM_RESULT_NG_DEVICE,
    )
    try:
        capture.run(on_trigger_frame, snap_event, _stop_event)
    finally:
        _stop_event.set()
        trigger_thread.join(timeout=5)
        inspect_thread.join(timeout=5)
