from src.retrieval.qwen3vl import search_visual, _model
from src.backend.clients.qdrant_client import get_qdrant_client

def ab_test():
    client = get_qdrant_client()
    query = "esploso albero motore e pistone del trattore Branson"
    print("Testing WITHOUT prompt_name='query'")
    # search_visual from qwen3vl returns a list of ScoredPoint (since it already does .points)
    hits_without = search_visual(client, query, limit=3)
    for h in hits_without:
        print(f"  {h.payload['doc']} p.{h.payload['page']} - score: {h.score}")

    print("\nTesting WITH prompt_name='query'")
    # We must patch embed_query to use prompt_name
    model = _model()
    # SentenceTransformers has prompt_name parameter in encode
    q_vec = model.encode([query], prompt_name="query")[0]
    
    from src.retrieval.pixelrag.index import QdrantVisualIndex
    idx = QdrantVisualIndex(client)
    # idx.search returns VisualHit which has .doc
    hits_with = idx.search(q_vec.tolist(), k=3)
    for h in hits_with:
        print(f"  {h.doc} p.{h.page} - score: {h.score}")

if __name__ == '__main__':
    ab_test()
