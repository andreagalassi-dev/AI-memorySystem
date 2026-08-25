import os
import asyncio
import chainlit as cl
from chainlit.user import User
from dotenv import load_dotenv

from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.graph.pipeline import answer  # noqa: E402
from src.common.roles import Role, parse_role  # noqa: E402
from src.graph.context import ContextAssembler  # noqa: E402
from src.serving.session_deps import get_deps  # noqa: E402
from src.ui.page_render import ensure_page_render, is_safe_doc_stem  # noqa: E402
# La guardia di accesso a un DOCUMENTO vive nel registro (src/registry/access.py),
# non nella UI: e' la stessa domanda che si fanno il ramo strutturato e il
# catalogo, e non deve avere una seconda casa qui dentro.
from src.registry.access import doc_visible_to  # noqa: E402
from src.ui.feedback_handler import save_feedback_ui  # noqa: E402

# --- Memoria long-term (Epica F.2c) ------------------------------------------
#
# Si importa SOLO `recall_validated`, MAI `MemoryStore`. Non e' pignoleria:
# `MemoryStore.recall` applica un pre-filtro RBAC sulla RIGA DI FEEDBACK, non
# sull'EVIDENZA — un binding 'aziendale'+'approved' e' visibile a chiunque
# anche quando cita un documento riservato ai tecnici. `recall_validated` e' lo
# stesso recall PIU' il `RevalidationGate`, che ri-verifica l'evidenza viva col
# ruolo di SESSIONE. Usare il magazzino nudo da qui sarebbe esattamente il leak
# che il cancello esiste per impedire (c'e' un test che vieta quell'import).
from src.registry.revalidation import recall_validated  # noqa: E402
from src.backend.clients.postgres_client import get_pg_conn  # noqa: E402
from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.ui.memory_card import (  # noqa: E402
    level_allows_card,
    render_memory_section,
    should_show_card,
    card_action_label,
)

load_dotenv()

import chainlit.data as cl_data  # noqa: E402
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer  # noqa: E402

from src.ui.storage_client import LocalStorageClient, doc_stem_from_element_key # noqa: E402

db_url = os.environ.get("DATABASE_URL")
if db_url:
    cl_data._data_layer = SQLAlchemyDataLayer(
        conninfo=db_url,
        storage_provider=LocalStorageClient(storage_dir="output/ui_elements")
    )

LEVEL_EMOJI = {
    "ALTA": "🟢 ALTA",
    "MEDIA": "🟡 MEDIA",
    "BASSA": "🔴 BASSA",
    # Epica H, cablata in parallelo su src/retrieval/confidence.py (Level.WEB
    # = "WEB", letto in sola lettura per allinearsi al contratto reale — non
    # tocchiamo quel file). Voce esplicita cosi' la cascata web non cade nel
    # fallback generico di render_level(): un'etichetta sua, mai confondibile
    # con ALTA/MEDIA/BASSA.
    "WEB": "🌐 WEB (non verificata)",
}

# --- Provenienza della risposta (Epica H: cascata web) -----------------------
#
# Il grafo (src/graph/pipeline.py) sta per guadagnare un ramo web, cablato da
# un altro agente NELLO STESSO momento: qui non lo tocchiamo. Ma l'interfaccia
# deve gia' distinguere in modo inequivocabile una risposta da catalogo da una
# presa online, e sopravvivere a un `final_level` NUOVO che ancora non
# conosciamo (distinto da ALTA/MEDIA/BASSA per le risposte web).
#
# Il segnale piu' forte non costa niente ed e' gia' nella FORMA del dato che
# `answer()` restituisce: una citazione corpus e' la coppia (doc_stem, page)
# usata ovunque nel sistema (vedi il loop qui sotto, e coerente con
# `citations: list[tuple[str, int]]` in pipeline.py); una citazione web e' un
# URL (stringa) o un mapping con almeno 'url' (stessa forma di
# `sanitized_sources` in src/retrieval/web_evidence.py — letta qui solo per
# duck-typing, questo modulo non importa quel file). Classificare sulla FORMA
# invece che sul nome di un campo non ancora deciso e' cio' che ci rende
# tolleranti al contratto che arrivera'.

