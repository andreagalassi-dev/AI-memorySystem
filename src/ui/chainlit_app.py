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
from src.common.roles import Role  # noqa: E402
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
    "BASSA": "🔴 BASSA"
}

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
@cl.on_chat_resume
async def on_chat_resume(thread: dict):
    # Ripristina la sessione dell'utente dal thread
    # Assumiamo che l'utente sia già loggato e in cl.user_session
    user = cl.user_session.get("user")
    if not user:
        return
    
    # Inizializza interactions a vuoto
    cl.user_session.set("interactions", {})
@cl.on_message
async def on_message(msg: cl.Message):
    user = cl.user_session.get("user")
    if not user:
        await cl.Message(content="Utente non autenticato.").send()
        return

    role = user.metadata.get("role")
    
    try:
        # Chiama graph pipeline in thread separato
        res = await asyncio.to_thread(answer, msg.content, role, deps=get_deps(role))
        
        final_answer = res.get("final_answer", "")
        final_level = res.get("final_level", "")
        citations = res.get("citations", [])
        
        display_level = LEVEL_EMOJI.get(final_level, final_level)
        
        # Pagine renderizzate (max 8 dedup)
        elements = []
        actions = []
        seen = set()
        
        main_citation = citations[0] if citations else None
        cit_text_lines = []
        
        for doc, page in citations:
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
        
        cit_text = ""
        if cit_text_lines:
            cit_text = "\n\n**Citazioni:**\n" + "\n".join(cit_text_lines)
            
        content = f"**Livello di confidenza:** {display_level}\n\n**Risposta:**\n{final_answer}{cit_text}"
        
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
            "citazioni": citations,
            "contesto": {
                "audit": res.get("audit", []),
                "evidence": ev_list
            }
        }
        interactions = cl.user_session.get("interactions", {})
        interactions[final_msg.id] = interaction_state
        cl.user_session.set("interactions", interactions)
        
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
        correzione=correzione
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
