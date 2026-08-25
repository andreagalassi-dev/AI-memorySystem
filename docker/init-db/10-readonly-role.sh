#!/bin/bash
# Fase 1.3 — Ruolo Postgres SELECT-only per il ramo STRUCTURED del Cervello Unificatore.
#
# Difesa in profondita': il tool SQL e' gia' anti-injection a livello applicativo
# (Text-to-Parameters, whitelist colonne, placeholder %s), ma la connessione con cui
# gira il ramo STRUCTURED usa questo ruolo read-only, cosi' anche un ipotetico bypass
# della whitelist non potrebbe scrivere: il DB stesso rifiuta INSERT/UPDATE/DELETE.
#
# Gira UNA SOLA VOLTA, all'inizializzazione di un volume Postgres VUOTO
# (/docker-entrypoint-initdb.d). Su un volume gia' popolato NON viene rieseguito:
# in quel caso creare il ruolo a mano una volta (vedi docs/superpowers/specs/
# fase1.3-handoff.md §5.1). Idempotente comunque (IF NOT EXISTS).
#
# Credenziali dev-only via env, override da .env in produzione (mai committare i segreti).
set -euo pipefail

RO_USER="${POSTGRES_RO_USER:-aike_ro}"
RO_PASSWORD="${POSTGRES_RO_PASSWORD:-aike_ro_dev}"
APP_USER="${POSTGRES_USER:-aike}"   # proprietario delle tabelle-dati (crea lo schema)
SCHEMA="estrazione"                  # Semantic Layer 1.2 (righe_listino)

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
	DO \$\$ BEGIN
	  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${RO_USER}') THEN
	    CREATE ROLE ${RO_USER} LOGIN PASSWORD '${RO_PASSWORD}';
	  END IF;
	END \$\$;

	-- niente privilegi di scrittura: SOLO connessione, uso schema e SELECT
	GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${RO_USER};
	CREATE SCHEMA IF NOT EXISTS ${SCHEMA} AUTHORIZATION ${APP_USER};
	GRANT USAGE ON SCHEMA ${SCHEMA} TO ${RO_USER};

	-- tabelle gia' presenti (di norma nessuna a init-time) ...
	GRANT SELECT ON ALL TABLES IN SCHEMA ${SCHEMA} TO ${RO_USER};
	-- ... e quelle che l'app (APP_USER) creera' in seguito: SELECT automatico
	ALTER DEFAULT PRIVILEGES FOR ROLE ${APP_USER} IN SCHEMA ${SCHEMA}
	  GRANT SELECT ON TABLES TO ${RO_USER};

	-- Lo schema public: serve al FILTRO RBAC del ramo strutturato (SPEC F.3).
	-- Dal 25/08 il SELECT su `righe_listino` fa JOIN su `documents` e
	-- `document_roles` per non servire a un ruolo i documenti di un altro:
	-- senza questi GRANT il ramo fallisce con "permission denied" e il
	-- sistema, fail-closed, non risponde piu' NEMMENO al ruolo autorizzato.
	GRANT USAGE ON SCHEMA public TO ${RO_USER};
	GRANT SELECT ON ALL TABLES IN SCHEMA public TO ${RO_USER};
	ALTER DEFAULT PRIVILEGES FOR ROLE ${APP_USER} IN SCHEMA public
	  GRANT SELECT ON TABLES TO ${RO_USER};
SQL

echo "[init-db] ruolo read-only '${RO_USER}' pronto su schema '${SCHEMA}' (SELECT-only)."