WEB_WARNING = (
    "\n\n⚠️ **Attenzione — fonte web non verificata.** Questa risposta (o parte di "
    "essa) viene da una ricerca online, non dal catalogo interno: la correttezza "
    "NON è garantita dal guardrail codici/prezzi. Verificare prima di usarla "
    "operativamente (es. per prezzi o codici ricambio)."
)


def _is_web_citation(c) -> bool:
    """True se la citazione è una fonte web (URL o mapping con 'url'),
    False se è una citazione corpus (doc_stem, page) — il caso di oggi."""
    if isinstance(c, str):
        return True
    if isinstance(c, dict):
        return "url" in c
    return False


def classify_citations(citations):
    """Separa le citazioni in (corpus, web), mantenendo l'ordine originale.

    Robusto a citations=None; una forma sconosciuta (né stringa/URL né
    mapping con 'url') resta trattata come corpus — è il comportamento di
    oggi (coppia doc,page) e non va rotto da un valore inatteso."""
    corpus, web = [], []
    for c in (citations or []):
        (web if _is_web_citation(c) else corpus).append(c)
    return corpus, web


def render_level(level: str | None) -> str:
    """Etichetta leggibile del livello di confidenza.

    Tollerante per costruzione a un livello NUOVO non ancora nella mappa
    (la cascata web ne introdurrà uno, distinto da ALTA/MEDIA/BASSA): non
    sparisce e non resta vuoto, esce con un'icona neutra + il testo grezzo —
    mai un'etichetta vuota a schermo."""
    if not level:
        return "❔ N/D"
    return LEVEL_EMOJI.get(level, f"❔ {level}")


def web_citation_link(c) -> "tuple[str, str] | None":
    """Estrae (etichetta, url) da una citazione web. None se non ricavabile."""
    if isinstance(c, str):
        return (c, c)
    if isinstance(c, dict):
        url = c.get("url")
        if not url:
            return None
        return (str(c.get("title") or url), str(url))
    return None


def provenance_summary(corpus_citations, web_citations) -> "tuple[str, str]":
    """Ritorna (etichetta_visuale, chiave_compatta).

    La chiave compatta è quella che finisce nel record di feedback (vedi
    on_message/on_feedback): deve sopravvivere alla promozione memoria
    (Epica F.2b, futura) — se la provenienza vivesse solo nella grafica si
    perderebbe proprio lì, e una risposta presa da internet potrebbe salire
    in memoria aziendale senza che nessuno sappia più da dove viene."""
    has_corpus = bool(corpus_citations)
    has_web = bool(web_citations)
    if has_web and has_corpus:
        return "📄🌐 Catalogo interno + Web (parte NON verificata)", "misto"
    if has_web:
        return "🌐 Web — fonte esterna, correttezza NON verificata", "web"
    if has_corpus:
        return "📄 Catalogo interno", "corpus"
    return "— nessuna fonte citata", "nessuna"

# --- Memoria long-term: la CARD di suggerimento (Epica F.2c) -----------------
#
# LA LEGGE DI QUESTO PUNTO, e il motivo per cui il codice qui sotto e' fatto
# cosi': **la memoria non tocca MAI la query**. Una versione precedente (pivot
# dell'11/08) lasciava che un binding confermato estendesse la domanda
# dell'utente, e un exploit provato end-to-end mostro' un ricordo sul RADIATORE
# che faceva rispondere ALTA col prezzo del pezzo sbagliato a una domanda sul
# CARBURATORE. Da allora la memoria vive FUORI dal percorso della query.
#
# Tre proprieta' STRUTTURALI, non tre guardie da ricordarsi di cablare:
#   1. il recall avviene DOPO `answer()`, con la risposta gia' calcolata:
#      non c'e' nessun momento in cui un ricordo possa influenzarla;
#   2. il testo del ricordo non entra ne' nella query ne' nei turni salvati in
#      sessione (che al giro dopo alimentano il riformulatore): finisce solo
#      nel testo del messaggio e nel payload di un pulsante;
#   3. il click su quel pulsante non e' una scorciatoia: chiama
#      `handle_question`, lo STESSO ingresso di un messaggio digitato.

