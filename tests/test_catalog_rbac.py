"""Il lookup di catalogo non e' un oracolo di esistenza su documenti di altri ruoli.

DIFETTO REALE, vivo in produzione fino al 25/08. Stessa famiglia della falla del
ramo strutturato (`tests/test_sql_rbac.py`), un piano piu' in basso: li' passava
il PREZZO, qui passa l'ESISTENZA del codice.

`make_catalog_lookup` alimenta `guardrail.verify_code`, che distingue

  - `MISS_RETRIEVAL` — il codice esiste a catalogo ma non e' nell'evidenza:
    la risposta si MOSTRA a MEDIA con "verifica consigliata";
  - `ABSENT` — il codice non esiste: allucinazione, risposta BLOCCATA.

Entrambi i rami del lookup erano ciechi al ruolo:
  - SSOT: `SELECT 1 FROM estrazione.righe_listino WHERE codice_normalizzato = %s`,
    nessun JOIN su `public.document_roles`;
  - proiezione: filtro Qdrant sul solo `code`, nessuna condizione su
    `ruoli_consentiti` (chiave presente nel payload di `aike_chunks_v2`).

Ampiezza misurata sul dato vivo: 73561 codici distinti in `righe_listino`, di cui
1072 visibili al `commerciale` -> 72489 invisibili, il **98.5%**. Per un
commerciale un codice tecnico-only risultava "a catalogo", e il guardrail
declassava da BLOCK a MEDIA-mostrata: l'esatto opposto della severita' dovuta.

I test del ramo SSOT toccano Postgres davvero — il difetto stava nella query SQL,
un finto non avrebbe potuto dimostrare nulla. Il fake del ramo Qdrant **applica**
il filtro invece di guardarne la forma (disciplina di
`tests/test_delete_document_points.py`): misura il comportamento, non l'intenzione.
"""

from __future__ import annotations

import psycopg
import pytest

from src.backend.clients.postgres_client import conninfo_readonly
from src.common.roles import Role
from src.retrieval.catalog import PgCatalog, make_catalog_lookup
from tests.conftest import needs_pg

# Codici REALI, tutti in documenti con `document_roles = ['tecnico']`.
# (verificati il 25/08 su `estrazione.righe_listino` + `public.document_roles`)
TECNICO_ONLY = {
    "877615": "rotax_912_914_IPC",
    "45440050": "valvorobica_flange_raccordi_listino_2025",
    "550106A": "era_sensori_catalogo",
    "00000001B007": "Mamoli_Catalogo_Ricambi_2021_IT_EN",   # lo stesso di test_sql_rbac
}
# Codice reale visibile a TUTTI e tre i ruoli: serve a provare che la correzione
# non spegne il catalogo per i ruoli non-tecnico (filtrare tutto non e' correggere).
CONDIVISO = "14M115"          # pi_pulley_listino_pulegge_2025
# Codici che NON esistono: reali con una cifra in piu' / una lettera in piu'.
QUASI_GIUSTI = ["877615999", "8776150", "45440051", "550106AZ"]


# ------------------------------------------------------------------ ramo SSOT


