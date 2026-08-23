"""Riparazione di COSA finisce nell'indice: identita' del documento nel denso,
codice ATOMICO nello sparse.

Due difetti misurati sull'indice di produzione `aike_chunks` (199447 punti):

 1. IDENTITA' ASSENTE. `dense_text` vedeva solo la descrizione della riga: in
    tutto `westermann_kehrmaschine` i token 'westermann'/'kehrmaschine' non
    compaiono mai nel testo indicizzato, mentre l'utente scrive "spazzatrice
    Westermann". 23 documenti (114050 chunk, 74.6% dell'indice) hanno identita'
    assente o quasi. Esperimento con BGE-M3 reale (2114 chunk, 7 query):
    aggiungendo l'identita' al DENSO le query che trovano il documento giusto
    passano da 3/7 a 7/7, tutte a rango 1; la separazione fra documenti
    raddoppia (0.106 -> 0.264) e la discriminazione DENTRO il documento
    MIGLIORA (MRR 0.692 -> 0.727). Aggiungere la PAGINA invece PEGGIORA:
    non si mette (vedi `test_mai_la_pagina_nel_denso`).

 2. CODICE FRANTUMATO. La docstring di embedding.py dichiarava il codice
    "token ATOMICO": era FALSO. `SimpleTokenizer` di BM25 sostituisce ogni
    non-`\\w` con spazio, quindi `14M-40` diventa due termini e `14M-50` ne
    condivide uno. Sull'indice reale 16108 codici distinti su 40111 (40.2%)
    contengono un separatore: e' un generatore latente di "quasi giusti", la
    classe di errore che CLAUDE.md vieta. `HD3-ES-1C/20` e `HD3-ES-0C/20`
    condividono oggi 3 termini su 4.

Il test piu' importante di questo file e' `TestCodiceAtomico`: e' la difesa
diretta contro i "quasi giusti".

FORMA DELLA DIFESA — corretta dopo una regressione MISURATA. La prima versione
metteva nell'indice il SOLO codice incollato, per ottenere che due codici quasi
giusti non condividessero alcun termine. Troppo forte: C004 ("codice
M125A-08-80") e' passato da rango 1 a NESSUN risultato, perche' il chunk giusto
- ridotto a un solo termine - non aveva piu' con cui battere i chunk
zuppa-di-numeri, che dai frammenti '08'/'80' della query si facevano colpire
piu' volte (26.55 contro 15.996). L'invariante giusto non e' "nessun termine in
comune", e' "esiste un termine che DISCRIMINA": il termine incollato, esclusivo
del codice esatto e rarissimo, quindi ad alto IDF. I frammenti restano su
ENTRAMBI i lati (documento e query). Vedi `TestRegressioneZuppaDiNumeri`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.ingestion.embedding import (
    atomic_code,
    dense_text,
    doc_identity,
    sparse_query_text,
    sparse_text,
)

ROOT = Path(__file__).resolve().parents[1]

CHUNK = {
    "chunk_id": "westermann_kehrmaschine/p0004/t1/r003",
    "code": "14M-40", "pos": "1B", "desc": "Vite M5/M5 screw",
    "qty": "50", "price": "20,00",
    "doc": "westermann_kehrmaschine", "page": 4, "lang": "DE",
}

# Coppie di codici REALI presi da `aike_chunks` che oggi condividono termini.
QUASI_GIUSTI = [
    ("14M-40", "14M-50"),
    ("HD3-ES-1C/20", "HD3-ES-0C/20"),
    ("HD3-ES-0ML/20", "HD3-ES-1LL/20"),
    ("18111-67JG3", "18111-67JG4-000"),
    ("X-75-55", "X-6210-7"),
    ("40.I10.F40.040", "40.I10.E50.025"),
    ("PR.A08.A10.011", "PR.A17.K05.020"),
    ("PDTL168-8M-50C", "PD60ST5-27AL-N"),
]


def _doc_terms(code: str) -> set[int]:
    """Termini sparse lato DOCUMENTO di un chunk che ha solo il codice."""
    from src.ingestion.embedding import embed_sparse_docs
    chunk = {"code": code, "pos": None, "desc": None}
    return {int(i) for i in embed_sparse_docs([sparse_text(chunk)])[0].indices}


def _query_terms(text: str) -> set[int]:
    from src.ingestion.embedding import embed_sparse_query
    return {int(i) for i in embed_sparse_query(sparse_query_text(text)).indices}


def _termine_incollato(code: str) -> set[int]:
    """Il solo termine BM25 della forma incollata: e' il segnale discriminante."""
    from src.ingestion.embedding import embed_sparse_docs
    return {int(i) for i in embed_sparse_docs([atomic_code(code)])[0].indices}


