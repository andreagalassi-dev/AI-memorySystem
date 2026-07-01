# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests>=2.31",
# ]
# ///
"""P0.3 — Verifica che l'inferenza LLM locale (Ollama) risponda.

Interroga l'endpoint `/api/tags` di Ollama: conferma che il demone e' attivo
e raggiungibile ed elenca i modelli installati.

Uso (dipendenza `requests` risolta al volo da uv via PEP 723, senza toccare
il pyproject.toml globale):

    uv run scripts/check_llm.py

L'URL base si puo' sovrascrivere con la variabile d'ambiente OLLAMA_BASE_URL
(default: http://localhost:11434).

Exit code: 0 = Ollama risponde; 1 = irraggiungibile / errore.
"""

from __future__ import annotations

import os
import sys

import requests

# Endpoint richiesto per la verifica: elenca i modelli installati.
BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
TAGS_URL = f"{BASE_URL}/api/tags"
TIMEOUT_S = 5


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


def main() -> int:
    print(f"[check_llm] Verifico Ollama su {TAGS_URL} ...")

    try:
        resp = requests.get(TAGS_URL, timeout=TIMEOUT_S)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        if _is_connection_refused(exc):
            print(
                "[check_llm] ERRORE: connessione rifiutata (ConnectionRefusedError).\n"
                f"            Il demone Ollama non risponde su {BASE_URL}.\n"
                "            Avvialo con 'ollama serve' (o l'app Ollama) e riprova.",
                file=sys.stderr,
            )
        else:
            print(
                f"[check_llm] ERRORE di connessione verso {BASE_URL}: {exc}",
                file=sys.stderr,
            )
        return 1
    except requests.exceptions.Timeout:
        print(
            f"[check_llm] ERRORE: timeout dopo {TIMEOUT_S}s su {TAGS_URL}. "
            "Ollama e' avviato ma non risponde in tempo.",
            file=sys.stderr,
        )
        return 1
    except requests.exceptions.HTTPError as exc:
        print(
            f"[check_llm] ERRORE HTTP dall'endpoint: {exc}",
            file=sys.stderr,
        )
        return 1
    except requests.exceptions.RequestException as exc:
        print(f"[check_llm] ERRORE imprevisto nella richiesta: {exc}", file=sys.stderr)
        return 1

    try:
        models = resp.json().get("models", [])
    except ValueError:
        print(
            "[check_llm] ERRORE: risposta non JSON dall'endpoint /api/tags.",
            file=sys.stderr,
        )
        return 1

    print(f"[check_llm] OK — Ollama risponde. Modelli installati: {len(models)}")
    for m in models:
        name = m.get("name", "?")
        size_gb = m.get("size", 0) / 1e9
        print(f"  - {name}  ({size_gb:.1f} GB)")
    if not models:
        print(
            "  (nessun modello installato: scaricane uno con "
            "'ollama pull qwen3:14b')"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