MEMORY_ACTION = "memory_suggest"

# Quanti hint chiedere al cancello e quante card mostrare. Si chiede piu' di
# quanto si mostri perche' il cancello ne scarta: chiedere 1 solo significa,
# nella pratica, non mostrare quasi mai nulla. Due card al massimo: con una
# sola si sceglie arbitrariamente fra candidati vicini (il difetto noto del
# selettore, vedi `tests/test_memory_policy_adversarial.py`, B.3), con molte la
# card smette di essere un suggerimento e diventa un secondo elenco da leggere.
MEMORY_RECALL_LIMIT = 5
MEMORY_MAX_CARDS = 2


def memory_suggestions(query: str, role: str, utente: str,
                       final_level: str | None) -> list:
    """Gli hint da PROPORRE accanto a questa risposta. Non solleva mai.

    Fail-closed in ogni ramo: qualunque dubbio -> nessun suggerimento. Il costo
    di una lista vuota e' una card in meno; il costo di un suggerimento
    sbagliato o non autorizzato e' il principio del progetto.

    Il gate di livello e' valutato PER PRIMO, prima di aprire una connessione:
    su una risposta ALTA la memoria non viene proprio interrogata, quindi non
    esiste alcun percorso lungo il quale un ricordo possa finirle accanto.
    """
    if not level_allows_card(final_level):
        return []
    try:
        # Ruolo di SESSIONE, tipizzato. `recall_validated` rifiuta una stringa
        # con TypeError apposta: un ruolo non valido non deve poter degradare
        # a un default permissivo. Qui degrada a "nessuna card".
        role_enum = parse_role(role)
    except Exception as exc:
        print(f"[memory-card] ruolo di sessione non valido ({exc}): nessuna card")
        return []

    try:
        with get_pg_conn() as conn:
            hints = recall_validated(
                conn, get_qdrant_client(), role=role_enum, utente=utente,
                query=query, limit=MEMORY_RECALL_LIMIT)
    except Exception as exc:
        # Stessa promessa del cancello: la memoria non rompe mai una risposta.
        print(f"[memory-card] recall fallito ({type(exc).__name__}: {exc}): "
              "nessuna card")
        return []

    out = []
    normalizzata = (query or "").strip().casefold()
    for h in hints or []:
        testo = getattr(h, "query", None)
        if not isinstance(testo, str) or not testo.strip():
            continue
        # Ri-suggerire la domanda appena fatta e' rumore puro: l'utente l'ha
        # gia' scritta e la risposta e' quella che sta leggendo.
        if testo.strip().casefold() == normalizzata:
            continue
        out.append(h)
        if len(out) >= MEMORY_MAX_CARDS:
            break
    return out


@cl.password_auth_callback
def auth_callback(username: str, password: str) -> User | None:
    users_env = os.environ.get("AIKE_UI_USERS", "")
    if not users_env:
        return None
    
    # Format: user:password:role,user2:pass2:role2...
    for user_entry in users_env.split(","):
        user_entry = user_entry.strip()
        if not user_entry:
            continue
        parts = user_entry.split(":")
        if len(parts) == 3:
            u, p, r = parts
            if u == username and p == password:
                # Validiamo il ruolo per sicurezza
                try:
                    Role(r)
                except ValueError:
                    continue
                return User(identifier=username, metadata={"role": r})
    return None
@cl.on_chat_start
async def on_chat_start():
    # Inizializza la cronologia conversazionale per il contesto (F.1b)
    cl.user_session.set("conv_turns", [])
