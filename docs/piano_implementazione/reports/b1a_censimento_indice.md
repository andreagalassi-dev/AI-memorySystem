# Censimento dell'Indice e Coverage (B1.a)

## 1. Numeri Totali

- **Totale PDF nel corpus**: 142
- **PDF Indicizzati (Univoci)**: 53
- **PDF Assenti dall'indice**: 89
- **Controllo Riconciliazione**: 53 (indicizzati) + 89 (assenti) = 142 PDF totali.

*Nota:* Nel database sono presenti 59 record in `public.documents`, ma 4 di questi (`DeliTools_SpareParts_List.xlsx`, `Electrolux_Distinta_Ricambi.xlsx`, `Mamoli_Test`, `GenericBrand_Test`) non corrispondono a file `.pdf` nel corpus. Un altro record corrisponde a un match ambiguo (vedi sezione `DA_INDAGARE`).

## 2. Tabella per Motivo (sui file assenti)

| Motivo | Conteggio | Descrizione |
|---|---|---|
| **MAI_TENTATO** | 88 | Nessun match coi `glob_pattern` di `ingestion_doc_map`. Mai passati alla pipeline. |
| **DA_INDAGARE** | 1 | Match ambiguo (il file corrisponde a più slug nel DB: `Mamoli_Catalogo_Ricambi_2021_IT_EN` e `Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf`). |
| **SCARTATO_DISPATCH** | 0 | Nessun file rientra in questa casistica. |
| **FALLITO_ESTRAZIONE** | 0 | Nessun file rientra in questa casistica. |
| **FORMATO_NON_GESTITO** | 0 | Nessun file rientra in questa casistica. |

## 3. Tabella per Categoria di Corpus

Conteggio dei soli file assenti raggruppati per categoria (sottocartella del corpus) e motivo:

| Categoria | MAI_TENTATO | DA_INDAGARE |
|---|---|---|
| `price_lists` | 28 | 0 |
| `electrical_components` | 13 | 0 |
| `spare_parts_lists` | 13 | 0 |
| `industrial_machinery` | 11 | 0 |
| `hardware_fasteners` | 7 | 0 |
| `tools_equipment` | 6 | 0 |
| `hydraulics` | 5 | 0 |
| `textile` | 2 | 0 |
| `construction_agricultural` | 1 | 0 |
| `engines` | 1 | 0 |
| `industrial_cleaning` | 1 | 0 |
| `original_dataset` | 0 | 1 |

## 4. Elenco Completo DA_INDAGARE

I seguenti PDF presentano un'ambiguità tra il nome del file e lo slug registrato nel database:
- `corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf` (Nel database esistono i documenti `Mamoli_Catalogo_Ricambi_2021_IT_EN` e `Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf`, rendendo impossibile un'associazione 1:1 certa).
