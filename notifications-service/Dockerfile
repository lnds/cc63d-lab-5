# ============================================================================
#  Multi-stage build (Go) — el caso extremo: imagen final desde `scratch`
# ============================================================================
#
#  Etapa 1 (builder): el toolchain de Go (~350MB) compila un binario estático.
#  Etapa 2 (scratch): imagen VACÍA + solo el binario. Resultado: ~10MB.
#
#  CGO_ENABLED=0 produce un binario sin dependencias de libc, así corre en una
#  imagen sin sistema operativo. Compáralo con la imagen Python (~150MB).
# ----------------------------------------------------------------------------

FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY go.mod .
COPY main.go .
RUN CGO_ENABLED=0 GOOS=linux go build -o notifications .

FROM scratch
COPY --from=builder /app/notifications /notifications
EXPOSE 8080
ENTRYPOINT ["/notifications"]
