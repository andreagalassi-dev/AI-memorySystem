"""Il ramo strutturato non serve i documenti di un altro ruolo.

DIFETTO REALE, vivo in produzione fino al 25/08 e riprodotto per intero prima
della correzione: un `commerciale` che chiedeva `codice 00000001B007` riceveva

    LIVELLO: ALTA
    "Il codice 00000001B007 ha un prezzo di 92.00 EUR
     (Mamoli_Catalogo_Ricambi_2021_IT_EN, pag. 224)"

da un documento **tecnico-only**. Massima confidenza, dato piu' sensibile che il
sistema tratta. Il `_SELECT` su `righe_listino` non toccava `document_roles`:
il ramo strutturato scavalcava l'RBAC che Qdrant applica su ogni altro percorso.
Ampiezza: 168495 righe su 169567 (99.4%) appartengono a documenti invisibili al
commerciale.

PERCHE' IL GATE NON L'AVEVA PRESO: il suo unico item RBAC chiede un codice con
`prezzo` NULL, la riga viene scartata a valle e il gate vede un rifiuto. Passava
per l'unico motivo che non c'entra con l'RBAC. Qui i test usano un codice con
valore NON nullo, che e' la condizione in cui il difetto si manifesta.

I test toccano Postgres davvero: un finto non avrebbe potuto dimostrare nulla,
perche' il difetto stava nella query SQL.
"""

from __future__ import annotations

import pytest

from src.backend.config import settings
from src.graph.tools.sql_tool import PgSqlExecutor, SqlParams, extract_params

pytestmark = pytest.mark.needs_pg

# codice con prezzo NON nullo in un documento visibile al solo `tecnico`
CODICE = "00000001B007"
DOC = "Mamoli_Catalogo_Ricambi_2021_IT_EN"


def _executor() -> PgSqlExecutor:
    return PgSqlExecutor(
        f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}")


class TestFiltroRuoloNelRamoStrutturato:
    def test_il_ruolo_autorizzato_riceve_la_riga(self):
        """Il rovescio necessario: una correzione che filtra TUTTO non e' una
        correzione, e senza questo test il difetto si 'chiuderebbe' spegnendo
        la funzione."""
        rows = _executor().run(extract_params(CODICE, f"codice {CODICE}", role="tecnico"))
        assert rows, "il tecnico deve continuare a ricevere il dato"
        assert any(r.doc_stem == DOC for r in rows)

    def test_il_ruolo_non_autorizzato_non_riceve_nulla(self):
        rows = _executor().run(extract_params(CODICE, f"codice {CODICE}", role="commerciale"))
        assert rows == [], f"leak RBAC: righe di un documento tecnico servite al commerciale: {rows}"

    def test_ruolo_inesistente_non_riceve_nulla(self):
        """Fail-closed: un ruolo che non e' in `document_roles` non vede niente,
        invece di vedere tutto — che sarebbe il comportamento di un LEFT JOIN."""
        rows = _executor().run(extract_params(CODICE, f"codice {CODICE}", role="ruolo-inventato"))
        assert rows == []

    def test_ruolo_vuoto_solleva_invece_di_interrogare(self):
        """Nessun default silenzioso: senza ruolo non si interroga il catalogo."""
        with pytest.raises(ValueError, match="role"):
            _executor().run(SqlParams(codice_normalizzato=CODICE, campo="prezzo", role=""))

    @pytest.mark.parametrize("campo_query", [
        f"codice {CODICE}",                    # -> prezzo (default)
        f"descrizione del codice {CODICE}",    # -> descrizione
    ])
    def test_il_filtro_vale_per_ogni_campo(self, campo_query):
        """Il gate passava perche' interrogava l'unico campo NULL. Il filtro non
        deve dipendere da QUALE colonna si chiede."""
        rows = _executor().run(extract_params(CODICE, campo_query, role="commerciale"))
        assert rows == []
