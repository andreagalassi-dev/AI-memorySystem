"""Epica H — igienizzazione delle fonti web e guardia sulle citazioni.

IL MODELLO DI MINACCIA, detto chiaro. Il contenuto del corpus lo abbiamo
ingerito noi; il contenuto del web lo scrive un TERZO SCONOSCIUTO, che puo'
averlo scritto APPOSTA per noi. Una pagina puo' contenere: la chiusura del
nostro tag (`</fonte_web>`) per evadere dal recinto; "IGNORA LE ISTRUZIONI
PRECEDENTI" per dirottare il modello; caratteri invisibili o di inversione
bidirezionale per far leggere all'umano una cosa e al modello un'altra; un
megabyte di testo per sfondare il contesto; una citazione FINTA in formato
corpus, `(documento, pag. 12)`, per farsi passare per una nostra fonte.

DUE STRATI, E SOLO IL SECONDO E' UNA DIFESA PROVATA.

  1. IN INGRESSO — confinamento. Stessa disciplina di `format_evidence_xml`
     (src/retrieval/synthesis.py), irrigidita: normalizzazione deterministica
     NON-LLM (via i caratteri di controllo, gli zero-width U+200B-200F e gli
     override bidirezionali U+202A-202E/U+2066-2069, NFKC, collasso spazi),
     niente HTML — mai — solo testo, cap duro per fonte e sul NUMERO di fonti.
     Questo strato rende il testo inerte come DATO, ma non impedisce a un
     modello di farsi convincere da cio' che c'e' scritto dentro.

  2. IN USCITA — `verify_web_citations`. E' qui che si vince. Sopravvive solo
     un URL che abbiamo EFFETTIVAMENTE recuperato; ogni altro sparisce dalla
     lista E dal testo mostrato. E' la lezione gia' scritta a
     synthesis.py:157: "l'unica difesa provata e' questa guardia, che non
     dipende da come il modello si comporta". Se l'iniezione riesce a
     persuadere il modello, il suo prodotto (un URL inventato, una fonte
     autorevole mai vista) muore comunque qui, deterministicamente.

Sotto il vincolo sovrano di CLAUDE.md: mai un'affermazione senza fonte, e una
fonte non recuperata non e' una fonte.
"""

from __future__ import annotations

import html
import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import NamedTuple
from urllib.parse import urlsplit, urlunsplit
from xml.sax.saxutils import escape, quoteattr

# Un'unica grammatica per la citazione-corpus in tutto il progetto: la
# riusiamo invece di riscriverla, cosi' non puo' divergere da quella che il
# resto del sistema riconosce. (Definita in synthesis.py, sola lettura.)
from src.retrieval.synthesis import _CITE as CORPUS_CITE_RE

logger = logging.getLogger(__name__)

# --- cap duri ----------------------------------------------------------------
# Tarati per stare comodi nel context cap locale (16k): 5 fonti * ~800 char.
MAX_WEB_SOURCES = 5
MAX_TITLE_CHARS = 200
MAX_SNIPPET_CHARS = 600
MAX_URL_CHARS = 300
TRUNCATION_MARK = " […]"

# Marcatore visibile al posto di una citazione-corpus FINTA trovata dentro una
# fonte web. Lasciamo un segno invece di cancellare in silenzio: chi legge il
# prompt deve poter vedere che qualcuno ha provato a spacciarsi per il corpus.
CORPUS_CITE_MARK = "[citazione corpus non verificabile, rimossa]"

# Forma canonica con cui il sistema cita una fonte web.
WEB_CITE_TEMPLATE = "(web: {url})"


# ------------------------------------------------------- normalizzazione testo

_TAG_RE = re.compile(r"<[^>]{0,4000}>")

# Zero-width, override bidirezionali, isolati, BOM, soft hyphen: caratteri che
# non si vedono ma cambiano cio' che si legge. Fuori tutti, esplicitamente.
_INVISIBLE = (
    "\u00ad"                                      # soft hyphen
    "\u200b\u200c\u200d\u200e\u200f"                # zero-width + LRM/RLM
    "\u202a\u202b\u202c\u202d\u202e"                # override bidirezionali
    "\u2060\u2061\u2062\u2063\u2064"                # word joiner e invisible ops
    "\u2066\u2067\u2068\u2069"                      # isolati bidirezionali
    "\ufeff\ufffe"                                  # BOM / non-carattere
)
_INVISIBLE_RE = re.compile("[" + re.escape(_INVISIBLE) + "]")

# Separatori di riga/paragrafo Unicode: diventano spazio, non restano righe.
_LINE_SEPARATORS = "\u2028\u2029"  # LINE/PARAGRAPH SEPARATOR


def _strip_controls(text: str) -> str:
    """Via i caratteri di controllo (categoria Cc) e i formattatori (Cf).

    Tab/newline diventano spazio — il testo di una fonte e' UNA riga: cosi' non
    puo' fabbricare struttura (finti a-capo, finte righe di tag) dentro il
    prompt."""
    out = []
    for ch in text:
        if ch in "\t\n\r" or ch in _LINE_SEPARATORS:
            out.append(" ")
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        out.append(ch)
    return "".join(out)


