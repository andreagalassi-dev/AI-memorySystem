"""Fase 1 (hardening) — Runner Massivo: idempotenza transazionale + Fast/Heavy + Watchdog — RED.

Invarianti architetturali (BLOCCO 3, Microtask 3.1):
1. IDEMPOTENZA: se l'extractor solleva a meta' documento, il DB non contiene righe
   parziali (rollback transazionale per-documento).
2. FAST/HEAVY: il runner conta quante pagine sono state bypassate come FAST_TEXT
   (puro testo) rispetto a quante mandate all'HEAVY dispatch.
3. WATCHDOG: il ModelMultiplexer fa eviction del modello precedente quando si cambia
   contesto, e l'evento e' loggato/osservabile.

I test usano FakeExtractor + FakeModelRuntime + Postgres su schema usa-e-getta:
nessun modello reale richiesto (validazione architetturale, non qualita' del modello).
"""

from __future__ import annotations

import logging

import fitz
import psycopg
import pytest
from psycopg import sql

from src.backend.clients.postgres_client import conninfo
from src.ingestion.extraction.extractor import FakeExtractor, PageRef
from src.ingestion.extraction.load import init_schema
from src.ingestion.extraction.schema import RigaGrezza
from src.ingestion.run_massive_extraction import run_document_massive
from src.serving.multiplexer import FakeModelRuntime, ModelMultiplexer
from tests.conftest import needs_pg

pytestmark = needs_pg

SCHEMA = "test_runner_massivo"


@pytest.fixture()
def pgconn():
    ident = sql.Identifier(SCHEMA)
    with psycopg.connect(conninfo()) as c:
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()
        init_schema(c, schema=SCHEMA)
        yield c
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()


def _make_text_pdf(path: str, pages_text: list[str]) -> None:
    """Crea un PDF born-digital minimale con `pages_text` per pagina.

    Usa insert_textbox con word-wrap per garantire testo sufficiente a triggerare
    FAST_TEXT (>= 300 chars richiesti dal page_classifier T_CHARS).
    """
    doc = fitz.open()
    for txt in pages_text:
        page = doc.new_page()
        rect = fitz.Rect(36, 36, page.rect.width - 36, page.rect.height - 36)
        page.insert_textbox(rect, txt, fontsize=10)
    doc.save(path)
    doc.close()


def _make_table_pdf(path: str, n_table_pages: int = 1) -> None:
    """Crea un PDF con pagine-tabella borderless (codici + prezzi allineati)."""
    doc = fitz.open()
    for _ in range(n_table_pages):
        page = doc.new_page()
        rows = [
            ("AB-1234", "Valvola A", "12.50"),
            ("CD-5678", "Filtro B", "8.30"),
            ("EF-9012", "Guarnizione C", "3.10"),
        ]
        y = 72
        for cod, desc, prezzo in rows:
            page.insert_text((72, y), cod, fontsize=9)
            page.insert_text((180, y), desc, fontsize=9)
            page.insert_text((360, y), prezzo, fontsize=9)
            y += 14
    doc.save(path)
    doc.close()


def _count_righe(conn, schema: str = SCHEMA) -> int:
    return conn.execute(
        sql.SQL("SELECT count(*) FROM {}.righe_listino").format(sql.Identifier(schema))
    ).fetchone()[0]


def test_extractor_crasha_db_senza_sporcia(pgconn, tmp_path):
    """Invariante 1: se l'extractor solleva a meta', il DB resta vuoto (rollback)."""
    pdf = tmp_path / "tab.pdf"
    _make_table_pdf(str(pdf), n_table_pages=2)

    rt = FakeModelRuntime()
    mux = ModelMultiplexer(rt, max_resident=1, ctx_cap=16000)

    call_count = {"n": 0}

    class CrashingExtractor:
        def extract(self, ref: PageRef) -> list[RigaGrezza]:
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulazione crash modello sulla 2a pagina tabella")
            return [RigaGrezza(codice_originale="AB-1234", descrizione="Valvola A", prezzo="12.50")]

    with pytest.raises(RuntimeError, match="crash modello"):
        run_document_massive(
            str(pdf),
            conn=pgconn,
            extractor=CrashingExtractor(),
            multiplexer=mux,
            model_name="glm-ocr",
            schema=SCHEMA,
        )

    assert _count_righe(pgconn) == 0, "il DB contiene righe parziali: non atomica!"


