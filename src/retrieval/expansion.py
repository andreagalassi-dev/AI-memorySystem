"""Debito §2 — Query Expansion DETERMINISTICA (dizionario sinonimi a DB).

Problema (thecnical-debt-analyst.md §2): il canale BM25 da' 0 sulle query
naturali cross-lingua ("piston pin" vs "spinotto pistone") e il denso da solo
non genera margine -> rifiuto BASSA. Qui la query viene espansa coi termini
fratelli del dizionario tecnico (Postgres, seed config/synonyms.json): niente
LLM a query-time, stessa filosofia del router (deterministico, auditabile).

Matching: n-grammi della query (3->1, longest-first, span consumati) contro i
termini approvati; l'espansione sono i termini fratelli dei concetti colpiti,
esclusi quelli gia' presenti nella query, cap MAX_TERMS. I termini vanno SOLO
nel canale sparse della route 'natural' (search.py): dense e reranker restano
sulla query originale; le route 'code'/'mixed' non sono MAI espanse.

Caricamento: cache di processo + reload_synonyms() (pattern brands.py). Il
degrado e' gestito dal chiamante (nodo grafo: NON-fatale, come fusion_node):
qui si solleva RuntimeError con rimedio chiaro.

LIMITE MISURATO (A/B 2026-07-05, adottato con questa riserva): l'espansione
migliora il recall dei CANDIDATI (naturali R@10 0.465->0.512, IT 0.421->0.500)
ma R@1 resta fermo: il collo di bottiglia a valle e' il RERANKER, che non
porta in cima i candidati recuperati (es. W003 arriva a rank 2). TODO futuro:
lavorare sul ranking (fine-tuning reranker o segnale lessicale nel riordino).
Regressione singola nota: W046 (DE 'Dichtring'->O-Ring, 10->None).
  AGGIORNAMENTO 22/08 — quella nota e' SUPERATA dalla misura: oggi
  l'espansione AIUTA W046 (canale sparse None->137, fuso 283->183). Il
  danno da espansione esiste ancora, ma su un ALTRO item: W048 (DE), dove
  gli 8 termini aggiunti diluiscono l'unico segnale utile e il bersaglio
  passa da rango 873 (fuso 1600, senza espansione) a NESSUNO. Morale
  operativa: l'espansione non si adotta ne' si scarta in blocco, si misura
  per item.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from functools import lru_cache

MAX_TERMS = 8
MAX_NUMBER_TERMS = 12
_NGRAM_MAX = 3
_WORD = re.compile(r"[0-9a-zà-ÿäöüß*./-]+")

# Guardia anti-diluizione (misurata su eval: W042 1->None): una query con un
# token code-like NON va espansa — il router puo' non riconoscere un codice
# fuori registro famiglie (es. '9S1BG0270') e l'espansione sommergerebbe
# l'unico segnale lessicale esatto. Stessa definizione conservativa del
# rilevatore default dei profili brand (una sola fonte di verita').
from src.ingestion.brands import DEFAULT_CODEY  # noqa: E402


def _load_from_db() -> dict:
    import psycopg

    from src.backend.clients.postgres_client import get_pg_conn
    from src.registry.synonyms import load_synonyms
    try:
        with get_pg_conn() as conn:
            return load_synonyms(conn, only_approved=True)
    except psycopg.errors.UndefinedTable as e:
        raise RuntimeError(
            "Dizionario sinonimi assente: eseguire "
            "`uv run scripts/seed_synonyms.py`") from e
    except psycopg.OperationalError as e:
        raise RuntimeError(
            "Dizionario sinonimi non caricabile: Postgres irraggiungibile "
            "(docker compose up -d postgres)") from e


@lru_cache(maxsize=1)
def _index() -> dict[str, list[tuple[str, tuple[tuple[str, str], ...]]]]:
    """term -> lista di (slug, ( (term1, lang1), ... ) )."""
    idx: dict[str, list[tuple[str, tuple[tuple[str, str], ...]]]] = {}
    for slug, c in _load_from_db().items():
        terms = tuple((t, lang) for t, lang in c["terms"])
        for t, _l in terms:
            idx.setdefault(t, []).append((slug, terms))
    return idx


def reload_synonyms() -> None:
    """Invalida la cache di processo (test, aggiornamenti del dizionario)."""
    _index.cache_clear()


def _tokens(text: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFKC", text or "").lower())


def expand_query(query: str, max_terms: int = MAX_TERMS, target_lang: str | None = None) -> list[str]:
    """Termini di espansione per la query (deterministici, ordinati, cap).

    Longest-match-first: un n-gramma che matcha consuma i suoi token (nessun
    doppio conteggio del singolo token dentro il multi-parola). Selezione
    ROUND-ROBIN tra i concetti colpiti: il cap non deve mai far cadere tutti
    i termini di un concetto a favore delle sole varianti del primo
    (misurato su eval: 'o-ring' e 'filtro' tagliati -> W046/W003 persi).
    
    Se `target_lang` e' specificato, restituisce SOLO i sinonimi in quella lingua
    (utile per query translation IT->EN).

    Ai sinonimi si SOMMANO (mai sostituiscono) le forme in cifre dei numeri
    scritti a parole, vedi `expand_number_words`. Due limiti voluti:
      - con `target_lang` NON si aggiungono: quel percorso serve al ramo
        VISIVO (pixelrag/search.py traduce la query per l'embedder), non al
        canale sparse, e non e' lo scopo di questa modifica;
      - la guardia code-like a inizio funzione vale per entrambi i meccanismi:
        se la query contiene gia' un token esatto tipo codice, non la si
        diluisce (verificato: nessuna query a numeri-parola la fa scattare).
    """
    toks = _tokens(query)
    if any(DEFAULT_CODEY.match(t.upper()) for t in toks):
        return []   # possibile codice fuori registro: mai diluirlo
    idx = _index()
    have = set(toks)

    matched: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    seen_slugs: set[str] = set()
    used = [False] * len(toks)
    for n in range(_NGRAM_MAX, 0, -1):
        for i in range(len(toks) - n + 1):
            if any(used[i:i + n]):
                continue
            gram = " ".join(toks[i:i + n])
            for slug, terms in idx.get(gram, []):
                if slug not in seen_slugs:
                    seen_slugs.add(slug)
                    matched.append((slug, terms))
                used[i:i + n] = [True] * n

    # candidati per concetto (senza i termini gia' in query), poi round-robin
    per_concept = []
    for _slug, terms in matched:
        cand = [t for t, lang in terms if not set(t.split()) <= have and (target_lang is None or lang == target_lang)]
        if cand:
            per_concept.append(cand)
    out: list[str] = []
    for rank in range(max(map(len, per_concept), default=0)):
        for cand in per_concept:
            if len(out) >= max_terms:
                break
            if rank < len(cand) and cand[rank] not in out:
                out.append(cand[rank])
        if len(out) >= max_terms:
            break
    if target_lang is not None:
        return out
    # cap SEPARATO: i numeri non devono ne' rubare posti ai sinonimi ne'
    # esserne sfrattati — sono due segnali diversi sullo stesso canale.
    return out + [t for t in expand_number_words(query) if t not in out]


# ===================================================================== numeri
# NUMERI SCRITTI A PAROLE -> forme in cifre presenti nel corpus.
#
# Il guasto: l'utente detta la misura ("vite testa svasata CINQUE PER TRENTA"),
# il documento la scrive in cifre ("VITE M5x30 UNI 7688 ZINC NERO"). Ne' BM25
# (token atomico 'm5x30') ne' il cross-encoder fanno quel ponte: la query
# manca il documento anche quando il documento c'e'.
#
# PERCHE' UNA LISTA A MANO E' LEGITTIMA QUI, e non lo era la stoplist di
# dominio che in questo progetto abbiamo gia' tagliato due volte: questa non e'
# un'opinione su quali parole contino, e' una BIIEZIONE COL SISTEMA NUMERICO.
# I nomi dei numeri 0-100 in IT/EN/DE sono un insieme CHIUSO, FINITO e
# ESAUSTIVO per costruzione (le decine composte si generano dalla regola, non
# si scelgono), quindi verificabile a unit test riga per riga. Una stoplist,
# al contrario, e' aperta e arbitraria: nessuno puo' dire quando e' completa.
#
# LE FORME EMESSE NON SONO INDOVINATE: sono state lette nel corpus indicizzato
# e verificate contro la tokenizzazione BM25 realmente in uso (fastembed
# Qdrant/bm25, stemmer 'italian'). Cosa si e' misurato:
#   'VITE M5x30 UNI 7688'  -> 'm5x30' e' UN SOLO token: solo la forma con la M
#                             attaccata aggancia; '5x30' NON aggancia.
#   'O-Ring 25 x 3,2mm'    -> tokens '25','x','3','2mm': solo la forma
#                             SPAZIATA '25 x 3,2' aggancia; '25x3,2' no.
#   'BOLT M12×100'         -> '×' e' un separatore: 'm12' + '100'; serve la
#                             forma spaziata con la M ('m12 x 100').
#   'SCHLAUCH 4X7'         -> '4x7' e' un solo token: la forma unita aggancia.
#   'MOLLA DIAM 8.5X26'    -> '.' e ',' sono separatori equivalenti, quindi
#                             '8,5' e '8.5' producono gli STESSI token: si
#                             emette una sola variante decimale.
# Da qui le quattro forme per coppia ('AxB', 'A x B', 'mAxB', 'mA x B'): non
# sono ridondanza, sono quattro tokenizzazioni diverse dello stesso fatto.
#
# FAIL-SAFE PER COSTRUZIONE: sono termini AGGIUNTIVI sul solo canale sparse
# (vedi search.py: la concatenazione BM25 E' un OR). La query dell'utente non
# viene mai riscritta; dense e reranker non la vedono. Il costo di un falso
# positivo e' quindi basso, e su questo si e' scelto:
#
# SCELTA DICHIARATA — non si prova a distinguere una misura da un numero
# qualunque ("le prime CINQUE pagine" emette comunque '5'). Un criterio
# robusto per farlo non esiste (servirebbe capire la frase), e sbagliarlo
# costerebbe recall vero per evitare rumore poco costoso. Unica eccezione,
# perche' misurabile e grossolana: gli ARTICOLI INDETERMINATIVI ('un', 'uno',
# 'una', 'ein', 'eine', 'one'...) non emettono mai da soli — in italiano
# 'un/una' sono articoli in quasi ogni frase, e '1' e' un token frequente.
# Restano validi come membri di una coppia dimensionale.

# ---- lessici: unita' 0-19 e decine, per lingua ------------------------------

_IT_UNITS = {"zero": 0, "uno": 1, "un": 1, "una": 1, "due": 2, "tre": 3,
             "quattro": 4, "cinque": 5, "sei": 6, "sette": 7, "otto": 8,
             "nove": 9, "dieci": 10, "undici": 11, "dodici": 12,
             "tredici": 13, "quattordici": 14, "quindici": 15, "sedici": 16,
             "diciassette": 17, "diciotto": 18, "diciannove": 19}
_IT_TENS = {"venti": 20, "trenta": 30, "quaranta": 40, "cinquanta": 50,
            "sessanta": 60, "settanta": 70, "ottanta": 80, "novanta": 90}

_EN_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
             "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
             "nineteen": 19}
_EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
            "seventy": 70, "eighty": 80, "ninety": 90}

_DE_UNITS = {"null": 0, "eins": 1, "ein": 1, "eine": 1, "zwei": 2, "drei": 3,
             "vier": 4, "fünf": 5, "fuenf": 5, "sechs": 6, "sieben": 7,
             "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12,
             "zwoelf": 12, "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15,
             "fuenfzehn": 15, "sechzehn": 16, "siebzehn": 17, "achtzehn": 18,
             "neunzehn": 19}
_DE_TENS = {"zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40,
            "fünfzig": 50, "fuenfzig": 50, "sechzig": 60, "siebzig": 70,
            "achtzig": 80, "neunzig": 90}

# Articoli indeterminativi: numeri validi, ma da soli quasi sempre articoli.
_ARTICLES = {"un", "uno", "una", "ein", "eine", "einen", "einem", "einer",
             "eines", "one"}

# Connettivi di DIMENSIONE ('cinque PER trenta', 'vier AUF sieben').
_DIM_CONN = {"per", "x", "by", "auf", "mal"}
# Connettivi DECIMALI ('drei KOMMA zwei').
_DEC_CONN = {"virgola", "komma", "point"}


def _build_number_lexicon() -> dict[str, int]:
    """Nomi dei numeri -> valore. GENERATO dalla regola di composizione delle
    decine, non elencato a mano: e' questo che rende la lista esaustiva per
    costruzione invece che un'opinione."""
    lex: dict[str, int] = {}
    lex.update(_IT_UNITS)
    lex.update(_EN_UNITS)
    lex.update(_DE_UNITS)
    lex.update(_IT_TENS)
    lex.update(_EN_TENS)
    lex.update(_DE_TENS)
    lex.update({"cento": 100, "hundred": 100, "hundert": 100})

    # italiano: elisione della vocale finale davanti a 1 e 8 (ventuno,
    # ventotto); 'tre' finale accentato (ventitre / ventitré).
    it_unit_word = {v: k for k, v in _IT_UNITS.items()
                    if 1 <= v <= 9 and k not in ("un", "una")}
    for tw, tv in _IT_TENS.items():
        for uv in range(1, 10):
            uw = it_unit_word[uv]
            stem = tw[:-1] if uv in (1, 8) else tw
            lex[stem + uw] = tv + uv
            if uv == 3:
                lex[stem + "tré"] = tv + 3

    # inglese: 'twenty-one' arriva qui gia' come 'twenty one' (il trattino e'
    # normalizzato a spazio) e viene composto a runtime da _parse_number.

    # tedesco: unita' + 'und' + decina, parola unica ('fünfundzwanzig').
    de_unit_word = {1: ["ein"], 2: ["zwei"], 3: ["drei"], 4: ["vier"],
                    5: ["fünf", "fuenf"], 6: ["sechs"], 7: ["sieben"],
                    8: ["acht"], 9: ["neun"]}
    for tw, tv in _DE_TENS.items():
        for uv, uws in de_unit_word.items():
            for uw in uws:
                lex[uw + "und" + tw] = tv + uv
    return lex


_NUMBER_WORDS = _build_number_lexicon()
_NUM_TOKEN = re.compile(r"\d+(?:[.,]\d+)?|[a-zà-ÿäöüß']+|[×x*]")


def _num_tokens(text: str) -> list[str]:
    """Tokenizzazione del normalizzatore: '-' diventa spazio (twenty-five),
    '×' e '*' diventano il connettivo 'x' (M12×100, M6*16)."""
    s = unicodedata.normalize("NFKC", text or "").lower()
    s = s.replace("-", " ").replace("×", " x ").replace("*", " x ")
    return _NUM_TOKEN.findall(s)


class _Num:
    __slots__ = ("value", "from_words", "start", "end", "article_only")

    def __init__(self, value: Decimal, from_words: bool, start: int, end: int,
                 article_only: bool = False):
        self.value = value
        self.from_words = from_words
        self.start = start
        self.end = end
        self.article_only = article_only


def _word_value(toks: list[str], i: int) -> tuple[int, int] | None:
    """(valore, token consumati) per un numero a parole a partire da toks[i]."""
    t = toks[i]
    # inglese composto su due token: 'twenty five'
    if t in _EN_TENS and i + 1 < len(toks) and toks[i + 1] in _EN_UNITS:
        u = _EN_UNITS[toks[i + 1]]
        if 1 <= u <= 9:
            return _EN_TENS[t] + u, 2
    v = _NUMBER_WORDS.get(t)
    return (v, 1) if v is not None else None


def _parse_number(toks: list[str], i: int) -> _Num | None:
    """Un numero a partire da toks[i]: cifre o parole, con eventuale parte
    decimale ('drei komma zwei') o mezzo ('otto e mezzo')."""
    t = toks[i]
    if t[0].isdigit():
        base, from_words, j = Decimal(t.replace(",", ".")), False, i + 1
        article = False
    else:
        wv = _word_value(toks, i)
        if wv is None:
            return None
        base, from_words = Decimal(wv[0]), True
        j = i + wv[1]
        article = wv[1] == 1 and t in _ARTICLES

    # parte decimale esplicita: 'virgola/komma/point' + numero
    if j + 1 < len(toks) and toks[j] in _DEC_CONN:
        nxt = _parse_number(toks, j + 1)
        if nxt is not None and nxt.value == nxt.value.to_integral_value():
            base = Decimal(f"{base}.{int(nxt.value)}")
            from_words = from_words or nxt.from_words
            j = nxt.end
            article = False
    # mezzo: 'otto e mezzo', 'and a half', 'und ein halb'
    elif j + 1 < len(toks) and toks[j] in ("e", "und") \
            and toks[j + 1] in ("mezzo", "mezza", "halb"):
        base, j, article = base + Decimal("0.5"), j + 2, False
    elif (j + 2 < len(toks) and toks[j] == "and" and toks[j + 1] == "a"
          and toks[j + 2] == "half"):
        base, j, article = base + Decimal("0.5"), j + 3, False

    return _Num(base, from_words, i, j, article)


def _fmt(v: Decimal) -> str:
    """Cifre come le scrive il corpus: intero secco, decimale con la virgola
    (per il tokenizer BM25 ',' e '.' sono separatori equivalenti — misurato)."""
    if v == v.to_integral_value():
        return str(int(v))
    return format(v.normalize(), "f").replace(".", ",")


def expand_number_words(query: str | None) -> list[str]:
    """Numeri scritti a parole -> le forme in cifre che il corpus usa davvero.

    Deterministica, senza LLM, senza I/O. Ritorna termini AGGIUNTIVI (mai una
    riscrittura della query) o [] se non c'e' niente da normalizzare. Vedi il
    blocco di commento sopra per il metodo e le scelte dichiarate.

    >>> expand_number_words("vite testa svasata cinque per trenta")
    ['5x30', '5 x 30', 'm5x30', 'm5 x 30']
    """
    toks = _num_tokens(query)
    if not toks:
        return []

    nums: list[_Num] = []
    i = 0
    while i < len(toks):
        n = _parse_number(toks, i)
        if n is None:
            i += 1
            continue
        nums.append(n)
        i = n.end

    out: list[str] = []
    have = set(toks)

    def emit(term: str) -> None:
        if term not in out and term not in have:
            out.append(term)

    paired: set[int] = set()
    for k in range(len(nums) - 1):
        a, b = nums[k], nums[k + 1]
        # coppia dimensionale: esattamente un connettivo tra i due numeri
        if b.start - a.end != 1 or toks[a.end] not in _DIM_CONN:
            continue
        if not (a.from_words or b.from_words):
            continue    # gia' in cifre: non c'e' niente da tradurre
        paired.update((k, k + 1))
        av, bv = _fmt(a.value), _fmt(b.value)
        emit(f"{av}x{bv}")
        emit(f"{av} x {bv}")
        if a.value == a.value.to_integral_value() \
                and b.value == b.value.to_integral_value():
            # 'M' delle filettature metriche: solo su coppie intere (M5x30)
            emit(f"m{av}x{bv}")
            emit(f"m{av} x {bv}")

    for k, n in enumerate(nums):
        if k in paired or not n.from_words or n.article_only:
            continue
        emit(_fmt(n.value))

    return out[:MAX_NUMBER_TERMS]
