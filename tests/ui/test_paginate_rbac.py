"""I pulsanti "Pagina prec/succ" non servono le pagine di un documento altrui.

DIFETTO REALE, vivo fino al 25/08. Stessa famiglia di `tests/test_sql_rbac.py` e
`tests/test_catalog_rbac.py`, un piano piu' in la': li' passava il PREZZO e
l'ESISTENZA di un codice, qui passa **l'IMMAGINE della pagina**.

Il payload di una `cl.Action` arriva dal client verbatim: in
`chainlit/server.py` l'handler di `/project/action` fa `Action(**payload.action)`
— non esiste un registro server-side delle azioni emesse, l'unico controllo e'
che la sessione sia dell'utente autenticato. `on_paginate` leggeva `doc`/`page`
dal payload e chiamava `ensure_page_render`, che risolve il PDF con un glob su
`corpus/**/{doc_stem}.pdf`: nessuna query, nessun ruolo, nessun filtro.

Ampiezza misurata sul dato vivo il 25/08: 147 documenti in `public.documents`,
di cui 8 visibili al `commerciale` e 8 all'`amministrazione`; i restanti 139
sono tecnico-only. Fra questi `Mamoli_Catalogo_Ricambi_2021_IT_EN`, lo STESSO
documento della prima falla RBAC: quella correzione ha chiuso la porta che
serviva il prezzo "92.00 EUR (pag. 224)" in TESTO e ha lasciato aperta quella
che serve **l'immagine della pagina 224**. Peggio: i pulsanti che `on_paginate`
rimette nel messaggio permettono di camminare il documento intero a click.

Disciplina dei test (quella di `tests/test_delete_document_points.py`): il finto
di `ensure_page_render` non guarda la forma della chiamata, la REGISTRA — cosi'
"niente render" e' una misura, non un'intenzione. La visibilita' invece tocca
Postgres davvero: e' una query SQL, un finto non proverebbe nulla.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import sql

from src.backend.clients.postgres_client import conninfo_readonly
from src.common.roles import Role
from src.registry.access import doc_visible_to, document_visible
from src.ui import chainlit_app
from src.ui.page_render import is_safe_doc_stem, resolve_pdf
from src.ui.storage_client import doc_stem_from_element_key
from tests.conftest import needs_pg

# Documento REALE visibile al solo `tecnico` (lo stesso di test_sql_rbac.py).
TECNICO_ONLY = "Mamoli_Catalogo_Ricambi_2021_IT_EN"
# Documento REALE visibile al `commerciale`: serve a provare che la guardia non
# spegne la paginazione per tutti (una guardia che blocca tutto non e' una guardia).
COMMERCIALE_OK = "trumpf_bending_brochure_EN"
# Non esiste in `public.documents`: la risposta deve essere INDISTINGUIBILE da
# quella del documento negato, altrimenti il messaggio e' un oracolo di esistenza.
INESISTENTE = "documento_che_non_esiste_mai_1234"


# --------------------------------------------------------------- finti di UI


class _Sessione:
    """`cl.user_session` senza websocket: un dizionario con la stessa API."""

    def __init__(self, dati: dict):
        self._dati = dict(dati)

    def get(self, chiave, default=None):
        return self._dati.get(chiave, default)

    def set(self, chiave, valore):
        self._dati[chiave] = valore


class _Utente:
    def __init__(self, metadata: dict):
        self.identifier = "utente-di-test"
        self.metadata = metadata


@pytest.fixture()
def ui(monkeypatch, tmp_path):
    """Sostituisce i tre punti di contatto con Chainlit e REGISTRA cosa succede.

    `render` e' la misura che conta: se resta vuota, nessun pixel di quel
    documento e' stato prodotto ne' servito.
    """
    stato = SimpleNamespace(messaggi=[], render=[])

    class _Messaggio:
        def __init__(self, content="", elements=None, actions=None, **_):
            self.content = content
            self.elements = list(elements or [])
            self.actions = list(actions or [])
            self.parent_id = None

        async def send(self):
            stato.messaggi.append(self)
            return self

        async def remove_actions(self):
            return None

    def _render_finto(doc, page):
        stato.render.append((doc, page))
        png = tmp_path / f"{page}.png"
        png.write_text("finti pixel")
        return png

    monkeypatch.setattr(chainlit_app, "ensure_page_render", _render_finto)
    monkeypatch.setattr(chainlit_app.cl, "Message", _Messaggio)
    monkeypatch.setattr(chainlit_app.cl, "Image",
                        lambda **kw: SimpleNamespace(tipo="image", **kw))

    def _login(ruolo, *, con_utente=True, con_metadata=True):
        if not con_utente:
            sessione = _Sessione({})
        else:
            metadata = {"role": ruolo} if con_metadata else {}
            sessione = _Sessione({"user": _Utente(metadata)})
        monkeypatch.setattr(chainlit_app.cl, "user_session", sessione)

    stato.login = _login
    stato.ultimo = lambda: stato.messaggi[-1].content if stato.messaggi else None
    return stato


def _click(doc, page=5, direzione="succ"):
    """Un click sul pulsante di paginazione, col payload che il client controlla."""
    azione = SimpleNamespace(
        name="paginate",
        payload={"doc": doc, "page": page, "dir": direzione},
        forId="msg-1",
    )
    asyncio.run(chainlit_app.on_paginate(azione))


# ------------------------------------------------- la guardia nel percorso UI


@needs_pg
class TestPaginazioneFiltrataPerRuolo:
    def test_il_ruolo_autorizzato_vede_ancora_la_pagina(self, ui):
        """Il rovescio necessario: la demo gira come `tecnico` e non deve
        cambiare di una virgola."""
        ui.login("tecnico")
        _click(TECNICO_ONLY, page=223, direzione="succ")
        assert ui.render == [(TECNICO_ONLY, 224)]
        assert ui.messaggi[-1].elements, "la pagina renderizzata deve essere allegata"

    def test_anche_il_commerciale_pagina_i_documenti_suoi(self, ui):
        """La guardia filtra per RUOLO, non spegne la funzione."""
        ui.login("commerciale")
        _click(COMMERCIALE_OK, page=2, direzione="succ")
        assert ui.render == [(COMMERCIALE_OK, 3)]

    def test_il_commerciale_non_ottiene_la_pagina_di_un_documento_tecnico(self, ui):
        ui.login("commerciale")
        _click(TECNICO_ONLY, page=223, direzione="succ")
        assert ui.render == [], (
            f"leak RBAC: renderizzata una pagina di {TECNICO_ONLY} (tecnico-only) "
            f"per il commerciale: {ui.render}")
        assert not ui.messaggi[-1].elements, "nessuna immagine deve essere allegata"

    def test_la_paginazione_negata_non_ripropone_i_pulsanti(self, ui):
        """Se restassero i pulsanti, il rifiuto sarebbe solo un passo indietro:
        si continuerebbe a camminare il documento a click."""
        ui.login("commerciale")
        _click(TECNICO_ONLY, page=223, direzione="succ")
        assert not ui.messaggi[-1].actions

    @pytest.mark.parametrize("ruolo", [
        "ruolo-inventato", "", "   ", None, 7,
        # niente sottostringhe: l'errore di F.1 (M1 che confrontava per
        # sottostringa, fixato in ac8da1a) non deve rinascere qui.
        "tecnic", "tecnico2", "tecnico,commerciale",
    ])
    def test_ruolo_ignoto_o_assente_non_renderizza(self, ui, ruolo):
        """Fail-closed: fuori dall'insieme chiuso dei ruoli non si vede nulla."""
        ui.login(ruolo)
        _click(TECNICO_ONLY, page=223, direzione="succ")
        assert ui.render == []

    @pytest.mark.parametrize("ruolo", ["TECNICO", " tecnico ", "Tecnico"])
    def test_il_ruolo_e_normalizzato_come_ovunque(self, ui, ruolo):
        """Non e' un buco: e' la STESSA normalizzazione (strip + lower) di
        `common.roles.parse_role` e di `catalog._role_str`. Il ruolo arriva
        dalla sessione — `auth_callback` lo valida con `Role(r)` — non dal
        payload; divergere qui creerebbe due nozioni di "ruolo" nel sistema.
        """
        ui.login(ruolo)
        _click(TECNICO_ONLY, page=223, direzione="succ")
        assert ui.render == [(TECNICO_ONLY, 224)]

    def test_sessione_senza_metadata_non_renderizza(self, ui):
        ui.login("tecnico", con_metadata=False)
        _click(TECNICO_ONLY, page=223, direzione="succ")
        assert ui.render == []

    def test_sessione_senza_utente_non_renderizza(self, ui):
        ui.login(None, con_utente=False)
        _click(TECNICO_ONLY, page=223, direzione="succ")
        assert ui.render == []

    def test_il_messaggio_non_dice_se_il_documento_esiste(self, ui):
        """La falla precedente era esattamente un oracolo di esistenza: il
        rifiuto per DOCUMENTO NEGATO e quello per DOCUMENTO INESISTENTE devono
        essere lo stesso identico testo, byte per byte."""
        ui.login("commerciale")
        _click(TECNICO_ONLY, page=223, direzione="succ")
        negato = ui.ultimo()

        _click(INESISTENTE, page=223, direzione="succ")
        inesistente = ui.ultimo()

        ui.login("tecnico")
        _click(INESISTENTE, page=223, direzione="succ")
        inesistente_per_chi_puo = ui.ultimo()

        assert negato == inesistente == inesistente_per_chi_puo, (
            "il testo del rifiuto distingue i tre casi: e' un oracolo di esistenza")
        assert TECNICO_ONLY not in negato and INESISTENTE not in inesistente

    @pytest.mark.parametrize("doc_ostile", [
        "../../../etc/passwd",
        "../corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN",
        "/etc/passwd",
        "..",
        "corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN",
        "Mamoli*",                              # glob: risolve il PDF vero
        "Mamoli_Catalogo_Ricambi_2021_IT_E?",   # glob: risolve il PDF vero
        "*",
        "[Mm]amoli*",
        "",
        None,
        223,
        {"doc": "Mamoli"},
    ])
    def test_doc_ostile_non_renderizza(self, ui, doc_ostile):
        """`doc` arriva dal client: e' input ostile, non un identificatore.

        Il ruolo qui e' `tecnico`, cioe' quello che vede TUTTI i documenti: se
        anche cosi' non si renderizza, il rifiuto viene dalla forma dell'input
        e non per caso dalla mancanza di privilegi.
        """
        ui.login("tecnico")
        _click(doc_ostile, page=223, direzione="succ")
        assert ui.render == []

    @pytest.mark.parametrize("page_ostile", [None, "223", "abc", 3.5, True, [223], {}])
    def test_page_ostile_non_renderizza(self, ui, page_ostile):
        ui.login("tecnico")
        _click(TECNICO_ONLY, page=page_ostile, direzione="succ")
        assert ui.render == []

    def test_pagina_fuori_range_bassa_non_renderizza(self, ui):
        """Da pagina 1 non si va a pagina 0: non c'e' niente da chiedere al
        disco, quindi non glielo si chiede."""
        ui.login("tecnico")
        _click(TECNICO_ONLY, page=1, direzione="prec")
        assert ui.render == []

    @pytest.mark.parametrize("dir_ostile", ["SUCC", "../", None, 1, ""])
    def test_direzione_ostile_non_renderizza(self, ui, dir_ostile):
        ui.login("tecnico")
        _click(TECNICO_ONLY, page=223, direzione=dir_ostile)
        assert ui.render == []


