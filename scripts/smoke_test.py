# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests>=2.31",
# ]
# ///
"""P0.7 — Smoke test end-to-end dell'infrastruttura locale.

Singolo entrypoint che valida in sequenza:
  [1/3] Infrastruttura Docker  — i 4 servizi (qdrant, backend, ocr, ui) su/healthy
  [2/3] LLM locale (Ollama)    — generazione minima reale
  [3/3] Backend & Vector DB    — GET /health (che a cascata pinga Qdrant)

Orchestrazione "auto, lascia com'era": porta su cio' che manca (docker compose
up -d se giu' + uvicorn come subprocess, dato che il container backend e' un
placeholder) e a fine test smonta SOLO cio' che ha avviato lui.

Uso:
    uv run scripts/smoke_test.py

Exit code: 0 = tutti i check verdi; 1 = primo check rosso (fail-fast).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Callable

import requests

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "").strip()
BACKEND_HOST = os.environ.get("SMOKE_BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("SMOKE_BACKEND_PORT", "8000"))
BACKEND_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"

EXPECTED_SERVICES = ["qdrant", "backend", "ocr", "ui"]
GEN_PROMPT = "Rispondi solo con la parola OK"

# Timeout per-check (secondi), tarati sul comportamento reale dei componenti.
DOCKER_TIMEOUT = 40    # healthcheck: start-period + interval ~10s
LLM_TIMEOUT = 75       # la prima generazione carica il modello in RAM (lento)
HEALTH_TIMEOUT = 25    # startup uvicorn + Qdrant ready
POLL_INTERVAL = 1.5

OK, RETRY, FAIL = "ok", "retry", "fail"

# ----------------------------------------------------------------------------- utils


def run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def backend_serving() -> bool:
    """True solo se qualcosa risponde HTTP su /health.

    Non uso un socket raw: il docker-proxy di un container che pubblica la porta
    tiene il TCP "aperto" anche senza servire HTTP -> ingannerebbe il controllo.
    Una qualsiasi risposta HTTP (200/503/...) = server reale; errori di
    connessione/timeout = niente backend.
    """
    try:
        requests.get(f"{BACKEND_URL}/health", timeout=2)
        return True
    except requests.exceptions.RequestException:
        return False


def wait_for(check: Callable[[], tuple[str, str]], timeout: float, desc: str) -> tuple[bool, str]:
    """Polling con deadline monotona. `check` ritorna (OK|RETRY|FAIL, dettaglio).

    Distingue "non ancora pronto" (RETRY, si continua entro la deadline) da
    "fallimento vero" (FAIL, stop immediato: ritentare e' inutile).
    """
    deadline = time.monotonic() + timeout
    detail = ""
    while True:
        status, detail = check()
        if status == OK:
            return True, detail
        if status == FAIL:
            return False, detail
        if time.monotonic() >= deadline:
            return False, f"timeout dopo {int(timeout)}s — ultimo stato: {detail}"
        time.sleep(POLL_INTERVAL)


def _parse_compose_ps(out: str) -> dict[str, tuple[str, str]]:
    """Ritorna {servizio: (state, health)} da `docker compose ps --format json`.

    Gestisce sia NDJSON (una riga per container) sia array JSON.
    """
    out = out.strip()
    if not out:
        return {}
    try:
        records = json.loads(out)
        if isinstance(records, dict):
            records = [records]
    except json.JSONDecodeError:
        records = [json.loads(line) for line in out.splitlines() if line.strip()]
    result: dict[str, tuple[str, str]] = {}
    for r in records:
        result[r.get("Service", "?")] = (r.get("State", ""), r.get("Health", "") or "")
    return result


# ----------------------------------------------------------------------------- checks


def check_docker() -> tuple[str, str]:
    rc, out, err = run(["docker", "compose", "ps", "--format", "json"])
    if rc != 0:
        return FAIL, f"'docker compose ps' errore: {err.strip() or out.strip()}"
    services = _parse_compose_ps(out)
    pending: list[str] = []
    for svc in EXPECTED_SERVICES:
        if svc not in services:
            pending.append(f"{svc}=assente")
            continue
        state, health = services[svc]
        if state != "running":
            pending.append(f"{svc}={state or 'sconosciuto'}")
        elif health and health != "healthy":
            pending.append(f"{svc}:health={health}")
    if pending:
        return RETRY, "in attesa: " + ", ".join(pending)
    return OK, "qdrant, backend, ocr, ui: running/healthy"


def check_llm() -> tuple[str, str]:
    try:
        tags = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        tags.raise_for_status()
    except requests.exceptions.ConnectionError:
        return RETRY, f"Ollama non raggiungibile su {OLLAMA_BASE_URL}"
    except requests.exceptions.RequestException as exc:
        return RETRY, f"/api/tags: {exc}"

    models = [m.get("name", "?") for m in tags.json().get("models", [])]
    if not models:
        return FAIL, "nessun modello Ollama installato (es. 'ollama pull qwen2.5:1.5b')"
    model = OLLAMA_MODEL if OLLAMA_MODEL in models else models[0]

    try:
        gen = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "prompt": GEN_PROMPT, "stream": False},
            timeout=LLM_TIMEOUT - 5,
        )
        gen.raise_for_status()
    except requests.exceptions.RequestException as exc:
        return RETRY, f"/api/generate: {exc}"

    text = (gen.json().get("response") or "").strip()
    if not text:
        return FAIL, "generazione riuscita ma risposta vuota"
    return OK, f"modello '{model}' -> {text!r}"


def check_backend() -> tuple[str, str]:
    try:
        r = requests.get(f"{BACKEND_URL}/health", timeout=5)
    except requests.exceptions.ConnectionError:
        return RETRY, f"backend non in ascolto su {BACKEND_URL}"
    except requests.exceptions.RequestException as exc:
        return RETRY, str(exc)
    if r.status_code == 200:
        return OK, f"/health 200 {r.json()}"
    if r.status_code == 503:
        return RETRY, f"/health 503 (Qdrant non ancora pronto?) {r.text}"
    return FAIL, f"/health status inatteso {r.status_code}: {r.text}"


# ----------------------------------------------------------------------------- main


def _all_services_running() -> bool:
    rc, out, _ = run(["docker", "compose", "ps", "--format", "json"])
    if rc != 0:
        return False
    services = _parse_compose_ps(out)
    return all(
        svc in services and services[svc][0] == "running" for svc in EXPECTED_SERVICES
    )


def main() -> int:
    print("=== SMOKE TEST — infrastruttura locale ===\n")

    rc, _, _ = run(["docker", "compose", "version"])
    if rc != 0:
        print("❌ Docker/Compose non disponibile. Avvia il daemon e riprova.")
        return 1

    started_compose = False
    backend_proc: subprocess.Popen | None = None
    backend_log = None

    try:
        # --- orchestrazione: porta su cio' che manca ---
        if not _all_services_running():
            print("· Container non tutti su → 'docker compose up -d' ...")
            rc, _, err = run(["docker", "compose", "up", "-d"])
            if rc != 0:
                print(f"❌ 'docker compose up -d' fallito: {err.strip()}")
                return 1
            started_compose = True

        if not backend_serving():
            print(f"· Backend non in ascolto su :{BACKEND_PORT} → avvio uvicorn ...")
            backend_log = tempfile.NamedTemporaryFile(
                prefix="smoke_backend_", suffix=".log", delete=False, mode="w"
            )
            backend_proc = subprocess.Popen(
                ["uv", "run", "uvicorn", "src.backend.main:app",
                 "--host", BACKEND_HOST, "--port", str(BACKEND_PORT)],
                stdout=backend_log, stderr=subprocess.STDOUT,
            )

        # --- check in sequenza (fail-fast) ---
        checks = [
            ("[1/3] Infrastruttura Docker", check_docker, DOCKER_TIMEOUT),
            ("[2/3] LLM locale (Ollama)", check_llm, LLM_TIMEOUT),
            ("[3/3] Backend & Vector DB", check_backend, HEALTH_TIMEOUT),
        ]
        for label, fn, timeout in checks:
            print(f"\n{label} ...")
            ok, detail = wait_for(fn, timeout, label)
            if ok:
                print(f"  ✅ {detail}")
            else:
                print(f"  ❌ {detail}")
                if backend_log is not None:
                    print(f"     (log backend: {backend_log.name})")
                print("\n=== SMOKE TEST FAILED ===")
                return 1

        print("\n=== ✅ SMOKE TEST PASSED — sistema locale operativo ===")
        return 0

    finally:
        # smonta SOLO cio' che ha avviato questo script
        if backend_proc is not None:
            backend_proc.terminate()
            try:
                backend_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                backend_proc.kill()
            print("· uvicorn (avviato dal test) terminato.")
        if backend_log is not None:
            backend_log.close()
        if started_compose:
            run(["docker", "compose", "down"])
            print("· 'docker compose down' (container avviati dal test rimossi).")


if __name__ == "__main__":
    raise SystemExit(main())
