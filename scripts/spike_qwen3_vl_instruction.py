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

# Compute embeddings for images
img_emb = model.encode(images, convert_to_tensor=True)

# Function to compute similarities
def get_sims(q, doc, dim):
    q_trunc = F.normalize(q[:, :dim], p=2, dim=1)
    doc_trunc = F.normalize(doc[:, :dim], p=2, dim=1)
    # Cosine similarity
    sims = util.cos_sim(q_trunc, doc_trunc)[0]
    return sims

print("=== SENZA INSTRUCTION (1024 DIM) ===")
query_emb_no = model.encode(queries, convert_to_tensor=True)
sims_1024 = get_sims(query_emb_no, img_emb, 1024)
for name, sim in zip(names, sims_1024):
    print(f"Cosine({name}) = {sim.item():.4f}")

print("\n=== CON INSTRUCTION (1024 DIM) ===")
# By default, it uses "default" prompt if prompt_name is not provided. But let's check explicit prompt.
# Let's try custom string or "query" prompt if it exists
try:
    query_emb_yes = model.encode(queries, prompt="Given a visual query, find the corresponding image.", convert_to_tensor=True)
except Exception as e:
    print(f"Error: {e}")
    query_emb_yes = query_emb_no

sims_1024_yes = get_sims(query_emb_yes, img_emb, 1024)
for name, sim in zip(names, sims_1024_yes):
    print(f"Cosine({name}) = {sim.item():.4f}")

