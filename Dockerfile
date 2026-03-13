FROM golang:1.23-bookworm AS build

WORKDIR /src

COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
COPY pkg ./pkg

RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/gateway ./cmd/gateway

FROM alpine:3.20

RUN apk add --no-cache ca-certificates docker-cli docker-cli-compose

COPY --from=build /out/gateway /usr/local/bin/gateway

ENV MOLTBOX_CONFIG_PATH=/etc/moltbox/config.yaml

EXPOSE 7460

ENTRYPOINT ["/usr/local/bin/gateway"]
