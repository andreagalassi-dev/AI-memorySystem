"""Epica H — Port della ricerca online + adapter (SearXNG, Fake, Frozen).

PERCHE' UN PORT. Stessa disciplina di `LanguageModel` (src/graph/llm.py),
`TableExtractor` (1.2) e `VisualEmbedder` (1.1): il motore di ricerca sta DIETRO
un Protocol, cosi' il resto del sistema si sviluppa e si prova senza rete. Qui
la cosa e' piu' che estetica: **nessun test di questo modulo tocca internet**, e
la demo puo' girare a wifi spento (vedi `FrozenWebSearch`).

PERCHE' SEARXNG AUTO-OSPITATO. Nessuna API a pagamento (nessuna chiave da
comprare, nessun segreto nel repo), e DuckDuckGo interrogato direttamente
risponde CAPTCHA. SearXNG e' un metamotore che gira in un container nostro,
aggrega piu' motori e restituisce JSON strutturato (title, url, content,
engine, score). Vedi il servizio `searxng` in docker-compose.yml.

FAIL-CLOSED, ED E' IL PUNTO. Il vincolo sovrano del progetto (CLAUDE.md) e':
giusto, oppure "non lo so", MAI sbagliato in silenzio. Applicato qui significa
che l'adapter, quando il servizio non c'e' / non risponde / risponde storto,
SOLLEVA `WebSearchUnavailable`. Non restituisce mai una lista vuota travestita
da "il web non sa niente", perche' quella bugia diventerebbe una risposta
sbagliata a valle. Un'installazione air-gapped semplicemente non avvia il
servizio: `build_web_search_from_env()` restituisce None e il ramo web non
esiste — il sistema si comporta identico a prima.

Questo modulo NON sanitizza e NON costruisce prompt: il testo che torna di qui
e' TESTO NON FIDATO scritto da terzi. L'igienizzazione e la guardia sulle
citazioni stanno in `src/retrieval/web_evidence.py`.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# --- variabili d'ambiente (vedi .env.example) --------------------------------
WEB_SEARCH_ENABLED_ENV = "AIKE_WEB_SEARCH"      # off | searxng | frozen
SEARXNG_URL_ENV = "SEARXNG_URL"
WEB_CASSETTE_ENV = "AIKE_WEB_CASSETTE"

DEFAULT_SEARXNG_URL = "http://localhost:8888"
DEFAULT_K = 5
DEFAULT_TIMEOUT_S = 6.0

# Tetto duro sulla risposta HTTP: un servizio compromesso (o un motore a monte
# impazzito) non deve poterci far ingoiare centinaia di MB. Superato il tetto
# la ricerca e' indisponibile — non "parzialmente riuscita".
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class WebSearchUnavailable(RuntimeError):
    """La ricerca online non e' utilizzabile ADESSO (servizio spento, timeout,
    risposta non valida). E' un errore esplicito, mai un risultato vuoto."""


@dataclass(frozen=True)
class WebResult:
    """Un risultato di ricerca, ancora GREZZO e NON FIDATO.

    `url` e' la fonte citabile: e' l'unica cosa che la guardia in uscita
    (`verify_web_citations`) accetta di veder comparire nella risposta.
    """

    title: str
    url: str
    snippet: str
    engine: str = ""
    score: float = 0.0

    def as_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet,
                "engine": self.engine, "score": self.score}


@runtime_checkable
class WebSearch(Protocol):
    """Contratto della ricerca online, indipendente dal motore."""

    def search(self, query: str, k: int = DEFAULT_K) -> list[WebResult]:
        ...