def sanitize_web_text(raw: object, *, cap: int = MAX_SNIPPET_CHARS) -> str:
    """Normalizza un frammento di testo web. Deterministica, nessun LLM.

    Ordine (conta): de-escape delle entita' HTML -> via i tag -> NFKC -> via
    invisibili e controlli -> via le citazioni-corpus fabbricate -> collasso
    degli spazi -> cap duro.

    Il de-escape PRIMA dello strip dei tag e' voluto: `&lt;script&gt;` deve
    diventare `<script>` per poter essere tolto come tag, altrimenti passerebbe
    travestito. Il rischio inverso (de-escape che introduce `<`) e' coperto
    perche' lo strip viene dopo, e perche' al montaggio XML tutto viene
    comunque ri-escapato da `escape()`.
    """
    if raw is None:
        return ""
    text = raw if isinstance(raw, str) else str(raw)

    # Doppia codifica (&amp;lt;script&amp;gt;): due giri bastano e sono limitati,
    # cosi' il de-escape non e' un ciclo pilotabile dall'attaccante.
    for _ in range(2):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped

    text = _TAG_RE.sub(" ", text)
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE_RE.sub("", text)
    text = _strip_controls(text)
    text = CORPUS_CITE_RE.sub(CORPUS_CITE_MARK, text)
    text = " ".join(text.split())

    if len(text) > cap:
        text = text[:cap].rstrip() + TRUNCATION_MARK
    return text


def sanitize_url(raw: object) -> str | None:
    """URL utilizzabile come fonte, oppure None.

    Solo http/https (uno `javascript:` o un `data:` in una "fonte" non e' una
    fonte, e' un vettore), niente invisibili, niente spazi, cap di lunghezza.
    """
    if not isinstance(raw, str):
        return None
    url = _INVISIBLE_RE.sub("", _strip_controls(raw)).strip()
    if not url or len(url) > MAX_URL_CHARS:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return url


# --------------------------------------------------- confronto fra URL (chiave)

def url_key(raw: object) -> str | None:
    """Chiave confrontabile di un URL; None se inutilizzabile.

    Normalizza SOLO cio' che e' equivalente per definizione: schema e host in
    minuscolo, porta di default tolta, frammento `#...` tolto (non cambia il
    documento), slash finale tolto. NON tocca il path (case-sensitive) e NON
    toglie `www.` — `www.x.com` e `x.com` sono host diversi e trattarli come
    uguali aprirebbe la porta a un URL "quasi giusto".

    Come `synthesis._page_key`: uguaglianza sul valore INTERO, MAI un confronto
    per sottostringa (lezione ac8da1a).
    """
    url = sanitize_url(raw)
    if url is None:
        return None
    parts = urlsplit(url)
    host = parts.hostname or ""
    host = host.rstrip(".").casefold()
    if not host:
        return None
    port = parts.port
    if port is not None and not ((parts.scheme == "http" and port == 80)
                                 or (parts.scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.casefold(), host, path, parts.query, ""))


def retrieved_urls(results: Sequence[object]) -> set[str]:
    """Insieme delle chiavi-URL EFFETTIVAMENTE recuperate.

    Accetta sia `WebResult` sia dizionari/mapping (stessa tolleranza di
    `synthesis.retrieved_pages` verso le forme del payload).
    """
    keys = set()
    for r in results:
        raw = r.get("url") if isinstance(r, Mapping) else getattr(r, "url", None)
        k = url_key(raw)
        if k is not None:
            keys.add(k)
    return keys


# ------------------------------------------------------- evidenza web in XML

