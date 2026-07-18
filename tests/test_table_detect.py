"""Fase 1.2 — Test del rilevamento pagine-tabella (euristica deterministica).

Unit: le regole pure su feature sintetiche (griglia, borderless, prosa, casi-guardia).
Integrazione: PDF sintetici via fitz (griglia rigata, listino borderless a colonne,
prosa) — ermetici, deterministici, nessun modello.
"""

from __future__ import annotations

import fitz

from src.ingestion.extraction.table_detect import classify_table, is_table_page


def _f(h_lines=0, v_lines=0, n_words=0, n_cols=0, n_rows=0, numeric_ratio=0.0):
    return {"h_lines": h_lines, "v_lines": v_lines, "n_words": n_words,
            "n_cols": n_cols, "n_rows": n_rows, "numeric_ratio": numeric_ratio}


# ---------------------------------------------------------- unit: classify_table


class TestClassifyRegole:
    def test_griglia_rigata(self):
        v = classify_table(_f(h_lines=5, v_lines=5, n_words=20))
        assert v.is_table and "griglia" in v.reason

    def test_borderless_a_colonne(self):
        v = classify_table(_f(n_words=24, n_cols=3, n_rows=8, numeric_ratio=0.5))
        assert v.is_table and "borderless" in v.reason

    def test_prosa_non_e_tabella(self):
        # molte righe ma una sola colonna e pochissimi valori
        v = classify_table(_f(n_words=90, n_cols=1, n_rows=18, numeric_ratio=0.02))
        assert not v.is_table

    def test_griglia_senza_contenuto_non_e_tabella(self):
        # cornice/box decorativo senza testo: la guardia MIN_WORDS blocca
        v = classify_table(_f(h_lines=5, v_lines=5, n_words=2))
        assert not v.is_table and "scarna" in v.reason

    def test_colonne_senza_valori_non_e_tabella(self):
        # allineamento a colonne ma densita' numerica sotto soglia
        v = classify_table(_f(n_words=30, n_cols=3, n_rows=8, numeric_ratio=0.05))
        assert not v.is_table

    def test_una_sola_famiglia_di_linee_non_basta(self):
        # solo orizzontali (es. righe di sottolineatura), niente verticali
        v = classify_table(_f(h_lines=8, v_lines=0, n_words=20))
        assert not v.is_table


# ------------------------------------------------------- integrazione: fitz


class TestIntegrazione:
    def test_griglia_rigata_reale(self):
        doc = fitz.open()
        page = doc.new_page()
        # 4 linee orizzontali + 4 verticali = griglia 3x3
        for y in (100, 120, 140, 160):
            page.draw_line((72, y), (300, y))
        for x in (72, 148, 224, 300):
            page.draw_line((x, 100), (x, 160))
        for i, y in enumerate((105, 125, 145)):
            page.insert_text((80, y), f"V0-{i} 12,50 pezzo", fontsize=8)

        v = is_table_page(page)
        assert v.is_table
        assert v.features["h_lines"] >= 3 and v.features["v_lines"] >= 3
        doc.close()

    def test_listino_borderless_reale(self):
        doc = fitz.open()
        page = doc.new_page()
        # 3 colonne allineate (x fissi) su 8 righe, con codici e prezzi, NESSUNA linea
        for row in range(8):
            y = 100 + row * 18
            page.insert_text((72, y), f"AB-{row:03d}0", fontsize=8)   # colonna codice
            page.insert_text((200, y), "Guarnizione", fontsize=8)     # colonna descr
            page.insert_text((400, y), f"{10 + row},50", fontsize=8)  # colonna prezzo

        v = is_table_page(page)
        assert v.is_table
        assert v.features["h_lines"] == 0 and v.features["v_lines"] == 0
        assert v.features["n_cols"] >= 3
        doc.close()

    def test_prosa_reale_non_e_tabella(self):
        doc = fitz.open()
        page = doc.new_page()
        body = "\n".join(
            "Avvertenze di sicurezza: leggere attentamente il manuale prima "
            "dell uso e conservare per future consultazioni." for _ in range(10))
        page.insert_text((72, 72), body, fontsize=9)

        v = is_table_page(page)
        assert not v.is_table
        doc.close()
