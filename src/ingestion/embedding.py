"""P2.3 — Text-prep e embedder per il dual-indexing (dense BGE-M3 + sparse BM25).

Separazione netta, per contratto:
  - le funzioni di TEXT-PREP sono pure (niente modelli, unit-testabili):
    l'embedder non vede mai chunk.text (filename/pagina = metadati di citazione)
    ne' JSON. Il denso vede SOLO la descrizione riparata; lo sparse vede
    pos + codice atomico + descrizione riparata. Qty/prezzo/doc/pagina restano
    payload, non termini di ricerca.
  - i LOADER dei modelli sono lazy (import di torch/fastembed dentro le funzioni):
    P2.4 riusera' gli stessi embedder e la stessa text-prep per le query.

Scelta sparse = BM25 (fastembed Qdrant/bm25), NON lo sparse learned di BGE-M3:
i subword XLM-R farebbero scorare alto i codici "quasi giusti" — la classe di
errore vietata dal progetto. Il modifier IDF della collezione (validato in P2.1)
e' progettato per term-frequency BM25.

ATTENZIONE — qui c'era un'AFFERMAZIONE FALSA, corretta ora: questa docstring
diceva che "BM25 tiene il codice come token ATOMICO". Non e' vero e non lo e'
mai stato: `SimpleTokenizer` di fastembed sostituisce ogni carattere non-`\w`
con uno spazio, quindi `14M-40` entrava nell'indice come DUE termini e `14M-50`
ne condivideva UNO. Sull'indice reale 16108 codici distinti su 40111 (40.2%)
contengono un separatore: era un generatore latente di "quasi giusti".
Oggi il codice entra nell'indice in ENTRAMBE le forme (frammenti + forma
incollata da `atomic_code()`): il termine incollato e' esclusivo del codice
esatto ed e' il segnale ad alto IDF che distingue i "quasi giusti", mentre i
frammenti restano perche' servono al chunk giusto per battere il rumore
(vedi `sparse_text`: toglierli ha causato una regressione misurata).
Difesa cablata in tests/test_index_identity_atomic.py::TestCodiceAtomico.
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path

# --- costanti condivise indice/query (P2.3 ingestion, P2.4 retrieval) -----------
# Indice testuale in uso. Passato a `_v2` il 24/08 dopo la riparazione del
# contenuto indicizzato (identita' del documento nel denso + codici atomici nello
# sparse): R@10 del metro narrativo 0.536 -> 0.625, golden gate IDENTICO alla
# produzione (22 PASS_ALTA / 10 PASS / 10 MISS, 0 WRONG_ALTA, zero item cambiati)
# e ramo `code` 22/22. Lo scambio e' una COSTANTE e non una manipolazione dei
# dati proprio per questo: `aike_chunks` resta intatta ed e' il rollback, che
# costa una riga.
COLLECTION = "aike_chunks_v2"
DENSE_NAME = "dense"
DENSE_MODEL = "BAAI/bge-m3"
DENSE_DIM = 1024
SPARSE_NAME = "sparse"
SPARSE_MODEL = "Qdrant/bm25"
# Stemmer BM25: unico e condiviso tra indicizzazione e query (i codici non
# passano dallo stemmer). Gestione multi-lingua fine: decisione a P2.4 sui numeri.
BM25_LANGUAGE = "italian"

# --- cache dei modelli fastembed: MAI nella temp di sistema ---------------------
# Se si chiama SparseTextEmbedding senza `cache_dir`, fastembed ripiega su
# `tempfile.gettempdir()/fastembed_cache` -> su macOS `/var/folders/<...>/T/`,
# che il SO svuota periodicamente (e su Linux `/tmp`, svuotato al reboot).
# Quando la cache viene svuotata il modello sparisce, e siccome il progetto gira
# air-gapped per scelta (`HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` in `.env`)
# fastembed non puo' riscaricarlo: solleva errore invece di ripararsi. Effetto
# osservato: `embed_sparse_query()` esplode, il ramo NARRATIVO e l'IBRIDO
# rispondono `BASSA` con testo vuoto — un guasto totale e SILENZIOSO.
# Percio' la cache sta in una directory persistente sotto la home, creata se
# manca. Override con AIKE_FASTEMBED_CACHE (deve restare fuori dalla temp).
FASTEMBED_CACHE_ENV = "AIKE_FASTEMBED_CACHE"
FASTEMBED_CACHE_DEFAULT = "~/.cache/fastembed"


def fastembed_cache_dir() -> Path:
    """Directory persistente dei modelli fastembed (creata se manca)."""
    raw = os.getenv(FASTEMBED_CACHE_ENV) or FASTEMBED_CACHE_DEFAULT
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_under_system_temp(path: Path) -> bool:
    """True se `path` cade nella temp del SO (cache volatile: vedi sopra)."""
    resolved = Path(path).expanduser().resolve()
    candidates = {Path(tempfile.gettempdir()), Path("/tmp"), Path("/var/folders")}
    for base in candidates:
        try:
            resolved.relative_to(Path(base).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False

# ------------------------------------------------------------------- text-prep

# Riparazione deterministica dello small-caps mangled di Docling su P231
# ('B oCCa /S PoUt' -> 'bocca /spout'). Due sole regole sicure:
#   R1: lettera singola MAIUSCOLA + token che inizia con lettera -> join
#       ('B oCCa', 'C HroMe', 'F ixinG').
#   R2: CONSONANTE singola minuscola + token che inizia con lettera -> join
#       ('d eviatore', 'l eva', 't erMoStatiCo'). Le vocali singole e la 'x'
#       sono parole legittime IT ('cromo e oro', 'Raccordo x flessibile') e
#       non si toccano qui.
#   R3: VOCALE singola minuscola + token con una MAIUSCOLA in posizione >=1
#       ('a CCeSSori', 'e PoCa'): il misto-maiuscole interno e' la firma del
#       mangling, una parola vera dopo 'e'/'a' non ce l'ha ('e oro', 'a Testa'
#       restano intatte). Niente dizionari, niente indovinare: il mangling che
#       queste regole non coprono resta e lo misura P2.4.
_ACC = "àèéìíòóùú"
_R1 = re.compile(rf"(?<![0-9A-Za-z{_ACC}])([A-Z]) (?=[A-Za-z{_ACC}])")
_R2 = re.compile(rf"(?<![0-9A-Za-z{_ACC}])([b-df-hj-np-tv-wyz]) (?=[A-Za-z{_ACC}])")
_R3 = re.compile(rf"(?<![0-9A-Za-z{_ACC}])([aeiou]) (?=[A-Za-z][a-z{_ACC}]*[A-Z])")


def repair_desc(desc: str | None) -> str | None:
    """Ripara il mangling e normalizza a minuscolo. La desc originale resta
    intatta nel payload: questa forma serve solo come input di ricerca."""
    if not desc:
        return None
    s = desc
    for _ in range(4):  # fixpoint: sequenze tipo 'S tyloS -C roMo' in un colpo
        new = _R3.sub(r"\1", _R2.sub(r"\1", _R1.sub(r"\1", s)))
        if new == s:
            break
        s = new
    return re.sub(r"\s+", " ", s).lower().strip() or None


# --- identita' del documento (canale DENSO) ------------------------------------
# Estensioni da togliere dallo stem: 218 chunk reali hanno il doc con estensione
# ('Mamoli_..._IT_EN.pdf', 'Electrolux_Distinta_Ricambi.xlsx'). Senza questo,
# 'pdf'/'xlsx' diventano token presenti SOLO in quei documenti: rumore che
# discrimina per motivi sbagliati.
_DOC_EXT = (".pdf", ".xlsx", ".xls", ".csv", ".docx", ".doc")


def doc_identity(chunk: dict) -> str | None:
    """Identita' leggibile del documento, dal suo stem: 'westermann_kehrmaschine'
    -> 'westermann kehrmaschine'.

    Sorgente = `doc` (presente su TUTTI i 199447 punti), non `doc_stem` (nullo
    su 32973 punti della vecchia ingestion). Nessuna lettura da Postgres: la
    marca sta gia' dentro lo stem in tutti i 119 documenti del corpus, e una
    dipendenza dal registro renderebbe la text-prep non piu' pura/testabile.
    """
    raw = chunk.get("doc") or chunk.get("doc_stem")
    if not raw:
        return None
    low = str(raw)
    for ext in _DOC_EXT:
        if low.lower().endswith(ext):
            low = low[: -len(ext)]
            break
    return re.sub(r"\s+", " ", low.replace("_", " ")).lower().strip() or None


def dense_text(chunk: dict) -> str | None:
    """Input del canale SEMANTICO: identita' del documento + descrizione riparata.

    L'IDENTITA' e' la riparazione principale di questo canale. Prima c'era solo
    la descrizione della riga: l'utente che scrive "spazzatrice Westermann" non
    poteva raggiungere `westermann_kehrmaschine`, perche' in TUTTO quel
    documento i token 'westermann'/'kehrmaschine' non comparivano mai nel testo
    indicizzato (23 documenti = 114050 chunk = 74.6% dell'indice hanno identita'
    assente o quasi). Misurato con BGE-M3 reale (2114 chunk, 7 query):
      - query che centrano il documento giusto: 3/7 -> 7/7, tutte a rango 1;
      - separazione fra documenti: 0.106 -> 0.264 (raddoppia);
      - discriminazione DENTRO il documento: MRR 0.692 -> 0.727,
        top-1 52.7% -> 60.7% su 150 prove (migliora, non peggiora).

    NIENTE PAGINA: misurato, aggiungerla al denso PEGGIORA. Niente codice
    (subword-rumore: dei codici si occupa BM25), niente pos/qty/prezzo.

    Senza descrizione -> None, invariato: 13125 chunk avrebbero un denso fatto
    della SOLA identita', identico per tutto il documento — un attrattore che
    sommergerebbe le righe vere.
    """
    desc = repair_desc(chunk.get("desc"))
    if not desc:
        return None
    ident = doc_identity(chunk)
    return f"{ident}: {desc}" if ident else desc


# --- codice atomico (canale LESSICALE) -----------------------------------------
_NOT_ALNUM = re.compile(r"[^0-9a-z]+")
# Un token di query e' "codice" se ha una cifra e un separatore -/. ma NESSUNA
# virgola: sul corpus reale nessuno dei 40111 codici contiene una virgola,
# mentre i prezzi sono tutti in formato europeo ('20,00', '1.234,50'). La
# virgola e' quindi il discriminante esatto fra codice e prezzo.
_QUERY_CODEY = re.compile(r"^(?=[^,]*$)(?=.*\d)(?=.*[-/.]).+$")
_STRIP_PUNCT = "?!;:()[]{}\"'«»"


def atomic_code(code: str | None) -> str | None:
    """Il codice come UN SOLO termine BM25: 'HD3-ES-1C/20' -> 'hd3es1c20'.

    Motivo: `SimpleTokenizer` di BM25 spezza su ogni non-`\w`, quindi
    'HD3-ES-1C/20' e 'HD3-ES-0C/20' condividevano 3 termini su 4 — un
    generatore di "quasi giusti". Con l'incollaggio ne condividono ZERO.

    Sicurezza dell'incollaggio, verificata sui 40111 codici distinti reali:
    produce solo 19 collisioni, e 18 sono lo STESSO ricambio scritto in due
    modi ('18111-67JG3' / '1811167JG3', 'CS-12' / 'CS12'): unirli e' un
    guadagno, non una perdita. Nessuna coppia di ricambi DIVERSI collide.
    """
    if not code:
        return None
    return _NOT_ALNUM.sub("", str(code).lower()) or None


def sparse_query_text(query: str) -> str:
    """Prepara una QUERY per il canale lessicale aggiungendo la forma incollata
    dei token che sono codici. ADDITIVA per contratto: i termini originali
    restano, quindi la stessa funzione continua a funzionare identica contro un
    indice con i codici ancora frantumati (un termine mai visto contribuisce 0
    al punteggio BM25 di ogni documento).

    E' l'unico punto in cui la preparazione del testo tocca una query: NON usa
    `dense_text`/`sparse_text` (che aggiungono l'identita' del DOCUMENTO e
    contaminerebbero la domanda dell'utente). Guardia cablata in
    tests/test_index_identity_atomic.py::TestTextPrepMaiAQueryTime.
    """
    if not query:
        return query
    extra: list[str] = []
    seen: set[str] = set()
    for tok in query.split():
        bare = tok.strip(_STRIP_PUNCT)
        if not bare or not _QUERY_CODEY.match(bare):
            continue
        glued = atomic_code(bare)
        if glued and len(glued) >= 4 and glued != bare.lower() and glued not in seen:
            seen.add(glued)
            extra.append(glued)
    return f"{query} {' '.join(extra)}" if extra else query


def sparse_text(chunk: dict) -> str:
    """Input del canale LESSICALE: pos + codice (frammenti E forma incollata)
    + descrizione riparata.

    ADDITIVO, simmetrico a `sparse_query_text`. Una prima versione metteva SOLO
    la forma incollata, per ottenere che due codici "quasi giusti" non
    condividessero NESSUN termine. Era troppo forte, e ha prodotto una
    regressione REALE, misurata sul golden set: C004 ("codice M125A-08-80")
    passava da rango 1 a NESSUN risultato. Catena causale:
      - il chunk giusto, ridotto al solo 'm125a0880', condivideva con la query
        1 SOLO termine (contro i 4 di prima);
      - i chunk zuppa-di-numeri ('Item Part No. Description Qty. | 1 2 3')
        continuavano invece a farsi colpire piu' volte dai frammenti '08'/'80'
        della query, e vincevano 26.55 contro 15.996.
    Togliere i frammenti al DOCUMENTO ha disarmato il chunk giusto lasciando
    armato il rumore. Con entrambe le forme: termini in comune con la query
    4 invece di 1, e C004 torna a rango 1.

    L'atomicita' che serve NON si perde. Non serve che i frammenti spariscano,
    serve che esista un termine che DISCRIMINA: '14M-40' e '14M-50' condividono
    1 termine su 3 ('14m'), ma il termine incollato ('14m40' / '14m50') e'
    esclusivo di ciascuno, ed essendo rarissimo e' quello ad alto IDF che decide
    il ranking. Vedi tests/test_index_identity_atomic.py::TestCodiceAtomico.

    NIENTE identita' del documento qui: nello sparse gonfierebbe la term
    frequency di 'westermann' su ogni chunk del documento, spostando l'IDF
    della collezione senza aggiungere informazione. L'identita' e' un fatto
    semantico e sta nel denso.
    """
    code = chunk.get("code")
    glued = atomic_code(code)
    parts = [chunk.get("pos"), code]
    # la forma incollata si aggiunge solo se dice qualcosa di nuovo: per i
    # 24003 codici gia' senza separatori ripeterla raddoppierebbe la term
    # frequency rispetto agli altri, falsando il confronto fra codici.
    if glued and code and glued != str(code).lower():
        parts.append(glued)
    parts.append(repair_desc(chunk.get("desc")))
    return " ".join(p for p in parts if p)


def point_id(chunk_id: str) -> str:
    """ID punto deterministico: stesso chunk = stesso punto, sempre."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


# ------------------------------------------------------------- modelli (lazy)


@lru_cache(maxsize=1)
def get_dense_model():
    from FlagEmbedding import BGEM3FlagModel

    return BGEM3FlagModel(DENSE_MODEL, use_fp16=False)


@lru_cache(maxsize=4)
def get_sparse_model(language: str | None = None):
    """Modello BM25. `language` sceglie SOLO lo stemmer/stopword; il default
    resta `BM25_LANGUAGE` (invariato) — il parametro serve a misurare in A/B
    lo stemmer su un indice parallelo senza toccare la produzione."""
    from fastembed import SparseTextEmbedding

    return SparseTextEmbedding(
        SPARSE_MODEL, language=language or BM25_LANGUAGE,
        cache_dir=str(fastembed_cache_dir())
    )


def embed_dense(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    out = get_dense_model().encode(texts, batch_size=batch_size, max_length=512)
    return [v.tolist() for v in out["dense_vecs"]]


def embed_sparse_docs(texts: list[str], language: str | None = None):
    """Embedding sparse lato documento (term frequencies; l'IDF lo applica Qdrant)."""
    return list(get_sparse_model(language).embed(texts))


def embed_sparse_query(text: str, language: str | None = None):
    """Embedding sparse lato query (stessa tokenizzazione/stemmer dell'indice)."""
    return list(get_sparse_model(language).query_embed(text))[0]
