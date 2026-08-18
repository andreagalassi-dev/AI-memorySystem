"""P2.3 — Test della text-prep per il dual-indexing (pure functions, zero modelli).

Verifica il contratto: l'embedder non vede metadati; denso = solo desc riparata;
sparse = pos + codice atomico + desc riparata; riparazione deterministica senza
falsi join sulle parole legittime.
"""

from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

from src.ingestion.embedding import (
    BM25_LANGUAGE,
    FASTEMBED_CACHE_ENV,
    SPARSE_MODEL,
    dense_text,
    fastembed_cache_dir,
    get_sparse_model,
    is_under_system_temp,
    point_id,
    repair_desc,
    sparse_text,
)

CHUNK = {
    "chunk_id": "MAMOLI_P060_IT/t01/r005/single",
    "text": "Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf pagina 60 | tabella 1 | ...",
    "code": "14R", "pos": "1B", "desc": "Vite M5/M5 screw",
    "qty": "50", "price": "20,00",
    "doc": "Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf", "page": 60, "lang": "IT",
}


class TestRepairDesc:
    def test_mangled_reale_p231(self):
        assert repair_desc("B oCCa /S PoUt 5540 a-B-o -C roMo /C HroMe") == \
            "bocca /spout 5540 a-b-o -cromo /chrome"
        assert repair_desc(
            "a CCeSSori FiSSaGGio d eviatore /d iverter F ixinG aCCeSSorieS"
        ) == "accessori fissaggio deviatore /diverter fixing accessories"
        assert repair_desc("l eva /l ever viP C40 v1>2018") == "leva /lever vip c40 v1>2018"
        assert repair_desc("ManiGlia /H andle 3 F ori C ity") == "maniglia /handle 3 fori city"
        assert repair_desc("d eviatore /d iverter 1814 S tyloS -C roMo /C HroMe") == \
            "deviatore /diverter 1814 stylos -cromo /chrome"
        assert repair_desc("B oCCa /S PoUt 5540 e PoCa -C roMo") == \
            "bocca /spout 5540 epoca -cromo"

    def test_parole_legittime_intatte(self):
        # vocali singole e 'x' sono parole IT vere: mai joinate
        assert repair_desc("Cromo e oro") == "cromo e oro"
        assert repair_desc("Raccordo x flessibile/Inlet for flexible connection") == \
            "raccordo x flessibile/inlet for flexible connection"
        assert repair_desc("vite a testa piana") == "vite a testa piana"

    def test_testo_pulito_invariato(self):
        assert repair_desc("Coppia maniglie/Pair of handle") == "coppia maniglie/pair of handle"
        assert repair_desc("Aeratore 24X1 IC/Aerator 24x1 IC") == "aeratore 24x1 ic/aerator 24x1 ic"

    def test_none_e_vuoto(self):
        assert repair_desc(None) is None
        assert repair_desc("") is None
        assert repair_desc("   ") is None


class TestDenseText:
    def test_solo_desc_riparata(self):
        assert dense_text(CHUNK) == "vite m5/m5 screw"

    def test_niente_metadati_ne_codice(self):
        t = dense_text(CHUNK)
        for vietato in ("14R", "14r", "1B", "pagina", "Mamoli", "20,00", "50"):
            assert vietato not in t

    def test_senza_desc_nessun_vettore_denso(self):
        assert dense_text({**CHUNK, "desc": None}) is None


class TestSparseText:
    def test_pos_codice_desc(self):
        assert sparse_text(CHUNK) == "1B 14R vite m5/m5 screw"

    def test_codice_atomico_presente(self):
        assert "14R" in sparse_text(CHUNK).split()

    def test_esclusi_qty_prezzo_e_citazione(self):
        t = sparse_text(CHUNK)
        for vietato in ("50", "20,00", "pagina", "Mamoli", "tabella"):
            assert vietato not in t

    def test_senza_desc_resta_pos_e_codice(self):
        assert sparse_text({**CHUNK, "desc": None}) == "1B 14R"

    def test_senza_pos(self):
        assert sparse_text({**CHUNK, "pos": None}) == "14R vite m5/m5 screw"


class TestPointId:
    def test_deterministico_e_distinto(self):
        a = point_id("MAMOLI_P231_IT/t01/r002/left")
        assert a == point_id("MAMOLI_P231_IT/t01/r002/left")
        assert a != point_id("MAMOLI_P231_IT/t01/r002/right")
        assert len(a) == 36  # uuid canonico


class TestFastembedCacheDir:
    """La cache fastembed NON deve mai finire nella temp del SO.

    Regressione protetta: `get_sparse_model()` chiamava `SparseTextEmbedding`
    senza `cache_dir`; fastembed ripiegava su `tempfile.gettempdir()`, macOS
    svuotava la directory e — con `HF_HUB_OFFLINE=1` — il modello non era piu'
    ricaricabile: ramo narrativo e ibrido rispondevano BASSA a vuoto.
    """

    def test_default_non_cade_nella_temp_di_sistema(self, monkeypatch):
        monkeypatch.delenv(FASTEMBED_CACHE_ENV, raising=False)
        path = fastembed_cache_dir()
        assert not is_under_system_temp(path), f"cache volatile: {path}"
        assert path.is_absolute() and "~" not in str(path)
        assert path.is_dir()

    def test_riconosce_le_directory_volatili(self):
        assert is_under_system_temp(Path(tempfile.gettempdir()) / "fastembed_cache")
        assert is_under_system_temp(Path("/tmp/fastembed_cache"))
        assert is_under_system_temp(Path("/var/folders/7t/xxx/T/fastembed_cache"))
        assert not is_under_system_temp(Path.home() / ".cache" / "fastembed")

    def test_override_via_env(self, monkeypatch, tmp_path):
        target = tmp_path / "cache_fastembed"
        monkeypatch.setenv(FASTEMBED_CACHE_ENV, str(target))
        path = fastembed_cache_dir()
        assert path == target and path.is_dir()  # creata se manca

    def test_get_sparse_model_passa_sempre_cache_dir(self, monkeypatch):
        """Il bug era un kwarg mancante: qui si prova che viene passato."""
        visto: dict = {}

        class _FakeSparse:
            def __init__(self, model, **kwargs):
                visto["model"] = model
                visto.update(kwargs)

        fake = types.ModuleType("fastembed")
        fake.SparseTextEmbedding = _FakeSparse  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "fastembed", fake)
        get_sparse_model.cache_clear()
        try:
            get_sparse_model()
        finally:
            get_sparse_model.cache_clear()  # mai lasciare lo stub in cache

        assert visto["model"] == SPARSE_MODEL
        assert visto["language"] == BM25_LANGUAGE
        assert "cache_dir" in visto, "cache_dir non passato: la temp tornerebbe in gioco"
        assert not is_under_system_temp(Path(visto["cache_dir"]))