class TestDocStemComeInputOstile:
    """Il glob di `resolve_pdf` e' il motivo per cui `doc` va validato per FORMA
    e non solo per visibilita': un pattern non e' un identificatore."""

    def test_il_glob_nel_doc_stem_risolverebbe_un_pdf_diverso(self):
        """La prova che i metacaratteri sono sfruttabili, non un'ipotesi.

        `resolve_pdf` fa `CORPUS_DIR.glob(f"**/{doc_stem}.pdf")`: con `doc_stem`
        = `Mamoli*` il pattern diventa `**/Mamoli*.pdf` e pesca il PDF reale.
        Senza il controllo di forma, un `doc` che NON e' uno slug (quindi
        invisibile a chiunque nel registro) potrebbe comunque materializzare
        pagine dal disco se un domani la guardia venisse spostata o allentata.
        """
        vero = resolve_pdf(TECNICO_ONLY)
        if vero is None:
            pytest.skip("corpus non presente su questa macchina")
        assert is_safe_doc_stem("Mamoli*") is False
        assert is_safe_doc_stem("Mamoli_Catalogo_Ricambi_2021_IT_E?") is False
        # e la forma sbarrata e' proprio quella che avrebbe pescato il PDF vero
        assert resolve_pdf("Mamoli*") is None
        assert resolve_pdf("Mamoli_Catalogo_Ricambi_2021_IT_E?") is None

    @pytest.mark.parametrize("stem", [
        "../../etc/passwd", "/etc/passwd", "..", "a/b", "a\\b", "*", "?", "[a]",
        "", "   ", " Mamoli", None, 3, b"Mamoli",
    ])
    def test_stem_non_plausibili(self, stem):
        assert is_safe_doc_stem(stem) is False

    @pytest.mark.parametrize("stem", [
        TECNICO_ONLY, COMMERCIALE_OK, "Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf",
        "DeliTools_SpareParts_List.xlsx", "rotax_912iS_IPC",
    ])
    def test_gli_slug_reali_restano_validi(self, stem):
        """Il rovescio: la regola non deve rifiutare i nomi veri del registro
        (punti ed estensioni inclusi — `documents.slug` ne contiene)."""
        assert is_safe_doc_stem(stem) is True