def test_runner_migra_schema_con_vecchia_pk(tmp_path):
    """Regressione: il runner di PRODUZIONE deve MIGRARE uno schema con la VECCHIA PK
    (doc,page,code) all'indice unico del punto 3. Senza init_schema nel runner, l'ON
    CONFLICT di _persist_tx solleva InvalidColumnReference (bug scoperto ri-ingestando
    Mamoli: la prod non era mai stata migrata). Verifica anche che la collisione intra-
    pagina venga PRESERVATA (entrambi i prezzi), non persa in silenzio."""
    schema = "test_runner_vecchia_pk"
    ident = sql.Identifier(schema)
    with psycopg.connect(conninfo()) as c:
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.execute(sql.SQL("CREATE SCHEMA {}").format(ident))
        # tabella con la VECCHIA PRIMARY KEY (stato storico della produzione, pre-punto-3)
        c.execute(sql.SQL(
            "CREATE TABLE {}.righe_listino ("
            "doc_stem text NOT NULL, page int NOT NULL, codice_normalizzato text NOT NULL, "
            "codice_originale text NOT NULL, descrizione text, prezzo numeric, "
            "updated_at timestamptz NOT NULL DEFAULT now(), "
            "PRIMARY KEY (doc_stem, page, codice_normalizzato))").format(ident))
        c.commit()

        pdf = tmp_path / "tab.pdf"
        _make_table_pdf(str(pdf), n_table_pages=1)

        class CollisionExtractor:  # stesso codice, DUE prezzi sulla stessa pagina
            def extract(self, ref: PageRef) -> list[RigaGrezza]:
                return [
                    RigaGrezza(codice_originale="AB-1234", descrizione="Valvola A", prezzo="12.50"),
                    RigaGrezza(codice_originale="AB-1234", descrizione="Valvola A", prezzo="9.90"),
                ]

        mux = ModelMultiplexer(FakeModelRuntime(), max_resident=1, ctx_cap=16000)
        # NON deve sollevare InvalidColumnReference: il runner auto-migra.
        run_document_massive(str(pdf), conn=c, extractor=CollisionExtractor(),
                             multiplexer=mux, model_name="glm-ocr", schema=schema)

        idx = [r[0] for r in c.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname=%s AND tablename='righe_listino'",
            (schema,)).fetchall()]
        prezzi = [str(r[0]) for r in c.execute(sql.SQL(
            "SELECT prezzo FROM {}.righe_listino WHERE codice_normalizzato=%s "
            "ORDER BY prezzo").format(ident), ("AB1234",)).fetchall()]
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()

    assert "righe_listino_uq" in idx, f"migrazione non applicata: {idx}"
    assert set(prezzi) == {"9.90", "12.50"}, f"collisione non preservata: {prezzi}"


