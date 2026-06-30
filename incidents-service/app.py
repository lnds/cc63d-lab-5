import logging
import os
import time
from datetime import datetime, timezone

import psycopg2
import requests
from flask import Flask, Response, g, jsonify, request, send_from_directory
from flask.json.provider import DefaultJSONProvider
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from psycopg2.extras import RealDictCursor


class ISOJSONProvider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, datetime):
            if o.tzinfo is None:
                o = o.replace(tzinfo=timezone.utc)
            return o.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return super().default(o)


app = Flask(__name__, static_folder="static")
app.json = ISOJSONProvider(app)
logging.basicConfig(level=logging.INFO)

# Cada dependencia es una URL en el environment. En Kubernetes apuntan al NOMBRE
# del Service (DNS interno del clúster): http://catalog-service:8080, etc.
DATABASE_URL = os.environ["DATABASE_URL"]
CATALOG_URL = os.environ["CATALOG_URL"]
NOTIFICATIONS_URL = os.environ["NOTIFICATIONS_URL"]


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


def now():
    return datetime.now(timezone.utc)


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


# --- Frontend ---
# incidents-service es la puerta de entrada (NodePort): además de su API, sirve la
# interfaz web. El navegador solo alcanza este servicio; el catálogo y el de
# notificaciones son internos (ClusterIP).

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/catalog/services", methods=["GET"])
def proxy_services():
    """Reenvía la lista de servicios del catálogo para poblar el selector del
    frontend. El navegador no puede llegar al catálogo (interno); esta puerta sí."""
    try:
        r = requests.get(f"{CATALOG_URL}/services", timeout=3)
    except requests.RequestException:
        return jsonify({"error": "catalog-service unavailable"}), 502
    return Response(r.content, status=r.status_code, mimetype="application/json")


# --- Incidents ---

@app.route("/incidents", methods=["GET"])
def list_incidents():
    status = request.args.get("status")
    if status:
        rows = query_all(
            "SELECT * FROM incidents WHERE status = %s ORDER BY started_at DESC",
            (status,),
        )
    else:
        rows = query_all("SELECT * FROM incidents ORDER BY started_at DESC")
    return jsonify([dict(r) for r in rows])


@app.route("/incidents", methods=["POST"])
def create_incident():
    data = request.json
    if not data or not data.get("title") or not data.get("service_id") or not data.get("severity"):
        return jsonify({"error": "title, service_id and severity are required"}), 400
    if data["severity"] not in (1, 2, 3, 4):
        return jsonify({"error": "severity must be between 1 and 4"}), 400

    # 1) Validar el servicio contra el catalog-service (NO hay JOIN entre bases:
    #    cada servicio es dueño de sus datos, se preguntan por HTTP).
    try:
        r = requests.get(f"{CATALOG_URL}/services/{data['service_id']}", timeout=3)
    except requests.RequestException:
        return jsonify({"error": "catalog-service unavailable"}), 502
    if r.status_code == 404:
        return jsonify({"error": "service not found"}), 404
    if r.status_code != 200:
        return jsonify({"error": "catalog-service error"}), 502
    service = r.json()

    # 2) Guardamos el nombre del servicio DESNORMALIZADO: así listar incidentes
    #    no obliga a llamar al catálogo en cada request.
    ts = now()
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO incidents (service_id, service_name, title, severity, status, started_at, created_by) "
            "VALUES (%s, %s, %s, %s, 'open', %s, %s) RETURNING id",
            (data["service_id"], service["name"], data["title"], data["severity"],
             ts, data.get("created_by", "system")),
        )
        incident_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO incident_timeline (incident_id, timestamp, author, message) "
            "VALUES (%s, %s, %s, %s)",
            (incident_id, ts, data.get("created_by", "system"),
             f"Incident created: {data['title']}"),
        )
    db.commit()

    # 3) Buscar el on-call (catalog-service) y disparar la notificación (Go).
    notification = _notify_oncall(incident_id, data, service, ts)

    return jsonify({"id": incident_id, "notification": notification}), 201


def _notify_oncall(incident_id, data, service, ts):
    """Pregunta el on-call al catálogo y, si hay, llama al notifications-service."""
    try:
        r = requests.get(
            f"{CATALOG_URL}/oncall/current/{data['service_id']}", timeout=3
        )
    except requests.RequestException:
        _add_timeline(incident_id, "system", "WARNING: catalog-service unavailable for on-call")
        return None
    if r.status_code != 200:
        _add_timeline(incident_id, "system", "WARNING: no on-call found for this service")
        return None

    oncall = r.json()
    message = f"[SEV{data['severity']}] {data['title']} on {service['name']}"
    payload = {"person": oncall["person"], "email": oncall["email"], "message": message}
    try:
        requests.post(f"{NOTIFICATIONS_URL}/notify", json=payload, timeout=3)
        _add_timeline(incident_id, "system",
                      f"Notified {oncall['person']} ({oncall['email']})")
    except requests.RequestException:
        _add_timeline(incident_id, "system",
                      "WARNING: notifications-service unavailable")
    return payload


