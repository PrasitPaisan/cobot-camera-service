"""
Shared, persistent MC protocol connection to the PLC, reused by both
app/plc/trigger.py (reads CAM_TRIG_DEVICE) and app/plc/result.py (writes/reads
back the OK/NG result bits). The PLC's dedicated port (PLC_PORT) only has one
connection slot configured on the PLC side, so only one TCP connection can be
open to it at a time -- a lock serializes access between the trigger-polling
thread and the result-writing thread so they never issue commands on the
socket at the same time.

Switched from a reconnect-every-call design (each read/write opened and
closed its own connection) after measuring, on one production deploy, ~3s to
establish a new TCP connection to this PLC's embedded Ethernet port vs
~5-10ms for a read/write on an already-open connection. Holding the
connection open eliminates that ~3s cost for everything after the first
connect -- only a dropped connection pays it again, and only once.
"""

import logging
import threading
from typing import Optional

import pymcprotocol

from app.config import settings

logger = logging.getLogger("camera_service.plc_connection")

_lock = threading.Lock()
_mc: Optional[pymcprotocol.Type3E] = None


def _connect_locked() -> None:
    global _mc
    mc = pymcprotocol.Type3E()
    mc.setaccessopt(commtype="binary")
    mc.soc_timeout = settings.PLC_SOCKET_TIMEOUT_SECONDS
    mc.connect(settings.PLC_HOST, settings.PLC_PORT)
    logger.info("Connected to PLC %s:%s (persistent)", settings.PLC_HOST, settings.PLC_PORT)
    _mc = mc


def _ensure_connected_locked() -> None:
    if _mc is None:
        _connect_locked()


def _drop_locked() -> None:
    global _mc
    if _mc is not None:
        try:
            _mc.close()
        except Exception:
            pass
        _mc = None


def read_bit(device: str) -> int:
    """Reads a single bit device. Reconnects and re-raises on any failure --
    callers already have their own retry/backoff (app/plc/trigger.py's outer
    run() loop)."""
    with _lock:
        _ensure_connected_locked()
        try:
            return _mc.batchread_bitunits(headdevice=device, readsize=1)[0]
        except Exception:
            _drop_locked()
            raise


def write_and_confirm_bit(device: str, value: int) -> bool:
    """Writes a single bit device and reads it back to confirm the PLC
    latched it. Reconnects and re-raises on any failure -- callers already
    retry (app/plc/result.py's _write_with_retries)."""
    with _lock:
        _ensure_connected_locked()
        try:
            _mc.batchwrite_bitunits(headdevice=device, values=[value])
            readback = _mc.batchread_bitunits(headdevice=device, readsize=1)[0]
            return readback == value
        except Exception:
            _drop_locked()
            raise
