from pathlib import Path
import torch
from PIL import Image
from colpali_engine.models import ColQwen3_5, ColQwen3_5Processor

def main():
    model_name = "athrael-soju/colqwen3.5-4.5B-v3"
    print(f"Loading {model_name}...")
    
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = ColQwen3_5.from_pretrained(
        model_name,
        torch_dtype=torch.bfloat16,
        device_map=device
    ).eval()
    
    processor = ColQwen3_5Processor.from_pretrained(model_name)
    
    # Queries
    queries = [
        "disegno del volano del motore marino Kohler",
        "esploso del forno KBS gastronomia"
    ]
    
    # Images
    # 1. Kohler GT: tp6954 p.14
    # 2. KBS GT: 1717 p.3
    import fitz
    root = Path(__file__).resolve().parent.parent
    
    def get_pdf_page(pdf_rel_path, pno):
        doc = fitz.open(root / pdf_rel_path)
        page = doc[pno - 1]
        pix = page.get_pixmap(dpi=150)
        from PIL import Image
        return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    kohler_img = get_pdf_page("corpus/engines/kohler_marine_tp6954.pdf", 14)
    kbs_img = get_pdf_page("corpus/gastronomy/kbs_gastrotechnik_1717.pdf", 3)
    altro_img = get_pdf_page("corpus/construction_agricultural/gehl_v270_parts.pdf", 94)
    blank_img = Image.new("RGB", (1024, 1024), color="white")

    
    images = [kohler_img, kbs_img, altro_img, blank_img]
    image_names = ["Kohler GT", "KBS GT", "Altro", "Blank"]
    
    print("Processing images...")
    with torch.no_grad():
        batch_images = processor.process_images(images).to(device)
        image_embeddings = model(**batch_images)
        
        batch_queries = processor.process_queries(queries).to(device)
        query_embeddings = model(**batch_queries)
        
        # Calculate MaxSim: Q x I
        scores = processor.score_multi_vector(query_embeddings, image_embeddings)
        
        for i, q in enumerate(queries):
            print(f"\nQuery: {q}")
            for j, img_name in enumerate(image_names):
                print(f"  vs {img_name}: {scores[i][j].item():.4f}")

if __name__ == "__main__":
    main()
