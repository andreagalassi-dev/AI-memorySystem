# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests>=2.31",
# ]
# ///
"""P0.3 — Verifica che l'inferenza LLM locale (Ollama) risponda e generi testo.

Due controlli in sequenza:
  1. GET  /api/tags     -> il demone e' attivo e raggiungibile; elenca i modelli.
  2. POST /api/generate -> prova di generazione reale con un prompt basilare,
                           per confermare che l'inferenza produca testo.

Uso (dipendenza `requests` risolta al volo da uv via PEP 723, senza toccare
il pyproject.toml globale):

    uv run scripts/check_llm.py

Variabili d'ambiente:
  OLLAMA_BASE_URL  URL base Ollama (default: http://localhost:11434).
  OLLAMA_MODEL     modello per la prova di generazione. Se assente, usa il
                   primo modello installato.

Exit code: 0 = Ollama risponde e genera; 1 = irraggiungibile / errore / no modelli.
"""

from __future__ import annotations

import os
import sys

import requests

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
TAGS_URL = f"{BASE_URL}/api/tags"
GENERATE_URL = f"{BASE_URL}/api/generate"

TAGS_TIMEOUT_S = 5
GEN_TIMEOUT_S = 60  # la prima generazione carica il modello in memoria: piu' lenta.

MODEL_OVERRIDE = os.environ.get("OLLAMA_MODEL", "").strip()
TEST_PROMPT = "Rispondi solo con la parola OK"


def _is_connection_refused(exc: BaseException) -> bool:
    """True se nella catena delle cause c'e' un ConnectionRefusedError.

    requests incapsula l'errore socket (demone spento / porta chiusa) dentro
    requests.exceptions.ConnectionError; risaliamo la catena per riconoscerlo.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, ConnectionRefusedError):
            return True
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return False


def _report_request_error(exc: Exception, url: str) -> None:
    """Stampa un messaggio d'errore chiaro per le eccezioni di requests."""
    if isinstance(exc, requests.exceptions.ConnectionError):
        if _is_connection_refused(exc):
            print(
                "[check_llm] ERRORE: connessione rifiutata (ConnectionRefusedError).\n"
                f"            Il demone Ollama non risponde su {BASE_URL}.\n"
                "            Avvialo con 'ollama serve' (o l'app Ollama) e riprova.",
                file=sys.stderr,
            )
        else:
            print(f"[check_llm] ERRORE di connessione verso {url}: {exc}", file=sys.stderr)
    elif isinstance(exc, requests.exceptions.Timeout):
        print(
            f"[check_llm] ERRORE: timeout su {url}. "
            "Ollama e' avviato ma non risponde in tempo.",
            file=sys.stderr,
        )
    elif isinstance(exc, requests.exceptions.HTTPError):
        print(f"[check_llm] ERRORE HTTP dall'endpoint {url}: {exc}", file=sys.stderr)
    else:
        print(f"[check_llm] ERRORE imprevisto verso {url}: {exc}", file=sys.stderr)


def get_models() -> list[str] | None:
    """Interroga /api/tags. Ritorna i nomi dei modelli, o None in caso di errore."""
    print(f"[check_llm] [1/2] Verifico il demone su {TAGS_URL} ...")
    try:
        resp = requests.get(TAGS_URL, timeout=TAGS_TIMEOUT_S)
        resp.raise_for_status()
        payload = resp.json()
    except requests.exceptions.RequestException as exc:
        _report_request_error(exc, TAGS_URL)
        return None
    except ValueError:
        print("[check_llm] ERRORE: risposta non JSON da /api/tags.", file=sys.stderr)
        return None

    models = [m.get("name", "?") for m in payload.get("models", [])]
    print(f"[check_llm] OK — demone attivo. Modelli installati: {len(models)}")
    for name in models:
        print(f"  - {name}")
    return models


def run_generation(model: str) -> bool:
    """Prova di generazione reale su /api/generate. True se produce testo."""
    print(f"[check_llm] [2/2] Prova di generazione con '{model}' ...")
    print(f"           prompt: {TEST_PROMPT!r}")
    try:
        resp = requests.post(
            GENERATE_URL,
            json={"model": model, "prompt": TEST_PROMPT, "stream": False},
            timeout=GEN_TIMEOUT_S,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.exceptions.RequestException as exc:
        _report_request_error(exc, GENERATE_URL)
        return False
    except ValueError:
        print("[check_llm] ERRORE: risposta non JSON da /api/generate.", file=sys.stderr)
        return False

    text = (payload.get("response") or "").strip()
    if not text:
        print(
            "[check_llm] ERRORE: generazione riuscita ma risposta vuota.",
            file=sys.stderr,
        )
        return False

    print(f"[check_llm] OK — risposta del modello: {text!r}")
    return True


def main() -> int:
    models = get_models()
    if models is None:
        return 1
    if not models:
        print(
            "[check_llm] Nessun modello installato: impossibile testare la "
            "generazione. Scaricane uno con 'ollama pull qwen2.5:1.5b'.",
            file=sys.stderr,
        )
        return 1

    if MODEL_OVERRIDE:
        if MODEL_OVERRIDE not in models:
            print(
                f"[check_llm] ERRORE: OLLAMA_MODEL='{MODEL_OVERRIDE}' non e' tra i "
                f"modelli installati {models}.",
                file=sys.stderr,
            )
            return 1
        model = MODEL_OVERRIDE
    else:
        model = models[0]

    return 0 if run_generation(model) else 1


if __name__ == "__main__":
    raise SystemExit(main())