@cl.on_chat_resume
async def on_chat_resume(thread: dict):
    # Ripristina la sessione dell'utente dal thread
    # Assumiamo che l'utente sia già loggato e in cl.user_session
    user = cl.user_session.get("user")
    if not user:
        return

    # Inizializza interactions a vuoto
    cl.user_session.set("interactions", {})
    # Una sessione ripresa riparte senza contesto ereditato (semplice e sicuro per ora)
    cl.user_session.set("conv_turns", [])
@cl.on_message
async def on_message(msg: cl.Message):
    """Un messaggio DIGITATO. Unico compito: consegnare il testo verbatim
    all'ingresso unico delle domande."""
    await handle_question(msg.content)


async def handle_question(question: str):
    """L'UNICO ingresso di una domanda, qualunque sia la sua origine.

    Ci passano sia il testo digitato (`on_message`) sia il testo di una card di
    memoria cliccata (`on_memory_suggest`). Deve restare uno solo: due percorsi
    paralleli sarebbero due insiemi di difese da tenere allineati a mano, ed e'
    esattamente cosi' che in questo progetto sono nate le guardie morte. Un
    click vale un turno utente perche' esegue QUESTO codice, non una sua copia
    ridotta.
    """
    user = cl.user_session.get("user")
    if not user:
        await cl.Message(content="Utente non autenticato.").send()
        return

    role = user.metadata.get("role")
    
    # Costruisci il contesto conversazionale (fail-safe: mai rompere la risposta)
    ctx = None
    try:
        raw = cl.user_session.get("conv_turns", [])
        ctx = ContextAssembler().build(raw, role=parse_role(role), window=6)
    except Exception as ctx_err:
        print(f"[conv-context] costruzione contesto fallita, degrado a None: {ctx_err}")
        ctx = None

    try:
        # Chiama graph pipeline in thread separato
        res = await asyncio.to_thread(answer, question, role, deps=get_deps(role), context=ctx)
        
        final_answer = res.get("final_answer", "")
        final_level = res.get("final_level", "")
        citations = res.get("citations", []) or []

        # Provenienza (Epica H): separa corpus (doc,page) da web (URL) sulla
        # FORMA della citazione, non su un nome di campo — vedi commento sopra
        # LEVEL_EMOJI. Una risposta dal corpus porta SEMPRE la miniatura della
        # pagina; una dal web mai, e porta invece un link + avviso.
        #
        # `web_node` (src/graph/pipeline.py, letto in sola lettura) lascia
        # `citations` VUOTA per una risposta web e mette gli URL in
        # `web_citations` (chiave separata nello stato del grafo, che
        # `answer()` restituisce intatto). classify_citations() resta comunque
        # la difesa: se in futuro una citazione web finisse anche dentro
        # `citations`, la riconoscerebbe lo stesso dalla forma.
        corpus_citations, web_from_citations = classify_citations(citations)
        web_citations = list(web_from_citations)
        for w in (res.get("web_citations") or []):
            if w not in web_citations:
                web_citations.append(w)
        prov_label, prov_key = provenance_summary(corpus_citations, web_citations)

        display_level = render_level(final_level)

        # Pagine renderizzate (max 8 dedup)
        elements = []
        actions = []
        seen = set()

        main_citation = corpus_citations[0] if corpus_citations else None
        cit_text_lines = []

        for doc, page in corpus_citations:
            if (doc, page) not in seen:
                seen.add((doc, page))

                # Ottieni PNG
                png_path = ensure_page_render(doc, page)
                if png_path:
                    element_name = f"{doc} p.{page}"
                    if (doc, page) == main_citation:
                        elements.append(
                            cl.Image(path=str(png_path), name=element_name, display="inline")
                        )
                        cit_text_lines.append(f"- Principale: {element_name}")
                        actions.append(
                            cl.Action(name="paginate", payload={"doc": doc, "page": page, "dir": "prec"}, label="◀ Pagina prec")
                        )
                        actions.append(
                            cl.Action(name="paginate", payload={"doc": doc, "page": page, "dir": "succ"}, label="Pagina succ ▶")
                        )
                    else:
                        elements.append(
                            cl.Image(path=str(png_path), name=element_name, display="side")
                        )
                        cit_text_lines.append(f"- Vedi: {element_name}")

                if len(seen) >= 8:
                    break

        # Fonti web: MAI una miniatura di pagina, solo link cliccabile +
        # segnalazione esplicita "non verificata" accanto a ciascuna fonte —
        # cosi' non si confonde nemmeno a un'occhiata distratta con "Vedi:".
        for c in web_citations:
            link = web_citation_link(c)
            if link is None:
                continue
            label, url = link
            cit_text_lines.append(f"- 🌐 [{label}]({url}) — _fonte web, non verificata_")

        cit_text = ""
        if cit_text_lines:
            cit_text = "\n\n**Citazioni:**\n" + "\n".join(cit_text_lines)

        content = (
            f"**Livello di confidenza:** {display_level}\n"
            f"**Provenienza:** {prov_label}\n\n"
            f"**Risposta:**\n{final_answer}{cit_text}"
        )
        if web_citations:
            content += WEB_WARNING

        # --- Memoria long-term: la card di suggerimento (Epica F.2c) ---------
        #
        # Qui, e non prima: la risposta e' gia' stata calcolata e scritta. La
        # memoria non puo' averla toccata, e questo e' un fatto della STRUTTURA
        # del codice, non una guardia che qualcuno deve ricordarsi di cablare.
        # In sede separata dalla risposta e dalle citazioni, con un'icona che
        # non appartiene a nessun'altra sezione: e' un suggerimento, e deve
        # essere impossibile scambiarlo per una risposta.
        mem_hints = await asyncio.to_thread(
            memory_suggestions, question, role, user.identifier, final_level)
        if should_show_card(final_level, mem_hints):
            sezione = render_memory_section(mem_hints)
            if sezione:
                content += sezione
                for h in mem_hints:
                    # Nel payload viaggia il TESTO della domanda suggerita.
                    # Al click quel testo vale esattamente quanto un testo
                    # DIGITATO — nessun privilegio, nessuna corsia
                    # preferenziale — quindi un payload manomesso dal client
                    # non e' un'escalation: e' una normale domanda utente.
                    # `hint_id` viaggia solo per l'audit.
                    actions.append(cl.Action(
                        name=MEMORY_ACTION,
                        payload={"text": h.query, "hint_id": h.hint_id},
                        label=card_action_label(h)))

        actions.extend([
            cl.Action(name="feedback", payload={"verdetto": "conferma"}, label="✓ Conferma"),
            cl.Action(name="feedback", payload={"verdetto": "correggi"}, label="✎ Correggi"),
            cl.Action(name="feedback", payload={"verdetto": "segnala"}, label="⚑ Segnala"),
        ])
        
        # Prepara l'evidenza troncata per il DB
        ev_list = []
        for e in res.get("evidence", []):
            try:
                edoc = e.doc if hasattr(e, "doc") else e.get("doc")
                epage = e.page if hasattr(e, "page") else e.get("page")
                ecode = e.code if hasattr(e, "code") else e.get("code")
                escore = e.score if hasattr(e, "score") else e.get("score")
                etext = e.text if hasattr(e, "text") else e.get("text")
                
                ev_dict = {
                    "doc": edoc,
                    "page": epage,
                    "code": ecode,
                    "score": escore,
                    "text": etext[:500] if etext else None
                }
                ev_list.append(ev_dict)
            except Exception:
                pass

        # Invia messaggio finale ROOT (parent_id=None forzato DOPO l'init per evitare sovrascritture dal contesto)
        final_msg = cl.Message(content=content, elements=elements, actions=actions)
        final_msg.parent_id = None
        await final_msg.send()

        # Salva lo stato per il feedback (chiave: ID del messaggio di risposta)
        interaction_state = {
            "query": question,
            "risposta": final_answer,
            "final_level": final_level,
            # Corpus + web insieme: `citations` da sola per una risposta web è
            # vuota (vedi sopra), e salvare solo quella perderebbe gli URL nel
            # record di feedback — esattamente il rischio del punto 3.
            "citazioni": corpus_citations + web_citations,
            # Provenienza (Epica H): serve nel RECORD, non solo a schermo — vedi
            # provenance_summary() sopra. Senza questo campo, la promozione
            # feedback->memoria (F.2b) non saprebbe piu' distinguere una
            # risposta da catalogo da una presa online.
            "provenienza": prov_key,
            "contesto": {
                "audit": res.get("audit", []),
                "evidence": ev_list
            }
        }
        interactions = cl.user_session.get("interactions", {})
        interactions[final_msg.id] = interaction_state
        cl.user_session.set("interactions", interactions)

        # Aggiorna la cronologia conversazionale (F.1b): utente + assistente
        conv_turns = cl.user_session.get("conv_turns", [])
        conv_turns.append({"role": "user", "content": question})
        conv_turns.append({
            "role": "assistant",
            "content": res.get("final_answer", ""),
            "evidence_ids": [tuple(c) for c in res.get("citations", [])],
        })
        # CAP: mantieni solo gli ultimi 20 turni per non far crescere la sessione all'infinito
        conv_turns = conv_turns[-20:]
        cl.user_session.set("conv_turns", conv_turns)

    except Exception as e:
        await cl.Message(content=f"Errore durante l'elaborazione: {e}", parent_id=None).send()


