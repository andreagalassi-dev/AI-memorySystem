"""P2.4 — Strategie di ricerca su Qdrant (dense / sparse / rrf / routed).

Riusa gli STESSI embedder e la stessa text-prep dell'ingestion
(`src/ingestion/embedding.py`): qualsiasi asimmetria indice/query falserebbe
le misure. `routed` applica il router deterministico: query 'code' ->
sparse-only; 'mixed'/'natural' -> ibrida RRF.
"""

from __future__ import annotations

from qdrant_client import QdrantClient, models

from src.ingestion.embedding import (
    COLLECTION,
    DENSE_NAME,
    SPARSE_NAME,
    embed_dense,
    embed_sparse_query,
    sparse_query_text,
)
from src.common.roles import Role
from src.retrieval.router import classify_query

STRATEGIES = ("dense", "sparse", "rrf", "routed")
# routed_rr (P2.8d, ADOTTATA): routed + cross-encoder BGE-reranker-v2-m3 sui
# top-20, SOLO per query non-code (misurato: R@1 +0.115, mixed 0.50->0.875,
# latenza mediana 0.76s dichiarata). E' la strategia di produzione per P3.


def _sparse_vec(query: str) -> models.SparseVector:
    """Vettore BM25 della query, con la forma INCOLLATA dei codici aggiunta
    (`sparse_query_text`): e' il lato query dell'atomicita' del codice.

    ADDITIVA: i termini originali restano, quindi contro un indice con i codici
    ancora frantumati il comportamento e' identico (un termine assente dalla
    collezione ha term-frequency 0 su ogni documento e contribuisce 0 al
    punteggio). Contro un indice con i codici atomici, e' il termine che rende
    il ricambio raggiungibile.

    NB: qui NON si usa `sparse_text`/`dense_text` dell'ingestion — quelle
    aggiungono l'identita' del DOCUMENTO e non devono mai vedere una query.
    """
    q = embed_sparse_query(sparse_query_text(query))
    return models.SparseVector(indices=q.indices.tolist(), values=q.values.tolist())


def version_filter(status: str) -> models.Filter:
    """P4.4 — Filtro sul payload `version_status` (indicizzato in P4.2)."""
    return models.Filter(must=[models.FieldCondition(
        key="version_status", match=models.MatchValue(value=status))])


def access_filter(version_status: str, role: Role) -> models.Filter:
    """P4.5 — AND(versione, ruolo): MatchValue su `ruoli_consentiti` (lista
    keyword indicizzata in P4.2) = "l'array contiene il ruolo". Il tipo Role
    e' l'unico ammesso: la validazione a monte e' gia' avvenuta."""
    return models.Filter(must=[
        models.FieldCondition(key="version_status",
                              match=models.MatchValue(value=version_status)),
        models.FieldCondition(key="ruoli_consentiti",
                              match=models.MatchValue(value=role.value)),
    ])


