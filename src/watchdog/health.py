from fastapi import FastAPI
from fastapi.responses import JSONResponse
import time

app = FastAPI(title="AIKE Watchdog Health")

_state = {
    "daemon_alive": False,
    "last_scan_started_at": None,
    "last_scan_completed_at": None,
    "files_in_processing": 0,
    "qdrant_ok": False,
    "postgres_ok": False,
}

def update_health_state(**kwargs):
    _state.update(kwargs)

@app.get("/health")
def health():
    now = time.time()
    started = _state["last_scan_started_at"]
    completed = _state["last_scan_completed_at"]
    
    # scan_blocked: ciclo iniziato ma non completato da > 30 minuti
    scan_blocked = (
        started is not None
        and (completed is None or completed < started)
        and (now - started) > 1800
    )
    ok = (
        _state["daemon_alive"]
        and _state["qdrant_ok"]
        and _state["postgres_ok"]
        and not scan_blocked
    )
    return JSONResponse(status_code=200 if ok else 503, content={
        "status": "ok" if ok else "degraded",
        "daemon_alive": _state["daemon_alive"],
        "last_scan_started_at": started,
        "last_scan_completed_at": completed,
        "scan_blocked": scan_blocked,
        "files_in_processing": _state["files_in_processing"],
        "qdrant_ok": _state["qdrant_ok"],
        "postgres_ok": _state["postgres_ok"],
    })
