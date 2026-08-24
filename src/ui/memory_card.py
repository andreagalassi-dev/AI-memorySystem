"""F.2c/F.2d — La CARD di suggerimento: come un ricordo si mostra a schermo.

Cos'e' questa card, e perche' e' fatta cosi'
--------------------------------------------
La memoria long-term di questo progetto ha una legge, nata da un exploit
PROVATO end-to-end l'11/08 (vedi la nota estesa in `src/graph/contextualize.py`
e la suite `tests/test_memory_policy_adversarial.py`):

    **LA MEMORIA NON TOCCA MAI LA QUERY.**

Una versione precedente lasciava che un binding confermato estendesse la
domanda dell'utente: un ricordo sul RADIATORE faceva rispondere ALTA col prezzo
del pezzo sbagliato a una domanda sul CARBURATORE — "sbagliato in silenzio",
cioe' l'unica cosa che il principio del progetto vieta senza appello.

Da li' il pivot: la memoria vive FUORI dal percorso della query e diventa un
SUGGERIMENTO accanto alla risposta, **dove un errore costa un click e non un
prezzo**. Questo modulo e' il pezzo che disegna quel suggerimento. Tre regole,
tutte visibili nel codice qui sotto:

  1. la card appare SOLO su una risposta BASSA o MEDIA (`should_show_card`).
     Su una ALTA un suggerimento e' rumore che invita a rimettere in
     discussione una risposta gia' fondata sull'evidenza;
  2. la card mostra la DOMANDA suggerita e la sua DATA, e nient'altro. In
     particolare MAI `hint.risposta`: quella e' prosa generata da un LLM che
     l'utente ha confermato NEL COMPLESSO (spesso con un pollice-su), e
     mostrarla accanto a una risposta vera la renderebbe indistinguibile da
     una risposta;
  3. il click vale come un TURNO UTENTE VERBATIM. Non e' questo modulo a
     realizzarlo (lo fa `src/ui/chainlit_app.py`), ma e' il motivo per cui qui
     non si costruisce nessuna "risposta pronta": la card e' un invito a
     RIFARE la domanda, non una scorciatoia per saltare le difese.

Cosa questo modulo NON fa
-------------------------
NON decide quali ricordi siano visibili: quello e' il `RevalidationGate`
(`src/registry/revalidation.py`), che ri-verifica l'evidenza viva col ruolo di
sessione. Qui arrivano SOLO hint gia' passati dal cancello. Questo modulo e'
PURO: nessun DB, nessuna rete, nessun import di Chainlit — cosi' resta
interamente testabile a tavolino e riusabile da una seconda interfaccia.

Il testo e' DATO, mai markup
----------------------------
`hint.query` e' testo scritto da un utente e salvato su DB. Prima del pivot il
bersaglio dell'injection era il prompt del riformulatore; oggi il bersaglio e'
il RENDERING (markdown/HTML nella bolla Chainlit). Il bersaglio cambia,
l'ostilita' no: `safe_hint_text` tratta quel testo come dato inerte.
"""

from __future__ import annotations

import re
from datetime import date, datetime

# Livelli su cui la card e' ammessa. Insieme CHIUSO, e volutamente per
# corrispondenza esatta: `render_level` nella UI e' tollerante a un livello
# ignoto (la cascata web ne ha introdotto uno, "WEB") perche' un'etichetta non
# deve mai sparire dallo schermo; qui la stessa tolleranza sarebbe il difetto,
# perche' MOSTREREBBE la card in un caso su cui nessuno ha deciso. Nel dubbio:
# niente card.
SHOW_ON_LEVELS = frozenset({"BASSA", "MEDIA"})

# Il testo suggerito e' una DOMANDA, non un documento: oltre questa soglia non
# sta piu' su un pulsante e non aiuta piu' nessuno. Il taglio e' anche una
# difesa banale contro un `hint.query` da megabyte.
MAX_QUERY_CHARS = 160

