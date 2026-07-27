import pytest
from scripts.measure_groundtruth import group_into_ybands, get_all_codes_in_band, find_code_in_band, extract_gt, compare_desc

def test_group_into_ybands():
    words = [
        # x0, y0, x1, y1, text
        (10, 100, 50, 110, "COD123"),
        (60, 102, 100, 112, "Description"),
        (110, 101, 150, 111, "12,50"),
        # Next band
        (10, 120, 50, 130, "COD456"),
        (60, 121, 100, 131, "Other"),
    ]
    bands = group_into_ybands(words)
    assert len(bands) == 2
    assert len(bands[0]) == 3
    assert len(bands[1]) == 2
    assert bands[0][0][4] == "COD123"

def test_get_all_codes_in_band():
    band = [(0, 0, 0, 0, "AB1234CD"), (0, 0, 0, 0, "notcode")]
    codes = get_all_codes_in_band(band)
    assert "AB1234CD" in codes

def test_find_code_in_band():
    band = [
        (0, 0, 0, 0, "A B12"),
        (0, 0, 0, 0, "34"),
        (0, 0, 0, 0, "CD")
    ]
    # Code is AB1234CD. text_no_spaces is AB1234CD. len=8.
    # Words lengths without spaces:
    # 0: "AB12" -> len 4
    # 1: "34" -> len 2
    # 2: "CD" -> len 2
    idx = find_code_in_band(band, "AB1234CD")
    assert idx == 2  # ends in word 2

def test_extract_gt():
    band = [
        (0, 0, 0, 0, "COD123"),
        (0, 0, 0, 0, "Vite"),
        (0, 0, 0, 0, "Testa"),
        (0, 0, 0, 0, "Esagonale"),
        (0, 0, 0, 0, "12,50"),
        (0, 0, 0, 0, "€")
    ]
    desc, price = extract_gt(band, 0)
    assert desc == "Vite Testa Esagonale"
    assert price == 12.50

def test_extract_gt_no_price():
    band = [
        (0, 0, 0, 0, "COD123"),
        (0, 0, 0, 0, "Just"),
        (0, 0, 0, 0, "Description")
    ]
    desc, price = extract_gt(band, 0)
    assert desc == "Just Description"
    assert price is None

def test_compare_desc():
    assert compare_desc("VITE TESTA ESAG M8", "Vite Testa Esag. M8") is True
    assert compare_desc("VITE", "Vite Testa Esagonale M8") is False