def search(client: QdrantClient, query: str, strategy: str, limit: int = 10,
           query_filter: models.Filter | None = None,
           extra_terms: list[str] | None = None):
    """Ritorna la lista di ScoredPoint per la strategia richiesta.

    `query_filter` (P4.4, additivo) limita i CANDIDATI senza toccare lo
    scoring: nelle strategie ibride va dentro OGNI prefetch (e' li' che si
    generano i candidati), oltre che sull'outer query.

    `extra_terms` (debito §2, additivo): termini della Query Expansion,
    concatenati SOLO nel testo del canale sparse (BM25: bag-of-terms, la
    concatenazione E' l'OR) delle strategie rrf/routed-natural. Il canale
    dense resta sulla query originale; 'code'/'mixed'/sparse-diretta li
    IGNORANO (mai inquinare la ricerca esatta dei codici).
    """
    # nome esplicito: NON e' la `sparse_text` di ingestion (quella vede un chunk,
    # non una query, e aggiunge l'identita' del documento). Qui e' solo la query
    # piu' i termini di espansione.
    sparse_query = (f"{query} {' '.join(extra_terms)}" if extra_terms else query)
    if strategy == "sparse":
        return client.query_points(
            COLLECTION, query=_sparse_vec(query), using=SPARSE_NAME,
            query_filter=query_filter, with_payload=True, limit=limit).points
    if strategy == "dense":
        return client.query_points(
            COLLECTION, query=embed_dense([query])[0], using=DENSE_NAME,
            query_filter=query_filter, with_payload=True, limit=limit).points
    if strategy == "rrf":
        return client.query_points(
            COLLECTION,
            prefetch=[
                models.Prefetch(query=embed_dense([query])[0],
                                using=DENSE_NAME, limit=limit * 2,
                                filter=query_filter),
                models.Prefetch(query=_sparse_vec(sparse_query),
                                using=SPARSE_NAME, limit=limit * 2,
                                filter=query_filter),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=query_filter, with_payload=True, limit=limit).points
    if strategy == "routed":
        route = classify_query(query)
        if route.query_type == "code":
            # SOLO il token-codice al canale lessicale: le parole-marker
            # ("part number", "codice") matcherebbero migliaia di chunk-testo
            # sommergendo il token unico (misurato in P2.6: 19.9 vs 15.9).
            return search(client, route.code_token, "sparse", limit,
                          query_filter=query_filter)
        if route.query_type == "mixed":
            # ibrida: lessicale sul token-codice, semantica sulla frase intera
            return client.query_points(
                COLLECTION,
                prefetch=[
                    models.Prefetch(query=embed_dense([query])[0],
                                    using=DENSE_NAME, limit=limit * 2,
                                    filter=query_filter),
                    models.Prefetch(query=_sparse_vec(route.code_token),
                                    using=SPARSE_NAME, limit=limit * 2,
                                    filter=query_filter),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                query_filter=query_filter, with_payload=True, limit=limit).points
        # natural: unica route dove l'espansione entra (scope v1 debito §2)
        return search(client, query, "rrf", limit, query_filter=query_filter,
                      extra_terms=extra_terms)
    if strategy == "routed_rr":
        from src.retrieval.rerank import rerank, rerank_topk

        route = classify_query(query)
        if route.query_type == "code":
            return search(client, query, "routed", limit, query_filter=query_filter)
        base = search(client, query, "routed", limit=rerank_topk(),
                      query_filter=query_filter, extra_terms=extra_terms)
        # reranker sulla query ORIGINALE: l'espansione serve al recall dei
        # candidati, il punteggio resta sulla domanda vera dell'utente
        return rerank(query, base, limit=limit)
    raise ValueError(f"strategia sconosciuta: {strategy}")

def group_by_code(hits: list) -> list:
    """Hop 1: Deduplica risultati tenendo solo il primo (score max) per ogni codice."""
    seen = set()
    out = []
    for h in hits:
        c = (getattr(h, "payload", None) or {}).get("code")
        if c not in seen:
            out.append(h)
            if c:
                seen.add(c)
    return out

def apply_hop2(client: QdrantClient, hits: list, role: Role, *,
               version_status: str = "current") -> tuple[list, list, str | None]:
    """Applica Hop 1 (group by) e Hop 2 (recupero children).

    P4.5/A6 — il recupero dei figli di un assembly applica lo STESSO filtro
    ruolo del resto del retrieval (`ruoli_consentiti` contiene il ruolo), in
    AND con `parent_id`: un ruolo non deve mai ricevere componenti riservati a
    un altro ruolo (l'RBAC è obbligatorio, non opzionale).

    DIFETTO TROVATO (25/08) e corretto qui — la META' MANCANTE di quella
    promessa: il filtro dei figli era ruolo + `parent_id` e BASTA. Un figlio
    `superseded` (o `deleted`) di un padre `current` rientrava nella distinta,
    e l'avviso di obsolescenza — che il grafo decide sul PADRE — non lo
    copriva. L'altra copia della regola, `_apply_hop2` in
    `src/graph/pipeline.py`, era gia' corretta perche' estende con
    `model_copy` il filtro d'accesso INTERO: il monito scritto li' ("una
    divergenza fra le due e' un bug, non una scelta") descriveva esattamente
    questa divergenza.

    Ampiezza reale del difetto, misurata su `aike_chunks_v2` il 25/08: 199689
    punti su 199689 sono `current`, zero `superseded`, zero `deleted`; dei 961
    punti con `parent_id` non nullo, ZERO sono non-`current`. Bersaglio VUOTO
    oggi. Resta una trappola: il grafo interroga gia' `superseded`/`deleted` in
    fallback, e la prima re-ingestion che marca una versione la arma.

    `version_status` keyword-only con default `"current"` — lo scope piu'
    restrittivo, lo stesso nome e lo stesso default di `search_unified` e di
    `visual_search`. Il default vale soprattutto per `src/retrieval/bom.py`,
    che NON lo passa: quel modulo ancora gia' il PADRE a
    `access_filter("current", role)` e con questo default i due scope
    coincidono senza che debba dire nulla. Il default sicuro e' quello che
    rende la BOM coerente per omissione: una distinta dichiarata corrente non
    puo' contenere componenti sostituiti, e se un domani `get_bom` volesse
    interrogare uno scope diverso dovrebbe dirlo in ENTRAMBI i punti, che e'
    esattamente la disciplina che qui mancava.

    Il filtro non e' riscritto a mano ma costruito da `access_filter` ed esteso
    con `model_copy` (stesso pattern del grafo e di `registry/revalidation.py`):
    un futuro irrigidimento di `access_filter` arriva qui da solo invece di
    essere silenziosamente perso — che e' il modo in cui la versione si era
    persa la prima volta.
    """
    grouped_hits = group_by_code(hits)
    children_hits = []
    hop2_warning = None
    if grouped_hits and grouped_hits[0].payload.get("is_assembly"):
        kit_code = grouped_hits[0].payload.get("code")
        if kit_code:
            access = access_filter(version_status, role)
            children_filter = access.model_copy(update={
                "must": list(access.must or []) + [
                    models.FieldCondition(
                        key="parent_id",
                        match=models.MatchValue(value=kit_code))]})
            # Hop 2 exact match
            records, _ = client.scroll(COLLECTION, scroll_filter=children_filter, limit=100)
            if not records:
                hop2_warning = "Assembly_Has_No_Children"
            else:
                for r in records:
                    children_hits.append(models.ScoredPoint(
                        # `getattr(..., 0)`: `.scroll` restituisce `models.Record`,
                        # che NON ha il campo `version` (id/payload/vector/
                        # shard_key/order_value). `r.version` sollevava
                        # AttributeError contro Qdrant vero — verificato il
                        # 25/08 sull'indice vivo, assieme `14557` con 3 figli:
                        # l'intero Hop 2 di QUESTO modulo esplodeva, quindi
                        # `get_bom`/`export_bom.py` non ha mai potuto
                        # restituire una distinta non vuota. `bom.py` la stessa
                        # difesa ce l'aveva gia' sul padre
                        # (`getattr(parent_record, "version", 0)`), qui no.
                        id=r.id, version=getattr(r, "version", 0),
                        score=grouped_hits[0].score, payload=r.payload, vector=None
                    ))
    return grouped_hits, children_hits, hop2_warning


def search_unified(client: QdrantClient, query: str, limit: int = 10, *,
                   role: Role, version_status: str = "current") -> dict:
    """Instradamento unificato testo/visione (P2.7).

    - query 'visual' -> collection visiva (pagine citabili con render);
      se porta anche un codice -> IN AGGIUNTA il canale lessicale su
      aike_chunks (l'esploso E la riga ricambio);
    - tutto il resto -> strategia routed su aike_chunks (invariata).
    Ritorna {"route", "reason", "text_hits", "visual_hits"}.

    DIFETTO TROVATO (25/08) e corretto qui: la firma promette RBAC
    fail-closed (`role` obbligatorio, keyword-only) da quando questa funzione
    esiste, ma il ruolo arrivava SOLO all'Hop 2 (`apply_hop2`, sui figli di un
    assieme). La ricerca PRINCIPALE — `search(client, query, "routed", limit)`
    e, nel ramo visivo, `search(client, route.code_token, "sparse", limit)` —
    girava senza `query_filter`: qualsiasi ruolo vedeva qualsiasi punto.
    Misurato sull'indice vivo: 196876 punti su 199689 (98.6%) di
    `aike_chunks_v2` erano raggiungibili da un ruolo non autorizzato
    attraverso questa funzione. E' il duale esatto di una guardia scritta e
    mai cablata: qui il parametro di sicurezza c'era nella firma, semplicemente
    non veniva usato dove contava.

    Effetto pratico oggi: il grafo di produzione (`src/graph/pipeline.py`,
    `build_default_deps._retrieve`) NON passa da qui — costruisce il suo
    proprio `access_filter` e lo passa a `search()` direttamente — quindi
    questo percorso era MORTO in produzione. L'unico chiamante e'
    `scripts/evaluate_hierarchy.py`, con `role=Role.TECNICO` fisso: per quel
    ruolo l'RBAC di ruolo era gia' un no-op prima della correzione (il tecnico
    vede tutto), quindi le sue misure non cambiano per l'aspetto ruolo — MA
    prima della correzione perdeva ANCHE `version_status`, quindi la sua
    ricerca principale poteva restituire documenti `superseded`/`deleted`
    mescolati al corrente. Con la correzione lo scope e' `current` di default
    (vedi sotto), quindi le misure di `evaluate_hierarchy` POSSONO cambiare se
    l'eval-set contiene documenti non-`current` raggiungibili solo dal difetto.

    `version_status` (parametro esplicito, default `"current"`): questa
    funzione non riceve dal chiamante lo scope di versione (a differenza di
    `build_default_deps._retrieve`, che lo riceve dal grafo). Il default piu'
    sicuro e' quello che esclude i documenti sostituiti/cancellati — stessa
    scelta e stesso nome di default di `visual_search`
    (`src/retrieval/pixelrag/search.py`) — cosi' un chiamante che non lo passa
    esplicitamente non rientra mai in una versione obsoleta per omissione.

    Il filtro e' costruito UNA VOLTA con `access_filter(version_status, role)`
    (definito in questo stesso modulo — nessun import incrociato con
    `src/graph/pipeline.py`, che anzi importa questa funzione da qui) e passato
    a ENTRAMBI i call-site della ricerca principale, oltre che ad `apply_hop2`
    come gia' accadeva.

    Lo stesso `version_status` viaggia ora fino ad `apply_hop2` (correzione del
    25/08, vedi la sua docstring): la ricerca principale e l'Hop 2 devono
    interrogare lo STESSO scope di versione, altrimenti la stessa risposta
    mescolerebbe un padre corrente con figli sostituiti.

    P4.5/A6 — `role` è obbligatorio (keyword-only, fail-closed) e viene
    propagato fino all'Hop 2 per il filtro RBAC sui figli.
    """
    route = classify_query(query)
    vf = access_filter(version_status, role)
    if route.query_type == "visual":
        from src.retrieval.pixelrag.search import visual_search
        from src.retrieval.pixelrag.index import QdrantVisualIndex
        from src.retrieval.pixelrag.embedder import Qwen3VLEmbedder
        from src.retrieval.pixelrag.reranker import Qwen3VLReranker
        from src.serving.multiplexer import ModelMultiplexer, FakeModelRuntime

        # Imposta max_resident=2 affinche' la coppia visiva embedder+reranker
        # coesista senza evincersi a vicenda. FakeModelRuntime per l'adapter Qwen.
        mux = ModelMultiplexer(runtime=FakeModelRuntime(), max_resident=2)
        embedder = Qwen3VLEmbedder(multiplexer=mux)
        reranker = Qwen3VLReranker(multiplexer=mux)
        index = QdrantVisualIndex(client)

        visual = visual_search(query, embedder=embedder, index=index, role=role, k=limit, reranker=reranker)

        text = (search(client, route.code_token, "sparse", limit, query_filter=vf)
                if route.code_token else [])
        text, children, hop2_warning = apply_hop2(
            client, text, role, version_status=version_status)
        return {"route": "visual", "reason": route.reason,
                "text_hits": text, "children_hits": children, "hop2_warning": hop2_warning,
                "visual_hits": visual}
    text, children, hop2_warning = apply_hop2(
        client, search(client, query, "routed", limit, query_filter=vf), role,
        version_status=version_status)
    return {"route": route.query_type, "reason": route.reason,
            "text_hits": text, "children_hits": children, "hop2_warning": hop2_warning,
            "visual_hits": []}