# Quanto testo entra nell'etichetta del pulsante (che nella UI e' stretta).
MAX_LABEL_CHARS = 60

UNKNOWN_DATE = "data ignota"

# Icona della card. Deliberatamente DIVERSA da tutte quelle gia' in uso nella
# UI — 🟢🟡🔴 (confidenza), 📄 (catalogo), 🌐 (web), ⚠️ (avviso), ❔ (livello
# ignoto): un suggerimento non deve poter essere scambiato per una risposta
# nemmeno a un'occhiata distratta.
CARD_ICON = "💡"

# Intestazione della sezione, nella stessa grammatica delle altre
# (`**Citazioni:**`, `**Provenienza:**`) introdotte in 03f385d.
SECTION_HEADER = f"{CARD_ICON} **Suggerimento dalla memoria** — non è una risposta."

# Schemi di URI che non devono sopravvivere al rendering. Non basta ESCAPARE la
# sintassi markdown del link: il testo `[x](javascript:alert(1))` con le
# parentesi escapate resterebbe comunque leggibile come `javascript:...`, e un
# client futuro (o un copia-incolla) potrebbe riattivarlo. Si toglie lo schema.
_DANGEROUS_SCHEME_RE = re.compile(
    r"(?i)\b(?:javascript|vbscript|data|file|about|blob)\s*:")
_SCHEME_PLACEHOLDER = "«schema-rimosso»"

# Caratteri con significato in markdown. `-` e `.` NON sono nell'insieme: da
# soli non aprono niente di pericoloso e il testo viene comunque emesso su UNA
# riga, dentro virgolette basse, quindi non puo' iniziare una lista o un
# titolo. Escaparli renderebbe solo la card illeggibile di barre rovesciate.
#
# `<` e `>` non sono qui: si trasformano in ENTITA' (vedi `_HTML_ENTITIES`).
# La barra rovesciata e' un escape MARKDOWN, e un parser HTML non la conosce:
# `\<img src=x\>` resta un tag valido per chiunque legga quel testo come HTML.
# L'entita' invece e' inerte per entrambi.
_MARKDOWN_SPECIALS = "\\`*_[]()#+!|~{}"

# L'ordine conta: `&` per primo, altrimenti le entita' appena inserite
# verrebbero a loro volta riscritte e il testo apparirebbe come `&amp;lt;`.
_HTML_ENTITIES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))

# Categoria unicode Cf (format): zero-width joiner, bidi override (U+202E, che
# inverte visivamente il testo), ecc. Invisibili e capaci di far leggere a un
# umano una cosa diversa da quella scritta: fuori.
_INVISIBLE_RE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\u00ad]")


def level_allows_card(level: object) -> bool:
    """True se su questo livello di confidenza la card e' ammessa.

    Separata da `should_show_card` perche' il chiamante deve poterla valutare
    PRIMA di interrogare la memoria: su una risposta ALTA non si tocca nemmeno
    il DB, cosi' non esiste alcun percorso lungo il quale un ricordo possa
    finire accanto a una risposta buona.
    """
    return isinstance(level, str) and level.strip() in SHOW_ON_LEVELS


def should_show_card(level: object, hints: object) -> bool:
    """True se c'e' qualcosa da suggerire E il livello lo consente."""
    try:
        return bool(hints) and level_allows_card(level)
    except Exception:
        return False


def format_hint_date(created_at: object) -> str:
    """La data del binding, o `UNKNOWN_DATE`. Non solleva MAI.

    `MemoryHint.created_at` e' una dataclass nuda, senza validazione: la
    versione pre-pivot faceva `created_at.date()` a nudo e un valore anomalo
    faceva esplodere l'INTERA risposta. Qui un valore non databile degrada.

    Solo `datetime`/`date` sono accettati: una stringa potrebbe essere
    qualunque cosa, e la data e' l'unico elemento della card che l'utente non
    puo' verificare da se'. Meglio dichiararla ignota che riportarne una
    inventata da un parsing ottimista.
    """
    if isinstance(created_at, datetime):
        return created_at.date().isoformat()
    if isinstance(created_at, date):
        return created_at.isoformat()
    return UNKNOWN_DATE


