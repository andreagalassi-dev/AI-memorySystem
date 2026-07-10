"""Fase 1.2 — Estrattore testuale block-aware per tabelle born-digital (no AI).

Perche' esiste: GLM-OCR (il VLM del binario HEAVY) e' un ottimo OCR di scansioni,
ma su una pagina born-digital con text-layer perfetto **corrompe i codici**
(`V09011500C01` -> `V0901150001`: perde la `C`; `V09012000G01` -> `V0901200001`).
Il codice e' il pilastro del guardrail deterministico (SPEC §D) e la chiave del
merge Fase 2: non puo' essere inventato da un modello quando il testo esatto e'
gia' sul PDF.

Questo modulo legge le coordinate delle parole (PyMuPDF `get_text("words")`) e
ricostruisce le righe per BLOCCHI di colonne, senza modelli. Il codice emesso e'
il token nativo **verbatim** (zero inferenza -> zero corruzione). Le righe che non
si ricostruiscono con sicurezza finiscono in una `reject lane` esplicita: mai una
riga fabbricata (Fail-Closed, coerente col chunker P2 e con l'estrazione GLM-OCR).

Routing (in `run_massive_extraction`): una pagina-tabella con text-layer affidabile
(`reliable_text_layer`, cioe' `not needs_ocr`) passa di qua; solo le VERE scansioni
(senza testo nativo) restano a GLM-OCR.

Layout gestito: listini multi-blocco affiancati (es. Mamoli 4 blocchi/pagina),
borderless (colonne allineate, nessun bordo cella -> `find_tables` di PyMuPDF le
manca). L'euristica di colonna e' volutamente semplice e deterministica: e' un primo
strato, tarabile per-brand come i profili di ingestion (`brands.py`).
"""

from __future__ import annotations

import re
from bisect import bisect_left
from decimal import Decimal, InvalidOperation

from src.ingestion.codes import base_cleanup
from src.ingestion.extraction.schema import RigaGrezza
from src.ingestion.ocr_fallback import needs_ocr

# Codice ricambio: alfanumerico maiuscolo, >=6 char, >=1 lettera E >=4 cifre.
# Esclude header ("CODICE": 0 cifre), numeri-modello ("2817": 0 lettere) e
# prezzi ("90,00": separatore non alfanumerico). Rilevatore di DEFAULT: i brand
# con codifiche diverse potranno iniettarne uno proprio (parametro `code_re`).
DEFAULT_CODE_RE = re.compile(r"^(?=.*[A-Z])(?=(?:.*\d){4,})[A-Z0-9]{6,}$")

# Prezzo europeo: 90,00 / 1.234,50 (migliaia con . o spazio, decimali con ,).
_PRICE_RE = re.compile(r"^\d{1,3}(?:[.\s]\d{3})*[.,]\d{2}$")
# Quantita' (colonna PZ X CONF): intero 1-3 cifre. Scartata (RigaGrezza non la porta).
_QTY_RE = re.compile(r"^\d{1,3}$")

COL_TOL = 20.0     # px: parole-codice entro questa distanza = stesso blocco
ROW_TOL = 3.0      # px: bucket di banda-riga per l'ordinamento di lettura
CODE_MARGIN = 8.0  # px: il confine tra blocchi cade appena a sinistra del codice


def reliable_text_layer(page) -> bool:
    """True se la pagina ha un text-layer nativo affidabile (born-digital).

    Predicato di routing: riusa la soglia gia' calibrata di `needs_ocr` (una
    scansione muta ha <50 char nativi). Nessuna soglia nuova.
    """
    return not needs_ocr(page)


def _to_decimal(tok: str) -> Decimal | None:
    """Converte un token-prezzo europeo in Decimal. None se non e' un prezzo."""
    s = tok.strip()
    if not _PRICE_RE.match(s):
        return None
    # L'ultimo separatore ,/. e' il decimale; gli altri sono migliaia.
    idx = max(s.rfind(","), s.rfind("."))
    int_part = re.sub(r"\D", "", s[:idx])
    dec_part = s[idx + 1:]
    try:
        return Decimal(f"{int_part or '0'}.{dec_part}")
    except (InvalidOperation, ValueError):
        return None


def _block_boundaries(code_xs: list[float]) -> list[float]:
    """Da x0 dei codici -> confini destri dei blocchi (cluster + margine).

    I codici si clusterizzano per prossimita' (COL_TOL). Il confine tra il blocco i
    e i+1 cade a `centro_{i+1} - CODE_MARGIN`: cosi' la colonna-prezzo (a destra del
    codice) resta nel PROPRIO blocco e non sconfina in quello successivo.
    """
    centers: list[float] = []
    for x in sorted(code_xs):
        if not centers or x - centers[-1] > COL_TOL:
            centers.append(x)
    # confini = frontiere tra cluster consecutivi (uno in meno dei centri)
    return [c - CODE_MARGIN for c in centers[1:]]


