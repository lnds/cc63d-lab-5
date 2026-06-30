# ============================================================================
#  Multi-stage build (Python) — separar la compilación del runtime
# ============================================================================
#
#  Etapa 1 (builder): instala las dependencias en un virtualenv. Trae compilador
#  y headers, que NO queremos arrastrar a producción.
#  Etapa 2 (runtime): imagen slim + solo el virtualenv y el código. Más liviana
#  y con menos superficie de ataque.
# ----------------------------------------------------------------------------

FROM python:3.12-slim AS builder
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
# Solo copiamos el virtualenv ya construido: el runtime no necesita pip ni gcc.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY app.py .
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "app:app"]
