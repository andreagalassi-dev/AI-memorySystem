# Handoff P4 → P5 — stato del sistema e contratti per la UI

> **P4 CHIUSO** (P4.1–P4.6). Data: 2026-07-04. DoD verificato FORMALMENTE
> end-to-end con switch reale + ripristino (`scripts/verify_p4_dod.py`):
> (1) nuova versione marca la vecchia `superseded` ✅ (2) avviso obsoleto
> compare ✅ (3) un ruolo non vede i documenti di un altro ✅.
> Regole invariate: un Punto alla volta, plan mode, OK esplicito, micro-step.

## 1. Architettura dati (novità P4)

**PostgreSQL = SSOT assoluta** di versioni e ruoli (`aike_postgres`,
postgres:17-alpine, named volume `postgres_storage`). **Qdrant = proiezione**:
si riconcilia DALLA SSOT, mai il contrario.

- Schema (`src/registry/schema.sql`): `documents` (slug logico) →
  `document_versions` (doc_stem fisico = join col payload `doc` di ENTRAMBE
  le collection; indice parziale **one-current-per-document**) →
  `document_roles` (CHECK sui 3 ruoli). Stato attuale: 57 documenti, tutti
  v1/current; ruoli: tecnico 57, commerciale 11, amministrazione 11.
- Accesso: `src/registry/registry.py` (connessione INIETTATA, transazioni
  interne). Config in `Settings` (`src/backend/config.py`), client factory
  `src/backend/clients/postgres_client.py`.
- Ruoli tipizzati: `src/common/roles.py` — `Role` StrEnum
  {commerciale, tecnico, amministrazione} + `parse_role` fail-closed.
  Seed per categoria: `config/default_roles.json` (tecnico vede tutto;
  commerciale/amministrazione solo price_lists + corporate_brochures).

## 2. Qdrant — payload dopo P4.2 (additivo)

Entrambe le collection hanno +3 chiavi: `version_status`
(current|superseded), `doc_version` (int), `ruoli_consentiti`
(list[keyword]) + **indici keyword** su version_status e ruoli_consentiti.
Chiavi e indici P2/P3 INTATTI (code/doc/lang/status; doc/page/cat).
32.966 + 1.035 punti, tutti current.

## 3. Flussi operativi

- **Nuova versione**: `uv run scripts/supersede_document.py <slug> <new_stem>
  --langs it,en` — fase 1 atomica su PG (guardia idempotenza: il rilancio
  post-crash NON crea v3), fase 2 riconciliazione convergente
  (`src/registry/sync.py`, patch selettiva con count esatti per stem).
  **Rilanciabile alla cieca dopo qualsiasi timeout.** Prima si ingesta il
  nuovo stem (pipeline P2), poi si fa lo switch; se v2 non ha punti lo
  script avvisa e la sync converge al run successivo.
- **Riconciliazione totale**: `uv run scripts/backfill_qdrant.py`
  (idempotente, "0 patchati" a regime). Registro: `registry_populate.py`.

## 4. API del motore (contratto per la UI P5)

```python
from src.graph.pipeline import answer
r = answer(query: str, role: Role | str)   # role OBBLIGATORIO
```
- `role` mancante → TypeError; None → TypeError; ignoto → ValueError.
  Difesa in profondità: anche l'invoke diretto del grafo senza ruolo valido
  → BASSA "Richiesta rifiutata: ruolo mancante o non valido" (mai retrieval/LLM).
- Risposta: `final_answer`, `final_level` (ALTA|MEDIA|BASSA), `audit`
  (lista, include `router[<ruolo>]` e l'eventuale `fallback su SOSTITUITO`),
  `evidence` (NamedTuple, ora con `version_status` e `doc_version`).
- **Avviso obsoleto** (per la UI, punto (5) del wow-effect P5): se la
  risposta viene SOLO dal sostituito, `final_answer` termina con
  `"ATTENZIONE: risposta basata su documentazione SOSTITUITA (obsoleta).
  Verificare la versione corrente."` e il livello è cappato a MEDIA.
  Cascade: superseded interrogato SOLO se la via current dà BASSA; le
  evidence superseded SOVRASCRIVONO le current (mai contesto misto).
- Filtro Qdrant: `access_filter(version_status, role)` = AND(must) su
  versione+ruolo, dentro `query_filter` di `search()` (prefetch ibridi
  inclusi). Scoring intoccato.

## 5. Scelte deliberate da NON "correggere" per sbaglio

1. `catalog_lookup` (guardrail) SENZA filtro versione/ruolo: verifica
   interna di esistenza codice; filtrarla creerebbe falsi BLOCK.
2. `cross_probe`/fusion senza filtro ruolo: NON cablata in produzione
   (`cross_probe=None`); quando P5+ la cablerà, aggiungere il filtro ruolo
   sul visivo (gancio: `search_visual` + `access_filter`).
3. Cascade solo su BASSA del RETRIEVAL: il rifiuto dell'LLM (seconda linea
   "non lo so") NON attiva retro-fallback, per design.
4. Doc con `cat=None` (3 stem storici con estensione nel nome): ruoli
   corretti a mano nella SSOT (2026-07-04). Un reseed da zero li
   riporterebbe al fallback tecnico: la SSOT è la verità, non rigenerarla.

## 6. Debiti/lezioni (non bloccanti)

- **Margine 0.00 su codici duplicati intra-doc**: righe ripetute dello
  stesso codice → score identici → "non lo so" (visto con '90716C' su
  Mamoli in P4.6). Debito di calibrazione P3.2, candidato a P5+ col
  feedback del tecnico.
- Smoke/eval su Qdrant SEMPRE con `SearchParams(exact=True)` e confronto
  ordinato: HNSW ha wobble di recall al confine top-k; i quasi-duplicati
  hanno score cosine identici a ordine libero.
- Esistono DUE stem Mamoli distinti in Qdrant (`..._IT_EN` 7245 punti e
  `..._IT_EN.pdf`, path ingestion diversi): da unificare in un futuro
  re-index, NON in patch.
- Debito lint preesistente in script P1 (12 errori ruff): sprint di
  cleanup dedicato a fine fase, come concordato.
- Tallone naturali IT cross-lingua (P2) invariato.

## 7. Puntatori

Grafo: `src/graph/pipeline.py`. Registro: `src/registry/`. Filtri:
`src/retrieval/search.py` (`version_filter`, `access_filter`). Verifica
DoD: `scripts/verify_p4_dod.py` (switch reale REVERSIBILE: ripristina da
solo; rilanciabile). Test: 300 verdi (`tests/test_registry.py`,
`test_sync.py`, `test_graph_versioning.py`, `test_rbac.py`,
`test_search_filter.py` + suite P1–P3). Spec: `docs/SPEC.md` §E, §F.3.
