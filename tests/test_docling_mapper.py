import pytest
import json
import os
from decimal import Decimal
from src.ingestion.extraction.schema import RigaGrezza
from src.ingestion.extraction.docling_mapper import DoclingMapper

def load_grid(doc_stem: str, page: int) -> list[list[str]]:
    path = f"tests/fixtures/docling_grids/{doc_stem}_p{page}.json"
    with open(path, "r") as f:
        return json.load(f)

# --- Existing test (preserved) ---

def test_mapper_mamoli_p225_00000029T007():
    grid = load_grid("Mamoli", 225)
    mapper = DoclingMapper()
    
    mapped = mapper.map_page(grid, doc_stem="Mamoli", page=225)
    
    # Check that 00000029T007 is found and price is 58.00, not 85
    found = None
    for r in mapped:
        if r.codice_originale == "00000029T007":
            found = r
            break
            
    assert found is not None, "Code 00000029T007 not found"
    assert found.prezzo == Decimal("58.00")


# --- D-B.1b: Short numeric code tests ---

class TestShortNumericCodes:
    """TDD tests for short numeric code recognition (4-5 digit codes like 8524, 10023, 14557).
    
    These codes were previously rejected by DOCLING_CODE_RE which required [0-9]{6,}
    for purely numeric codes. The fix uses column-context heuristics to accept them
    when followed by descriptive text.
    """
    
    def test_mamoli_p8_code_8524(self):
        """Code 8524 at index 1 followed by 'Cappuccio/Cap' must be recognized."""
        grid = load_grid("Mamoli", 8)
        mapper = DoclingMapper()
        stats = {}
        mapped = mapper.map_page(grid, doc_stem="Mamoli", page=8, stats=stats)
        
        codes = {r.codice_originale: r for r in mapped}
        assert "8524" in codes, f"Code 8524 not found. Found codes: {list(codes.keys())}"
        assert codes["8524"].prezzo == Decimal("14.00")
        assert codes["8524"].descrizione is not None
        assert "Cappuccio" in codes["8524"].descrizione
    
    def test_mamoli_p8_code_10023(self):
        """Code 10023 followed by 'Asta e pomolo/Rod and knob' must be recognized."""
        grid = load_grid("Mamoli", 8)
        mapper = DoclingMapper()
        mapped = mapper.map_page(grid, doc_stem="Mamoli", page=8)
        
        codes = {r.codice_originale: r for r in mapped}
        assert "10023" in codes, f"Code 10023 not found. Found codes: {list(codes.keys())}"
        assert codes["10023"].prezzo == Decimal("16.00")
    
    def test_mamoli_p8_code_14557(self):
        """Code 14557 followed by 'Organi fissaggio piantana/...' must be recognized."""
        grid = load_grid("Mamoli", 8)
        mapper = DoclingMapper()
        mapped = mapper.map_page(grid, doc_stem="Mamoli", page=8)
        
        codes = {r.codice_originale: r for r in mapped}
        assert "14557" in codes, f"Code 14557 not found. Found codes: {list(codes.keys())}"
        assert codes["14557"].prezzo == Decimal("13.50")

    def test_nsk_p135_dimension_180_not_emitted(self):
        """180 is a bearing dimension (OD=180mm) on nsk p135, NOT a code.
        The row ['32220J','-','','100','180','49','446.58'] has 180 surrounded by numbers.
        """
        grid = load_grid("nsk", 135)
        mapper = DoclingMapper()
        mapped = mapper.map_page(grid, doc_stem="nsk", page=135)
        
        codes = {r.codice_originale for r in mapped}
        assert "180" not in codes, "180 should NOT be emitted as a code (it's a dimension)"
        # 32220J should still be there
        assert "32220J" in codes, "32220J should still be recognized as a code"
    
    def test_nsk_p138_dimension_180_not_emitted(self):
        """180 appears inside the fused dimension cell on nsk p138.
        Must NOT be emitted as a code.
        """
        grid = load_grid("nsk", 138)
        mapper = DoclingMapper()
        mapped = mapper.map_page(grid, doc_stem="nsk", page=138)
        
        codes = {r.codice_originale for r in mapped}
        assert "180" not in codes, "180 should NOT be emitted as a code (dimension in fused cell)"

    def test_mamoli_p138_existing_codes_preserved(self):
        """Existing alphanumeric codes on Mamoli p138 (90416C) must still work.
        Additionally, short numeric codes (6080, 60184, etc.) should NOW be recognized.
        """
        grid = load_grid("Mamoli", 138)
        mapper = DoclingMapper()
        mapped = mapper.map_page(grid, doc_stem="Mamoli", page=138)
        
        codes = {r.codice_originale for r in mapped}
        # 90416C is alphanumeric → always matched by DOCLING_CODE_RE
        assert "90416C" in codes, "Existing alphanumeric code 90416C should still be recognized"
        # Short numeric codes should NOW be found after the fix
        for expected in ["6080", "60184", "60161", "60167", "91060", "10011"]:
            assert expected in codes, f"Short numeric code {expected} should now be recognized"

    def test_kohler_p33_callout_1234_not_emitted(self):
        """1234 is a callout number on kohler_marine_p33, NOT a code.
        It has no description and no price, so it must be rejected.
        """
        grid = load_grid("kohler_marine", 33)
        mapper = DoclingMapper()
        mapped = mapper.map_page(grid, doc_stem="kohler_marine", page=33)
        
        codes = {r.codice_originale for r in mapped}
        assert "1234" not in codes, "1234 should NOT be emitted as a code (it's a callout without desc/price)"

    def test_mamoli_p8_stats_short_code_accepted(self):
        """Stats must track accepted short codes."""
        grid = load_grid("Mamoli", 8)
        mapper = DoclingMapper()
        stats = {}
        mapper.map_page(grid, doc_stem="Mamoli", page=8, stats=stats)
        
        accepted = stats.get("short_code_accepted", [])
        accepted_codes = [entry[0] for entry in accepted]
        assert "8524" in accepted_codes, f"8524 should be in short_code_accepted. Got: {accepted_codes}"


