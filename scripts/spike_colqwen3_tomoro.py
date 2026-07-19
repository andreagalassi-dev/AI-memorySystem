import os
import time
import psutil
from pathlib import Path
import torch
from PIL import Image
import fitz

import sys
sys.path.append(str(Path(__file__).resolve().parent.parent))
from src.vendor.colqwen3_tomoro.modeling_colqwen3 import ColQwen3
from src.vendor.colqwen3_tomoro.processing_colqwen3 import ColQwen3Processor

def print_memory():
    process = psutil.Process(os.getpid())
    print(f"RSS Memory: {process.memory_info().rss / (1024 * 1024):.2f} MB")

def get_pdf_page(pdf_rel_path, pno):
    root = Path("/Users/AndreaGalassi/Documents/AI/AI-memorySystem")
    doc = fitz.open(root / pdf_rel_path)
    page = doc[pno - 1]
    pix = page.get_pixmap(dpi=150)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

def main():
    model_name = "TomoroAI/tomoro-colqwen3-embed-4b"
    revision = "bf790bd8780b098b86453444632a184bb770be1a"
    print(f"Loading {model_name}...")
    
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    try:
        model = ColQwen3.from_pretrained(
            model_name,
            revision=revision,
            attn_implementation="sdpa",
            torch_dtype=torch.bfloat16,
            trust_remote_code=False,
        ).to(device).eval()
        
        processor = ColQwen3Processor.from_pretrained(
            model_name,
            revision=revision,
            trust_remote_code=False,
        )
        
        metas = [n for n, p in model.named_parameters() if p.device.type == "meta"]
        assert not metas, f"Parametri non materializzati: {metas[:5]}"
        
    except Exception:
        import traceback
        traceback.print_exc()
        return

    print_memory()
    
    queries = [
        "disegno del volano del motore marino Kohler",
        "esploso del forno KBS gastronomia"
    ]
    
    print("Extracting images...")
    kohler_img = get_pdf_page("corpus/engines/kohler_marine_tp6954.pdf", 14)
    kbs_img = get_pdf_page("corpus/gastronomy/kbs_gastrotechnik_1717.pdf", 3)
    altro_img = get_pdf_page("corpus/construction_agricultural/gehl_v270_parts.pdf", 94)
    altro2_img = get_pdf_page("corpus/pumps/iwaki_TC-X031VTN.pdf", 2)
    from PIL import Image
    blank_img = Image.new("RGB", (1024, 1024), color="white")
    
    images = [kohler_img, kbs_img, altro_img, altro2_img, blank_img]
    image_names = ["Kohler GT", "KBS GT", "Altro", "Altro2", "Blank"]
    
    print("Processing images...")
    t0 = time.time()
    with torch.no_grad():
        try:
            # Usually ColPali processors have process_images and process_queries
            if hasattr(processor, "process_images"):
                batch_images = processor.process_images(images).to(device)
            else:
                batch_images = processor(images=images, return_tensors="pt", padding=True).to(device)
            
            image_embeddings = model(**batch_images).embeddings
            
            t1 = time.time()
            print(f"Time for {len(images)} images: {t1 - t0:.2f} s -> {(t1 - t0) / len(images):.2f} s/img")
            
            if hasattr(processor, "process_queries"):
                batch_queries = processor.process_queries(queries).to(device)
            else:
                batch_queries = processor(text=queries, return_tensors="pt", padding=True).to(device)
            query_embeddings = model(**batch_queries).embeddings
        except Exception:
            import traceback
            traceback.print_exc()
            return
            
    print(f"Query shape: {query_embeddings.shape}")
    print(f"Image shape: {image_embeddings.shape}")
    
    print("Calculating scores...")
    with torch.no_grad():
        try:
            if hasattr(processor, "score_multi_vector"):
                scores = processor.score_multi_vector(query_embeddings, image_embeddings)
            elif hasattr(model, "score_multi_vector"):
                scores = model.score_multi_vector(query_embeddings, image_embeddings)
            else:
                # Manual MaxSim
                # q: (b_q, n_q, d), i: (b_i, n_i, d)
                # output: (b_q, b_i)
                scores = torch.einsum("qnd,imd->qimn", query_embeddings, image_embeddings)
                scores = scores.max(dim=-1)[0].sum(dim=-1)
        except Exception:
            import traceback
            traceback.print_exc()
            return
            
    for i, q in enumerate(queries):
        print(f"\nQuery: {q}")
        for j, img_name in enumerate(image_names):
            print(f"  vs {img_name}: {scores[i][j].item():.4f}")

    print_memory()

if __name__ == "__main__":
    main()
