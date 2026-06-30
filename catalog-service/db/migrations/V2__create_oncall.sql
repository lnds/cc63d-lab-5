-- V2__create_oncall.sql — rotaciones on-call. La FK a services vive DENTRO de
-- esta misma base, porque services y oncall son del mismo servicio.
CREATE TABLE oncall (
    id          SERIAL PRIMARY KEY,
    service_id  INTEGER NOT NULL REFERENCES services(id),
    person      TEXT NOT NULL,
    email       TEXT NOT NULL,
    start_date  DATE NOT NULL,
    end_date    DATE NOT NULL
);