def _is_http_url(url: str) -> bool:
    """Solo http/https. Blocca javascript:, data:, file:, ecc. — uno schema
    esotico in una 'fonte' non e' una fonte, e' un vettore."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _result_from_searxng(raw: object) -> WebResult | None:
    """Converte una voce JSON di SearXNG. Scarta (None) cio' che non ha un URL
    http/https utilizzabile: senza fonte citabile il risultato non serve."""
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    if not isinstance(url, str) or not _is_http_url(url):
        return None
    try:
        score = float(raw.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return WebResult(
        title=str(raw.get("title") or ""),
        url=url.strip(),
        # SearXNG chiama "content" lo snippet; puo' mancare o essere null.
        snippet=str(raw.get("content") or ""),
        engine=str(raw.get("engine") or ""),
        score=score,
    )


class SearxngWebSearch:
    """Adapter `WebSearch` sull'istanza SearXNG del compose (profilo "web").

    Usa `httpx`, gia' presente nell'ambiente come dipendenza transitiva: nessun
    SDK nuovo da aggiungere (vincolo esplicito del progetto).

    Interroga in GET `/search?q=...&format=json`; il formato JSON e' abilitato
    in `docker/searxng/settings.yml` (senza quella riga l'endpoint da' 403).
    """

    def __init__(self, base_url: str = DEFAULT_SEARXNG_URL, *,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 language: str | None = None,
                 client=None):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.language = language
        self._client = client  # iniettabile: nei test si passa un doppio

    def _get_client(self):
        if self._client is not None:
            return self._client, False
        import httpx  # import pigro: l'ambiente di test non deve dipenderne
        return httpx.Client(timeout=self.timeout_s), True

    def search(self, query: str, k: int = DEFAULT_K) -> list[WebResult]:
        if not query or not query.strip():
            return []
        params = {"q": query.strip(), "format": "json"}
        if self.language:
            params["language"] = self.language

        client, owned = self._get_client()
        try:
            payload = self._fetch(client, params)
        except WebSearchUnavailable:
            raise
        except Exception as exc:  # rete giu', DNS, timeout, TLS...
            raise WebSearchUnavailable(
                f"SearXNG non raggiungibile su {self.base_url}: {exc}") from exc
        finally:
            if owned:
                client.close()

        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise WebSearchUnavailable("SearXNG: risposta senza campo 'results'")

        out: list[WebResult] = []
        for raw in raw_results:
            res = _result_from_searxng(raw)
            if res is not None:
                out.append(res)
            if len(out) >= max(0, k):
                break
        logger.info("web search '%s': %d risultati (k=%d)", query, len(out), k)
        return out

    def _fetch(self, client, params: dict) -> dict:
        """GET con tetto sui byte letti. Il tetto e' applicato mentre si legge,
        non dopo: una risposta enorme non deve entrare in memoria per intero."""
        url = f"{self.base_url}/search"
        with client.stream("GET", url, params=params) as resp:
            status = getattr(resp, "status_code", 0)
            if status != 200:
                raise WebSearchUnavailable(f"SearXNG ha risposto HTTP {status}")
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise WebSearchUnavailable(
                        f"SearXNG: risposta oltre il tetto di {MAX_RESPONSE_BYTES} byte")
                chunks.append(chunk)
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise WebSearchUnavailable(f"SearXNG: JSON non valido ({exc})") from exc
        if not isinstance(payload, dict):
            raise WebSearchUnavailable("SearXNG: JSON di forma inattesa")
        return payload


class FakeWebSearch:
    """Motore deterministico per i test: risultati fissi, handler o errore.

    Gemello di `FakeLLM` (src/graph/llm.py), stesse tre modalita':
    - `results`: lista restituita sempre;
    - `handler`: funzione `(query, k) -> list[WebResult]`;
    - `error`: eccezione SOLLEVATA a ogni chiamata (prova del fail-closed).
    Registra le chiamate in `calls`; `handler` ha precedenza su `results`.
    """

    def __init__(self, results: Sequence[WebResult] | None = None, *,
                 handler=None, error: Exception | None = None):
        self._results = list(results or [])
        self._handler = handler
        self._error = error
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, k: int = DEFAULT_K) -> list[WebResult]:
        self.calls.append((query, k))
        if self._error is not None:
            raise self._error
        if self._handler is not None:
            return list(self._handler(query, k))[:k]
        return list(self._results)[:k]


# ------------------------------------------------------------ cassetta offline

CASSETTE_VERSION = 1


def _cassette_key(query: str) -> str:
    """Chiave di lookup: minuscole e spazi collassati. NIENTE confronto per
    sottostringa (lezione ac8da1a): o e' la stessa domanda, o e' un'altra."""
    return " ".join(query.split()).casefold()


