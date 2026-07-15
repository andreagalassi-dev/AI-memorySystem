# Fase 7 — Refactoring verso SaaS (Futuro Scalabile)

> **Dipende da:** Fasi 1-6 completate, stabili, e validate in locale (server on-premise o cloud privato) su almeno 2-3 clienti reali paganti.
> **Obiettivo:** Trasformare un'installazione server-client isolata in una piattaforma SaaS Multi-Tenant scalabile globalmente.

Questa fase rappresenta lo scaglione finale e si attiva **solo dopo** che il proof-of-concept e la validazione del mercato (Product-Market Fit) sono stati superati, per evitare *"premature optimization"*.

## 7.1 Refactoring del Backend (Da PoC a Enterprise)
- **FastAPI / Microservizi:** Abbandono del server monolitico di prototipazione (Streamlit/Chainlit diretto). Il backend diventa un'architettura **FastAPI** robusta ed esposta puramente tramite endpoint REST o gRPC/Websockets.
- **Autenticazione Avanzata:** Transizione a standard OIDC/OAuth2 con JWT, supportando ruoli granulari, Single Sign-On (SSO) per le aziende e isolamento delle autorizzazioni.
- **Rilascio e Versioning:** Implementazione del versioning severo delle API (`/api/v1/agent`, `/api/v2/ingestion`), controllo CORS e regole di Rate Limiting / Quotas per billing.

## 7.2 Refactoring del Frontend
- L'intera interfaccia Chat, Proof View, e il Pannello Amministratore vengono ricreati *da zero* utilizzando un framework UI moderno e ampiamente supportato (**React** con Next.js, o **Vue.js**).
- **Vantaggi:** Branding totalmente personalizzabile per il white-labeling, design responsivo cross-device, PWA (Progressive Web App) scaricabile da tablet in magazzino.

## 7.3 Infrastruttura Cloud di Produzione
- **Multi-Tenancy Reale:** Il sistema gestirà contemporaneamente aziende diverse. Architetturalmente, l'isolamento si evolve in *Row-Level Security* su PostgreSQL o cluster isolati (namespaces) per ogni Tenant. 
- **Scalabilità Inferenza:** Abbandono dei singoli Mac Server in favore di **Server GPU Dedicati in Cluster** (NVIDIA RTX 4090 o A100). 
- Questo apre le porte a modelli >70B (es. Nemotron 70B o Llama 3 70B), mantenendo costi sostenibili grazie a software di batching per l'inferenza condivisa come `vLLM` o `TensorRT-LLM`.
- **Telemetria Globale:** Adozione di piattaforme di Observability distribuite (es. LangSmith, Datadog) per il monitoraggio di latenza (TTFT), costi token globali, e detection di drop di confidenza nell'AI.

---

## Riepilogo Dipendenze Flusso AI

```
[Infrastruttura Dati Preesistente (Fase 0)]
        │
        ├──→ Fase 1: Pipeline Doppio Binario (PixelRAG + GLM-OCR + Gemma 4)
        │       │
        │       ├──→ Fase 2: Merge Cataloghi/Listini
        │       │
        │       ├──→ Fase 3: Fallback Triplo Livello + Igienizzazione Agente Web
        │       │
        │       └──→ Fase 4: UI Chainlit + Ciclo Feedback
        │               │
        │               ├──→ Fase 5: Memoria a Lungo Termine (Tencent Agent Memory)
        │               │
        │               └──→ Fase 6: Integrazione Semantic Layer ERP (Stock/Prezzi)
        │
        └──→ [Maturità Prodotto / Go-to-Market]
                │
                └──→ Fase 7: Refactoring SaaS Cloud (FastAPI + React/Vue.js + vLLM)
```