@cl.action_callback(MEMORY_ACTION)
async def on_memory_suggest(action: cl.Action):
    """Click su una card: la domanda suggerita viene RIFATTA come turno utente.

    Tre cose, in quest'ordine, e nessuna e' cosmetica:

      1. il testo si prende dal payload e si usa VERBATIM. Non si concatena
         alla domanda precedente, non si fonde col contesto, non si "arricchisce"
         in nessun modo: un suggerimento concatenato sarebbe la memoria che
         estende la query sotto un altro nome, cioe' precisamente cio' che il
         pivot dell'11/08 vieta;
      2. si stampa un'ECO come messaggio UTENTE. L'utente deve vedere scritto
         cio' che ha appena chiesto: e' la differenza fra "la memoria ha deciso
         per me" e "l'ho chiesto io, e lo leggo";
      3. si chiama `handle_question`, cioe' lo stesso ingresso di un messaggio
         digitato. Nessuna scorciatoia: riformulazione, RBAC, guardrail codici
         e soglie di confidenza vengono rieseguiti tutti, da capo.
    """
    user = cl.user_session.get("user")
    if not user:
        return

    payload = getattr(action, "payload", None) or {}
    testo = payload.get("text")
    if not isinstance(testo, str) or not testo.strip():
        await cl.Message(content="Suggerimento non più disponibile.").send()
        return
    testo = testo.strip()

    # Eco come turno UTENTE. Se il tipo di messaggio non fosse disponibile
    # (deriva dell'API Chainlit), si degrada a un messaggio normale: meglio
    # un'eco meno riconoscibile che nessuna eco, perche' l'utente resterebbe
    # senza traccia scritta di cio' che e' stato chiesto per suo conto.
    try:
        eco = cl.Message(content=testo, type="user_message")
    except Exception:
        eco = cl.Message(content=f"↩️ {testo}")
    eco.parent_id = None
    await eco.send()

    await handle_question(testo)


