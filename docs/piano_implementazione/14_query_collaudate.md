# Query collaudate per la demo — con risposte attese verificate

> **Come leggere questo file:** ogni query è stata eseguita dal reviewer contro il
> sistema VIVO nella configurazione demo finale (`demo-mercoledi @ 6c7602b`,
> tile_k=200, rerank top-8) la notte del 13-14/07. La "risposta attesa" è l'output
> osservato. Le query codice sono SOLO quelle uscite con fiducia ALTA (guardrail
> superato); le visive SOLO quelle col render giusto al primo posto.
> ⚠️ Il generatore non è deterministico al 100%: la formulazione può variare
> leggermente, ma codice/documento/pagina restano quelli.

## 1. Query CODICE (15, tutte ALTA con fonte)

| Query | Risposta attesa (doc, pagina) |
|---|---|
| `codice 14576` | "Bocca s/foro asta scarico / Spout without pop-up hole" (Mamoli_Catalogo_Ricambi_2021_IT_EN, pag. 100) |
| `codice 242788` | codice ricambio 242788 (gehl_v270_parts, pag. 171) |
| `codice GM41013` | assembly filtro separatore acqua-carburante, qty 1 (kohler_marine_tp6954, pag. 95) — ⭐ descrizione parlante, ottima in demo |
| `codice M125A-08-80` | valore 16.0 (kohler_industrial_tp7120, pag. 41) |
| `codice 961267` | CABLE SUPPORT ASSY. cilindri 2 e 4 (rotax_912_914_IPC, pag. 79) |
| `codice 877615` | EXTRACTOR ASSY., qty 1 (rotax_912iS_IPC, pag. 15) — già query 2 dello script |
| `codice 77300` | CYLINDER, PISTON AND RING ASSEMBLY, qty 1 (lycoming_IGO540_PC116, pag. 76) — ⭐ |
| `codice 67193` | Retainer (lycoming_IGO540_PC116, pag. 99) |
| `codice AF02110000E4` | SCREW, RKR LEVER ADJUSTING, qty 8 (branson_4520R_parts, pag. 14) |
| `codice HRA0100700A4` | (branson_4520R_parts, pag. 8) |
| `codice 415869` | VITE D.3,5X16 TSPTC AUTOF. UNI 6955 NERA (manzoni_comac_spareparts_A, pag. 45) |
| `codice 420025` | ANTIROMBO 123.5X155 H85 MOT ASPIRAZ. M17 (manzoni_comac_spareparts_A, pag. 47) — ⭐ |
| `codice 1H30023MM` | SPOOL ASSEMBLY (iwaki_TC-X252PH-NPT, pag. 3) |
| `codice 102036200PF` | M36 x 2, qty 55 (oleodinamica_sangro_catalogo_generale, pag. 44) |
| `codice 154060130` | M58 x 1,5, qty 59 (oleodinamica_sangro_catalogo_generale, pag. 35) |

⚠️ **RITIRATA**: `codice PD16T10-40AL-F` (era la query 3 dello script) — nel ricollaudo
è uscita BASSA (guardrail: codice non confermato). Sostituita con GM41013 nello script.

## 2. Query VISIVE (6 — il primo tab del viewer mostra la pagina indicata)

| Query | Lingua | Render atteso |
|---|---|---|
| `esploso albero motore e pistone del trattore Branson` | IT | branson_4520R_parts, pag. 22 — ⭐ visiva in ITALIANO |
| `exploded view of the Gehl foot valve` | EN | gehl_v270_parts, pag. 167 (titolare script) |
| `exploded view of the Kids MICRON sweeper` | EN | flow_exploded_2022, pag. 16 (titolare script) |
| `Explosionszeichnung der NordCap Kühltruhe` | DE | NordCap_Esploso_Ricambi_DE, pag. 2 — ⭐ visiva in TEDESCO |
| `esploso della duplicatrice Silca Triax con tabella ricambi` | IT | Silca_TriaxPro_Ricambi_Multilingua, pag. 2 |
| `despiece de la trituradora Panciroli BL200` | ES | panciroli_garmec_BL200, pag. 24 (riserva) |

Tempo a freddo ~1,5-2 min l'una: **vanno TUTTE pre-riscaldate la mattina** (dopo, dalla
cache, sono istantanee). Nei tab compaiono fino a 8 tavole simili per disambiguare.

## 3. Query NATURALI (2, fiducia MEDIA — provarle nella prova generale)

| Query | Risposta attesa |
|---|---|
| `chiusura di scarico con la retina per i capelli, diametro 72` | tappo con filtro ø72, codice 12045, (Mamoli, pag. 133 — primo tab coerente) |
| `quale norma indica il montaggio B3 o B5 di un motore elettrico?` | riferimento IEC 34-7 |

## 4. Rifiuto onesto (da vendere come feature)

| Query | Risposta attesa |
|---|---|
| `Qual è la capitale della Francia?` | "Non ho evidenza sufficiente per rispondere." + pannello documenti VUOTO |

## Regole d'ingaggio
1. In demo: partire dalle query dello script (file 11), attingere a QUESTA lista per
   bis/richieste extra. MAI improvvisare fuori lista.
2. Se il pubblico detta una query libera: accettarla solo su temi da manuale e
   presentare l'eventuale "non lo so" come feature.
3. I 21 codici scartati dal collaudo (MEDIA/BASSA) e le 9 visive rosse dell'eval NON
   vanno usati: il sistema lì risponde onestamente ma senza il colpo di scena ALTA.