def _add_timeline(incident_id, author, message):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO incident_timeline (incident_id, timestamp, author, message) "
            "VALUES (%s, %s, %s, %s)",
            (incident_id, now(), author, message),
        )
    db.commit()


@app.route("/incidents/<int:incident_id>", methods=["GET"])
def get_incident(incident_id):
    incident = query_one("SELECT * FROM incidents WHERE id = %s", (incident_id,))
    if not incident:
        return jsonify({"error": "incident not found"}), 404
    timeline = query_all(
        "SELECT * FROM incident_timeline WHERE incident_id = %s ORDER BY timestamp",
        (incident_id,),
    )
    result = dict(incident)
    result["timeline"] = [dict(t) for t in timeline]
    return jsonify(result)


@app.route("/incidents/<int:incident_id>", methods=["PATCH"])
def update_incident(incident_id):
    data = request.json
    if not data:
        return jsonify({"error": "request body required"}), 400

    db = get_db()
    incident = query_one("SELECT * FROM incidents WHERE id = %s", (incident_id,))
    if not incident:
        return jsonify({"error": "incident not found"}), 404

    ts = now()
    author = data.get("author", "system")
    with db.cursor() as cur:
        if "status" in data:
            new_status = data["status"]
            if new_status not in ("open", "investigating", "mitigated", "resolved"):
                return jsonify({"error": "invalid status"}), 400
            resolved_at = ts if new_status == "resolved" else None
            cur.execute(
                "UPDATE incidents SET status = %s, resolved_at = COALESCE(%s, resolved_at) WHERE id = %s",
                (new_status, resolved_at, incident_id),
            )
            cur.execute(
                "INSERT INTO incident_timeline (incident_id, timestamp, author, message) "
                "VALUES (%s, %s, %s, %s)",
                (incident_id, ts, author, f"Status changed to {new_status}"),
            )
        if "message" in data:
            cur.execute(
                "INSERT INTO incident_timeline (incident_id, timestamp, author, message) "
                "VALUES (%s, %s, %s, %s)",
                (incident_id, ts, author, data["message"]),
            )
    db.commit()
    return jsonify({"ok": True})


# --- Post-Mortems ---

@app.route("/postmortems", methods=["POST"])
def create_postmortem():
    data = request.json
    required = ("incident_id", "summary", "root_cause", "impact", "action_items")
    if not data or not all(k in data for k in required):
        return jsonify({"error": f"{', '.join(required)} are required"}), 400

    db = get_db()
    incident = query_one("SELECT * FROM incidents WHERE id = %s", (data["incident_id"],))
    if not incident:
        return jsonify({"error": "incident not found"}), 404
    if incident["status"] != "resolved":
        return jsonify({"error": "incident must be resolved before writing a post-mortem"}), 400

    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO postmortems (incident_id, summary, root_cause, impact, action_items, lessons) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (data["incident_id"], data["summary"], data["root_cause"],
                 data["impact"], data["action_items"], data.get("lessons", "")),
            )
            new_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO incident_timeline (incident_id, timestamp, author, message) "
                "VALUES (%s, %s, %s, %s)",
                (data["incident_id"], now(), data.get("author", "system"),
                 "Post-mortem published"),
            )
        db.commit()
    except psycopg2.errors.UniqueViolation:
        db.rollback()
        return jsonify({"error": "post-mortem already exists for this incident"}), 409

    return jsonify({"id": new_id}), 201


@app.route("/postmortems", methods=["GET"])
def list_postmortems():
    rows = query_all("""
        SELECT p.*, i.title as incident_title, i.severity, i.service_name
        FROM postmortems p JOIN incidents i ON p.incident_id = i.id
        ORDER BY p.created_at DESC
    """)
    return jsonify([dict(r) for r in rows])


@app.route("/postmortems/<int:postmortem_id>", methods=["GET"])
def get_postmortem(postmortem_id):
    row = query_one("""
        SELECT p.*, i.title as incident_title, i.severity, i.service_name
        FROM postmortems p JOIN incidents i ON p.incident_id = i.id
        WHERE p.id = %s
    """, (postmortem_id,))
    if not row:
        return jsonify({"error": "post-mortem not found"}), 404
    return jsonify(dict(row))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