@cl.action_callback("feedback")
async def on_feedback(action: cl.Action):
    user = cl.user_session.get("user")
    if not user:
        return
        
    verdetto = action.payload.get("verdetto")
    msg_id = action.forId
    
    interactions = cl.user_session.get("interactions", {})
    state = interactions.get(msg_id)
    if not state:
        await cl.Message(content="Sessione feedback scaduta o non trovata.").send()
        return

    correzione = None
    if verdetto == "correggi":
        res = await cl.AskUserMessage(content="Inserisci la correzione:", timeout=120).send()
        if res:
            correzione = res.get('output') if isinstance(res, dict) else getattr(res, 'content', str(res))
        else:
            await cl.Message(content="Nessuna correzione fornita. Feedback annullato.").send()
            return

    # Esegue insert_feedback (che apre/chiude connessione) in un thread separato
    fb_id = await asyncio.to_thread(
        save_feedback_ui,
        utente=user.identifier,
        ruolo=user.metadata.get("role", "sconosciuto"),
        query=state["query"],
        risposta=state["risposta"],
        final_level=state["final_level"],
        citazioni=state["citazioni"],
        contesto=state["contesto"],
        verdetto=verdetto,
        correzione=correzione,
        provenienza=state.get("provenienza", "corpus")
    )

    # Rimuovi i pulsanti per evitare feedback multipli
    msg_to_update = cl.Message(content="")
    msg_to_update.id = action.forId
    await msg_to_update.remove_actions()

    if fb_id is not None:
        await cl.Message(content=f"Feedback registrato (Verdetto: {verdetto}).").send()
    else:
        await cl.Message(content="Errore durante la registrazione del feedback. Riprovare più tardi.").send()

