import os
from pathlib import Path
from typing import Any, Dict, Optional, Union
from chainlit.data.storage_clients.base import BaseStorageClient

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
        file_path = self._validate_key(object_key)
        
        if not overwrite and file_path.exists():
            pass # Keep existing
        else:
            if isinstance(data, str):
                data = data.encode("utf-8")
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(data)
            
        return {
            "object_key": object_key,
            "url": await self.get_read_url(object_key)
        }