def test_fast_heavy_conteggi_loggati(pgconn, tmp_path, caplog):
    """Invariante 2: il runner logga pagine FAST_TEXT vs HEAVY."""
    pdf = tmp_path / "mixed.pdf"
    # testo ricco (>300 chars) per triggerare FAST_TEXT nel page_classifier
    long_text = (
        "Indice dei contenuti del manuale tecnico.\n"
        "Questo documento descrive le procedure di manutenzione dei compressori "
        "industriali, incluse le tabelle di ricambi, gli schemi elettrici e le "
        "istruzioni di montaggio per i tecnici specializzati.\n"
        "Consultare il capitolo dedicato per ogni intervento programmato e "
        "seguire le indicazioni di sicurezza riportate in ogni sezione del "
        "documento. Le procedure diagnostice devono essere eseguite da personale "
        "qualificato e autorizzato secondo le normative vigenti in materia. "
    ) * 2
    _make_text_pdf(str(pdf), [long_text, long_text])
    # aggiungi una 3a pagina con una tabella borderless
    doc = fitz.open(str(pdf))
    page = doc.new_page()
    for cod, desc, prezzo in [("XY-9999", "Pompa", "45.00")]:
        page.insert_text((72, 72), cod, fontsize=9)
        page.insert_text((180, 72), desc, fontsize=9)
        page.insert_text((360, 72), prezzo, fontsize=9)
    out = tmp_path / "mixed_full.pdf"
    doc.save(str(out))
    doc.close()
    pdf = out

    rt = FakeModelRuntime()
    mux = ModelMultiplexer(rt, max_resident=1, ctx_cap=16000)
    extractor = FakeExtractor(
        {
            ("mixed", 3): [
                RigaGrezza(codice_originale="XY-9999", descrizione="Pompa", prezzo="45.00")
            ],
        }
    )

    caplog.set_level(logging.INFO, logger="src.ingestion.run_massive_extraction")
    result = run_document_massive(
        str(pdf),
        conn=pgconn,
        extractor=extractor,
        multiplexer=mux,
        model_name="glm-ocr",
        schema=SCHEMA,
    )

    assert result.fast_pages >= 2, f"attese >=2 FAST, got {result.fast_pages}"
    assert result.heavy_pages >= 1, f"attese >=1 HEAVY, got {result.heavy_pages}"
    log_text = caplog.text
    assert "FAST" in log_text or "fast" in log_text
    assert "HEAVY" in log_text or "heavy" in log_text


def test_watchdog_eviction_al_cambio_modello(pgconn, tmp_path, caplog):
    """Invariante 3: il multiplexer carica il modello e l'evento e' osservabile."""
    pdf = tmp_path / "tab.pdf"
    _make_table_pdf(str(pdf), n_table_pages=1)

    rt = FakeModelRuntime()
    mux = ModelMultiplexer(rt, max_resident=1, ctx_cap=16000)
    extractor = FakeExtractor(
        {
            ("tab", 1): [RigaGrezza(codice_originale="AB-1234", descrizione="V", prezzo="1.0")],
        }
    )

    caplog.set_level(logging.INFO)
    run_document_massive(
        str(pdf),
        conn=pgconn,
        extractor=extractor,
        multiplexer=mux,
        model_name="glm-ocr",
        schema=SCHEMA,
    )

    # Il multiplexer deve aver caricato il modello (invariante watchdog).
    assert ("load", "glm-ocr", 16000) in rt.calls
    # Il runner deve aver loggato il cambio di contesto (modello in RAM).
    assert "glm-ocr" in caplog.text


def test_born_digital_routing_usa_text_extractor_e_non_carica_vlm(pgconn, tmp_path):
    """ROUTING: una pagina-tabella born-digital passa dal text-extractor (codici
    esatti) e NON carica il modello VLM (che ne corromperebbe i codici)."""
    pdf = tmp_path / "tab.pdf"
    _make_table_pdf(str(pdf), n_table_pages=1)  # born-digital, codici AB-1234...

    rt = FakeModelRuntime()
    mux = ModelMultiplexer(rt, max_resident=1, ctx_cap=16000)

    class ExplodingVlm:
        def extract(self, ref: PageRef):
            raise AssertionError("il VLM non deve essere chiamato su pagina born-digital")

    # codice ancorato nel testo nativo della pagina (grounding lo valida)
    text_ex = FakeExtractor(
        {("tab", 1): [RigaGrezza(codice_originale="AB-1234", descrizione="Valvola A", prezzo="12.50")]}
    )

    result = run_document_massive(
        str(pdf),
        conn=pgconn,
        extractor=ExplodingVlm(),
        multiplexer=mux,
        model_name="glm-ocr",
        schema=SCHEMA,
        text_extractor_factory=lambda _doc: text_ex,
    )

    assert result.righe_upsert == 1, "la riga born-digital non e' stata estratta dal text path"
    assert any(p.route == "HEAVY_TABLE_TEXT" for p in result.pages)
    # nessun caricamento di modello: il VLM resta a riposo sul percorso testuale
    assert all(c[0] != "load" for c in rt.calls), f"il VLM e' stato caricato: {rt.calls}"
