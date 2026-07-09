"""Fase 1.1 — Test della geometria PURA del tiling (nessun PDF, nessuna immagine).

Verifica rigorosa della matematica dei tile: numero, posizioni, dimensione uniforme,
COPERTURA TOTALE (nessun buco) e OVERLAP effettivo, piu' i casi limite e la
validazione fail-closed dei parametri.
"""

from __future__ import annotations

import pytest

from src.retrieval.pixelrag.tiling import (
    DEFAULT_OVERLAP,
    Tile,
    _axis_starts,
    tile_grid,
)


# ------------------------------------------------------------- _axis_starts (1D)


class TestAxisStarts:
    def test_pagina_piu_piccola_del_tile_un_solo_start(self):
        assert _axis_starts(800, 1024, 0.12) == [0]

    def test_pagina_uguale_al_tile_un_solo_start(self):
        assert _axis_starts(1024, 1024, 0.12) == [0]

    def test_primo_start_zero_ultimo_clampato_al_bordo(self):
        s = _axis_starts(2500, 1024, 0.12)
        assert s[0] == 0
        assert s[-1] == 2500 - 1024   # clamp: l'ultimo tile tocca il bordo

    def test_copertura_totale_senza_buchi(self):
        # ogni tile inizia entro la fine del precedente (contiguita' con overlap)
        dim, tile = 2500, 1024
        s = _axis_starts(dim, tile, 0.12)
        for prev, cur in zip(s, s[1:]):
            assert cur <= prev + tile          # nessun buco
            assert cur > prev                  # progresso stretto
        assert s[-1] + tile == dim             # bordo destro coperto

    def test_overlap_effettivo_positivo(self):
        dim, tile, ov = 2500, 1024, 0.12
        s = _axis_starts(dim, tile, ov)
        # i tile interni si sovrappongono di circa tile*overlap
        stride = s[1] - s[0]
        assert stride < tile                    # c'e' sovrapposizione
        assert abs(stride - round(tile * (1 - ov))) <= 1

    def test_piu_overlap_piu_tile(self):
        pochi = _axis_starts(4000, 1024, 0.0)
        molti = _axis_starts(4000, 1024, 0.30)
        assert len(molti) > len(pochi)

    def test_tutti_gli_start_in_range(self):
        dim, tile = 3333, 1024
        for s in _axis_starts(dim, tile, 0.12):
            assert 0 <= s <= dim - tile

    def test_overlap_zero_tassella_a_passo_pieno(self):
        # passo == tile: primo a 0, poi 1024, ultimo clampato
        s = _axis_starts(3000, 1024, 0.0)
        assert s[0] == 0 and s[1] == 1024 and s[-1] == 3000 - 1024


class TestAxisStartsValidazione:
    def test_tile_non_positivo_rifiutato(self):
        with pytest.raises(ValueError):
            _axis_starts(2000, 0, 0.12)

    def test_overlap_fuori_range_rifiutato(self):
        with pytest.raises(ValueError):
            _axis_starts(2000, 1024, 1.0)
        with pytest.raises(ValueError):
            _axis_starts(2000, 1024, -0.1)


# ------------------------------------------------------------- tile_grid (2D)


class TestTileGrid:
    def test_pagina_piccola_un_solo_tile_dimensione_pagina(self):
        tiles = tile_grid(600, 400, tile_px=1024, overlap=0.12)
        assert tiles == [Tile(row=0, col=0, x0=0, y0=0, w=600, h=400)]

    def test_numero_tile_e_prodotto_degli_assi(self):
        xs = _axis_starts(2500, 1024, 0.12)
        ys = _axis_starts(3200, 1024, 0.12)
        tiles = tile_grid(2500, 3200, tile_px=1024, overlap=0.12)
        assert len(tiles) == len(xs) * len(ys)

    def test_indici_row_col_e_coordinate_coerenti(self):
        xs = _axis_starts(2500, 1024, 0.12)
        ys = _axis_starts(3200, 1024, 0.12)
        tiles = tile_grid(2500, 3200, tile_px=1024, overlap=0.12)
        by_rc = {(t.row, t.col): t for t in tiles}
        for r, y in enumerate(ys):
            for c, x in enumerate(xs):
                t = by_rc[(r, c)]
                assert (t.x0, t.y0) == (x, y)
                assert (t.w, t.h) == (1024, 1024)

    def test_tile_dimensione_uniforme_e_dentro_la_pagina(self):
        pw, ph = 2500, 3200
        for t in tile_grid(pw, ph, tile_px=1024, overlap=0.12):
            assert t.w == 1024 and t.h == 1024
            assert 0 <= t.x0 and t.x0 + t.w <= pw
            assert 0 <= t.y0 and t.y0 + t.h <= ph

    def test_copertura_2d_ogni_angolo_coperto(self):
        pw, ph = 2500, 3200
        tiles = tile_grid(pw, ph, tile_px=1024, overlap=0.12)
        # esiste un tile che copre l'angolo in alto a sx e uno per il basso a dx
        assert any(t.x0 == 0 and t.y0 == 0 for t in tiles)
        assert any(t.x0 + t.w == pw and t.y0 + t.h == ph for t in tiles)

    def test_default_overlap_usato(self):
        a = tile_grid(2500, 2500)
        b = tile_grid(2500, 2500, tile_px=1024, overlap=DEFAULT_OVERLAP)
        assert a == b

    def test_dimensioni_pagina_non_valide_rifiutate(self):
        with pytest.raises(ValueError):
            tile_grid(0, 100)
        with pytest.raises(ValueError):
            tile_grid(100, -1)
