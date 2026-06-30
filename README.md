# Lab 5 — Microservicios políglotas en Kubernetes

Clase 6 (CC63D). El monolito de gestión de incidentes se descompone en **tres
microservicios** —dos en Python y uno en Go— cada uno con su **propia base de
datos**, y se despliega en un clúster **Kubernetes local (minikube)**.

Es un **tutorial guiado**: el repo trae todo resuelto y funcionando. La idea es
que lo levantes paso a paso, entiendas cada pieza y operes el clúster.

## Arquitectura

```
                       ┌─────────────────────┐
        crear          │  incidents-service  │   (Python)
     incidente  ─────► │  incidents + timeline + postmortems
                       └──────────┬──────────┘
                                  │  HTTP (REST)
              ┌───────────────────┼────────────────────┐
              ▼                                          ▼
     ┌─────────────────┐                      ┌──────────────────────┐
     │ catalog-service │  (Python)            │ notifications-service │ (Go)
     │ services+oncall │                      │  POST /notify         │
     └────────┬────────┘                      └──────────────────────┘
              │                                          │
     ┌────────▼────────┐    ┌──────────────────┐        (sin base de datos)
     │   catalog-db    │    │   incidents-db   │
     │  (PostgreSQL)   │    │  (PostgreSQL)    │   database-per-service:
     └─────────────────┘    └──────────────────┘   cada base es de un solo dueño
```

Al crear un incidente, `incidents-service`:
1. pregunta a `catalog-service` si el servicio existe (no hay JOIN entre bases),
2. le pregunta quién está de turno (on-call),
3. llama a `notifications-service` (Go) para avisar.

| Servicio | Lenguaje | Base | Expuesto |
|----------|----------|------|----------|
| `catalog-service` | Python/Flask | `catalog-db` | interno (ClusterIP) |
| `incidents-service` | Python/Flask | `incidents-db` | **NodePort** (entrada) |
| `notifications-service` | **Go** | — | interno (ClusterIP) |

## Requisitos

- Docker
- minikube + kubectl
- (para el seed) `curl` y `python3` en tu máquina

## Paso a paso

### 1. Arrancar el clúster

```bash
make start          # minikube start --driver=docker
```

### 2. Construir las imágenes y comparar tamaños

```bash
make build
make sizes
```

Verás el efecto de los **multi-stage builds**: el servicio Go, compilado a un
binario estático sobre `scratch`, pesa **~7 MB**; los Python slim, **~175 MB**.

### 3. Cargar las imágenes en minikube

minikube tiene su propio almacén de imágenes, separado de tu Docker local:

```bash
make load
```

### 4. Desplegar

```bash
make deploy
```

`make deploy` aplica los manifiestos **en orden** y espera en cada paso:

1. `namespace` y `secret` (la contraseña de PostgreSQL).
2. Las dos bases como **StatefulSet** (con volumen persistente, `volumeClaimTemplates`).
3. Los **ConfigMaps** con las migraciones SQL.
4. **Flyway** como **Job** por servicio (aplica las migraciones).
5. Los tres **Deployments** + **Services**.

> Cada app trae un `initContainer` que espera a que su base acepte conexiones,
> así no arranca antes de tiempo. Es la forma de expresar dependencias de
> arranque en Kubernetes (no existe un `depends_on` como en docker compose).

### 5. Datos de ejemplo y probar el flujo

```bash
make seed
```

Crea un servicio y un turno on-call (en `catalog-service`) y luego un incidente
(en `incidents-service`). La respuesta incluye la notificación generada. Míra el
log del servicio Go:

```bash
kubectl logs -n incidentes -l app=notifications-service
# NOTIFY -> Ana Soto <ana@example.com>: [SEV2] Latencia alta en checkout on checkout
```

### 6. Abrir la interfaz web

```bash
make open           # minikube service incidents-service -n incidentes
```

`incidents-service` es la puerta de entrada (NodePort) y, además de su API, sirve
una **interfaz web** para ver y crear incidentes y escribir post-mortems. El
navegador solo alcanza este servicio: el catálogo y el de notificaciones son
internos (ClusterIP). Por eso el selector de servicios de la interfaz va contra
`GET /catalog/services`, una ruta que `incidents-service` **reenvía** al catálogo
dentro del clúster (un proxy simple, el patrón de un *gateway*).

## Operar el clúster

```bash
make status         # pods, services, statefulsets, jobs

make scale          # catalog-service a 4 réplicas
                    # mata un pod a mano y mira cómo K8s lo recrea:
kubectl delete pod -n incidentes <pod>

make rollout        # rolling update de notifications-service (v1 -> v2)
make undo           # rollback a la versión anterior
```

## Database-per-service

Cada servicio es **dueño exclusivo** de su base. No hay claves foráneas entre
bases ni consultas cruzadas: `incidents-service` guarda `service_id` y una copia
**desnormalizada** de `service_name`, y le pregunta al catálogo por HTTP cuando
necesita más. Compruébalo:

```bash
kubectl exec -n incidentes catalog-db-0   -- psql -U postgres -d catalog   -c '\dt'
kubectl exec -n incidentes incidents-db-0 -- psql -U postgres -d incidents -c '\dt'
```

## Limpieza

```bash
make undeploy       # borra el namespace (y con él todo lo desplegado)
make stop           # detiene minikube
```

## Estructura

```
catalog-service/        Python/Flask — services + oncall
  app.py  Dockerfile  requirements.txt  db/migrations/
incidents-service/      Python/Flask — incidents + timeline + postmortems
  app.py  Dockerfile  requirements.txt  db/migrations/
notifications-service/  Go — POST /notify, GET /health (imagen scratch)
  main.go  go.mod  Dockerfile
k8s/                    manifiestos (namespace, secret, StatefulSets, Jobs, Deployments, Services)
Makefile  seed.sh
```
