"""Il guardrail deve vedere TUTTO il catalogo, non solo la proiezione Qdrant.

La fonte di verita' dei codici e' `estrazione.righe_listino` in Postgres (SSOT);
`aike_chunks` ne e' una **proiezione**, e oggi e' indietro: 28 documenti hanno righe
di listino e zero chunk, e la re-estrazione D-B ha aggiunto righe senza riproiettare.

Conseguenza misurata: `catalog_lookup` trova meno della meta' dei codici reali, quindi
`verify_code` li marca `ABSENT` ("codice non a catalogo / allucinato" -> BLOCK ->
risposta nascosta) invece di `MISS_RETRIEVAL` ("esiste a catalogo ma non nell'evidenza"
-> VERIFY -> risposta mostrata con "verifica consigliata"). Un codice che esiste
davvero non e' un'allucinazione.

Questi test sono la prova end-to-end sul dato reale; le invarianti DB-free (unione,
normalizzazione, degrado se Postgres e' giu') stanno in `TestUnione`/`TestDegrado`.
"""

from __future__ import annotations

import psycopg
import pytest

from src.backend.clients.postgres_client import conninfo_readonly
from src.common.roles import Role
from src.ingestion.codes import canonical
from src.retrieval.guardrail import _literal_key, verify_code
from tests.conftest import needs_pg, needs_qdrant

SAMPLE = 50


def _sample_real_codes(n: int = SAMPLE) -> list[str]:
    """Campione DETERMINISTICO di codici reali dalla SSOT (forma originale, quella
    che il modello scrive in risposta)."""
    with psycopg.connect(conninfo_readonly()) as c, c.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (codice_normalizzato) codice_originale "
            "FROM estrazione.righe_listino "
            "WHERE codice_normalizzato <> '' "
            "ORDER BY codice_normalizzato, codice_originale "
            "LIMIT %s", (n,))
        return [r[0] for r in cur.fetchall()]


@needs_pg
@needs_qdrant
class TestCoperturaSSOT:
    def test_catalog_lookup_vede_i_codici_reali_del_listino(self):
        """ROSSO PRIMA: a HEAD `catalog_lookup` interroga solo `aike_chunks` e trova
        ~45% di questi codici, tutti presenti in `righe_listino`."""
        from src.graph.pipeline import build_default_deps

        codes = _sample_real_codes()
        assert len(codes) == SAMPLE, "il campione richiede righe_listino popolata"
        lookup = build_default_deps(Role.TECNICO).catalog_lookup
        mancanti = [c for c in codes if not lookup(_literal_key(c))]
        assert not mancanti, (
            f"{len(mancanti)}/{len(codes)} codici REALI di righe_listino non risultano "
            f"a catalogo: {mancanti[:10]}")

    def test_codice_reale_non_recuperato_e_miss_retrieval_non_absent(self):
        """Il verdetto che conta: MISS_RETRIEVAL (mostra con avviso), non ABSENT
        (blocca). Evidenza vuota = il retrieval non ha portato la riga."""
        from src.graph.pipeline import build_default_deps

        lookup = build_default_deps(Role.TECNICO).catalog_lookup
        codes = _sample_real_codes()
        # un codice reale che la proiezione Qdrant NON copre: e' il caso che oggi
        # viene bloccato come allucinazione.
        scoperti = [c for c in codes if not _in_qdrant(_literal_key(c))]
        if not scoperti:
            pytest.skip("nessun codice del campione e' fuori dalla proiezione Qdrant")
        v = verify_code(scoperti[0], [], lookup)
        assert v.status == "MISS_RETRIEVAL", (
            f"{scoperti[0]!r} esiste in righe_listino ma il guardrail lo dichiara "
            f"{v.status}: {v.reason}")

    def test_i_codici_inventati_restano_absent(self):
        """L'altra meta' del contratto: allargare il catalogo NON deve aprire la
        porta alle allucinazioni, nemmeno a quelle "quasi giuste" (un codice reale
        con una cifra in piu')."""
        from src.graph.pipeline import build_default_deps

        lookup = build_default_deps(Role.TECNICO).catalog_lookup
        finti = ["XZ99-INVENTATO-000", "ABC12345678999", "GM99999999",
                 "14576X", "877615999"]      # gli ultimi due: reali + una cifra
        passati = [c for c in finti if verify_code(c, [], lookup).status != "ABSENT"]
        assert not passati, f"codici inesistenti non piu' bloccati: {passati}"


def _in_qdrant(key: str) -> bool:
    from qdrant_client import models

    from src.backend.clients.qdrant_client import get_qdrant_client
    r = get_qdrant_client().count("aike_chunks", exact=True, count_filter=models.Filter(
        must=[models.FieldCondition(key="code", match=models.MatchValue(value=key))]))
    return r.count > 0


@needs_pg
class TestNormalizzazione:
    def test_codice_normalizzato_e_la_forma_canonical(self):
        """La chiave di join fra guardrail e SSOT: `righe_listino.codice_normalizzato`
        e' esattamente `canonical(codice_originale)` — mai la forma letterale."""
        with psycopg.connect(conninfo_readonly()) as c, c.cursor() as cur:
            cur.execute(
                "SELECT codice_originale, codice_normalizzato "
                "FROM estrazione.righe_listino "
                "WHERE codice_originale <> codice_normalizzato LIMIT 200")
            rows = cur.fetchall()
        assert rows, "servono codici con separatori per provare la normalizzazione"
        assert all(canonical(o) == n for o, n in rows)
        # ...e la forma letterale del guardrail NON coincide: cercare `_literal_key`
        # in `codice_normalizzato` non troverebbe nulla.
        assert any(_literal_key(o) != n for o, n in rows)


