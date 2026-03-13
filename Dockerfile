FROM golang:1.23-bookworm AS build

WORKDIR /src

COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
COPY pkg ./pkg

RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/gateway ./cmd/gateway

FROM alpine:3.20

ARG DOCKER_COMPOSE_VERSION=v2.40.2

RUN apk add --no-cache ca-certificates curl docker-cli git \
 && mkdir -p /usr/libexec/docker/cli-plugins \
 && curl -fsSL -o /usr/libexec/docker/cli-plugins/docker-compose "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-linux-x86_64" \
 && chmod +x /usr/libexec/docker/cli-plugins/docker-compose \
 && docker compose version

COPY --from=build /out/gateway /usr/local/bin/gateway

ENV MOLTBOX_CONFIG_PATH=/etc/moltbox/config.yaml

EXPOSE 7460

ENTRYPOINT ["/usr/local/bin/gateway"]
