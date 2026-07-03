"""P2.8d — Reranker cross-encoder BGE-reranker-v2-m3 (transformers NATIVO).

Implementazione diretta con AutoModelForSequenceClassification: il wrapper
FlagEmbedding.FlagReranker usa `tokenizer.prepare_for_model`, API rimossa in
transformers 5.x (terza incompatibilita' di versione del progetto, dopo
EasyOCR e i LoRA di colpali-engine — stavolta almeno esplode rumorosamente).

Il router decide DOVE applicarlo: mai sulle query 'code' (gia' R@1~0.94:
il cross-encoder puo' solo peggiorarle e costa latenza).
"""

from __future__ import annotations

from functools import lru_cache

RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_TOPK = 20


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
