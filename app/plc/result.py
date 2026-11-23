"""
Signals the PLC with the vision-inspection result after a trigger snapshot:
pulses CAM_RESULT_OK_DEVICE or CAM_RESULT_NG_DEVICE 1 -> (hold) -> 0, reading
each write back to confirm the PLC actually latched it (same pattern
backend_fast's /camera/inspection-result uses) -- a write call not raising
only means the TCP request/response round-tripped, not that the PLC's
program actually picked the bit up. Each write is retried a few times before
giving up, since the reset-to-0 step in particular must not silently fail --
if it does, the bit is left stuck at 1 until the next trigger's pulse happens
to clear it. Writes go through app/plc/connection.py's shared persistent
connection, the same dedicated PLC_PORT app/plc/trigger.py reads
CAM_TRIG_DEVICE on -- that module's lock keeps the two from hitting the PLC
at the same time.
"""

import logging
import time

from app.config import settings
from app.plc import connection as plc_connection

logger = logging.getLogger("camera_service.plc_result")

_WRITE_RETRY_ATTEMPTS = 3
_WRITE_RETRY_DELAY_SECONDS = 0.2


def _write_and_confirm_bit(device: str, value: int) -> bool:
    return plc_connection.write_and_confirm_bit(device, value)


def _write_with_retries(device: str, value: int) -> bool:
    for attempt in range(1, _WRITE_RETRY_ATTEMPTS + 1):
        try:
            if _write_and_confirm_bit(device, value):
                logger.info("Wrote %s=%d (confirmed, attempt %d)", device, value, attempt)
                return True
            logger.warning(
                "Attempt %d/%d: wrote %s=%d but readback didn't match",
                attempt, _WRITE_RETRY_ATTEMPTS, device, value,
            )
        except Exception:
            logger.exception("Attempt %d/%d: failed to write %s=%d", attempt, _WRITE_RETRY_ATTEMPTS, device, value)

        if attempt < _WRITE_RETRY_ATTEMPTS:
            time.sleep(_WRITE_RETRY_DELAY_SECONDS)

    logger.error(
        "Giving up after %d attempts -- %s may be stuck NOT at %d. Manual check may be needed.",
        _WRITE_RETRY_ATTEMPTS, device, value,
    )
    return False


def _pulse(device: str) -> None:
    """Blocking for CAM_RESULT_HOLD_SECONDS -- call this from a background thread."""
    if not _write_with_retries(device, 1):
        return

    time.sleep(settings.CAM_RESULT_HOLD_SECONDS)

    _write_with_retries(device, 0)


def signal_ok() -> None:
    _pulse(settings.CAM_RESULT_OK_DEVICE)


def signal_ng() -> None:
    _pulse(settings.CAM_RESULT_NG_DEVICE)
