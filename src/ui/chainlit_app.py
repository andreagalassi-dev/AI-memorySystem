import os
import asyncio
import chainlit as cl
from chainlit.user import User
from dotenv import load_dotenv

from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.graph.pipeline import answer
from src.common.roles import Role

load_dotenv()

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

@cl.on_message
async def on_message(msg: cl.Message):
    user = cl.user_session.get("user")
    if not user:
        await cl.Message(content="Utente non autenticato.").send()
        return

    role = user.metadata.get("role")
    
    wait_msg = cl.Message(content="⏳ Elaborazione in corso...")
    await wait_msg.send()

    try:
        # Chiama graph pipeline in thread separato
        res = await asyncio.to_thread(answer, msg.content, role)
        
        final_answer = res.get("final_answer", "")
        final_level = res.get("final_level", "")
        citations = res.get("citations", [])
        
        # Formatta le citazioni
        cit_text = ""
        if citations:
            cit_text = "\n\n**Citazioni:**\n" + "\n".join(
                f"- Documento: {doc}, Pagina: {page}" for doc, page in citations
            )
            
        content = f"**Livello di confidenza:** {final_level}\n\n**Risposta:**\n{final_answer}{cit_text}"
        
        wait_msg.content = content
        await wait_msg.update()
        
    except Exception as e:
        wait_msg.content = f"Errore durante l'elaborazione: {e}"
        await wait_msg.update()