def safe_hint_text(text: object, *, max_chars: int = MAX_QUERY_CHARS) -> str:
    """Rende `text` inerte per il rendering. Stringa vuota se inutilizzabile.

    Nell'ordine, e l'ordine conta:
      1. solo `str` (un `bytes` o un numero non e' una domanda di nessuno);
      2. via gli invisibili (bidi override compresi: farebbero leggere altro);
      3. tutto su UNA riga — cosi' il testo non puo' forgiare una seconda
         sezione del messaggio (`**Risposta:**`, `**Livello di confidenza:**`),
         che e' l'injection dell'11/08 col bersaglio spostato dal prompt allo
         schermo;
      4. troncamento (il cap vale sul testo VISIBILE, quindi prima di
         escapare);
      5. via gli schemi di URI pericolosi;
      6. `& < >` diventano ENTITA' — inerti sia per markdown sia per un parser
         HTML, che invece ignorerebbe una barra rovesciata;
      7. escape del markdown restante — per ultimo, altrimenti le barre
         rovesciate entrerebbero nel conteggio e nei confronti dei passi
         precedenti.
    """
    if not isinstance(text, str):
        return ""
    cleaned = _INVISIBLE_RE.sub(" ", text)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return ""
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "…"
    cleaned = _DANGEROUS_SCHEME_RE.sub(_SCHEME_PLACEHOLDER, cleaned)
    for ch, entity in _HTML_ENTITIES:
        cleaned = cleaned.replace(ch, entity)
    out = []
    for ch in cleaned:
        if ch in _MARKDOWN_SPECIALS:
            out.append("\\")
        out.append(ch)
    return "".join(out)


def card_action_label(hint: object) -> str:
    """Etichetta del pulsante. Dice che si RIFA' la domanda, non che si sa gia'.

    Il verbo e' importante: "Rifai questa domanda" annuncia un'azione
    dell'utente; qualunque formula come "mostra il prezzo" prometterebbe un
    risultato che nessuno ha ancora calcolato.
    """
    text = safe_hint_text(getattr(hint, "query", None),
                          max_chars=MAX_LABEL_CHARS)
    # L'etichetta di un pulsante non e' markdown: le barre rovesciate
    # dell'escape la sporcherebbero e basta. Il testo e' gia' su una riga,
    # senza invisibili e senza schemi, che e' cio' che conta qui.
    text = text.replace("\\", "")
    return f"{CARD_ICON} Rifai: {text}" if text else f"{CARD_ICON} Rifai la domanda"


def render_memory_card(hint: object) -> str:
    """Il blocco markdown di UNA card. Stringa vuota se l'hint e' inutilizzabile.

    Non solleva mai: un hint anomalo costa una card in meno, mai una risposta.
    """
    try:
        text = safe_hint_text(getattr(hint, "query", None))
        if not text:
            return ""
        quando = format_hint_date(getattr(hint, "created_at", None))
        return (
            f"> «{text}»\n"
            f"> _Domanda confermata in passato ({quando}). "
            f"Il pulsante qui sotto la ripropone come una tua nuova domanda: "
            f"verrà rieseguita da capo, con le stesse verifiche._"
        )
    except Exception:
        return ""


def render_memory_section(hints) -> str:
    """La sezione completa (intestazione + card), o stringa vuota.

    Il chiamante ha gia' deciso col gate di livello: qui si impagina soltanto.
    """
    try:
        cards = [c for c in (render_memory_card(h) for h in (hints or [])) if c]
    except Exception:
        return ""
    if not cards:
        return ""
    return "\n\n" + SECTION_HEADER + "\n" + "\n\n".join(cards)
