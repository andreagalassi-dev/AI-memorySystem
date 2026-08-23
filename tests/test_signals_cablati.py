"""Difesa di CLASSE: un campo di `Signals` letto da `confidence_level` ma MAI
popolato da chi costruisce i segnali in PRODUZIONE.

Perche' esiste questo file
--------------------------
Il progetto ha una classe di difetto ricorrente: una guardia scritta, testata e
verde — e mai cablata dove gira il sistema. Tre casi accertati, tutti su
`Signals` (`src/retrieval/confidence.py`):
  * `singleton`/`is_code`  — scritti il 2/08 (3d97180), mai passati da
    `confidence_node`; riparati il 18/08 (92eb955).
  * `hop2_warning`         — `confidence_level` ha `if s.hop2_warning -> MEDIA`,
    il grafo non lo popola mai.
  * `is_generic_code`      — non popolato dal grafo, e con la stringa di
    confronto sbagliata a monte.
In tutti e tre i casi i test unitari erano VERDI, perche' costruiscono `Signals`
a mano passando i campi: dimostrano che la FUNZIONE funziona, non che il SISTEMA
la usi.

Come questo test evita di stantirsi
-----------------------------------
Niente elenchi di nomi scritti a mano nel corpo dei controlli. Tutto e' derivato:
  1. i CAMPI da coprire vengono da `dataclasses.fields(Signals)` — aggiungere un
     campo al dataclass lo mette automaticamente sotto esame;
  2. i COSTRUTTORI VIVI si scoprono a RUNTIME, eseguendo il grafo reale con
     dipendenze fittizie e spiando ogni costruzione di `Signals` — non c'e' una
     lista di moduli da aggiornare, e un costruttore che nessun percorso di
     produzione raggiunge NON conta (e' esattamente il caso di
     `confidence.extract_signals`, che nel repo e' usato dai soli test: un
     controllo statico "qualcuno da qualche parte passa il campo" sarebbe verde
     e inutile);
  3. i CAMPI PASSATI si osservano dagli argomenti reali della chiamata, non da
     un elenco;
  4. i CAMPI LETTI da `confidence_level` si ricavano dal suo AST.

Le sole liste scritte a mano sono le ECCEZIONI, in fondo dichiarate campo per
campo con la motivazione: e l'asserzione e' di UGUAGLIANZA, quindi un'eccezione
che venisse riparata rende il test rosso e va rimossa (non puo' marcire in
silenzio).
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import pathlib
import sys

import pytest

from src.graph.pipeline import Deps, Evidence, build_graph
from src.retrieval.confidence import Signals, Thresholds, confidence_level


# --------------------------------------------------------------- ECCEZIONI
# Difetti DICHIARATI. Meglio visibili qui che silenziosi in produzione.
# L'asserzione e' `==`: riparare uno di questi rende il test rosso finche' non
# si toglie la voce. Aggiungerne una e' una scelta, non una dimenticanza.

CAMPI_NON_CABLATI_NOTI = {
    "hop2_warning": (
        "DIFETTO NOTO, non ancora riparato — vedi F7 in memoria. "
        "`confidence_level` ha `if s.hop2_warning: -> MEDIA`, ma il grafo non "
        "popola mai il campo: il warning nasce solo in `search.apply_hop2`, che "
        "in produzione non viene chiamata (il grafo ha la sua `_apply_hop2`). "
        "Bersaglio reale misurato: 210 assiemi, ~6 su 68 campionati senza figli. "
        "Cablarlo E' un cambio di comportamento (riduce la copertura ALTA): lo "
        "decide il reviewer con una misura, non questo test."
    ),
    "is_generic_code": (
        "DIFETTO NOTO, non ancora riparato — vedi F7 in memoria. Doppio guasto: "
        "(1) il grafo non popola il campo; (2) anche cablandolo NON sparerebbe, "
        "perche' il segnale confronta `code_normalized_by == 'generic'` mentre "
        "nel corpus quel valore non esiste (su 199447 chunk i valori reali sono "
        "'default' e None): la stringa e' sbagliata. Riparare = cambio di "
        "comportamento, lo decide il reviewer con una misura."
    ),
}

CAMPI_COSTANTI_AL_CALLSITE = {
    "gate": (
        "INTENZIONALE. Nel grafo il guardrail gira DOPO confidence "
        "(confidence -> fusion -> synthesis -> guardrail -> finalize), quindi al "
        "callsite il gate non esiste ancora ed e' passato come 'PASS' fisso. "
        "I rami BLOCK/VERIFY di `confidence_level` sono ridondanti qui ma NON "
        "morti a livello di sistema: `reconcile()` applica il gate a valle "
        "(BLOCK -> BASSA, VERIFY -> MEDIA)."
    ),
    "agreement": (
        "DA DECIDERE (candidato 4o caso della stessa classe, segnalato al "
        "reviewer). `fusion_node` gira DOPO confidence e per decisione misurata "
        "in P3.3 l'accordo NON promuove MEDIA->ALTA: passare None qui e' "
        "coerente. Ma nessuno ricalcola il livello dopo fusion, quindi la "
        "clausola `s.agreement != 'disagree'` del ramo ALTA e' di fatto morta a "
        "questo callsite. Cablarla e' un cambio di comportamento: serve una "
        "misura, non una correzione al volo."
    ),
}


# --------------------------------------------------------------- STRUMENTI

CAMPI = tuple(f.name for f in dataclasses.fields(Signals))


@dataclasses.dataclass(frozen=True)
class Costruzione:
    """Una costruzione di `Signals` osservata mentre il sistema gira."""
    file: str
    funzione: str
    lineno: int
    campi_passati: frozenset[str]


def _spia_signals(monkeypatch) -> list[Costruzione]:
    """Sostituisce `Signals` con una factory-spia OVUNQUE possa essere usato.

    Due mosse, insieme complete: si rimpiazza l'attributo nel modulo canonico
    (`src.retrieval.confidence`), cosi' ogni import successivo — anche lazy,
    dentro una funzione — prende la spia; e si rimpiazza in ogni modulo `src.*`
    gia' caricato che lo ha importato per nome (li' il binding e' gia' stato
    risolto e non passerebbe dal modulo canonico).
    """
    osservate: list[Costruzione] = []

    def spia(*args, **kwargs):
        fr = inspect.currentframe().f_back
        passati = set(CAMPI[:len(args)]) | set(kwargs)
        osservate.append(Costruzione(
            file=fr.f_code.co_filename,
            funzione=getattr(fr.f_code, "co_qualname", fr.f_code.co_name),
            lineno=fr.f_lineno,
            campi_passati=frozenset(passati),
        ))
        return Signals(*args, **kwargs)

    import src.retrieval.confidence as _canonico
    monkeypatch.setattr(_canonico, "Signals", spia)
    for mod in list(sys.modules.values()):
        nome = getattr(mod, "__name__", "")
        if not (nome == "src" or nome.startswith("src.")):
            continue
        if getattr(mod, "Signals", None) is Signals:
            monkeypatch.setattr(mod, "Signals", spia, raising=False)
    return osservate


def _nome_chiamato(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _argomenti_per_campo(file: str, lineno: int) -> dict[str, ast.expr]:
    """AST della chiamata `Signals(...)` che copre `lineno` in `file`.

    Serve per distinguere "campo passato con un valore che dipende dai dati" da
    "campo passato con una costante": un argomento costante non popola nulla,
    la regola che lo legge non puo' scattare da quel callsite.
    """
    albero = ast.parse(pathlib.Path(file).read_text(encoding="utf-8"))
    candidate = [n for n in ast.walk(albero)
                 if isinstance(n, ast.Call)
                 and _nome_chiamato(n.func) == "Signals"
                 and n.lineno <= lineno <= (n.end_lineno or n.lineno)]
    assert len(candidate) == 1, (
        f"non riesco a isolare la chiamata Signals(...) in {file} intorno alla "
        f"riga {lineno} (trovate {len(candidate)}). Il test va aggiornato: "
        "meglio rosso che una difesa che finge di guardare."
    )
    call = candidate[0]
    per_campo: dict[str, ast.expr] = {}
    for i, a in enumerate(call.args):
        assert not isinstance(a, ast.Starred), (
            f"{file}:{lineno} costruisce Signals con *args: gli argomenti non "
            "sono ispezionabili staticamente. Passa i campi per nome."
        )
        per_campo[CAMPI[i]] = a
    for kw in call.keywords:
        assert kw.arg is not None, (
            f"{file}:{lineno} costruisce Signals con **kwargs: gli argomenti non "
            "sono ispezionabili staticamente. Passa i campi per nome."
        )
        per_campo[kw.arg] = kw.value
    return per_campo


def _campi_letti_da_confidence_level() -> set[str]:
    """I campi di `Signals` su cui `confidence_level` prende decisioni.

    Si raccolgono tutti gli accessi ad attributo il cui nome e' un campo di
    `Signals` (`s.margin`, `signals.gate`, ...). Nessuna alias-analysis: i nomi
    dei campi sono distintivi e non collidono con gli altri attributi usati nella
    funzione (`t.tau_hi`, `t.tau_lo`).
    """
    albero = ast.parse(inspect.getsource(confidence_level))
    return {n.attr for n in ast.walk(albero)
            if isinstance(n, ast.Attribute) and n.attr in CAMPI}


def _fai_girare_la_produzione() -> None:
    """Esegue il grafo REALE con dipendenze fittizie (niente LLM, niente rete).

    Non e' un test di comportamento: serve solo a far passare il sistema dai
    nodi che costruiscono i segnali, sulle rotte che li raggiungono.
    """
    ev1 = Evidence(doc="rotax_912_914_IPC", page=22, lang="EN", code="877615",
                   desc="PISTON PIN", text=None, qty=1, price=None,
                   type="table_row", status="clean", warnings=[], score=9.0)
    ev2 = ev1._replace(code="877616", desc="RING", score=2.0)
    deps = Deps(
        retrieve=lambda *a, **k: ([ev1, ev2], []),
        synthesize=lambda q, ev: "Lo spinotto e' 877615 (rotax_912_914_IPC, pag. 22).",
        catalog_lookup=lambda k: False,
        thresholds=Thresholds(tau_hi=0.5, tau_lo=0.2),
    )
    grafo = build_graph(deps)
    # rotta narrativa + rotta codice: entrambe convergono su confidence_node.
    grafo.invoke({"query": "a cosa serve lo spinotto del pistone", "role": "tecnico"})
    grafo.invoke({"query": "codice 877615", "role": "tecnico"})


@pytest.fixture
def costruzioni(monkeypatch) -> list[Costruzione]:
    osservate = _spia_signals(monkeypatch)
    _fai_girare_la_produzione()
    assert osservate, (
        "nessuna costruzione di Signals osservata facendo girare il grafo: la "
        "spia non aggancia piu' il codice di produzione. Test da aggiornare."
    )
    return osservate


# ----------------------------------------------------------------- I TEST

class TestSignalsCablati:

    def test_ogni_campo_di_signals_e_popolato_in_produzione(self, costruzioni):
        """Rosso se un campo del dataclass non arriva mai da chi decide davvero."""
        letti = _campi_letti_da_confidence_level()
        per_costruttore: dict[str, set[str]] = {}
        for c in costruzioni:
            per_costruttore.setdefault(c.funzione, set()).update(c.campi_passati)

        mancanti: dict[str, set[str]] = {}
        for funzione, passati in per_costruttore.items():
            buchi = set(CAMPI) - passati
            if buchi:
                mancanti[funzione] = buchi

        tutti_i_buchi = set().union(*mancanti.values()) if mancanti else set()
        dettaglio = "\n".join(
            f"  {f}() non popola: "
            + ", ".join(sorted(f"{c}{' [LETTO da confidence_level: DIFESA MORTA]' if c in letti else ''}"
                               for c in sorted(b)))
            for f, b in sorted(mancanti.items()))
        assert tutti_i_buchi == set(CAMPI_NON_CABLATI_NOTI), (
            "campi di Signals non popolati dai costruttori VIVI in produzione "
            "diversi da quelli dichiarati.\n" + dettaglio
            + f"\n  dichiarati come difetti noti: {sorted(CAMPI_NON_CABLATI_NOTI)}"
            + "\nSe hai aggiunto un campo a Signals e una regola in "
              "confidence_level, valorizzalo anche in chi costruisce i segnali "
              "nel grafo. Se hai riparato un difetto noto, togli la sua voce da "
              "CAMPI_NON_CABLATI_NOTI."
        )

    def test_nessun_campo_e_una_costante_al_callsite(self, costruzioni):
        """Un argomento costante non popola nulla: la regola non puo' scattare."""
        costanti: set[str] = set()
        dettaglio: list[str] = []
        for c in costruzioni:
            for campo, valore in _argomenti_per_campo(c.file, c.lineno).items():
                if isinstance(valore, ast.Constant):
                    costanti.add(campo)
                    dettaglio.append(f"  {c.funzione}(): {campo}="
                                     f"{valore.value!r} (costante)")
        assert costanti == set(CAMPI_COSTANTI_AL_CALLSITE), (
            "campi di Signals passati come COSTANTE dal codice di produzione "
            "diversi da quelli dichiarati.\n" + "\n".join(sorted(set(dettaglio)))
            + f"\n  dichiarati: {sorted(CAMPI_COSTANTI_AL_CALLSITE)}"
        )

    def test_le_eccezioni_sono_motivate_e_riguardano_campi_esistenti(self):
        """Un'eccezione senza motivazione, o su un campo sparito, e' rumore."""
        for tabella, nome in ((CAMPI_NON_CABLATI_NOTI, "CAMPI_NON_CABLATI_NOTI"),
                              (CAMPI_COSTANTI_AL_CALLSITE, "CAMPI_COSTANTI_AL_CALLSITE")):
            for campo, motivo in tabella.items():
                assert campo in CAMPI, (
                    f"{nome} elenca '{campo}', che non e' piu' un campo di "
                    "Signals: eccezione stantia, va tolta.")
                assert len(motivo) > 80, (
                    f"{nome}['{campo}'] non spiega perche' l'eccezione esiste.")

    def test_i_campi_letti_da_confidence_level_sono_sotto_esame(self):
        """Se una regola legge un attributo che non e' un campo del dataclass,
        l'ispezione automatica non lo vedrebbe: meglio saperlo."""
        letti = _campi_letti_da_confidence_level()
        assert letti, "nessun campo letto rilevato: l'ispezione AST non funziona piu'."
        assert letti <= set(CAMPI)
