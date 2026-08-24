"""Debito §2 — Test della Query Expansion (dizionario, matcher, nodo grafo).

Vincoli sotto test: espansione DETERMINISTICA e cappata; SOLO route natural;
nodo NON-fatale (dizionario giu' -> comportamento senza espansione, mai
blocco); la query in stato non viene mai riscritta; round-trip seed<->DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.retrieval import expansion
from src.retrieval.expansion import expand_query, reload_synonyms
from tests.conftest import needs_pg

SEED_PATH = Path(__file__).resolve().parents[1] / "config" / "synonyms.json"

FAKE_DICT = {
    "spinotto": {"note": None, "terms": [["spinotto", "IT"],
                                         ["piston pin", "EN"],
                                         ["kolbenbolzen", "DE"]]},
    "spia-livello": {"note": None, "terms": [["spia", "IT"], ["vetrino", "IT"],
                                             ["livello visivo", "IT"],
                                             ["sight glass", "EN"]]},
    "vite": {"note": None, "terms": [["vite", "IT"], ["screw", "EN"],
                                     ["schraube", "DE"]]},
}


@pytest.fixture()
def fake_dict(monkeypatch):
    monkeypatch.setattr(expansion, "_load_from_db", lambda: FAKE_DICT)
    reload_synonyms()
    yield
    reload_synonyms()


class TestMatcher:
    def test_espansione_cross_lingua(self, fake_dict):
        assert expand_query("spinotto del pistone Rotax") == [
            "piston pin", "kolbenbolzen"]

    def test_multi_parola_longest_first(self, fake_dict):
        # 'livello visivo' matcha come bigramma: niente doppio match sul singolo
        out = expand_query("livello visivo per SCV")
        assert out == ["spia", "vetrino", "sight glass"]

    def test_termini_gia_in_query_esclusi(self, fake_dict):
        out = expand_query("vite screw")
        assert "vite" not in out and "screw" not in out
        assert out == ["schraube"]

    def test_cap_max_terms(self, fake_dict):
        out = expand_query("vite spinotto spia", max_terms=3)
        assert len(out) == 3

    def test_deterministica(self, fake_dict):
        q = "spinotto e vite della spia"
        assert expand_query(q) == expand_query(q)

    def test_query_senza_match(self, fake_dict):
        assert expand_query("V09156800007") == []
        assert expand_query("") == []

    def test_case_e_accenti_normalizzati(self, fake_dict):
        assert expand_query("SPINOTTO") == ["piston pin", "kolbenbolzen"]

    def test_guardia_token_code_like(self, fake_dict):
        # codice fuori registro famiglie (caso W042): MAI diluire il canale
        # lessicale con l'espansione
        assert expand_query("il 9S1BG0270 e' una vite di tenuta tonda?") == []

    def test_round_robin_tra_concetti(self, fake_dict):
        # il cap non deve consumarsi sulle varianti del primo concetto
        # (caso W046/W003: 'o-ring'/'filtro' tagliati)
        out = expand_query("vite del spinotto", max_terms=3)
        assert out == ["screw", "piston pin", "schraube"]


class TestNodoGrafo:
    def _mk_deps(self, expand=None, retrieve=None):
        from src.graph.pipeline import Deps
        from src.retrieval.confidence import CALIBRATED_V1
        return Deps(retrieve=retrieve or (lambda q, t=None: ([], [])),
                    synthesize=lambda q, ev: "", catalog_lookup=lambda k: False,
                    thresholds=CALIBRATED_V1, expand=expand)

    def test_solo_route_natural(self):
        from src.graph.pipeline import expansion_node
        deps = self._mk_deps(expand=lambda q: ["boccola"])
        assert expansion_node({"route": "code", "query": "x"}, deps) == {}
        assert expansion_node({"route": "mixed", "query": "x"}, deps) == {}
        out = expansion_node({"route": "natural", "query": "x"}, deps)
        assert out["expansion_terms"] == ["boccola"]

    def test_senza_expand_dep_no_op(self):
        from src.graph.pipeline import expansion_node
        assert expansion_node({"route": "natural", "query": "x"},
                              self._mk_deps(expand=None)) == {}

    def test_non_fatale_su_errore(self):
        from src.graph.pipeline import expansion_node

        def boom(q):
            raise RuntimeError("Postgres irraggiungibile")
        out = expansion_node({"route": "natural", "query": "x"},
                             self._mk_deps(expand=boom))
        assert out["expansion_terms"] == []
        assert "NON-FATALE" in out["audit"][0]

    def test_termini_fluiscono_a_retrieve_query_intatta(self):
        from src.graph.pipeline import build_graph
        seen = {}

        def spy_retrieve(query, terms=None):
            seen["query"], seen["terms"] = query, terms
            return []

        deps = self._mk_deps(expand=lambda q: ["bushing", "gleitlager"],
                             retrieve=spy_retrieve)
        g = build_graph(deps)
        g.invoke({"query": "boccola del trinciaerba", "role": "tecnico"})
        assert seen["query"] == "boccola del trinciaerba"   # MAI riscritta
        assert seen["terms"] == ["bushing", "gleitlager"]

    def test_retrocompatibile_retrieve_un_argomento(self):
        # senza expand (o senza termini) retrieve e' chiamata con la sola query
        from src.graph.pipeline import build_graph
        seen = {}

        def legacy_retrieve(query):
            seen["query"] = query
            return []

        deps = self._mk_deps(expand=None, retrieve=legacy_retrieve)
        build_graph(deps).invoke({"query": "boccola", "role": "tecnico"})
        assert seen["query"] == "boccola"


# ------------------------------------------------------- round-trip su DB

@needs_pg
class TestRoundTripDB:
    def test_seed_load_equivalente_al_json(self, conn):
        from src.registry.synonyms import (init_schema, load_seed_config,
                                           load_synonyms, upsert_concept)
        init_schema(conn)
        cfg = load_seed_config(SEED_PATH)
        for slug, c in cfg.items():
            upsert_concept(conn, slug, c)
        assert load_synonyms(conn) == cfg

    def test_upsert_idempotente_e_llm_proposed_sopravvive(self, conn):
        from src.registry.synonyms import (init_schema, load_seed_config,
                                           load_synonyms, upsert_concept)
        init_schema(conn)
        cfg = load_seed_config(SEED_PATH)
        for slug, c in cfg.items():
            upsert_concept(conn, slug, c)
        # proposta LLM non approvata: invisibile al load, sopravvive al re-seed
        conn.execute(
            "INSERT INTO synonym_terms (concept_id, term, lang, source, approved) "
            "SELECT id, 'proposta llm', 'IT', 'llm_proposed', false "
            "FROM synonym_concepts WHERE slug = 'vite'")
        conn.commit()
        prima = load_synonyms(conn)
        assert ["proposta llm", "IT"] not in prima["vite"]["terms"]
        upsert_concept(conn, "vite", cfg["vite"])  # re-seed del concetto
        n = conn.execute("SELECT count(*) FROM synonym_terms "
                         "WHERE term = 'proposta llm'").fetchone()[0]
        assert n == 1 and load_synonyms(conn) == prima

    def test_lingua_non_ammessa_rifiutata(self, conn):
        from src.registry.synonyms import init_schema, upsert_concept
        init_schema(conn)
        with pytest.raises(ValueError, match="lingua"):
            upsert_concept(conn, "x", {"terms": [["bobina", "PT"]]})


# ============================== dizionario: forma delle famiglie aggiunte
# Test di FORMA sul seed reale, senza DB. Bloccano regressioni silenziose su
# scelte che sono LOAD-BEARING e non si vedono leggendo il JSON.

def _seed() -> dict:
    from src.registry.synonyms import load_seed_config
    return load_seed_config(SEED_PATH)


def _seed_raw() -> dict:
    import json
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))["concepts"]


class TestFamigliaPaesi:
    """Sigle paese sulla spina elettrica: famiglia chiusa, attestata nel corpus."""

    def test_sigla_prima_del_nome_del_paese(self):
        # MISURATO: con il nome del paese davanti, il cap MAX_TERMS mangia la
        # sigla e NARR-W005 resta a rank None; con la sigla davanti va a 2.
        # L'ordine del seed E' l'ordine di espansione: non e' cosmetica.
        raw = _seed_raw()
        paesi = [s for s in raw if s.startswith("paese-")]
        assert paesi, "la famiglia paese-* e' sparita dal seed"
        for slug in paesi:
            langs = list(raw[slug]["terms"])
            assert langs[0] == "*", (
                f"{slug}: la sigla ('*') deve essere il primo gruppo di "
                f"termini, trovato {langs[0]!r}")

    def test_nessuna_sigla_collide_con_parole_vere(self):
        # Le sigle finiscono nel canale sparse come token qualunque: una sigla
        # che e' anche una parola comune (IT/EN/DE/FR/ES) espanderebbe query
        # innocenti. 'de' da solo compare in 7 query del metro narrativo.
        VIETATE = {"de", "it", "fr", "es", "us", "in", "an", "am", "al", "la",
                   "le", "el", "un", "on", "no", "so", "to", "or", "as", "at",
                   "by", "do", "du", "en", "et", "je", "me", "ne", "se", "si",
                   "su", "te", "tu", "va", "ex", "re", "be", "he", "we", "is",
                   "if", "of", "up", "my", "ma", "mi", "ti", "vi", "ci", "da",
                   "di", "e", "a", "o", "i", "y", "im", "um", "zu", "so"}
        raw = _seed_raw()
        for slug in [s for s in raw if s.startswith("paese-")]:
            for sigla in raw[slug]["terms"]["*"]:
                assert sigla not in VIETATE, (
                    f"{slug}: la sigla {sigla!r} collide con una parola comune")

    def test_un_concetto_per_paese_mai_uno_solo(self):
        # Un unico concetto 'sigle-paese' farebbe espandere 'australiana' anche
        # in uk/cn/jp: le righe SBAGLIATE della stessa pagina del catalogo.
        raw = _seed_raw()
        for slug in [s for s in raw if s.startswith("paese-")]:
            assert len(raw[slug]["terms"]["*"]) <= 2, (
                f"{slug}: un concetto-paese porta le sigle di UN paese solo "
                f"(al massimo due grafie), trovate {raw[slug]['terms']['*']}")


class TestEspansioneSulSeedReale:
    """I due casi misurati, contro il dizionario vero e il cap vero."""

    @pytest.fixture()
    def seed_dict(self, monkeypatch):
        monkeypatch.setattr(expansion, "_load_from_db", _seed)
        reload_synonyms()
        yield
        reload_synonyms()

    def test_w005_emette_la_sigla_au_sotto_il_cap(self, seed_dict):
        out = expand_query("prolunga elettrica tripolare da tre metri "
                           "con presa australiana")
        assert "au" in out, (
            "'au' e' il termine che porta NARR-W005 da rank None a 2: "
            f"il cap l'ha mangiato, uscita = {out}")

    def test_q050_emette_heating_plate(self, seed_dict):
        out = expand_query("codice del piatto riscaldante dell'affettatrice")
        assert "heating plate" in out, out

    def test_query_con_codice_resta_non_espansa(self, seed_dict):
        # NARR-Q047 ('power cord for the EDL397363 slicer') e' protetta dalla
        # guardia code-like: misurato che 'heating plate' la porterebbe da
        # rank 1 a None. La guardia deve continuare a scattare.
        assert expand_query("power cord for the EDL397363 slicer") == []


# ==================== seed: il FILE e' la fonte di verita' (riconciliazione)
# Regressione sulla CLASSE di bug che ha prodotto la divergenza del 02/08:
# il seed era additivo, quindi due concetti aggiunti a mano a DB ('nero',
# 'zincato') sono rimasti attivi in produzione senza esistere nel repo. Lo
# stato del dizionario non era ricostruibile da nessun artefatto versionato.

@needs_pg
class TestSeedRiconciliazione:
    @pytest.fixture()
    def seed_main(self, conn, monkeypatch):
        """`scripts.seed_synonyms.main` puntato allo schema di test."""
        from contextlib import nullcontext

        import scripts.seed_synonyms as seed
        monkeypatch.setattr(seed, "get_pg_conn", lambda: nullcontext(conn))
        return seed

    @staticmethod
    def _intruso(conn):
        from src.registry.synonyms import upsert_concept
        upsert_concept(conn, "zz-intruso", {"note": "aggiunto a mano",
                                            "terms": [["intruso", "IT"]]})

    def test_concetto_fuori_dal_seed_viene_disattivato(self, conn, seed_main):
        from src.registry.synonyms import load_seed_config, load_synonyms
        assert seed_main.main([]) == 0
        self._intruso(conn)
        assert "zz-intruso" in load_synonyms(conn)

        assert seed_main.main([]) == 0
        dopo = load_synonyms(conn)
        assert "zz-intruso" not in dopo
        # il DB e' ORA ricostruibile dal file: e' il punto dell'esercizio
        assert dopo == load_seed_config(SEED_PATH)

    def test_disattivazione_e_soft_non_cancella_le_righe(self, conn, seed_main):
        assert seed_main.main([]) == 0
        self._intruso(conn)
        assert seed_main.main([]) == 0
        row = conn.execute("SELECT is_active FROM synonym_concepts "
                           "WHERE slug = 'zz-intruso'").fetchone()
        assert row == (False,), "la riga deve sopravvivere, solo disattivata"
        n = conn.execute(
            "SELECT count(*) FROM synonym_terms t JOIN synonym_concepts c "
            "ON c.id = t.concept_id WHERE c.slug = 'zz-intruso'").fetchone()[0]
        assert n == 1, "i termini dell'extra non vanno distrutti"

    def test_keep_extra_conserva_ma_lo_dichiara(self, conn, seed_main, capsys):
        from src.registry.synonyms import load_synonyms
        assert seed_main.main([]) == 0
        self._intruso(conn)
        assert seed_main.main(["--keep-extra"]) == 0
        assert "zz-intruso" in load_synonyms(conn)
        out = capsys.readouterr().out
        assert "NON e' ricostruibile dal repo" in out

    def test_idempotente_zero_scritture(self, conn, seed_main, capsys):
        assert seed_main.main([]) == 0
        capsys.readouterr()
        assert seed_main.main([]) == 0
        assert "0 scritture" in capsys.readouterr().out

    def test_llm_proposed_sopravvive_alla_riconciliazione(self, conn, seed_main):
        from src.registry.synonyms import load_synonyms
        assert seed_main.main([]) == 0
        conn.execute(
            "INSERT INTO synonym_terms (concept_id, term, lang, source, "
            "approved) SELECT id, 'proposta llm', 'IT', 'llm_proposed', false "
            "FROM synonym_concepts WHERE slug = 'vite'")
        conn.commit()
        assert seed_main.main([]) == 0
        n = conn.execute("SELECT count(*) FROM synonym_terms "
                         "WHERE term = 'proposta llm'").fetchone()[0]
        assert n == 1
        assert ["proposta llm", "IT"] not in load_synonyms(conn)["vite"]["terms"]


@needs_pg
def test_seed_file_allineato_alla_produzione():
    """Il file e la PRODUZIONE non devono divergere (il guasto del 02/08).

    Non e' un test sullo schema di test: legge il registro reale. Se fallisce,
    qualcuno ha toccato il dizionario a mano senza riportarlo nel repo —
    rimedio: `uv run scripts/seed_synonyms.py`.
    """
    from src.backend.clients.postgres_client import get_pg_conn
    from src.registry.synonyms import load_seed_config, load_synonyms
    with get_pg_conn() as c:
        prod = load_synonyms(c, only_approved=True)
    assert prod == load_seed_config(SEED_PATH), (
        "config/synonyms.json e il DB divergono: eseguire "
        "`uv run scripts/seed_synonyms.py`")
