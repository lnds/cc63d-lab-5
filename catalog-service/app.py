import logging
import os
import time
from datetime import date, datetime, timezone

import psycopg2
from flask import Flask, Response, g, jsonify, request
from flask.json.provider import DefaultJSONProvider
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from psycopg2.extras import RealDictCursor


# --- JSON: fechas como ISO-8601 (UTC con 'Z') ---

class ISOJSONProvider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, datetime):
            if o.tzinfo is None:
                o = o.replace(tzinfo=timezone.utc)
            return o.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(o, date):
            return o.strftime("%Y-%m-%d")
        return super().default(o)


app = Flask(__name__)
app.json = ISOJSONProvider(app)
logging.basicConfig(level=logging.INFO)

# Factor III (12-Factor): la configuración vive en el environment.
# Este servicio es dueño de SU base de datos (database-per-service).
DATABASE_URL = os.environ["DATABASE_URL"]


# --- Métricas Prometheus (la app sigue instrumentada desde la Clase 4) ---

REQUEST_COUNT = Counter(
    "http_requests_total", "Total de requests HTTP",
    ["method", "endpoint", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "Latencia de las requests HTTP",
    ["endpoint"],
)


def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.before_request
def _start_timer():
    g._start_time = time.perf_counter()


@app.after_request
def _record_metrics(response):
    if request.endpoint == "metrics":
        return response
    endpoint = request.endpoint or "unknown"
    elapsed = time.perf_counter() - getattr(g, "_start_time", time.perf_counter())
    REQUEST_LATENCY.labels(endpoint=endpoint).observe(elapsed)
    REQUEST_COUNT.labels(
        method=request.method, endpoint=endpoint, status=response.status_code
    ).inc()
    return response


@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


def query_all(sql, params=()):
    with get_db().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(sql, params=()):
    with get_db().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


# --- Health ---

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# --- Services ---

@app.route("/services", methods=["GET"])
def list_services():
    rows = query_all("SELECT * FROM services ORDER BY name")
    return jsonify([dict(r) for r in rows])


@app.route("/services", methods=["POST"])
def create_service():
    data = request.json
    if not data or not data.get("name") or not data.get("team"):
        return jsonify({"error": "name and team are required"}), 400
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO services (name, team, slo_target, sli_type) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (data["name"], data["team"],
                 data.get("slo_target", 99.9), data.get("sli_type", "availability")),
            )
            new_id = cur.fetchone()["id"]
        db.commit()
    except psycopg2.errors.UniqueViolation:
        db.rollback()
        return jsonify({"error": f"service '{data['name']}' already exists"}), 409
    return jsonify({"id": new_id}), 201


@app.route("/services/<int:service_id>", methods=["GET"])
def get_service(service_id):
    row = query_one("SELECT * FROM services WHERE id = %s", (service_id,))
    if not row:
        return jsonify({"error": "service not found"}), 404
    return jsonify(dict(row))


# --- On-Call ---

@app.route("/oncall", methods=["GET"])
def list_oncall():
    rows = query_all("""
        SELECT o.*, s.name as service_name
        FROM oncall o JOIN services s ON o.service_id = s.id
        ORDER BY o.start_date DESC
    """)
    return jsonify([dict(r) for r in rows])


@app.route("/oncall", methods=["POST"])
def create_oncall():
    data = request.json
    required = ("service_id", "person", "email", "start_date", "end_date")
    if not data or not all(k in data for k in required):
        return jsonify({"error": f"{', '.join(required)} are required"}), 400
    db = get_db()
    service = query_one("SELECT id FROM services WHERE id = %s", (data["service_id"],))
    if not service:
        return jsonify({"error": "service not found"}), 404
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO oncall (service_id, person, email, start_date, end_date) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (data["service_id"], data["person"], data["email"],
             data["start_date"], data["end_date"]),
        )
        new_id = cur.fetchone()["id"]
    db.commit()
    return jsonify({"id": new_id}), 201


@app.route("/oncall/current/<int:service_id>", methods=["GET"])
def get_current_oncall(service_id):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = query_one(
        "SELECT * FROM oncall WHERE service_id = %s "
        "AND start_date <= %s AND end_date >= %s LIMIT 1",
        (service_id, today, today),
    )
    if not row:
        return jsonify({"error": "no one on-call for this service today"}), 404
    return jsonify(dict(row))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