def _termini_testo(testo: str) -> set[int]:
    from src.ingestion.embedding import embed_sparse_docs
    return {int(i) for i in embed_sparse_docs([testo])[0].indices}


# --------------------------------------------------------------- codice atomico


class TestAtomicCode:
    def test_rimuove_i_separatori(self):
        assert atomic_code("14M-40") == "14m40"
        assert atomic_code("HD3-ES-1C/20") == "hd3es1c20"
        assert atomic_code("40.I10.F40.040") == "40i10f40040"

    def test_codice_gia_atomico_invariato(self):
        # 24003 codici su 40111 non hanno separatori: per loro nulla cambia.
        assert atomic_code("877615") == "877615"
        assert atomic_code("0USBX4") == "0usbx4"

    def test_none_e_degeneri(self):
        assert atomic_code(None) is None
        assert atomic_code("") is None
        assert atomic_code("---") is None  # nessun alfanumerico: non e' un codice


class TestCodiceAtomico:
    """IL test: due codici "quasi giusti" non devono condividere NESSUN termine."""

    @pytest.mark.parametrize("a,b", QUASI_GIUSTI)
    def test_il_termine_incollato_e_esclusivo_del_codice_esatto(self, a, b):
        """L'invariante che conta: esiste un termine presente nel codice esatto
        e ASSENTE dal quasi-giusto. Senza atomicita' non esisteva — 'HD3-ES-1C/20'
        e 'HD3-ES-0C/20' avevano SOLO termini in comune (3 su 4)."""
        ga = _termine_incollato(a)
        assert ga, f"{a} deve produrre un termine incollato"
        assert ga <= _doc_terms(a), f"il termine incollato di {a} non e' nell'indice"
        assert ga & _doc_terms(b) == set(), (
            f"il termine incollato di {a} compare anche in {b}: "
            "non discrimina piu' i 'quasi giusti'")

    @pytest.mark.parametrize("a,b", QUASI_GIUSTI)
    def test_la_query_raggiunge_il_suo_codice_col_termine_discriminante(self, a, b):
        """Contro-prova: la query deve colpire il termine esclusivo del proprio
        codice, e non quello del gemello. Che condivida dei FRAMMENTI col
        gemello e' ammesso e voluto (i frammenti servono a battere il rumore);
        cio' che non deve mai accadere e' condividere il discriminante."""
        q = _query_terms(a)
        assert q & _termine_incollato(a), f"la query '{a}' non raggiunge il suo codice"
        assert q & _termine_incollato(b) == set(), (
            f"la query '{a}' colpisce il termine esclusivo del gemello {b}")

    @pytest.mark.parametrize("a,b", QUASI_GIUSTI)
    def test_i_frammenti_restano_indicizzati(self, a, b):
        """Guardia contro la regressione opposta (quella che e' successa
        davvero): il codice non deve MAI ridursi al solo termine incollato."""
        if atomic_code(a) == a.lower():
            pytest.skip("codice senza separatori: non ha frammenti distinti")
        assert len(_doc_terms(a)) > len(_termine_incollato(a)), (
            f"{a} e' entrato nell'indice col solo termine incollato: e' la "
            "riduzione che ha fatto sparire C004 dai risultati")

    def test_codice_scritto_senza_separatori_trova_lo_stesso_chunk(self):
        """Guadagno collaterale: oggi 'HD3ES1C20' non trovava 'HD3-ES-1C/20'
        (nessun termine in comune). Con l'atomicita' lo trova."""
        assert _query_terms("HD3ES1C20") & _doc_terms("HD3-ES-1C/20")