# ------------------------------------- la seconda porta: i file degli elementi


class TestNomeElementoVersoDocumento:
    """`doc_stem_from_element_key` e' l'inversa della convenzione di nome di
    `LocalStorageClient.upload_file`. Se sbaglia in eccesso nega file
    legittimi, se sbaglia in difetto lascia passare pagine di altri ruoli."""

    @pytest.mark.parametrize("chiave, atteso", [
        # forma nuova (sanitizzata da upload_file)
        (f"{TECNICO_ONLY}_p0224.png", TECNICO_ONLY),
        (f"uuid-thread/uuid-elemento/{TECNICO_ONLY}_p0075.png", TECNICO_ONLY),
        ("flow_exploded_2022_p0007.png", "flow_exploded_2022"),
        # forma vecchia rimasta su disco (senza estensione)
        (f"{TECNICO_ONLY} p.27", TECNICO_ONLY),
        (f"uuid/uuid/{TECNICO_ONLY} p.224", TECNICO_ONLY),
    ])
    def test_riconosce_le_due_generazioni_di_nome(self, chiave, atteso):
        assert doc_stem_from_element_key(chiave) == atteso

    @pytest.mark.parametrize("chiave", [
        "dummy.png", "uuid/uuid/dummy.png", "allegato_utente.pdf", "", None, 7,
    ])
    def test_cio_che_non_e_una_pagina_resta_None(self, chiave):
        """None = "non e' una pagina di documento", NON "consentito": e' il
        chiamante a decidere, e per questi file decide di servirli come prima."""
        assert doc_stem_from_element_key(chiave) is None

    @pytest.mark.parametrize("chiave", ["*_p0001.png", "[Mm]amoli_p0001.png"])
    def test_uno_stem_non_plausibile_non_diventa_un_documento(self, chiave):
        assert doc_stem_from_element_key(chiave) is None

    def test_su_una_chiave_con_traversal_si_guarda_il_solo_nome(self):
        """La chiave contiene legittimamente `/` (`{thread}/{elemento}/nome`),
        quindi si deriva dal NOME. Un `../` non fa quindi saltare il controllo:
        produce uno stem che nessun ruolo vede (-> 404), e il traversal vero e
        proprio resta bloccato dal controllo `is_relative_to` gia' presente.
        """
        assert doc_stem_from_element_key("../../etc/passwd_p0001.png") == "passwd"


