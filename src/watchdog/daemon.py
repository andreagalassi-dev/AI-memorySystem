import time
import os
import logging
import signal
import sys
from src.watchdog.scanner import scan_directory, diff_files
from src.watchdog.worker import process_diffs
from src.watchdog.health import update_health_state

# Driver DB unico in tutto il progetto: psycopg (v3), come src/registry/registry.py.
import psycopg

logger = logging.getLogger(__name__)

class PollingDaemon:
    def __init__(self, corpus_path: str, interval: int, dsn: str, default_role: str):
        self.corpus_path = corpus_path
        self.interval = interval
        self.dsn = dsn
        self.default_role = default_role
        self.running = True

        signal.signal(signal.SIGINT, self.handle_exit)
        signal.signal(signal.SIGTERM, self.handle_exit)

    def handle_exit(self, signum, frame):
        logger.info(f"Received exit signal {signum}. Shutting down gracefully...")
        self.running = False

    def _check_db_connections(self):
        # We also check qdrant here
        from src.backend.clients.qdrant_client import get_qdrant_client
        try:
            get_qdrant_client()
            update_health_state(qdrant_ok=True)
        except Exception:
            update_health_state(qdrant_ok=False)
            raise

        try:
            with psycopg.connect(self.dsn) as conn:
                update_health_state(postgres_ok=True)
        except Exception:
            update_health_state(postgres_ok=False)
            raise

    def run(self):
        logger.info("Starting PollingDaemon...")
        update_health_state(daemon_alive=True)

        while self.running:
            try:
                self._check_db_connections()

                # psycopg3: la connessione ha .execute()/.commit() direttamente,
                # nessun cursore separato. scanner/worker ricevono la connessione.
                with psycopg.connect(self.dsn) as conn:
                    started = time.time()
                    update_health_state(last_scan_started_at=started)

                    logger.info("Scanning directory...")
                    current_files = scan_directory(self.corpus_path)

                    diffs = diff_files(conn, current_files, self.corpus_path)
                    if diffs:
                        logger.info(f"Found {len(diffs)} diffs: {diffs}")

                        # Update processing metrics
                        processing_count = conn.execute(
                            "SELECT count(*) FROM tracked_files WHERE status='processing'"
                        ).fetchone()[0]
                        update_health_state(files_in_processing=processing_count)

                        process_diffs(conn, diffs, self.corpus_path, self.default_role)

                    completed = time.time()
                    update_health_state(last_scan_completed_at=completed, files_in_processing=0)

            except Exception as e:
                logger.error(f"Error during polling cycle: {e}", exc_info=True)

            if self.running:
                logger.info(f"Cycle completed. Sleeping for {self.interval} seconds...")
                time.sleep(self.interval)

        update_health_state(daemon_alive=False)

import threading
import uvicorn
from src.watchdog.health import app as health_app

def start_health_server():
    uvicorn.run(health_app, host="0.0.0.0", port=8001, log_level="error")

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Avvia health endpoint su thread separato
    threading.Thread(target=start_health_server, daemon=True).start()

    corpus_path = os.environ.get("WATCHDOG_CORPUS_PATH", "/corpus")
    interval = int(os.environ.get("WATCHDOG_SCAN_INTERVAL_SECS", "300"))
    dsn = os.environ.get("POSTGRES_DSN", "postgresql://aike:aike_dev@postgres:5432/aike_registry")
    default_role = os.environ.get("WATCHDOG_DEFAULT_ROLE", "tecnico")

    daemon = PollingDaemon(corpus_path, interval, dsn, default_role)
    daemon.run()

if __name__ == "__main__":
    main()