class TestSparseQueryText:
    def test_e_additiva_mai_distruttiva(self):
        """La forma incollata si AGGIUNGE, non sostituisce: cosi' la stessa
        funzione resta compatibile con l'indice v1 (codici frantumati), dove
        i termini originali sono gli unici che matchano."""
        out = sparse_query_text("prezzo del 14M-40")
        assert "14M-40" in out and "14m40" in out

    def test_non_tocca_il_linguaggio_naturale(self):
        q = "quanto costa la spazzatrice Westermann?"
        assert sparse_query_text(q) == q

    def test_non_incolla_i_prezzi(self):
        # '20,00' non e' un codice: incollarlo a '2000' inventerebbe un termine.
        assert sparse_query_text("costa 20,00 euro") == "costa 20,00 euro"

    def test_nessun_duplicato(self):
        out = sparse_query_text("14M-40 e 14M-40")
        assert out.split().count("14m40") == 1


class TestSparseText:
    def test_contiene_sia_i_frammenti_sia_la_forma_incollata(self):
        t = sparse_text(CHUNK)
        assert "14m40" in t.split(), "manca il termine discriminante"
        assert "14M-40" in t.split(), "mancano i frammenti (regressione C004)"

    def test_codice_senza_separatori_non_e_duplicato(self):
        # ripetere '877615' raddoppierebbe la sua term frequency rispetto ai
        # codici con separatori: confronto falsato fra codici
        assert sparse_text({**CHUNK, "code": "877615"}).split().count("877615") == 1

    def test_pos_e_desc_restano(self):
        t = sparse_text(CHUNK)
        assert "1B" in t.split()
        assert "vite" in t

    def test_niente_identita_documento_nello_sparse(self):
        """L'identita' va SOLO nel denso: nello sparse gonfierebbe la term
        frequency di 'westermann' su tutti i chunk del documento, spostando
        l'IDF senza aggiungere informazione."""
        assert "westermann" not in sparse_text(CHUNK).lower()

    def test_senza_codice_non_esplode(self):
        assert sparse_text({**CHUNK, "code": None}) == "1B vite m5/m5 screw"


# ------------------------------------------------------- identita' del documento


class TestDocIdentity:
    def test_stem_normalizzato(self):
        assert doc_identity({"doc": "westermann_kehrmaschine"}) == "westermann kehrmaschine"
        assert doc_identity({"doc": "kohler_industrial_tp7120"}) == "kohler industrial tp7120"

    def test_estensione_rimossa(self):
        # 218 chunk reali hanno il doc con estensione ('....pdf', '....xlsx'):
        # 'pdf' sarebbe un token di rumore presente solo in quei documenti.
        assert doc_identity({"doc": "Electrolux_Distinta_Ricambi.xlsx"}) == \
            "electrolux distinta ricambi"
        assert doc_identity({"doc": "Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf"}) == \
            "mamoli catalogo ricambi 2021 it en"

    def test_fallback_su_doc_stem(self):
        assert doc_identity({"doc_stem": "gehl_v270_parts"}) == "gehl v270 parts"

    def test_assente(self):
        assert doc_identity({}) is None
        assert doc_identity({"doc": None}) is None


class TestDenseText:
    def test_identita_prima_della_descrizione(self):
        assert dense_text(CHUNK) == "westermann kehrmaschine: vite m5/m5 screw"

    def test_mai_la_pagina_nel_denso(self):
        """Misurato: aggiungere la pagina al denso PEGGIORA. Guardia esplicita."""
        t = dense_text({**CHUNK, "page": 4, "desc": "Vite M5"})
        assert "pagina" not in t and "page" not in t
        assert "p0004" not in t

    def test_mai_codice_qty_prezzo_nel_denso(self):
        t = dense_text(CHUNK)
        for vietato in ("14M-40", "14m40", "1B", "20,00", "50"):
            assert vietato not in t

    def test_senza_desc_nessun_vettore_denso(self):
        """Invariato di proposito: 13125 chunk senza descrizione resterebbero
        con un denso fatto della SOLA identita', identico per tutto il
        documento — un attrattore che sommergerebbe le righe vere."""
        assert dense_text({**CHUNK, "desc": None}) is None

    def test_senza_identita_resta_la_sola_desc(self):
        assert dense_text({**CHUNK, "doc": None}) == "vite m5/m5 screw"


