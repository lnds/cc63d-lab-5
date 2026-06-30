# ============================================================================
#  Lab 5 — Microservicios políglotas en Kubernetes (minikube)
# ============================================================================
#  Flujo típico de la clase:
#     make start      # arranca minikube
#     make build      # construye las 3 imágenes
#     make load       # las carga en minikube
#     make deploy     # crea todo en el clúster, en orden
#     make seed       # datos de ejemplo
#     make open       # abre el incidents-service en el navegador
#  Visualizar:
#     make dashboard  # abre el Kubernetes Dashboard en el navegador
#  Operar:
#     make status / make scale / make rollout / make undo
#  Limpieza:
#     make undeploy / make stop
# ----------------------------------------------------------------------------

NS := incidentes
SERVICES := catalog-service incidents-service notifications-service

.PHONY: start build load deploy migrations-config seed open status logs \
        dashboard scale rollout undo sizes undeploy stop

start:
	minikube start

# --- Construir las imágenes (en el daemon Docker local) ---
build:
	docker build -t catalog-service:v1 ./catalog-service
	docker build -t incidents-service:v1 ./incidents-service
	docker build -t notifications-service:v1 ./notifications-service

# --- Cargar las imágenes en el clúster minikube ---
load:
	minikube image load catalog-service:v1
	minikube image load incidents-service:v1
	minikube image load notifications-service:v1

# --- Comparar tamaños: el caso de los multi-stage builds ---
sizes:
	@docker images --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' \
	  | grep -E 'catalog-service|incidents-service|notifications-service'

# --- ConfigMaps con las migraciones (se montan en los Jobs de Flyway) ---
migrations-config:
	kubectl create configmap catalog-migrations -n $(NS) \
	  --from-file=catalog-service/db/migrations/ \
	  --dry-run=client -o yaml | kubectl apply -f -
	kubectl create configmap incidents-migrations -n $(NS) \
	  --from-file=incidents-service/db/migrations/ \
	  --dry-run=client -o yaml | kubectl apply -f -

# --- Desplegar todo, en orden, esperando en cada paso ---
deploy:
	kubectl apply -f k8s/00-namespace.yaml
	kubectl apply -f k8s/01-secret.yaml
	kubectl apply -f k8s/10-catalog-db.yaml -f k8s/11-incidents-db.yaml
	$(MAKE) migrations-config
	kubectl rollout status statefulset/catalog-db -n $(NS) --timeout=120s
	kubectl rollout status statefulset/incidents-db -n $(NS) --timeout=120s
	kubectl apply -f k8s/20-catalog-flyway-job.yaml -f k8s/21-incidents-flyway-job.yaml
	kubectl wait --for=condition=complete job/catalog-flyway job/incidents-flyway -n $(NS) --timeout=120s
	kubectl apply -f k8s/30-notifications.yaml -f k8s/31-catalog.yaml -f k8s/32-incidents.yaml
	kubectl rollout status deployment/notifications-service -n $(NS) --timeout=120s
	kubectl rollout status deployment/catalog-service -n $(NS) --timeout=120s
	kubectl rollout status deployment/incidents-service -n $(NS) --timeout=120s

# --- Datos de ejemplo (servicio + on-call) vía el catalog-service ---
seed:
	./seed.sh

open:
	minikube service incidents-service -n $(NS)

status:
	kubectl get pods,svc,statefulset,job -n $(NS)

logs:
	kubectl logs -n $(NS) -l app=incidents-service --tail=50

# --- Dashboard web de Kubernetes (vista visual del clúster) ---
# Habilita los addons dashboard + metrics-server, levanta un proxy y abre el
# navegador. Quedará en primer plano: corta con Ctrl-C cuando termines.
dashboard:
	minikube dashboard

# --- Operar el clúster (parte 3 del lab) ---
scale:
	kubectl scale deployment/catalog-service -n $(NS) --replicas=4
	kubectl get pods -n $(NS) -l app=catalog-service

rollout:
	kubectl set image deployment/notifications-service -n $(NS) \
	  notifications=notifications-service:v2
	kubectl rollout status deployment/notifications-service -n $(NS)

undo:
	kubectl rollout undo deployment/notifications-service -n $(NS)
	kubectl rollout status deployment/notifications-service -n $(NS)

undeploy:
	kubectl delete namespace $(NS)

stop:
	minikube stop
