import torch
import torch.nn.functional as F
from sentence_transformers import SentenceTransformer, util
from PIL import Image

model_id = "Qwen/Qwen3-VL-Embedding-2B"
model = SentenceTransformer(model_id, trust_remote_code=True)

queries = ["Ricambi Mamoli P231"]

img_mamoli = Image.open("./data/ground_truth/MAMOLI_P231_IT/page.png").convert("RGB")
img_random = Image.open("./output/visual/renders/opacmare_catalogo_ricambi_idraulica/p0063.png").convert("RGB")
img_blank = Image.new('RGB', (1024, 1024), color = 'white')

images = [img_mamoli, img_blank, img_random]
names = ["Mamoli P231", "Blank Image", "Opacmare P0063"]

# Compute embeddings
query_emb = model.encode(queries, convert_to_tensor=True)
img_emb = model.encode(images, convert_to_tensor=True)

# Function to compute similarities given dimension
def get_sims(q, doc, dim):
    q_trunc = q[:, :dim]
    doc_trunc = doc[:, :dim]
    # Cosine similarity
    sims = util.cos_sim(q_trunc, doc_trunc)[0]
    return sims

print("=== 2048 DIM ===")
sims_2048 = get_sims(query_emb, img_emb, 2048)
for name, sim in zip(names, sims_2048):
    print(f"Cosine({name}) = {sim.item():.4f}")

print("\n=== 1024 DIM (MRL) ===")
sims_1024 = get_sims(query_emb, img_emb, 1024)
for name, sim in zip(names, sims_1024):
    print(f"Cosine({name}) = {sim.item():.4f}")

