import torch
from sentence_transformers import SentenceTransformer
from PIL import Image

model_id = "Qwen/Qwen3-VL-Embedding-2B"
model = SentenceTransformer(model_id, trust_remote_code=True)

img_right = Image.open("./data/ground_truth/MAMOLI_P231_IT/page.png").convert("RGB")
img_wrong = Image.new('RGB', (224, 224), color = 'blue')

# Compute embeddings
out_text = model.encode(["Ricambi Mamoli P231"], output_value="token_embeddings")
out_img_right = model.encode([img_right], output_value="token_embeddings")
out_img_wrong = model.encode([img_wrong], output_value="token_embeddings")

text_embs = out_text[0]
img_right_embs = out_img_right[0]
img_wrong_embs = out_img_wrong[0]

print(f"Text shape: {text_embs.shape}")
print(f"Img right shape: {img_right_embs.shape}")
print(f"Img wrong shape: {img_wrong_embs.shape}")

def maxsim(query, doc):
    sim = torch.matmul(torch.tensor(query), torch.tensor(doc).T)
    max_sims, _ = sim.max(dim=1)
    return max_sims.sum().item()

score_right = maxsim(text_embs, img_right_embs)
score_wrong = maxsim(text_embs, img_wrong_embs)

print(f"Score right (Mamoli page): {score_right}")
print(f"Score wrong (Blank page): {score_wrong}")