# ------------------------------------- la guardia contro l'errore piu' insidioso


class TestTextPrepMaiAQueryTime:
    """`dense_text` aggiunge l'identita' del DOCUMENTO: se una query passasse
    dalla stessa funzione, la domanda dell'utente verrebbe contaminata con
    l'identita' di un documento arbitrario, e l'A/B misurerebbe un'illusione.
    Verificato che oggi non accade (search.py embedda il testo grezzo della
    query); questo test lo CABLA, cosi' non puo' tornare vero in silenzio.

    Il controllo e' sull'AST, non sul testo: cercare le stringhe nel sorgente
    darebbe falsi allarmi sui commenti che PARLANO di queste funzioni."""

    NOMI_VIETATI = {"dense_text", "sparse_text"}

    def _moduli_retrieval(self):
        return sorted((ROOT / "src" / "retrieval").rglob("*.py"))

    def test_nessun_modulo_di_retrieval_importa_la_text_prep(self):
        colpevoli = []
        for py in self._moduli_retrieval():
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and \
                        node.module.startswith("src.ingestion"):
                    for a in node.names:
                        if a.name in self.NOMI_VIETATI:
                            colpevoli.append(f"{py.relative_to(ROOT)}:{a.name}")
        assert colpevoli == [], (
            f"text-prep di INGESTION importata a query-time: {colpevoli}")

    def test_nessun_modulo_di_retrieval_chiama_la_text_prep(self):
        colpevoli = []
        for py in self._moduli_retrieval():
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                nome = f.id if isinstance(f, ast.Name) else (
                    f.attr if isinstance(f, ast.Attribute) else None)
                if nome in self.NOMI_VIETATI:
                    colpevoli.append(f"{py.relative_to(ROOT)}:{node.lineno}")
        assert colpevoli == [], (
            f"text-prep di INGESTION chiamata a query-time: {colpevoli}")

    def test_la_guardia_vede_davvero_la_violazione(self):
        """Anti-"guardia scritta e mai cablata": se introduco la violazione,
        il controllo AST deve accorgersene."""
        tree = ast.parse("from src.ingestion.embedding import dense_text\n"
                         "x = dense_text({})\n")
        importati = {a.name for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom) for a in n.names}
        chiamati = {n.func.id for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert importati & self.NOMI_VIETATI and chiamati & self.NOMI_VIETATI


# --------------------------------------- regressione C004: la zuppa di numeri


class TestRegressioneZuppaDiNumeri:
    """Il caso REALE che ha bocciato la prima versione di `sparse_text`.

    C004 ("codice M125A-08-80") era a rango 1 in produzione; con il codice
    ridotto alla sola forma incollata e' sparito dai risultati. A vincere erano
    chunk senza codice e con descrizioni zuppa-di-numeri ('Item Part No.
    Description Qty. | 1 2 3'), colpiti piu' volte dai frammenti '08' e '80'
    della query: 26.55 contro 15.996.

    Il test riproduce il MECCANISMO su un mini-corpus in cui i frammenti
    numerici sono comuni (IDF basso) e il codice esatto e' raro (IDF alto),
    ordinando con la stessa formula di Qdrant (modifier=IDF, valori di
    documento BM25 di fastembed, valori di query 1).

    ONESTA' SU COSA QUESTO TEST PROVA E COSA NO. Su 6 documenti il chunk giusto
    vince in ENTRAMBE le varianti: quello che il mini-corpus riproduce non e'
    il sorpasso, e' il CROLLO DEL MARGINE che lo rende possibile —
        additivo        [5.89, 0.0, 0.80, 0.95, 0.91, 0.95]  margine 4.94
        solo incollato  [2.57, 0.0, 1.47, 1.75, 1.67, 1.75]  margine 0.82
    cioe' il vantaggio del chunk giusto si riduce di sei volte. Il sorpasso
    vero ha bisogno dell'IDF di una collezione da 199447 punti, dove le zuppe
    di numeri sono migliaia: quella prova non sta in una unit test e si fa dal
    vivo con `scripts/ab_code_branch.py` (criterio: 22/22 come la produzione).
    Il test qui sotto e' quindi una sentinella sul margine, non una simulazione
    del gate: e' `test_la_variante_solo_incollato_dimezza_il_margine` a
    discriminare le due varianti, non il test del vincitore.
    """

    CODICE = "M125A-08-80"
    QUERY = "codice M125A-08-80"
    GIUSTO = {"code": CODICE, "pos": "12",
              "desc": "Cylinder head gasket kit"}
    # zuppe di numeri reali: nessun codice, cifre a raffica
    ZUPPE = [
        {"code": None, "pos": None, "desc": "Item Part No. Description Qty. | 1 2 3"},
        {"code": None, "pos": None, "desc": "Relay 268371 | Bracket 08 80 12 4"},
        {"code": None, "pos": None, "desc": "08 80 08 80 08 80 12 24 36 48"},
        {"code": None, "pos": None, "desc": "Ref 08 Qty 80 Item 08 Part 80"},
        {"code": None, "pos": None, "desc": "1 08 2 80 3 08 4 80 5 08 6 80"},
    ]

    @staticmethod
    def _classifica(testi: list[str], query: str) -> list[float]:
        """Punteggio BM25 con IDF di collezione, come lo calcola Qdrant."""
        import math

        from src.ingestion.embedding import embed_sparse_docs, embed_sparse_query

        docs = [{int(i): float(v) for i, v in zip(d.indices, d.values)}
                for d in embed_sparse_docs(testi)]
        n_doc = len(docs)
        df: dict[int, int] = {}
        for d in docs:
            for t in d:
                df[t] = df.get(t, 0) + 1
        q = embed_sparse_query(sparse_query_text(query))
        termini_query = {int(i) for i in q.indices}
        punteggi = []
        for d in docs:
            s = 0.0
            for t in termini_query & set(d):
                idf = math.log(1 + (n_doc - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * d[t]
            punteggi.append(s)
        return punteggi

    def _testi(self, solo_incollato: bool) -> list[str]:
        if not solo_incollato:
            return [sparse_text(c) for c in [self.GIUSTO, *self.ZUPPE]]
        # la variante bocciata: il codice ridotto alla sola forma incollata
        fuori = [f"{self.GIUSTO['pos']} {atomic_code(self.CODICE)} "
                 f"{self.GIUSTO['desc'].lower()}"]
        return fuori + [sparse_text(c) for c in self.ZUPPE]

    def test_il_chunk_col_codice_esatto_batte_la_zuppa_di_numeri(self):
        """Condizione necessaria (non sufficiente: vedi la docstring di classe —
        su 6 documenti passerebbe anche la variante bocciata)."""
        punteggi = self._classifica(self._testi(solo_incollato=False), self.QUERY)
        migliore = max(range(len(punteggi)), key=punteggi.__getitem__)
        assert migliore == 0, (
            f"la zuppa di numeri batte il codice esatto: {punteggi} — e' "
            "esattamente la regressione C004")

    def test_la_variante_solo_incollato_dimezza_il_margine(self):
        """E' QUESTO il test che discrimina le due varianti. Togliendo i
        frammenti al documento il vantaggio del chunk giusto sulle zuppe di
        numeri crolla (misurato: 4.94 -> 0.82, sei volte meno). Su una
        collezione reale quel margine residuo non basta e il chunk sparisce."""
        con = self._classifica(self._testi(solo_incollato=False), self.QUERY)
        senza = self._classifica(self._testi(solo_incollato=True), self.QUERY)
        margine_con = con[0] - max(con[1:])
        margine_senza = senza[0] - max(senza[1:])
        assert margine_con > 3 * margine_senza, (
            "togliere i frammenti non fa crollare il margine: il mini-corpus "
            f"non riproduce piu' il meccanismo ({margine_con} vs {margine_senza})")

    def test_termini_in_comune_query_documento(self):
        """Il numero che il reviewer ha misurato: 4 termini in comune con la
        text-prep additiva, 1 solo con la variante bocciata."""
        q = _query_terms(self.QUERY)
        additivo = _termini_testo(sparse_text({"code": self.CODICE,
                                               "pos": None, "desc": None}))
        incollato = _termini_testo(atomic_code(self.CODICE))
        assert len(q & additivo) > len(q & incollato)
        assert len(q & incollato) == 1
