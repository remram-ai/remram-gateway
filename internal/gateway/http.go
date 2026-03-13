package gateway

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/remram-ai/moltbox-gateway/internal/docker"
	"github.com/remram-ai/moltbox-gateway/internal/runtime"
	"github.com/remram-ai/moltbox-gateway/internal/services"
	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", s.handleHealth)
	mux.HandleFunc("/status", s.handleStatus)
	mux.HandleFunc("/docker/ping", s.handleDockerPing)
	mux.HandleFunc("/docker/run", s.handleDockerRun)
	mux.HandleFunc("/service/status", s.handleServiceStatus)
	mux.HandleFunc("/service/deploy", s.handleServiceDeploy)
	mux.HandleFunc("/execute", s.handleExecute)
	return mux
}

func (s *Server) handleHealth(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use GET /health"))
		return
	}

	s.writeJSON(writer, http.StatusOK, cli.GatewayHealthResult{
		OK:      true,
		Service: "gateway",
		Version: cli.Version,
	})
}

func (s *Server) handleStatus(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use GET /status"))
		return
	}

	s.writeJSON(writer, http.StatusOK, cli.GatewayStatusResult{
		OK:            true,
		Route:         &cli.Route{Resource: "gateway", Kind: cli.KindGateway, Action: "status"},
		Service:       "gateway",
		Version:       cli.Version,
		ListenAddress: s.listenAddress,
		DockerSocket:  s.dockerSocketPath,
	})
}

func (s *Server) handleDockerPing(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use GET /docker/ping"))
		return
	}

	route := &cli.Route{Resource: "gateway", Kind: cli.KindGatewayDocker, Action: "ping", Subject: "docker"}

	ctx, cancel := context.WithTimeout(request.Context(), 5*time.Second)
	defer cancel()

	info, err := s.dockerClient.Version(ctx)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			route,
			"docker_unavailable",
			fmt.Sprintf("failed to contact Docker via %s", s.dockerSocketPath),
			"verify the gateway container has the Docker socket mounted",
		))
		return
	}

	s.writeJSON(writer, http.StatusOK, cli.DockerPingResult{
		OK:            true,
		Route:         route,
		DockerVersion: info.Version,
		APIVersion:    info.APIVersion,
		MinAPIVersion: info.MinAPIVersion,
		GitCommit:     info.GitCommit,
		GoVersion:     info.GoVersion,
		OS:            info.OS,
		Arch:          info.Arch,
		KernelVersion: info.KernelVersion,
	})
}

func (s *Server) handleDockerRun(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /docker/run"))
		return
	}

	var payload cli.DockerRunRequest
	if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(nil, "parse_error", "invalid JSON request body", "send JSON with an image field"))
		return
	}

	image := strings.TrimSpace(payload.Image)
	route := &cli.Route{Resource: "gateway", Kind: cli.KindGatewayDocker, Action: "run", Subject: image}
	if image == "" {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(route, "parse_error", "missing image name", "use: gateway docker run <image>"))
		return
	}

	ctx, cancel := context.WithTimeout(request.Context(), 2*time.Minute)
	defer cancel()

	result, err := s.dockerClient.RunImage(ctx, image)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			route,
			"docker_run_failed",
			fmt.Sprintf("failed to run image '%s'", image),
			err.Error(),
		))
		return
	}

	s.writeJSON(writer, http.StatusOK, cli.DockerRunResult{
		OK:            true,
		Route:         route,
		Image:         image,
		ContainerID:   result.ID,
		ContainerName: result.Name,
	})
}

func (s *Server) handleServiceStatus(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use GET /service/status"))
		return
	}

	service := strings.TrimSpace(request.URL.Query().Get("service"))
	route := &cli.Route{Resource: "gateway", Kind: cli.KindGatewayService, Action: "status", Subject: service}
	if service == "" {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(route, "parse_error", "missing service query parameter", "use GET /service/status?service=<service>"))
		return
	}

	ctx, cancel := context.WithTimeout(request.Context(), 5*time.Second)
	defer cancel()

	info, err := s.dockerClient.InspectContainer(ctx, service)
	if err != nil {
		if errors.Is(err, docker.ErrContainerNotFound) {
			s.writeJSON(writer, http.StatusNotFound, cli.Error(
				route,
				"service_not_found",
				fmt.Sprintf("service container '%s' was not found", service),
				"verify the service is deployed on the host Docker engine",
			))
			return
		}
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			route,
			"docker_unavailable",
			fmt.Sprintf("failed to inspect service '%s'", service),
			"verify the gateway container has Docker access",
		))
		return
	}

	s.writeJSON(writer, http.StatusOK, cli.ServiceStatusResult{
		OK:            true,
		Route:         route,
		Service:       service,
		ContainerName: strings.TrimPrefix(info.Name, "/"),
		Image:         info.Config.Image,
		Status:        info.State.Status,
		Running:       info.State.Running,
	})
}

func (s *Server) handleServiceDeploy(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /service/deploy"))
		return
	}

	var payload cli.RouteRequest
	if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(nil, "parse_error", "invalid JSON request body", "send JSON with the target service"))
		return
	}

	service := strings.TrimSpace(payload.Service)
	route := &cli.Route{Resource: "gateway", Kind: cli.KindGatewayService, Action: "deploy", Subject: service}
	if payload.Route != nil {
		route = payload.Route
	}
	if strings.TrimSpace(route.Subject) == "" {
		route.Subject = service
	}
	if strings.TrimSpace(route.Subject) == "" {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(route, "parse_error", "missing service name", "use: gateway service deploy <service>"))
		return
	}

	s.writeJSON(writer, http.StatusNotImplemented, Payload(route))
}

func (s *Server) handleExecute(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /execute"))
		return
	}

	var payload cli.RouteRequest
	if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(nil, "parse_error", "invalid JSON request body", "send JSON with the parsed route"))
		return
	}

	if payload.Route == nil {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(nil, "parse_error", "missing route in execute request", "send JSON with the parsed route"))
		return
	}

	var response any
	switch payload.Route.Resource {
	case "gateway":
		response = Payload(payload.Route)
	case "dev", "test", "prod":
		response = runtime.Payload(payload.Route)
	case "ollama", "opensearch", "caddy":
		response = services.Payload(payload.Route)
	default:
		response = cli.Error(payload.Route, "parse_error", "unsupported route", "use a documented command")
	}

	s.writeJSON(writer, http.StatusOK, response)
}

func (s *Server) writeJSON(writer http.ResponseWriter, status int, payload any) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = cli.WriteJSON(writer, payload)
}
