#!/usr/bin/env bash
# Datos de ejemplo: crea un servicio y un turno on-call en el catalog-service,
# y luego un incidente en el incidents-service. El incidente dispara la cadena
# completa: incidents -> catalog (on-call) -> notifications (Go).
#
# Como catalog e incidents son internos al clúster, abrimos un port-forward
# temporal para hablarles desde la máquina local.
set -euo pipefail

NS=incidentes

echo "==> port-forward a catalog-service y incidents-service"
kubectl port-forward -n "$NS" svc/catalog-service 8081:8080 >/dev/null 2>&1 &
PF_CATALOG=$!
kubectl port-forward -n "$NS" svc/incidents-service 8082:8080 >/dev/null 2>&1 &
PF_INCIDENTS=$!
trap 'kill $PF_CATALOG $PF_INCIDENTS 2>/dev/null || true' EXIT
sleep 3

START=$(python3 -c "from datetime import date,timedelta;print(date.today()-timedelta(days=365))")
END=$(python3 -c "from datetime import date,timedelta;print(date.today()+timedelta(days=365))")

echo "==> crea servicio 'checkout' (catalog-service)"
curl -s -X POST localhost:8081/services \
  -H 'Content-Type: application/json' \
  -d '{"name":"checkout","team":"payments","slo_target":99.95}'
echo

echo "==> crea turno on-call para el servicio 1 (catalog-service)"
curl -s -X POST localhost:8081/oncall \
  -H 'Content-Type: application/json' \
  -d "{\"service_id\":1,\"person\":\"Ana Soto\",\"email\":\"ana@example.com\",\"start_date\":\"$START\",\"end_date\":\"$END\"}"
echo

echo "==> crea incidente SEV2 (incidents-service -> catalog -> notifications)"
curl -s -X POST localhost:8082/incidents \
  -H 'Content-Type: application/json' \
  -d '{"service_id":1,"title":"Latencia alta en checkout","severity":2,"created_by":"ana"}'
echo

echo
echo "Listo. Mira la notificación en los logs del servicio Go:"
echo "  kubectl logs -n $NS -l app=notifications-service"
