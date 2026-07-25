from decimal import Decimal

from src.ingestion.extraction.quality_guard import is_jumbled_page, is_spillover_row
from src.ingestion.extraction.schema import RigaListino
from src.ingestion.run_massive_extraction import _persist_tx


def make_riga(descrizione: str | None) -> RigaListino:
    """Helper per creare una RigaListino."""
    return RigaListino(
        codice_originale="TEST",
        descrizione=descrizione,
        prezzo=Decimal("10.0"),
        codice_normalizzato="TEST",
        doc_stem="test_doc",
        page=1,
    )


def test_is_spillover_row():
    # Buone (non spillover)
    assert not is_spillover_row("Rosone/Wall plate 2817 Stylos")
    assert not is_spillover_row("32x40 NR")
    assert not is_spillover_row("ENGINE S4L MITSUBISHI")
    assert not is_spillover_row(None)

    # Jumbled (spillover)
    # max_rep >= 3:
    assert is_spillover_row("GR1 GR1 GR2 GR2 GR4 GR7 GR1")
    # >= 4 value_token:
    assert is_spillover_row('ST-0286-4M-F 25.5000 25-1/2" 13.7500 13-')


def test_is_jumbled_page():
    # Pagina buona
    buone = [
        make_riga("Rosone/Wall plate 2817 Stylos"),
        make_riga("32x40 NR"),
        make_riga("ENGINE S4L MITSUBISHI"),
        make_riga("Altro pezzo 123"),
        make_riga("Ancora buono 456"),
    ]
    assert not is_jumbled_page(buone)

    # Pagina jumbled
    jumbled = [
        make_riga("GR1 GR1 GR2 GR2 GR4 GR7 GR1"),
        make_riga('ST-0286-4M-F 25.5000 25-1/2" 13.7500 13-'),
        make_riga("Altro pezzo jumbled 10 20 30 40 50"),
        make_riga("Buona riga 123"),
        make_riga("Altra riga buona 456"),
    ]
    # 3 spillover su 5 = 60% > 40%
    assert is_jumbled_page(jumbled)
    
    buona_2_righe = [
        make_riga("Rosone/Wall plate 2817 Stylos"),
        make_riga("32x40 NR"),
    ]
    assert not is_jumbled_page(buona_2_righe)

    # Pagina jumbled (>= 4 righe)
    jumbled = [
        make_riga("GR1 GR1 GR2 GR2 GR4 GR7 GR1"),
        make_riga('ST-0286-4M-F 25.5000 25-1/2" 13.7500 13-'),
        make_riga("Altro pezzo jumbled 10 20 30 40 50"),
        make_riga("Buona riga 123"),
        make_riga("Altra riga buona 456"),
    ]
    # 3 spillover su 5 = 60% > 40%
    assert is_jumbled_page(jumbled)
    
    # Pagina jumbled con 3 righe (tutte spillover, stile bgr/nsk)
    # Deve restituire True
    poche_righe_jumbled = [
        make_riga("GR1 GR1 GR2 GR2 GR4 GR7 GR1"),
        make_riga('ST-0286-4M-F 25.5000 25-1/2" 13.7500 13-'),
        make_riga("Pos. 1A 1A 1A 1A 1A 1A 1A"),
    ]
    assert is_jumbled_page(poche_righe_jumbled)

    # Singola riga: passa sempre (troppo poco segnale, <2 righe)
    singola_riga = [
        make_riga("GR1 GR1 GR2 GR2 GR4 GR7 GR1"),
    ]
    assert not is_jumbled_page(singola_riga)



from unittest.mock import MagicMock, patch
from src.ingestion.run_massive_extraction import run_document_massive
from src.serving.multiplexer import ModelMultiplexer


@patch("src.ingestion.run_massive_extraction.dispatch_pdf")
@patch("src.ingestion.run_massive_extraction.fitz.open")
@patch("src.ingestion.run_massive_extraction.is_table_page")
@patch("src.ingestion.run_massive_extraction.needs_ocr", return_value=False)
@patch("src.ingestion.run_massive_extraction.route_table_page")
@patch("src.ingestion.run_massive_extraction.partition")
@patch("src.ingestion.run_massive_extraction._persist_tx")
@patch("src.ingestion.run_massive_extraction.is_jumbled_page")
def test_integration_runner_jumbled(
    mock_is_jumbled_page,
    mock_persist_tx,
    mock_partition,
    mock_route_table_page,
    mock_needs_ocr,
    mock_is_table_page,
    mock_fitz_open,
    mock_dispatch_pdf,
):
    """Testa che il runner chiami la guardia e non persista se JUMBLED."""
    # Mock dispatch
    mock_dispatch = MagicMock()
    mock_dispatch.n_pages = 1
    mock_dispatch.born_digital = False
    mock_dispatch.pages = [MagicMock(page=1, route="HEAVY")]
    mock_dispatch.fast_pages = 0
    mock_dispatch.heavy_pages = 1
    mock_dispatch_pdf.return_value = mock_dispatch

    # Mock is_table_page
    mock_table_verdict = MagicMock(is_table=True, reason="table")
    mock_is_table_page.return_value = mock_table_verdict

    # Mock route (usa VLM per forza)
    fake_extractor = MagicMock()
    fake_extractor.extract.return_value = []
    mock_route_table_page.return_value = (fake_extractor, "HEAVY_TABLE_VLM")

    # Simuliamo che l'estrattore/grounding restituisca righe jumbled
    jumbled_riga = make_riga("GR1 GR1 GR2 GR2 GR4 GR7 GR1")
    mock_partition.return_value = ([jumbled_riga]*5, []) # 5 valide jumbled, 0 scarti
    
    # La guardia scatta
    mock_is_jumbled_page.return_value = True
    
    # Per non fallire durante _persist_tx (se fosse chiamato per errore)
    mock_persist_tx.return_value = {"righe_upsert": 0, "scarti": 0}

    mock_conn = MagicMock()
    mock_multiplexer = MagicMock(spec=ModelMultiplexer)

    result = run_document_massive(
        filepath="fake.pdf",
        conn=mock_conn,
        extractor=fake_extractor,
        multiplexer=mock_multiplexer,
        model_name="fake_model"
    )

    # _persist_tx NON deve essere chiamato con valide se c'e' jumbled
    assert mock_persist_tx.call_count == 0

    assert len(result.pages) == 1
    page_out = result.pages[0]
    assert page_out.n_valide == 0
    assert page_out.route == "JUMBLED_SKIP"

    mock_is_jumbled_page.assert_called_once()