class TestShortCodeHeuristicUnit:
    """Unit tests for the _is_short_numeric_code heuristic function."""
    
    def test_code_followed_by_description(self):
        """A short numeric token followed by descriptive text is a code."""
        from src.ingestion.extraction.docling_mapper import _is_short_numeric_code
        row = ["2", "8524", "Cappuccio/Cap", "1", "14,00"]
        assert _is_short_numeric_code(row, 1) is True
    
    def test_dimension_surrounded_by_numbers(self):
        """A number surrounded by only numbers is a dimension, not a code."""
        from src.ingestion.extraction.docling_mapper import _is_short_numeric_code
        row = ["32220J", "-", "", "100", "180", "63", "1022.53"]
        assert _is_short_numeric_code(row, 4) is False
    
    def test_quantity_single_digit(self):
        """A single-digit number should not be a code."""
        from src.ingestion.extraction.docling_mapper import _is_short_numeric_code
        row = ["2", "8524", "Cappuccio/Cap", "1", "14,00"]
        assert _is_short_numeric_code(row, 0) is False
    
    def test_quantity_two_digits(self):
        """A two-digit number should not be a code (too short)."""
        from src.ingestion.extraction.docling_mapper import _is_short_numeric_code
        row = ["10", "60184", "Maniglia completa/Handle", "1", "52,00"]
        assert _is_short_numeric_code(row, 0) is False
    
    def test_five_digit_code_with_description(self):
        """A 5-digit number followed by description is a code."""
        from src.ingestion.extraction.docling_mapper import _is_short_numeric_code
        row = ["3", "14557", "Organi fissaggio piantana/Sidevalve fixing kit", "1", "13,50"]
        assert _is_short_numeric_code(row, 1) is True
    
    def test_last_cell_no_description(self):
        """A number at end of row (no following description) is not a code."""
        from src.ingestion.extraction.docling_mapper import _is_short_numeric_code
        row = ["32220J", "-", "", "100", "180"]
        assert _is_short_numeric_code(row, 4) is False
    
    def test_header_row_not_code(self):
        """Header text following doesn't count as description for code recognition."""
        from src.ingestion.extraction.docling_mapper import _is_short_numeric_code
        # POS column followed by CODE header - the "1" is a POS, not code
        row = ["1", "CODICE", "DESCRIZIONE", "QTY", "Euro"]
        assert _is_short_numeric_code(row, 0) is False