def _build_riga(code: str, tokens: list[str]) -> RigaGrezza | None:
    """Costruisce una RigaGrezza dai token che seguono il codice. None -> reject.

    Codice = verbatim. Prezzo = ultimo token-prezzo. Descrizione = i restanti token
    (esclusi prezzo e quantita'). Se non c'e' ne' descrizione ne' prezzo -> reject
    (un codice isolato e' rumore/header, non una riga).
    """
    prezzo = None
    price_idx = -1
    for i in range(len(tokens) - 1, -1, -1):
        p = _to_decimal(tokens[i])
        if p is not None:
            prezzo = p
            price_idx = i
            break
    desc_tokens = [
        t for i, t in enumerate(tokens)
        if i != price_idx and not _QTY_RE.match(t) and _to_decimal(t) is None
    ]
    descrizione = base_cleanup(" ".join(desc_tokens)) or None
    if descrizione is None and prezzo is None:
        return None
    try:
        return RigaGrezza(codice_originale=code, descrizione=descrizione, prezzo=prezzo)
    except Exception:
        return None


def reconstruct_rows(
    words: list[tuple],
    *,
    code_re: re.Pattern = DEFAULT_CODE_RE,
    row_tol: float = ROW_TOL,
) -> tuple[list[RigaGrezza], list[dict]]:
    """Ricostruisce le righe di una pagina-tabella dalle parole posizionate.

    Args:
      words: tuple in stile PyMuPDF `get_text("words")` — servono (x0, y0, text)
             agli indici 0, 1, 4.
      code_re: rilevatore di token-codice (default per codici alfanumerici).
      row_tol: tolleranza px per il bucket di banda-riga (ordine di lettura).

    Ritorna: (righe valide, reject) — le reject portano codice+motivo per audit.
    Fail-Closed: nessuna riga fabbricata; l'incerto va in reject, non in DB.
    """
    code_words = [w for w in words if code_re.match((w[4] or "").strip())]
    if not code_words:
        return [], []

    boundaries = _block_boundaries([w[0] for w in code_words])

    def block_of(x0: float) -> int:
        return bisect_left(boundaries, x0)

    # partiziona le parole per blocco di colonna
    n_blocks = len(boundaries) + 1
    per_block: list[list[tuple]] = [[] for _ in range(n_blocks)]
    for w in words:
        per_block[block_of(w[0])].append(w)

    righe: list[RigaGrezza] = []
    rejects: list[dict] = []
    for block in per_block:
        # ordine di lettura dentro il blocco: banda-riga (y) poi x
        block.sort(key=lambda w: (round(w[1] / row_tol) * row_tol, w[0]))
        cur_code: str | None = None
        cur_tokens: list[str] = []
        for w in block:
            tok = (w[4] or "").strip()
            if not tok:
                continue
            if code_re.match(tok):
                if cur_code is not None:
                    _flush(cur_code, cur_tokens, righe, rejects)
                cur_code = tok
                cur_tokens = []
            elif cur_code is not None:
                cur_tokens.append(tok)
        if cur_code is not None:
            _flush(cur_code, cur_tokens, righe, rejects)

    return righe, rejects


def _flush(code: str, tokens: list[str], righe: list, rejects: list) -> None:
    riga = _build_riga(code, tokens)
    if riga is not None:
        righe.append(riga)
    else:
        rejects.append({"codice": code, "motivo": "riga_non_ricostruibile"})


class TextTableExtractor:
    """Adapter `TableExtractor` block-aware su text-layer nativo (no modello).

    Drop-in nel runner massivo (stesso Port di `FakeExtractor`/`GlmOcrExtractor`).
    Le righe scartate restano su `self.rejects` per audit (come `markdown_outputs`
    per GLM-OCR). `doc=None` e' ammesso per il solo check di Protocol (no `extract`).
    """

    def __init__(self, doc, *, code_re: re.Pattern = DEFAULT_CODE_RE):
        self._doc = doc
        self._code_re = code_re
        self.rejects: list[dict] = []

    def extract(self, ref) -> list[RigaGrezza]:
        page = self._doc.load_page(ref.page - 1)
        words = page.get_text("words")
        righe, rejects = reconstruct_rows(words, code_re=self._code_re)
        self.rejects = rejects
        return righe