# --------------------------------------------------------------- invarianti DB-free

class _FakeQdrant:
    """Conta i punti con payload `code` == key, come il vero client."""

    def __init__(self, codes: set[str], raises: Exception | None = None) -> None:
        self.codes = codes
        self.raises = raises
        self.calls: list[str] = []

    def count(self, collection, exact, count_filter):  # noqa: ARG002
        if self.raises is not None:
            raise self.raises
        key = count_filter.must[0].match.value
        self.calls.append(key)
        return type("R", (), {"count": 1 if key in self.codes else 0})()


class _FakePgCatalog:
    def __init__(self, canon: set[str]) -> None:
        self.canon = canon
        self.calls: list[str] = []

    def contains(self, key: str) -> bool:
        self.calls.append(key)
        return canonical(key) in self.canon


class TestUnione:
    def test_unione_non_sostituzione(self):
        from src.retrieval.catalog import make_catalog_lookup

        qd = _FakeQdrant({"SOLO-QDRANT"})
        pg = _FakePgCatalog({"SOLOSSOT"})
        lookup = make_catalog_lookup(qd, pg)
        assert lookup("SOLO-QDRANT") is True      # solo nella proiezione
        assert lookup("SOLO-SSOT") is True        # solo nella SSOT
        assert lookup("MAI-VISTO") is False       # in nessuna delle due

    def test_ssot_interrogata_in_forma_canonical(self):
        """Il chiamante passa la forma letterale `340-006-734`; in `righe_listino`
        il codice sta come `340006734`. Senza `canonical` il fix non troverebbe nulla."""
        from src.retrieval.catalog import make_catalog_lookup

        pg = _FakePgCatalog({"340006734"})
        assert make_catalog_lookup(_FakeQdrant(set()), pg)("340-006-734") is True

    def test_senza_ssot_resta_il_comportamento_storico(self):
        from src.retrieval.catalog import make_catalog_lookup

        qd = _FakeQdrant({"ABC"})
        lookup = make_catalog_lookup(qd, None)
        assert lookup("ABC") is True
        assert lookup("XYZ") is False

    def test_errore_qdrant_propaga(self):
        """`verify_code` cattura e ritorna MISS_RETRIEVAL: quella promessa resta."""
        from src.retrieval.catalog import make_catalog_lookup

        lookup = make_catalog_lookup(_FakeQdrant(set(), raises=RuntimeError("qdrant giu'")),
                                     _FakePgCatalog(set()))
        with pytest.raises(RuntimeError):
            lookup("ABC")
        assert verify_code("ABC", [], lookup).status == "MISS_RETRIEVAL"


class TestDegrado:
    def test_postgres_irraggiungibile_non_solleva_e_lascia_passare_qdrant(self, caplog):
        from src.retrieval.catalog import PgCatalog, make_catalog_lookup

        pg = PgCatalog("host=127.0.0.1 port=1 user=x dbname=x connect_timeout=1")
        qd = _FakeQdrant({"ABC"})
        lookup = make_catalog_lookup(qd, pg)
        assert pg.contains("ABC") is False        # degrado, non eccezione
        assert lookup("ABC") is True              # la proiezione risponde comunque
        assert lookup("ZZZ") is False

    def test_postgres_giu_non_ritenta_a_ogni_token(self):
        """Un Postgres irraggiungibile costa UN tentativo, non uno per candidato:
        altrimenti il guardrail stallerebbe su ogni risposta."""
        import time

        from src.retrieval.catalog import PgCatalog

        pg = PgCatalog("host=127.0.0.1 port=1 user=x dbname=x connect_timeout=1")
        assert pg.contains("ABC") is False
        assert pg._down_until > time.monotonic()
        t0 = time.monotonic()
        for _ in range(50):
            assert pg.contains("ABC") is False
        assert time.monotonic() - t0 < 0.5     # breaker aperto: nessun connect

    def test_codice_vuoto_non_interroga_il_db(self):
        from src.retrieval.catalog import PgCatalog

        pg = PgCatalog("host=127.0.0.1 port=1 user=x dbname=x connect_timeout=1")
        assert pg.contains("---") is False        # canonical('---') == '' -> nessuna query
        assert pg._conn is None

    def test_una_sola_connessione_riusata(self):
        """Non una connessione per token: il singleton di processo e' per conninfo."""
        from src.retrieval.catalog import get_pg_catalog

        a = get_pg_catalog("host=x dbname=y")
        b = get_pg_catalog("host=x dbname=y")
        assert a is b


@needs_pg
class TestConnessioneRiusata:
    def test_connessione_unica_su_molte_chiamate(self):
        from src.retrieval.catalog import PgCatalog

        pg = PgCatalog(conninfo_readonly())
        codes = _sample_real_codes(20)
        assert all(pg.contains(_literal_key(c)) for c in codes)
        conn = pg._conn
        assert conn is not None and not conn.closed
        for c in codes:
            pg.contains(_literal_key(c))
        assert pg._conn is conn                   # stessa connessione, nessuna nuova
        pg.close()
