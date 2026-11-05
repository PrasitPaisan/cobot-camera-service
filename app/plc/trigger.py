"""
Watches the PLC's camera-trigger bit (CAM_TRIG_DEVICE, e.g. M6000) over MC
protocol and calls back on every 0 -> 1 edge, after a short settle delay
(CAMERA_TRIGGER_SETTLE_SECONDS) so the snapshot isn't taken mid-motion.
Read-only: this module never writes CAM_TRIG_DEVICE -- the PLC resets it back
to 0 itself. Connects over PLC_PORT, the port dedicated to camera_service
(see app/plc/result.py for the write side, which shares the same port).

Polls continuously at PLC_TRIGGER_POLL_SECONDS from process start -- no
idle/active gating on RUNNING_DEVICE/READY_DEVICE, so a trigger is never
missed waiting for a mode switch. The one exception is busy_event: once an
edge is detected, polling pauses until main.py clears busy_event (once the
snapshot has been judged and the OK/NG result sent back to the PLC). There's
no point reading the trigger bit again before that -- the cobot won't raise
it a second time until it has our result -- and pausing means this module's
reads and app/plc/result.py's writes are never issued to the PLC at the same
time, without needing an explicit lock.
"""

import logging
import threading
from typing import Callable

import pymcprotocol

from app.config import settings

logger = logging.getLogger("camera_service.plc_trigger")


def _read_bit(device: str) -> int:
    mc = pymcprotocol.Type3E()
    mc.setaccessopt(commtype="binary")
    mc.connect(settings.PLC_HOST, settings.PLC_PORT)
    try:
        return mc.batchread_bitunits(headdevice=device, readsize=1)[0]
    finally:
        mc.close()


def _poll_loop(on_trigger: Callable[[], None], stop_event: threading.Event, busy_event: threading.Event) -> None:
    previous = 0
    while not stop_event.is_set():
        if busy_event.is_set():
            stop_event.wait(settings.PLC_TRIGGER_POLL_SECONDS)
            continue

        current = _read_bit(settings.CAM_TRIG_DEVICE)
        logger.debug("Poll: %s=%s", settings.CAM_TRIG_DEVICE, current)
        if current == 1 and previous == 0:
            logger.info(
                "Camera trigger detected on %s, snapshot in %.2fs -- pausing trigger poll until result is sent",
                settings.CAM_TRIG_DEVICE,
                settings.CAMERA_TRIGGER_SETTLE_SECONDS,
            )
            busy_event.set()
            threading.Timer(settings.CAMERA_TRIGGER_SETTLE_SECONDS, on_trigger).start()
        previous = current
        stop_event.wait(settings.PLC_TRIGGER_POLL_SECONDS)


def run(on_trigger: Callable[[], None], stop_event: threading.Event, busy_event: threading.Event) -> None:
    """Blocks until stop_event is set, reconnecting on any PLC/network failure."""
    while not stop_event.is_set():
        try:
            _poll_loop(on_trigger, stop_event, busy_event)
        except Exception:
            logger.exception("PLC trigger poll loop crashed, will retry")
            stop_event.wait(settings.PLC_RECONNECT_SECONDS)
