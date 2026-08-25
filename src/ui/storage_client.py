import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional, Union
from chainlit.data.storage_clients.base import BaseStorageClient

from src.ui.page_render import is_safe_doc_stem

# L'INVERSA della convenzione di nome che `upload_file` applica qui sotto, piu'
# la forma pre-sanitizzatore rimasta su disco. Sta in questo file perche' e'
# questo file a decidere come si chiamano gli elementi: la regola e la sua
# inversa devono poter cambiare insieme.
#   nuova   -> "Mamoli_Catalogo_Ricambi_2021_IT_EN_p0224.png"
#   vecchia -> "Mamoli_Catalogo_Ricambi_2021_IT_EN p.224"   (senza estensione)
# Misurato il 25/08 su output/ui_elements: 78 file su 87 sono pagine di
# documento (77 con uno stem presente in `public.documents`, 1 residuo di test),
# 9 sono `dummy.png` — non pagine, e infatti non matchano.
_NOME_PAGINA = re.compile(r"^(?P<stem>.+?)(?:_p\d{4}(?:\.png)?| p\.\d+)$")


def doc_stem_from_element_key(object_key: str) -> str | None:
    """Il doc_stem della pagina servita da questo file, o None se non e' una pagina.

    None significa "questo file non e' una pagina di documento" (una miniatura
    generica, un allegato): NON significa "consentito". La decisione di cosa
    farne e' del chiamante — vedi `serve_ui_element` in chainlit_app.
    """
    if not isinstance(object_key, str) or not object_key:
        return None
    nome = PurePosixPath(object_key).name
    match = _NOME_PAGINA.match(nome)
    if not match:
        return None
    stem = match.group("stem")
    return stem if is_safe_doc_stem(stem) else None

class LocalStorageClient(BaseStorageClient):
    """
    A local storage client for Chainlit that saves files to a local directory.
    Used to persist UI elements (like images) in the local filesystem without needing cloud services.
    """
    
    def __init__(self, storage_dir: str = "output/ui_elements"):
        self.storage_dir = Path(storage_dir)
        # Create the directory if it doesn't exist
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
    def _validate_key(self, object_key: str) -> Path:
        """Validates the object_key to prevent path traversal and returns the absolute path."""
        # Clean the key: remove leading slashes
        clean_key = object_key.lstrip("/")
        
        # Resolve the path and ensure it's within the storage_dir
        file_path = (self.storage_dir / clean_key).resolve()
        
        try:
            file_path.relative_to(self.storage_dir.resolve())
        except ValueError:
            raise ValueError(f"Invalid object_key (path traversal attempt): {object_key}")
            
        return file_path

    async def close(self) -> None:
        pass

    async def delete_file(self, object_key: str) -> bool:
        try:
            file_path = self._validate_key(object_key)
            if file_path.exists():
                file_path.unlink()
            return True
        except Exception:
            return False

    async def get_read_url(self, object_key: str) -> str:
        # Validate path
        self._validate_key(object_key)
        # We need a URL that Chainlit UI can serve. Chainlit can serve local files 
        # via its /files endpoint if properly configured, or we can just return a local absolute path 
        # or a relative URL like /files/object_key if we mount it.
        # By default Chainlit serves elements natively if they have url.
        # Let's return a proper Chainlit path or absolute path. Let's see how elements are served.
        # Chainlit serves static files if we return /public/ or similar, but for elements it often 
        # just needs a URL. If we use LocalStorage, Chainlit's BaseStorageClient docs usually 
        # expect a full URL. However, chainlit provides an endpoint for files if they are in storage.
        # Actually, if we just return /files/{object_key} maybe it works? 
        # Wait, if we use a LocalStorageClient, where does Chainlit fetch the file from?
        # Let's look at how Chainlit serves local elements when no storage client is used:
        # it serves them from memory or disk. But when persisted, it needs a URL.
        # If we return the local absolute file path, can Chainlit serve it? Or do we need to mount a static directory?
        # "Chainlit 2.11 custom route mounting" was mentioned in a previous conversation.
        # But wait! If we just return `/files/{object_key}` we might need to mount it.
        return f"/files/ui_elements/{object_key}"

    async def upload_file(
        self,
        object_key: str,
        data: Union[bytes, str],
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Sanitize object_key to prevent spaces and weird characters
        import re
        # Convert something like "Documento p.26" to "Documento_p0026.png"
        sanitized_key = object_key
        def pad_page(match):
            return f"_p{int(match.group(1)):04d}"
        sanitized_key = re.sub(r"\s+p\.(\d+)", pad_page, sanitized_key)
        sanitized_key = sanitized_key.replace(" ", "_")
        if not sanitized_key.endswith(".png") and mime == "image/png":
            sanitized_key += ".png"
            
        file_path = self._validate_key(sanitized_key)
        
        if not overwrite and file_path.exists():
            pass # Keep existing
        else:
            if isinstance(data, str):
                data = data.encode("utf-8")
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(data)
            
        return {
            "object_key": sanitized_key,
            "url": await self.get_read_url(sanitized_key)
        }
