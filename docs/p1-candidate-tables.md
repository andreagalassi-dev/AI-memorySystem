# P1 — Tabelle candidate per il ground truth (per lingua)

> Selezione del **campione difficile** del gate (P1.2). Ogni lingua ha ≥2 tabelle,
> scelte per coprire sia un caso **pulito** sia un **worst-case**. Riferimenti a
> file/pagina/foglio verificati sull'output baseline (`output/spike/baseline/`).
> Questi `table_id` sono la fonte per `scripts/prepare_ground_truth.py`.

## Sintesi
| table_id | file | pagina/foglio | lingua/e | profilo | perché |
|----------|------|---------------|----------|---------|--------|
| `MAMOLI_P231_IT` | Mamoli_Catalogo_Ricambi_2021 | p.231 | IT (/EN) | pulita-ish | listino ricambi denso; espone **`**` iniettato nei codici** (`V0**91568**00007`) |
| `MAMOLI_P060_IT` | Mamoli_Catalogo_Ricambi_2021 | p.60 | IT (/EN) | **worst-case** | più ricambi (pos/codice/qtà/prezzo) **compressi in una cella** con `<br>`; header sbavato `RICAMBI/SPAR`\|`E PARTS` |
| `NORDCAP_P003_DE` | NordCap_Esploso_Ricambi_DE | p.3 | DE | pulita | `Bild\|Artikelnr.\|Artikelbezeichnung`, 13 righe nette |
| `NORDCAP_P004_DE` | NordCap_Esploso_Ricambi_DE | p.4 | DE | **worst-case** | header spezzato `2551-P0485-1<br>A`\|`rtikelnr.`; descrizioni che sbavano tra colonne |
| `ELECTROLUX_EN` | Electrolux_Distinta_Ricambi | foglio `PARTS_LIST_-_ENGLISH` | EN | pulita (XLSX) | riferimento EN "già pulito"; header a riga 6 (NO/DESCRIPTION/QTY/COMPONENT PNC) |
| `DELITOOLS_EN` | DeliTools_SpareParts_List | foglio `Sheet1` | EN (+ZH) | pulita (XLSX) | riferimento EN; header a riga 2; descrizione EN + cinese |
| `SILCA_P003` | Silca_TriaxPro_Ricambi_Multilingua | p.3 | IT/EN/DE/FR/ES | multilingua | colonne `Ref\|Code\|IT\|EN\|DE\|FR\|ES` — 1 lingua per colonna; `Code<br>descr` |
| `SILCA_P004` | Silca_TriaxPro_Ricambi_Multilingua | p.4 | IT/EN/DE/FR/ES | multilingua | continuazione tabella Silca (righe 53+) — test tabella **multi-pagina** |
| `ELECTROLUX_ES` | Electrolux_Distinta_Ricambi | foglio `PARTS_LIST_-_SPANISH` | ES | pulita (XLSX) | riferimento ES "già pulito" |

## Copertura per lingua (per la Metrica 1)
- **IT** → `MAMOLI_P231_IT`, `MAMOLI_P060_IT` (+ colonna IT di Silca come cross-check).
- **EN** → `ELECTROLUX_EN`, `DELITOOLS_EN` (+ colonna EN di Silca).
- **DE** → `NORDCAP_P003_DE`, `NORDCAP_P004_DE` (+ colonna DE di Silca).
- **FR** → colonne FR di `SILCA_P003`, `SILCA_P004`.
- **ES** → `ELECTROLUX_ES` + colonne ES di `SILCA_P003`, `SILCA_P004`.

## Modi di fallimento coperti dal campione (SPEC §D)
- **Celle unite / righe-multiple in una cella** → `MAMOLI_P060_IT`, colonne Silca (`Code<br>descr`).
- **Header ripetuto/spezzato** → `MAMOLI_P231_IT`, `NORDCAP_P004_DE`.
- **Tabella multi-pagina** → `SILCA_P003`+`SILCA_P004` (stessa tabella, header solo su p.3).
- **Codici corrotti da markup** → `MAMOLI_*` (`**` nei codici).
- **Reading order multilingua affiancato** → `SILCA_*`.
- **Esploso / pagina grafica** → NordCap (pagina esploso è immagine; qui usiamo le
  pagine-tabella p.3/p.4, non la pagina-immagine p.2, che va alla pipeline visiva P2).

## Note
- Mamoli è IT/EN nella **stessa cella** descrizione (`italiano/english`): la tabella
  è assegnata a **IT** per la Metrica 1; la parte EN è coperta dai due XLSX e da Silca.
- I due XLSX servono da **ancora affidabile** per EN/ES: baseline già sufficiente,
  Marker probabilmente overkill — utile come termine di paragone alto.