# Un SOLO testo per ogni rifiuto della paginazione: negato per ruolo, documento
# inesistente, doc malformato, render fallito. Non e' cortesia, e' la lezione
# della falla precedente: "non hai accesso al documento X" (o anche solo un
# testo diverso) e' gia' un ORACOLO DI ESISTENZA — dice al commerciale che
# quel documento c'e' e non e' suo. Nessuna interpolazione di `doc`: due
# rifiuti devono essere confrontabili byte per byte (test esplicito).
PAGINA_NON_DISPONIBILE = "La pagina richiesta non è disponibile o è fuori range."


@cl.action_callback("paginate")
async def on_paginate(action: cl.Action):
    """Un click su "Pagina prec/succ".

    ATTENZIONE, e' il cuore della cosa: il payload di una `cl.Action` NON e'
    un dato di fiducia. Chainlit ricostruisce l'azione dal corpo della POST
    (`chainlit/server.py`: `action = Action(**payload.action)`) e non tiene
    alcun registro delle azioni realmente emesse: l'unico controllo e' che la
    sessione sia dell'utente autenticato. Quindi `doc` e `page` qui dentro
    valgono quanto un parametro di query scritto a mano.

    E' lo stesso ragionamento gia' fatto per la card di memoria (vedi il
    commento in `handle_question`), ma con una conclusione diversa, perche' li'
    il payload conteneva TESTO che vale quanto un testo digitato — nessun
    privilegio — mentre qui contiene l'identita' di un DOCUMENTO e la si usa
    per andare a prendere dei pixel dal disco. `paginate` e' nato accanto a
    quel percorso e non ne ha ereditato la difesa: fino al 25/08 rendeva
    l'immagine di qualunque pagina di qualunque documento a qualunque utente
    autenticato — 139 documenti su 147 tecnico-only, `Mamoli_Catalogo_
    Ricambi_2021_IT_EN` compreso, cioe' lo stesso documento della falla del
    ramo strutturato: chiusa la porta del PREZZO in testo, restava aperta
    quella dell'IMMAGINE della stessa pagina.

    Ordine dei controlli (ognuno fail-closed): forma della pagina -> ruolo di
    sessione -> forma del doc -> VISIBILITA' del doc -> solo allora il disco.
    """
    from src.ui.pagination import calculate_next_page

    payload = action.payload or {}
    doc = payload.get("doc")
    current_page = payload.get("page")
    direction = payload.get("dir")

    try:
        target_page = calculate_next_page(current_page, direction)
    except ValueError:
        # Non parla di documenti: nessuna informazione da proteggere qui.
        await cl.Message(content="Direzione o pagina non valida.").send()
        return

    # Il ruolo della SESSIONE, mai dal payload. `handle_question` lo legge
    # allo stesso modo; se manca l'utente o manca il ruolo, `doc_visible_to`
    # risponde False — un solo ramo, nessun caso speciale da tenere allineato.
    user = cl.user_session.get("user")
    role = user.metadata.get("role") if user is not None else None

    consentito = (
        target_page >= 1
        and is_safe_doc_stem(doc)
        and await asyncio.to_thread(doc_visible_to, doc, role)
    )
    png_path = ensure_page_render(doc, target_page) if consentito else None

    if not png_path:
        # Un unico messaggio, e senza i pulsanti: se li rimettessimo, il
        # rifiuto sarebbe solo un passo indietro e si continuerebbe a
        # camminare il documento a click.
        await cl.Message(content=PAGINA_NON_DISPONIBILE).send()
        return

    elements = [
        cl.Image(path=str(png_path), name=f"{doc} p.{target_page}", display="inline")
    ]
    actions = [
        cl.Action(name="paginate", payload={"doc": doc, "page": target_page, "dir": "prec"}, label="◀ Pagina prec"),
        cl.Action(name="paginate", payload={"doc": doc, "page": target_page, "dir": "succ"}, label="Pagina succ ▶")
    ]
    
    msg = cl.Message(
        content=f"Pagina {target_page} del documento **{doc}**:",
        elements=elements,
        actions=actions
    )
    msg.parent_id = None
    await msg.send()

