import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.vendor.colqwen3_tomoro.modeling_colqwen3 import ColQwen3
import torch
from PIL import Image

model_id = "TomoroAI/tomoro-colqwen3-embed-4b"

model = ColQwen3.from_pretrained(model_id, trust_remote_code=False)
print("Process Checksum:", model.embedding_proj_layer.weight.sum().item())

# Create dummy image
img = Image.new('RGB', (224, 224), color = 'red')

from src.vendor.colqwen3_tomoro.processing_colqwen3 import ColQwen3Processor
processor = ColQwen3Processor.from_pretrained(model_id, trust_remote_code=False)

inputs = processor(text=["Hello"], images=[img], return_tensors="pt")
with torch.no_grad():
    output = model(**inputs)

print("Process Embedding Sum:", output.embeddings.sum().item())