@needs_pg
class TestSSOTFiltrataPerRuolo:
    def _cat(self) -> PgCatalog:
        return PgCatalog(conninfo_readonly())

    @pytest.mark.parametrize("codice", sorted(TECNICO_ONLY))
    def test_il_ruolo_autorizzato_continua_a_trovare_il_codice(self, codice):
        """Il rovescio necessario: una correzione che filtra tutto non e' una
        correzione."""
        cat = self._cat()
        try:
            assert cat.contains(codice, Role.TECNICO) is True, (
                f"{codice} esiste in righe_listino ed e' visibile al tecnico")
        finally:
            cat.close()

    @pytest.mark.parametrize("codice", sorted(TECNICO_ONLY))
    def test_il_ruolo_non_autorizzato_non_lo_trova(self, codice):
        cat = self._cat()
        try:
            assert cat.contains(codice, Role.COMMERCIALE) is False, (
                f"leak RBAC: {codice} vive solo in {TECNICO_ONLY[codice]} "
                "(tecnico-only) ma risulta a catalogo per il commerciale")
        finally:
            cat.close()

    def test_ruolo_inesistente_non_trova_nulla(self):
        """Fail-closed: un ruolo che non e' in `document_roles` vede ZERO, non
        tutto — che sarebbe il comportamento di un LEFT JOIN."""
        cat = self._cat()
        try:
            assert cat.contains("877615", "ruolo-inventato") is False
            assert cat.contains(CONDIVISO, "ruolo-inventato") is False
        finally:
            cat.close()

    @pytest.mark.parametrize("ruolo", ["", None])
    def test_ruolo_mancante_solleva_invece_di_interrogare(self, ruolo):
        """Nessun default silenzioso. E' un errore di PROGRAMMAZIONE, non un
        guasto d'ambiente: il contratto "contains non solleva mai" copre il
        Postgres irraggiungibile, non un chiamante che dimentica il ruolo."""
        cat = self._cat()
        try:
            with pytest.raises(ValueError, match="role"):
                cat.contains("877615", ruolo)
        finally:
            cat.close()

    def test_il_commerciale_vede_ancora_i_suoi_documenti(self):
        cat = self._cat()
        try:
            assert cat.contains(CONDIVISO, Role.COMMERCIALE) is True
            assert cat.contains(CONDIVISO, Role.TECNICO) is True
        finally:
            cat.close()

    @pytest.mark.parametrize("codice", QUASI_GIUSTI)
    def test_i_quasi_giusti_non_matchano(self, codice):
        """Aggiungere un JOIN non deve trasformare l'uguaglianza in un prefisso."""
        cat = self._cat()
        try:
            assert cat.contains(codice, Role.TECNICO) is False
        finally:
            cat.close()

    def test_per_il_tecnico_il_filtro_non_cambia_nulla(self):
        """Il numero da sorvegliare: la demo gira come `tecnico`, e per il tecnico
        (147/147 documenti visibili) il JOIN non deve togliere neanche una riga."""
        with psycopg.connect(conninfo_readonly()) as c, c.cursor() as cur:
            cur.execute("SELECT count(*), count(DISTINCT codice_normalizzato) "
                        "FROM estrazione.righe_listino")
            righe_tot, codici_tot = cur.fetchone()
            cur.execute(
                "SELECT count(*), count(DISTINCT r.codice_normalizzato) "
                "FROM estrazione.righe_listino r "
                "JOIN public.documents d ON d.slug = r.doc_stem "
                "JOIN public.document_roles dr ON dr.document_id = d.id AND dr.role = %s",
                ("tecnico",))
            righe_tec, codici_tec = cur.fetchone()
        assert (righe_tec, codici_tec) == (righe_tot, codici_tot), (
            f"il filtro toglie {righe_tot - righe_tec} righe al tecnico: "
            "il percorso della demo cambierebbe")


# --------------------------------------------------------------- ramo Qdrant


def _match(punto: dict, cond) -> bool:
    """Semantica Qdrant di `MatchValue`: su un payload LISTA (come
    `ruoli_consentiti`) matcha se il valore e' CONTENUTO nella lista."""
    val = punto.get(cond.key)
    if isinstance(val, list):
        return cond.match.value in val
    return val == cond.match.value


class FakeQdrant:
    """Applica DAVVERO il filtro ricevuto: misura il comportamento, non la forma."""

    def __init__(self, corpus: list[dict]) -> None:
        self.corpus = list(corpus)
        self.chiamate = 0

    def count(self, collection, exact=True, count_filter=None):  # noqa: ARG002
        self.chiamate += 1
        n = sum(1 for p in self.corpus
                if all(_match(p, c) for c in (count_filter.must or [])))
        return type("R", (), {"count": n})()


# la proiezione reale: `code` letterale + `ruoli_consentiti` lista
PROIEZIONE = [
    {"code": "877615", "ruoli_consentiti": ["tecnico"]},
    {"code": "877615", "ruoli_consentiti": ["tecnico"]},          # count 2 senza ruolo
    {"code": "45440050", "ruoli_consentiti": ["tecnico"]},
    {"code": "14M115", "ruoli_consentiti": ["tecnico", "commerciale", "amministrazione"]},
]


