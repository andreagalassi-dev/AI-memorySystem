# OCR — placeholder Fase 0 (P0.2).
# Immagine leggera che tiene su il container in attesa della pipeline OCR (P1).
FROM python:3.11-slim

WORKDIR /app

# Il codice reale viene montato come volume (./src -> /app/src) da docker-compose.

# Healthcheck banale: il container e' "healthy" finche' Python risponde.
HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=2s \
    CMD ["python", "-c", "import sys; sys.exit(0)"]

# Tiene su il container in attesa dell'entrypoint reale.
CMD ["sleep", "infinity"]