from chainlit.server import app  # noqa: E402
from src.serving.session_deps import get_serving_health  # noqa: E402

@app.get("/aike/serving-health")
async def serving_health_endpoint():
    return get_serving_health()

from fastapi import Request, Depends, HTTPException
from fastapi.responses import FileResponse
from chainlit.auth import get_current_user

@app.get("/files/ui_elements/{filename:path}")
async def serve_ui_element(
    filename: str,
    user = Depends(get_current_user)
):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Filtro per RUOLO sui file (era un TODO -> epica F/RBAC). Questo percorso
    # serve le PAGINE gia' renderizzate: e' la stessa risorsa di `on_paginate`
    # per un'altra porta, e senza questo controllo la correzione di `paginate`
    # sarebbe stata solo uno spostamento della falla.
    #
    # Il nome del file porta il documento (`{stem}_p0224.png`), quindi il ruolo
    # si applica al documento derivato. Un nome che NON e' una pagina di
    # documento (`dummy.png`, un allegato) passa come prima: qui non c'e' un
    # documento da proteggere e negarlo romperebbe elementi legittimi.
    # Misurato il 25/08: 78 file su 87 sono pagine, 77 con uno stem presente in
    # `public.documents` (l'unico fuori e' `test_image_p0001.png`, residuo di
    # test alla radice, irraggiungibile da un URL di elemento reale).
    #
    # Gravita' reale del buco che si chiude: la chiave e' `{thread_uuid}/
    # {element_uuid}/nome`, servono DUE uuid e non sono enumerabili; inoltre
    # oggi in `AIKE_UI_USERS` utente e ruolo sono 1:1. Era dormiente, non vivo.
    stem = doc_stem_from_element_key(filename)
    if stem is not None:
        # `user` qui e' quello del JWT o quello persistito dal data layer:
        # entrambi portano `metadata['role']` (verificato il 25/08 su
        # chainlit.users). Se un domani non lo portasse, si nega: fail-closed.
        metadata = getattr(user, "metadata", None) or {}
        ruolo = metadata.get("role")
        if not await asyncio.to_thread(doc_visible_to, stem, ruolo):
            # 404 e non 403, per lo stesso motivo del messaggio unico di
            # `on_paginate`: un 403 direbbe "quel documento esiste e non e'
            # tuo". Il perche' vero resta nel log del server.
            print(f"[rbac] elemento negato: {filename} (doc={stem}, ruolo={ruolo!r})")
            raise HTTPException(status_code=404, detail="File non trovato")

    file_path = Path("output/ui_elements") / filename
    if not file_path.resolve().is_relative_to(Path("output/ui_elements").resolve()):
        raise HTTPException(status_code=403, detail="Path traversal non consentito")
        
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File non trovato")
        
    return FileResponse(str(file_path))

# Ensure the routes are placed before Chainlit's catch-all SPA route
app.router.routes.insert(0, app.router.routes.pop())
app.router.routes.insert(0, app.router.routes.pop())