class _FakePgCatalog:
    """SSOT finta gia' filtrata per ruolo: `{codice: {ruoli}}`."""

    def __init__(self, tabella: dict[str, set[str]]) -> None:
        self.tabella = tabella

    def contains(self, key: str, role) -> bool:
        from src.ingestion.codes import canonical
        if not role:
            raise ValueError("role obbligatorio")
        return str(role) in self.tabella.get(canonical(key), set())


class TestRamoQdrantFiltratoPerRuolo:
    def test_il_tecnico_lo_trova_il_commerciale_no(self):
        qd = FakeQdrant(PROIEZIONE)
        assert make_catalog_lookup(qd, None, Role.TECNICO)("877615") is True
        assert make_catalog_lookup(qd, None, Role.COMMERCIALE)("877615") is False, (
            "leak RBAC nella proiezione: 2 punti tecnico-only contati per il commerciale")

    def test_il_documento_condiviso_resta_visibile(self):
        qd = FakeQdrant(PROIEZIONE)
        assert make_catalog_lookup(qd, None, Role.COMMERCIALE)("14M115") is True

    def test_ruolo_inesistente_non_vede_nulla(self):
        qd = FakeQdrant(PROIEZIONE)
        assert make_catalog_lookup(qd, None, "ruolo-inventato")("877615") is False
        assert make_catalog_lookup(qd, None, "ruolo-inventato")("14M115") is False

    def test_un_codice_assente_resta_assente(self):
        qd = FakeQdrant(PROIEZIONE)
        assert make_catalog_lookup(qd, None, Role.TECNICO)("877615999") is False

    @pytest.mark.parametrize("ruolo", ["", None])
    def test_ruolo_mancante_non_costruisce_il_lookup(self, ruolo):
        """Il ruolo si valida al WIRING, non per token: cosi' l'errore non
        diventa un `MISS_RETRIEVAL` silenzioso dentro `verify_code`."""
        qd = FakeQdrant(PROIEZIONE)
        with pytest.raises(ValueError, match="role"):
            make_catalog_lookup(qd, None, ruolo)
        assert qd.chiamate == 0


# ------------------------------------------------------------- composizione


class TestComposizioneDeiDueRami:
    """L'errore piu' probabile: correggere un ramo e lasciare l'altro."""

    def test_qdrant_non_recupera_cio_che_la_ssot_ha_negato(self):
        pg = _FakePgCatalog({"877615": {"tecnico"}})
        qd = FakeQdrant(PROIEZIONE)
        assert make_catalog_lookup(qd, pg, Role.COMMERCIALE)("877615") is False, (
            "la SSOT filtra ma la proiezione rimette dentro il codice")
        assert make_catalog_lookup(qd, pg, Role.TECNICO)("877615") is True

    def test_la_ssot_non_recupera_cio_che_qdrant_ha_negato(self):
        """Il simmetrico: codice presente SOLO nella SSOT, ruolo non autorizzato."""
        pg = _FakePgCatalog({"SOLOSSOT": {"tecnico"}})
        qd = FakeQdrant([])
        assert make_catalog_lookup(qd, pg, Role.COMMERCIALE)("SOLO-SSOT") is False
        assert make_catalog_lookup(qd, pg, Role.TECNICO)("SOLO-SSOT") is True


@needs_pg
class TestComposizioneSuPostgresReale:
    def test_il_commerciale_non_trova_un_codice_tecnico_only(self):
        """SSOT vera + proiezione finta che CONTIENE il codice: se il ramo Qdrant
        non filtrasse, il commerciale se lo ritroverebbe comunque a catalogo."""
        cat = PgCatalog(conninfo_readonly())
        qd = FakeQdrant(PROIEZIONE)
        try:
            assert make_catalog_lookup(qd, cat, Role.TECNICO)("877615") is True
            assert make_catalog_lookup(qd, cat, Role.COMMERCIALE)("877615") is False
        finally:
            cat.close()