class FrozenWebSearch:
    """Rilegge una cassetta JSON registrata: ricerca online SENZA rete.

    A cosa serve davvero: la dimostrazione non deve dipendere dal wifi della
    sala. Si registra la cassetta una volta con `record_cassette()` contro
    SearXNG vero, la si versiona, e da li' in poi il ramo web e' riproducibile
    byte per byte — anche su una macchina air-gapped.

    Su domanda non registrata restituisce [] e lo LOGGA: la lista vuota qui NON
    e' una bugia (la cassetta e' davvero tutto cio' che sappiamo) e spinge il
    sistema verso "non lo so", che e' la direzione sicura.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._entries = self._load(self.path)

    @staticmethod
    def _load(path: Path) -> dict[str, list[WebResult]]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WebSearchUnavailable(f"cassetta web assente: {path}") from exc
        except json.JSONDecodeError as exc:
            raise WebSearchUnavailable(f"cassetta web illeggibile: {path} ({exc})") from exc
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            raise WebSearchUnavailable(f"cassetta web di forma inattesa: {path}")
        out: dict[str, list[WebResult]] = {}
        for query, results in entries.items():
            items = []
            for r in results if isinstance(results, list) else []:
                if not isinstance(r, dict) or not _is_http_url(str(r.get("url", ""))):
                    continue
                try:
                    score = float(r.get("score") or 0.0)
                except (TypeError, ValueError):
                    score = 0.0
                items.append(WebResult(
                    title=str(r.get("title") or ""),
                    url=str(r["url"]).strip(),
                    snippet=str(r.get("snippet") or r.get("content") or ""),
                    engine=str(r.get("engine") or ""),
                    score=score,
                ))
            out[_cassette_key(str(query))] = items
        return out

    @property
    def queries(self) -> list[str]:
        return sorted(self._entries)

    def search(self, query: str, k: int = DEFAULT_K) -> list[WebResult]:
        hits = self._entries.get(_cassette_key(query))
        if hits is None:
            logger.warning("cassetta web: nessuna registrazione per %r (%s)",
                           query, self.path)
            return []
        return list(hits)[:k]


def record_cassette(engine: WebSearch, queries: Iterable[str], path: str | Path,
                    *, k: int = DEFAULT_K) -> Path:
    """Registra le risposte di un motore vero in una cassetta riproducibile.

    Usato una tantum da uno script di preparazione demo, MAI dai test.
    """
    out = Path(path)
    entries = {q: [r.as_dict() for r in engine.search(q, k=k)] for q in queries}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"version": CASSETTE_VERSION, "engine": type(engine).__name__,
         "entries": entries},
        ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("cassetta web scritta: %s (%d domande)", out, len(entries))
    return out


# --------------------------------------------------------------------- factory

def build_web_search_from_env(env: dict | None = None) -> WebSearch | None:
    """Costruisce il motore secondo l'ambiente, o None se la ricerca e' spenta.

    None NON e' un errore: e' l'installazione air-gapped, o semplicemente il
    servizio `searxng` non avviato (sta dietro il profilo compose "web"). Il
    chiamante che riceve None non deve avere un ramo web — cosi' il sistema si
    comporta ESATTAMENTE come prima che l'epica H esistesse.

    - AIKE_WEB_SEARCH=off (default) -> None
    - AIKE_WEB_SEARCH=searxng       -> SearxngWebSearch su $SEARXNG_URL
    - AIKE_WEB_SEARCH=frozen        -> FrozenWebSearch su $AIKE_WEB_CASSETTE
    """
    e = os.environ if env is None else env
    mode = str(e.get(WEB_SEARCH_ENABLED_ENV, "off")).strip().lower()
    if mode in ("", "off", "0", "false", "no"):
        return None
    if mode == "frozen":
        cassette = e.get(WEB_CASSETTE_ENV)
        if not cassette:
            logger.warning("AIKE_WEB_SEARCH=frozen ma %s non e' impostata: ramo web spento",
                           WEB_CASSETTE_ENV)
            return None
        return FrozenWebSearch(cassette)
    if mode in ("searxng", "on", "1", "true"):
        return SearxngWebSearch(str(e.get(SEARXNG_URL_ENV) or DEFAULT_SEARXNG_URL))
    logger.warning("valore %s=%r non riconosciuto: ramo web spento",
                   WEB_SEARCH_ENABLED_ENV, mode)
    return None
