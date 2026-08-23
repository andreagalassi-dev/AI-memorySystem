"""P2.8d — Reranker cross-encoder BGE-reranker-v2-m3 (transformers NATIVO).

Implementazione diretta con AutoModelForSequenceClassification: il wrapper
FlagEmbedding.FlagReranker usa `tokenizer.prepare_for_model`, API rimossa in
transformers 5.x (terza incompatibilita' di versione del progetto, dopo
EasyOCR e i LoRA di colpali-engine — stavolta almeno esplode rumorosamente).

Il router decide DOVE applicarlo: mai sulle query 'code' (gia' R@1~0.94:
il cross-encoder puo' solo peggiorarle e costa latenza).
"""

from __future__ import annotations

import os
from functools import lru_cache

RERANK_MODEL = "BAAI/bge-reranker-v2-m3"

#: Profondita' del pool RRF che arriva al cross-encoder TESTUALE (non il
#: reranker visivo, che ha le sue AIKE_VISUAL_*).
#:
#: Default 50 dal 22/08, MISURATO. A 20 il cross-encoder — che esiste apposta
#: per riordinare bene — vedeva venti candidati, mentre 22 bersagli del metro
#: narrativo stanno fra la posizione 11 e la 165 e nove di essi proprio fra la
#: 11 e la 20: appena oltre il bordo.
#:   - recupero (deterministico): bersagli nelle evidenze finali 24/56 -> 30/56,
#:     IT 10/31 -> 16/31;
#:   - e2e sulle 8 query in cui il pool cambia qualcosa: bersaglio citato 0/7
#:     -> 4/7 sui guadagni, 1/1 -> 0/1 sulla perdita = **+3 netto**, con
#:     **zero ALTA sbagliate**. Tre dei quattro recuperi (Q035, Q038, Q045)
#:     erano errori CONFIDENTI: citavano la pagina sbagliata, ora la giusta;
#:   - golden gate a 50: 22 PASS_ALTA / 10 PASS / 10 MISS, 0 WRONG_ALTA,
#:     **zero item cambiati** campo per campo vs il riferimento a68a780.
#:
#: Costo: il cross-encoder isolato passa da 0.93s a 2.61s (2.8x), ma e2e la
#: latenza mediana NON peggiora (18.9s a 50 contro 20.1s a 20): a dominare e'
#: la generazione, e +1.7s ci si perde dentro. Non si sale a 100: li' il costo
#: e' 9.2x (8.56s) per soli 5 bersagli in piu' — quattro volte il prezzo per
#: bersaglio guadagnato.
RERANK_TOPK_DEFAULT = 50

#: Nome della variabile d'ambiente che lo sovrascrive (convenzione AIKE_*).
RERANK_TOPK_ENV = "AIKE_RERANK_TOPK"


def rerank_topk() -> int:
    """Pool RRF da dare al cross-encoder, letto dall'ambiente AD OGNI CHIAMATA.

    Perche' pigro e non una costante calcolata all'import: `.env` viene caricato
    da `src/common/run_config.load_runtime_env()`, che gira DOPO l'import di
    parecchi moduli. Una costante fotografata all'import leggerebbe un ambiente
    non ancora popolato e misurerebbe una configurazione che non esiste — lo
    stesso errore documentato in `src/common/run_config.py`.

    Valore assente, non numerico o <= 0 -> `RERANK_TOPK_DEFAULT`: un pool rotto
    deve degradare al riferimento noto, non a zero candidati.
    """
    raw = os.getenv(RERANK_TOPK_ENV)
    if raw is None:
        return RERANK_TOPK_DEFAULT
    try:
        value = int(raw.strip())
    except (AttributeError, ValueError):
        return RERANK_TOPK_DEFAULT
    return value if value > 0 else RERANK_TOPK_DEFAULT


def __getattr__(name: str):
    """`RERANK_TOPK` resta leggibile come attributo di modulo, ma e' calcolato.

    Serve perche' i sette punti che fanno `from src.retrieval.rerank import
    RERANK_TOPK` (fra cui gli script di misura, che NON vanno modificati) si
    trovino sotto l'interruttore senza che nessuno di essi cambi una riga.
    """
    if name == "RERANK_TOPK":
        return rerank_topk()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@lru_cache(maxsize=1)
def _reranker():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(RERANK_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(
        RERANK_MODEL, dtype=torch.float32).to(device).eval()
    return tok, model, device


def rerank(query: str, hits: list, limit: int = 10) -> list:
    """Riordina gli hit Qdrant per score cross-encoder su (query, text)."""
    import torch

    if not hits:
        return hits
    tok, model, device = _reranker()
    pairs = [(query, h.payload.get("text") or h.payload.get("desc") or "")
             for h in hits]
    batch = tok(pairs, padding=True, truncation=True, max_length=512,
                return_tensors="pt").to(device)
    with torch.no_grad():
        scores = model(**batch).logits.squeeze(-1).float().cpu().tolist()
    if not isinstance(scores, list):
        scores = [scores]
    ranked = sorted(zip(scores, hits), key=lambda t: -t[0])
    return [h for _, h in ranked[:limit]]
