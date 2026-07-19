import pytest
import asyncio
from pathlib import Path
from src.ui.storage_client import LocalStorageClient

def test_local_storage_upload_and_get(tmp_path):
    storage_dir = tmp_path / "ui_elements"
    client = LocalStorageClient(storage_dir=str(storage_dir))
    
    async def run():
        # Upload
        result = await client.upload_file("test_obj.png", b"fake_image_data", mime="image/png")
        assert result["object_key"] == "test_obj.png"
        assert result["url"].endswith("test_obj.png")
        
        # File exists on disk
        saved_file = storage_dir / "test_obj.png"
        assert saved_file.exists()
        assert saved_file.read_bytes() == b"fake_image_data"
        
        # Get read URL
        url = await client.get_read_url("test_obj.png")
        assert url == result["url"]
        
    asyncio.run(run())

def test_local_storage_path_traversal(tmp_path):
    storage_dir = tmp_path / "ui_elements"
    client = LocalStorageClient(storage_dir=str(storage_dir))
    
    async def run():
        # Attempt path traversal
        with pytest.raises(ValueError, match="Invalid object_key"):
            await client.upload_file("../secret.txt", b"hack", mime="text/plain")
            
        with pytest.raises(ValueError, match="Invalid object_key"):
            await client.get_read_url("../secret.txt")
            
    asyncio.run(run())

def test_local_storage_delete(tmp_path):
    storage_dir = tmp_path / "ui_elements"
    client = LocalStorageClient(storage_dir=str(storage_dir))
    
    async def run():
        await client.upload_file("to_delete.txt", b"delete me")
        assert (storage_dir / "to_delete.txt").exists()
        
        res = await client.delete_file("to_delete.txt")
        assert res is True
        assert not (storage_dir / "to_delete.txt").exists()
        
        # Delete non-existent
        res_false = await client.delete_file("non_existent.txt")
        assert res_false is True
        
    asyncio.run(run())