# --------------------------------------------- la guardia in isolamento (SQL)


@needs_pg
class TestDocVisibleToInIsolamento:
    def test_il_ruolo_autorizzato_vede_il_documento(self):
        assert doc_visible_to(TECNICO_ONLY, "tecnico") is True

    def test_il_ruolo_non_autorizzato_non_lo_vede(self):
        assert doc_visible_to(TECNICO_ONLY, "commerciale") is False

    def test_accetta_anche_l_enum_Role(self):
        """La UI passa una stringa, il resto del sistema un `Role`: una sola
        normalizzazione, come in `catalog._role_str`."""
        assert doc_visible_to(TECNICO_ONLY, Role.TECNICO) is True
        assert doc_visible_to(TECNICO_ONLY, Role.COMMERCIALE) is False

    @pytest.mark.parametrize("ruolo", [
        "ruolo-inventato", "", "   ", None, 7, "tecnic", "tecnico2"])
    def test_ruolo_ignoto_o_assente_non_vede_nulla(self, ruolo):
        """Fail-closed: un ruolo fuori da `document_roles` non vede niente,
        invece di vedere tutto — che sarebbe il comportamento di un LEFT JOIN."""
        assert doc_visible_to(TECNICO_ONLY, ruolo) is False

    @pytest.mark.parametrize("doc", [INESISTENTE, "Mamoli%", "Mamoli*", "", None])
    def test_documento_inesistente_non_e_visibile(self, doc):
        """`Mamoli%` prova che il confronto e' `=` e non `LIKE`: un jolly SQL
        non deve poter pescare uno slug diverso."""
        assert doc_visible_to(doc, "tecnico") is False

    def test_documento_senza_ruoli_non_e_visibile_a_nessuno(self):
        """L'invariante fail-closed dell'INNER JOIN su un registro sintetico.

        Le tabelle sono TEMPORANEE (sessione-locali, invisibili alle altre
        connessioni, sparite alla disconnessione) e la transazione viene
        annullata: il registro reale non viene toccato in nessun modo.
        """
        with psycopg.connect(conninfo_readonly()) as conn:
            try:
                conn.execute(
                    "CREATE TEMP TABLE documents (id serial PRIMARY KEY, slug text)")
                conn.execute(
                    "CREATE TEMP TABLE document_roles (document_id int, role text)")
                conn.execute(
                    "INSERT INTO documents (slug) VALUES ('senza_ruoli'), ('con_ruoli')")
                conn.execute("INSERT INTO document_roles VALUES (2, 'tecnico')")

                assert document_visible(
                    conn, "con_ruoli", "tecnico", schema="pg_temp") is True
                assert document_visible(
                    conn, "senza_ruoli", "tecnico", schema="pg_temp") is False
            finally:
                conn.rollback()

    def test_il_tecnico_continua_a_vedere_tutto_il_registro(self):
        """La demo gira come `tecnico`: la misura che dice che non e' cambiato
        nulla e' il conteggio, non l'impressione."""
        with psycopg.connect(conninfo_readonly()) as conn:
            slugs = [r[0] for r in conn.execute(
                sql.SQL("SELECT slug FROM public.documents")).fetchall()]
        assert len(slugs) >= 147, f"registro piu' piccolo del previsto: {len(slugs)}"
        invisibili = [s for s in slugs if not doc_visible_to(s, "tecnico")]
        assert invisibili == [], f"documenti spariti al tecnico: {invisibili}"

    def test_lo_stesso_documento_dietro_la_porta_dei_file(self):
        """La pagina renderizzata si puo' chiedere anche a `/files/ui_elements`:
        se solo `paginate` fosse filtrato, la falla sarebbe spostata, non chiusa."""
        assert doc_stem_from_element_key(
            f"uuid-thread/uuid-elemento/{TECNICO_ONLY}_p0224.png") == TECNICO_ONLY
        assert doc_visible_to(TECNICO_ONLY, "commerciale") is False

    def test_postgres_irraggiungibile_nega_invece_di_esplodere(self, monkeypatch):
        """Un guasto d'ambiente non deve far passare la pagina (e nemmeno far
        esplodere il click): degrada a NEGATO."""
        monkeypatch.setattr(
            "src.registry.access.conninfo_readonly",
            lambda: "host=127.0.0.1 port=1 user=nessuno dbname=nessuno")
        assert doc_visible_to(TECNICO_ONLY, "tecnico") is False
