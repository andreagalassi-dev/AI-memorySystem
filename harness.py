import asyncio
import os
import uuid
import json
from datetime import datetime

from sqlalchemy import text
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.element import Image

from src.ui.storage_client import LocalStorageClient

async def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL missing")
        return

    from chainlit.context import init_ws_context
    from chainlit.session import WebsocketSession
    
    session = WebsocketSession(
        id="mock_id",
        socket_id="mock_socket",
        emit=lambda *args, **kwargs: None,
        emit_call=lambda *args, **kwargs: None,
        client_type="webapp",
        thread_id="mock",
        user=None,
        user_env={}
    )
    init_ws_context(session)

    # Setup data layer
    data_layer = SQLAlchemyDataLayer(
        conninfo=db_url,
        storage_provider=LocalStorageClient(storage_dir="output/ui_elements")
    )
    
    thread_id = str(uuid.uuid4())
    step_id = str(uuid.uuid4())
    
    # 1. Crea Thread/User
    user_id_uuid = str(uuid.uuid4())
    from chainlit.user import User
    user = User(identifier="harness", metadata={})
    # Data layer might not have user.id so let's just assume chainlit generates it or we can't easily mock it.
    # Actually, data_layer.create_user returns the created PersistedUser
    persisted_user = await data_layer.create_user(user)
    
    # 1.5 Crea Thread
    await data_layer.update_thread(thread_id=thread_id, name="Test Thread", user_id=persisted_user.id if persisted_user else None, metadata={}, tags=[])
    
    # 2. Crea un Step ROOT (parent_id = None) come farebbe cl.Message(parent_id=None).send()
    step_dict = {
        "id": step_id,
        "name": "assistant",
        "type": "assistant_message",
        "threadId": thread_id,
        "parentId": None, # ROOT
        "disableFeedback": False,
        "display": "inline",
        "language": None,
        "indent": 0,
        "isError": False,
        "waitForAnswer": False,
        "output": "Test message with inline image",
        "createdAt": datetime.utcnow().isoformat(),
        "start": datetime.utcnow().isoformat(),
        "end": datetime.utcnow().isoformat(),
        "generation": None,
        "metadata": {}
    }
    
    await data_layer.create_step(step_dict)
    print(f"Created step {step_id}")
    
    # 3. Crea e allega un elemento, come farebbe cl.Message(elements=[...]).send()
    # Scrive file su disco e lo registra nel DB.
    # In cl.Message.send(), gli elementi vengono creati tramite data_layer.create_element()
    
    img = Image(path="output/ui_elements/dummy.png", name="doc1 p.1", display="inline")
    # Facciamo finta che il path esista, lo creiamo per test:
    os.makedirs("output/ui_elements", exist_ok=True)
    with open("output/ui_elements/dummy.png", "wb") as f:
        f.write(b"fake image data")
        
    element_dict = {
        "id": img.id,
        "threadId": thread_id,
        "type": "image",
        "url": img.url,
        "chainlitKey": None,
        "name": img.name,
        "display": img.display,
        "objectKey": getattr(img, 'object_key', None),
        "size": getattr(img, 'size', None),
        "language": getattr(img, 'language', None),
        "forId": step_id,
        "mime": getattr(img, 'mime', "image/png")
    }
    
    # Upload and create element
    if data_layer.storage_provider:
        # read dummy data
        with open("output/ui_elements/dummy.png", "rb") as f:
            data = f.read()
        uploaded = await data_layer.storage_provider.upload_file(
            object_key=f"{thread_id}/{img.id}/dummy.png",
            data=data,
            mime=img.mime
        )
        element_dict["url"] = uploaded.get("url")
        element_dict["objectKey"] = uploaded.get("object_key")

    await data_layer.create_element(element_dict)
    print(f"Created element {img.id} for step {step_id}")

    await asyncio.sleep(1)
    
    # 4. Verify in DB
    print("\n--- DB VERIFICATION ---")
    from sqlalchemy import text
    async with data_layer.engine.connect() as conn:
        res = (await conn.execute(text(f"SELECT id, name, type, \"parentId\" FROM chainlit.steps WHERE id = '{step_id}'"))).mappings().all()
        print("STEP ROOT:", dict(res[0]) if res else None)
        
        res = (await conn.execute(text(f"SELECT id, type, name, display, \"objectKey\" FROM chainlit.elements WHERE \"forId\" = '{step_id}'"))).mappings().all()
        print("ELEMENT:", dict(res[0]) if res else None)
        
if __name__ == "__main__":
    asyncio.run(main())
