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
from src.ui.page_render import ensure_page_render  # noqa: E402
from src.ui.feedback_handler import save_feedback_ui  # noqa: E402

load_dotenv()

import chainlit.data as cl_data  # noqa: E402
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer  # noqa: E402

from src.ui.storage_client import LocalStorageClient # noqa: E402

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
        res = await asyncio.to_thread(answer, msg.content, role, deps=get_deps(role), context=ctx)
        
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
            "query": msg.content,
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
        conv_turns.append({"role": "user", "content": msg.content})
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

@cl.action_callback("paginate")
async def on_paginate(action: cl.Action):
    from src.ui.pagination import calculate_next_page
    
    payload = action.payload
    doc = payload.get("doc")
    current_page = payload.get("page")
    direction = payload.get("dir")
    
    try:
        target_page = calculate_next_page(current_page, direction)
    except ValueError:
        await cl.Message(content="Direzione non valida.").send()
        return

    png_path = ensure_page_render(doc, target_page)
    
    if not png_path:
        await cl.Message(content=f"La pagina {target_page} del documento {doc} non è disponibile o è fuori range.").send()
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
    print(f"MY ROUTE HIT for {filename}, user={user}")
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    # TODO: Il filtro per RUOLO sui file è debito tecnico -> epica F/RBAC
    file_path = Path("output/ui_elements") / filename
    if not file_path.resolve().is_relative_to(Path("output/ui_elements").resolve()):
        raise HTTPException(status_code=403, detail="Path traversal non consentito")
        
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File non trovato")
        
    return FileResponse(str(file_path))

# Ensure the routes are placed before Chainlit's catch-all SPA route
app.router.routes.insert(0, app.router.routes.pop())
app.router.routes.insert(0, app.router.routes.pop())