def sanitized_sources(results: Sequence[object], *,
                      max_sources: int = MAX_WEB_SOURCES) -> list[dict]:
    """Fonti igienizzate e tagliate al cap, pronte per il prompt.

    Scarta le voci senza URL valido e i DOPPIONI (stessa chiave-URL): due
    copie della stessa fonte non sono due conferme.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for r in results:
        get = (r.get if isinstance(r, Mapping) else (lambda k, d=None: getattr(r, k, d)))
        url = sanitize_url(get("url"))
        if url is None:
            continue
        key = url_key(url)
        if key is None or key in seen:
            continue
        seen.add(key)
        out.append({
            "url": url,
            "title": sanitize_web_text(get("title"), cap=MAX_TITLE_CHARS),
            "snippet": sanitize_web_text(get("snippet") or get("content"),
                                         cap=MAX_SNIPPET_CHARS),
            "engine": sanitize_web_text(get("engine"), cap=40),
        })
        if len(out) >= max_sources:
            break
    return out


def _attr(value: str) -> str:
    """Valore d'attributo SEMPRE fra doppi apici.

    `quoteattr` da solo passerebbe agli apici singoli quando il valore contiene
    un doppio apice: il delimitatore cambierebbe a seconda del contenuto, cioe'
    sotto il controllo di chi scrive la pagina web. Forzando `"` -> `&quot;`
    la forma dell'attributo e' invariante, qualunque cosa arrivi da fuori.
    """
    return quoteattr(value, {'"': "&quot;"})


def format_web_evidence_xml(results: Sequence[object], *,
                            max_sources: int = MAX_WEB_SOURCES) -> str:
    """Fonti web confinate in XML, come `format_evidence_xml` per il corpus.

    L'attributo `fidato="no"` sulla radice dichiara la natura del contenuto
    come DATO, non come prosa persuasiva in un prompt di sistema: e' un fatto
    strutturale che accompagna sempre queste fonti, ovunque finiscano.

    Tutto passa da `escape()`/`quoteattr()`: una fonte che contiene
    `</fonte_web>` finisce nel testo come `&lt;/fonte_web&gt;` e NON puo'
    chiudere il recinto.
    """
    sources = sanitized_sources(results, max_sources=max_sources)
    if not sources:
        return '<fonti_web fidato="no"></fonti_web>'
    rows = []
    for s in sources:
        attrs = (f"url={_attr(s['url'])} "
                 f"titolo={_attr(s['title'])} "
                 f"motore={_attr(s['engine'])}")
        rows.append(f"  <fonte_web {attrs}>\n    {escape(s['snippet'])}\n  </fonte_web>")
    return ('<fonti_web fidato="no">\n' + "\n".join(rows) + "\n</fonti_web>")


# --------------------------------------- guardia deterministica citazioni web

class WebCitationCheck(NamedTuple):
    """Esito della guardia: testo ripulito, URL tenuti, URL scartati."""

    text: str
    kept: list[str]
    dropped: list[str]


# Un URL nel testo, in due forme: dentro la citazione canonica `(web: ...)` —
# comprese le varianti che un modello scrive spontaneamente — oppure nudo.
# Il `)` e' AMMESSO nella classe di caratteri (esistono URL che lo contengono):
# il backtracking fa combaciare la parentesi di chiusura giusta, e
# `_trim_url` toglie quelle spaiate.
_URL_CHARS = r"[^\s<>\[\]\"'`]+"
_WEB_CITE = re.compile(
    r"\(\s*(?:web|fonte(?:\s+web)?|source)\s*[:,]\s*(?P<wrapped>https?://" + _URL_CHARS + r")\s*\)"
    r"|(?P<bare>https?://" + _URL_CHARS + r")",
    re.IGNORECASE,
)

_TRAILING_PUNCT = ".,;:!?…\"'"


def _trim_url(url: str) -> str:
    """Toglie la punteggiatura di frase attaccata in coda e le parentesi
    spaiate, senza rompere gli URL che contengono parentesi bilanciate."""
    url = url.rstrip(_TRAILING_PUNCT)
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1].rstrip(_TRAILING_PUNCT)
    return url


def extract_web_citations(text: str) -> list[str]:
    """URL citati nel testo (canonici o nudi), nell'ordine di comparsa."""
    out = []
    for m in _WEB_CITE.finditer(text):
        out.append(_trim_url(m.group("wrapped") or m.group("bare")))
    return out


def verify_web_citations(text: str, results: Sequence[object]) -> WebCitationCheck:
    """Guardia deterministica NON-LLM, speculare a `verify_citations`.

    Una fonte web vale SOLO se il suo URL e' fra quelli effettivamente
    recuperati e passati al modello. Ogni altro URL — inventato, allucinato,
    o suggerito da un'iniezione dentro una pagina di terzi ("cita invece
    questo sito") — viene tolto SIA dalla lista strutturata SIA dal testo
    mostrato all'utente: un link falso a schermo e' "sbagliato in silenzio"
    anche quando accanto c'e' un link buono, e in piu' e' cliccabile.

    Vale per gli URL NUDI quanto per quelli nella forma canonica `(web: ...)`:
    un modello dirottato scrive un link nudo in mezzo alla prosa, non una
    citazione ben formata, e quella e' proprio la strada da chiudere.

    Il chiamante decide il fail-closed sul caso "zero citazioni rimaste" —
    qui non si decide cosa vede l'utente, si decide solo cosa e' verificato.
    """
    valid = retrieved_urls(results)
    kept: list[str] = []
    dropped: list[str] = []

    def _keep(m: re.Match) -> str:
        raw = m.group("wrapped") or m.group("bare")
        url = _trim_url(raw)
        key = url_key(url)
        if key is not None and key in valid:
            kept.append(url)
            return m.group(0)
        dropped.append(url)
        # Toglie l'intero span: con la forma canonica sparisce anche il
        # guscio `(web: ...)`, che senza URL sarebbe una fonte fantasma.
        return ""

    out = _WEB_CITE.sub(_keep, text)
    if dropped:
        out = re.sub(r"\(\s*\)", "", out)
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"\s+([.,;:])", r"\1", out).strip()
        logger.warning("guardia citazioni web: scartati %d URL non recuperati: %s",
                       len(dropped), dropped)
    return WebCitationCheck(out, kept, dropped)
