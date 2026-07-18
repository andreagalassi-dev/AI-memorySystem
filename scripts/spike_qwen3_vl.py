import torch
from sentence_transformers import SentenceTransformer
from PIL import Image

model_id = "Qwen/Qwen3-VL-Embedding-2B"
model = SentenceTransformer(model_id, trust_remote_code=True)

img_right = Image.new('RGB', (224, 224), color = 'red')
img_wrong = Image.new('RGB', (224, 224), color = 'blue')

# Compute embeddings without pooling to get the patch/token vectors
out_text = model.encode(["Cosa c'e' di rosso?"], output_value="token_embeddings")
out_img_right = model.encode([img_right], output_value="token_embeddings")
out_img_wrong = model.encode([img_wrong], output_value="token_embeddings")

# In SentenceTransformers, output_value="token_embeddings" returns a list of tensors for each input
text_embs = out_text[0]  # shape (num_tokens, dim)
img_right_embs = out_img_right[0]  # shape (num_patches, dim)
img_wrong_embs = out_img_wrong[0]

print(f"Text tokens shape: {text_embs.shape}")
print(f"Image patches shape: {img_right_embs.shape}")

# Compute MaxSim: for each query token, find max similarity across image patches, then sum
def maxsim(query, doc):
    # query: (num_tokens, dim)
    # doc: (num_patches, dim)
    # Compute similarity matrix: (num_tokens, num_patches)
    sim = torch.matmul(torch.tensor(query), torch.tensor(doc).T)
    # Max over patches for each token
    max_sims, _ = sim.max(dim=1)
    return max_sims.sum().item()

score_right = maxsim(text_embs, img_right_embs)
score_wrong = maxsim(text_embs, img_wrong_embs)

print(f"Score right: {score_right}")
print(f"Score wrong: {score_wrong}")

