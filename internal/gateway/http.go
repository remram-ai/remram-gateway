package gateway

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

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
	mux.HandleFunc("/service/restart", s.handleServiceRestart)
	mux.HandleFunc("/service/passthrough", s.handleServicePassthrough)
	mux.HandleFunc("/logs", s.handleGatewayLogs)
	mux.HandleFunc("/update", s.handleGatewayUpdate)
	mux.HandleFunc("/runtime/reload", s.handleRuntimeReload)
	mux.HandleFunc("/runtime/checkpoint", s.handleRuntimeCheckpoint)
	mux.HandleFunc("/runtime/skill/deploy", s.handleRuntimeSkillDeploy)
	mux.HandleFunc("/runtime/skill/rollback", s.handleRuntimeSkillRollback)
	mux.HandleFunc("/runtime/openclaw", s.handleRuntimeOpenClaw)
	mux.HandleFunc("/token/create", s.handleTokenCreate)
	mux.HandleFunc("/token/list", s.handleTokenList)
	mux.HandleFunc("/token/delete", s.handleTokenDelete)
	mux.HandleFunc("/token/rotate", s.handleTokenRotate)
	mux.HandleFunc("/mcp", s.handleMCP)
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

	result, err := s.orchestrator.ServiceStatus(ctx, route, service)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			route,
			"service_status_failed",
			fmt.Sprintf("failed to inspect service '%s'", service),
			err.Error(),
		))
		return
	}

	missing := true
	for _, container := range result.Containers {
		if container.Present {
			missing = false
			break
		}
	}
	if missing {
		s.writeJSON(writer, http.StatusNotFound, cli.Error(
			route,
			"service_not_found",
			fmt.Sprintf("service '%s' was not found", service),
			"verify the service is deployed through the gateway",
		))
		return
	}

	s.writeJSON(writer, http.StatusOK, result)
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

	ctx, cancel := context.WithTimeout(request.Context(), 2*time.Minute)
	defer cancel()

	result, err := s.orchestrator.DeployService(ctx, route, route.Subject)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			route,
			"service_deploy_failed",
			fmt.Sprintf("failed to deploy service '%s'", route.Subject),
			err.Error(),
		))
		return
	}

	s.writeJSON(writer, http.StatusOK, result)
}

func (s *Server) handleServiceRestart(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /service/restart"))
		return
	}

	route, ok := s.parseServiceRouteRequest(writer, request, "restart")
	if !ok {
		return
	}

	ctx, cancel := context.WithTimeout(request.Context(), 45*time.Second)
	defer cancel()

	result, err := s.orchestrator.RestartService(ctx, route, route.Subject)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			route,
			"service_restart_failed",
			fmt.Sprintf("failed to restart service '%s'", route.Subject),
			err.Error(),
		))
		return
	}

	s.writeJSON(writer, http.StatusOK, result)
}

func (s *Server) handleServicePassthrough(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /service/passthrough"))
		return
	}

	payload, ok := s.parseRouteRequest(writer, request, "send JSON with the parsed service route")
	if !ok {
		return
	}
	if payload.Route == nil || payload.Route.Kind != cli.KindServiceNative {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(payload.Route, "parse_error", "missing service passthrough route", "use a documented service passthrough command"))
		return
	}

	ctx, cancel := context.WithTimeout(request.Context(), 2*time.Minute)
	defer cancel()

	result, err := s.orchestrator.ServicePassthrough(ctx, payload.Route)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			payload.Route,
			"service_passthrough_failed",
			fmt.Sprintf("failed to execute %s passthrough", payload.Route.Resource),
			err.Error(),
		))
		return
	}
	if !result.OK {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			payload.Route,
			"service_passthrough_failed",
			fmt.Sprintf("%s passthrough command failed", payload.Route.Resource),
			result.Stdout,
		))
		return
	}

	s.writeJSON(writer, http.StatusOK, result)
}

func (s *Server) handleGatewayLogs(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use GET /logs"))
		return
	}

	route := &cli.Route{Resource: "gateway", Kind: cli.KindGateway, Action: "logs"}
	ctx, cancel := context.WithTimeout(request.Context(), 30*time.Second)
	defer cancel()

	result, err := s.orchestrator.GatewayLogs(ctx, route)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			route,
			"gateway_logs_failed",
			"failed to read gateway logs",
			err.Error(),
		))
		return
	}
	if !result.OK {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			route,
			"gateway_logs_failed",
			"failed to read gateway logs",
			result.Stdout,
		))
		return
	}
	s.writeJSON(writer, http.StatusOK, result)
}

func (s *Server) handleGatewayUpdate(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /update"))
		return
	}

	route := &cli.Route{Resource: "gateway", Kind: cli.KindGateway, Action: "update", Subject: "gateway"}
	ctx, cancel := context.WithTimeout(request.Context(), 30*time.Second)
	defer cancel()

	result, err := s.orchestrator.GatewayUpdate(ctx, route)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			route,
			"gateway_update_failed",
			"failed to deploy gateway service",
			err.Error(),
		))
		return
	}
	s.writeJSON(writer, http.StatusOK, result)
}

func (s *Server) handleTokenCreate(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /token/create"))
		return
	}
	payload, ok := s.parseRouteRequest(writer, request, "send JSON with the named token route")
	if !ok {
		return
	}
	route := &cli.Route{Resource: "gateway", Kind: cli.KindGatewayToken, Action: "create"}
	if payload.Route != nil {
		route = payload.Route
	}
	result, err := s.tokenManager.Create(route)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(route, "token_create_failed", "failed to create MCP token", err.Error()))
		return
	}
	s.writeJSON(writer, http.StatusOK, result)
}

func (s *Server) handleTokenList(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use GET /token/list"))
		return
	}
	route := &cli.Route{Resource: "gateway", Kind: cli.KindGatewayToken, Action: "list"}
	result, err := s.tokenManager.List(route)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(route, "token_list_failed", "failed to list MCP tokens", err.Error()))
		return
	}
	s.writeJSON(writer, http.StatusOK, result)
}

func (s *Server) handleTokenDelete(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /token/delete"))
		return
	}
	payload, ok := s.parseRouteRequest(writer, request, "send JSON with the named token route")
	if !ok {
		return
	}
	route := &cli.Route{Resource: "gateway", Kind: cli.KindGatewayToken, Action: "delete"}
	if payload.Route != nil {
		route = payload.Route
	}
	result, err := s.tokenManager.Delete(route)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(route, "token_delete_failed", "failed to delete MCP token", err.Error()))
		return
	}
	s.writeJSON(writer, http.StatusOK, result)
}

func (s *Server) handleTokenRotate(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /token/rotate"))
		return
	}
	payload, ok := s.parseRouteRequest(writer, request, "send JSON with the named token route")
	if !ok {
		return
	}
	route := &cli.Route{Resource: "gateway", Kind: cli.KindGatewayToken, Action: "rotate"}
	if payload.Route != nil {
		route = payload.Route
	}
	result, err := s.tokenManager.Rotate(route)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(route, "token_rotate_failed", "failed to rotate MCP token", err.Error()))
		return
	}
	s.writeJSON(writer, http.StatusOK, result)
}

func (s *Server) handleMCP(writer http.ResponseWriter, request *http.Request) {
	result, err := s.tokenManager.ValidateBearerToken(request.Header.Get("Authorization"))
	if err != nil {
		s.logMCPAuth(request, "", false, "validation_error")
		s.writeJSON(writer, http.StatusUnauthorized, cli.Error(nil, "unauthorized", "failed to validate MCP token", err.Error()))
		return
	}
	if !result.Authorized {
		if s.mcpAuthLimiter.RecordFailure(request.RemoteAddr) {
			s.logMCPAuth(request, "", false, "rate_limited")
			s.writeJSON(writer, http.StatusTooManyRequests, cli.Error(nil, "rate_limited", "too many failed MCP authentication attempts", "wait and retry with a valid bearer token"))
			return
		}
		s.logMCPAuth(request, "", false, "invalid_token")
		s.writeJSON(writer, http.StatusUnauthorized, cli.Error(nil, "unauthorized", "missing or invalid MCP token", "send Authorization: Bearer <token>"))
		return
	}
	s.mcpAuthLimiter.RecordSuccess(request.RemoteAddr)
	s.logMCPAuth(request, result.Name, true, "authorized")
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /mcp"))
		return
	}

	body, err := io.ReadAll(request.Body)
	if err != nil {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(nil, "parse_error", "failed to read MCP request body", err.Error()))
		return
	}
	response, ok, err := s.mcpServer.HandleMessage(body)
	if err != nil {
		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(writer).Encode(map[string]any{
			"jsonrpc": "2.0",
			"error": map[string]any{
				"code":    -32700,
				"message": "parse error",
			},
		})
		return
	}
	if !ok {
		writer.WriteHeader(http.StatusNoContent)
		return
	}
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(writer).Encode(response)
}

func (s *Server) logMCPAuth(request *http.Request, tokenName string, authorized bool, reason string) {
	if s.logger == nil {
		return
	}
	attrs := []any{
		"token_name", tokenName,
		"success", authorized,
		"remote_address", authRemoteKey(request.RemoteAddr),
		"reason", reason,
	}
	if authorized {
		s.logger.Info("mcp auth", attrs...)
		return
	}
	s.logger.Warn("mcp auth", attrs...)
}

func (s *Server) handleRuntimeReload(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /runtime/reload"))
		return
	}

	payload, ok := s.parseRouteRequest(writer, request, "send JSON with the parsed runtime route")
	if !ok {
		return
	}
	if payload.Route == nil || payload.Route.Kind != cli.KindRuntimeAction || payload.Route.Action != "reload" {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(payload.Route, "parse_error", "missing runtime reload route", "use: dev|test|prod reload"))
		return
	}

	ctx, cancel := context.WithTimeout(request.Context(), 2*time.Minute)
	defer cancel()

	result, err := s.orchestrator.RuntimeReload(ctx, payload.Route)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			payload.Route,
			"runtime_reload_failed",
			fmt.Sprintf("failed to reload runtime '%s'", payload.Route.Runtime),
			err.Error(),
		))
		return
	}
	s.writeJSON(writer, http.StatusOK, result)
}

func (s *Server) handleRuntimeCheckpoint(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /runtime/checkpoint"))
		return
	}

	payload, ok := s.parseRouteRequest(writer, request, "send JSON with the parsed runtime route")
	if !ok {
		return
	}
	if payload.Route == nil {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(nil, "parse_error", "missing route in checkpoint request", "send JSON with the parsed route"))
		return
	}

	ctx, cancel := context.WithTimeout(request.Context(), 10*time.Minute)
	defer cancel()

	result, err := s.orchestrator.RuntimeCheckpoint(ctx, payload.Route)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			payload.Route,
			"runtime_checkpoint_failed",
			fmt.Sprintf("failed to checkpoint runtime '%s'", payload.Route.Runtime),
			err.Error(),
		))
		return
	}
	s.writeJSON(writer, http.StatusOK, result)
}

func (s *Server) handleRuntimeSkillDeploy(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /runtime/skill/deploy"))
		return
	}

	payload, ok := s.parseRouteRequest(writer, request, "send JSON with the parsed runtime skill route")
	if !ok {
		return
	}
	if payload.Route == nil || payload.Route.Kind != cli.KindRuntimeSkill || payload.Route.Action != "deploy" {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(payload.Route, "parse_error", "missing runtime skill deploy route", "use: dev|test|prod skill deploy <skill>"))
		return
	}

	ctx, cancel := context.WithTimeout(request.Context(), 2*time.Minute)
	defer cancel()

	result, err := s.orchestrator.RuntimeSkillDeploy(ctx, payload.Route)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			payload.Route,
			"runtime_skill_deploy_failed",
			fmt.Sprintf("failed to deploy skill '%s' into runtime '%s'", payload.Route.Subject, payload.Route.Runtime),
			err.Error(),
		))
		return
	}
	s.writeJSON(writer, http.StatusOK, result)
}

func (s *Server) handleRuntimeSkillRollback(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /runtime/skill/rollback"))
		return
	}

	payload, ok := s.parseRouteRequest(writer, request, "send JSON with the parsed runtime skill route")
	if !ok {
		return
	}
	if payload.Route == nil || payload.Route.Kind != cli.KindRuntimeSkill || payload.Route.Action != "rollback" {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(payload.Route, "parse_error", "missing runtime skill rollback route", "use: dev|test|prod skill rollback <skill>"))
		return
	}

	ctx, cancel := context.WithTimeout(request.Context(), 2*time.Minute)
	defer cancel()

	result, err := s.orchestrator.RuntimeSkillRollback(ctx, payload.Route)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			payload.Route,
			"runtime_skill_rollback_failed",
			fmt.Sprintf("failed to rollback skill '%s' in runtime '%s'", payload.Route.Subject, payload.Route.Runtime),
			err.Error(),
		))
		return
	}
	s.writeJSON(writer, http.StatusOK, result)
}

func (s *Server) handleRuntimeOpenClaw(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /runtime/openclaw"))
		return
	}

	payload, ok := s.parseRouteRequest(writer, request, "send JSON with the parsed runtime route")
	if !ok {
		return
	}
	if payload.Route == nil || payload.Route.Kind != cli.KindRuntimeNative {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(payload.Route, "parse_error", "missing runtime openclaw route", "use: dev|test|prod openclaw <command>"))
		return
	}

	ctx, cancel := context.WithTimeout(request.Context(), 2*time.Minute)
	defer cancel()

	result, err := s.orchestrator.RuntimeOpenClaw(ctx, payload.Route)
	if err != nil {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			payload.Route,
			"runtime_openclaw_failed",
			fmt.Sprintf("failed to execute OpenClaw command in '%s'", payload.Route.Runtime),
			err.Error(),
		))
		return
	}
	if !result.OK {
		s.writeJSON(writer, http.StatusBadGateway, cli.Error(
			payload.Route,
			"runtime_openclaw_failed",
			fmt.Sprintf("OpenClaw command failed in '%s'", payload.Route.Runtime),
			result.Stdout,
		))
		return
	}
	s.writeJSON(writer, http.StatusOK, result)
}

func (s *Server) handleExecute(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		s.writeJSON(writer, http.StatusMethodNotAllowed, cli.Error(nil, "parse_error", "method not allowed", "use POST /execute"))
		return
	}

	payload, ok := s.parseRouteRequest(writer, request, "send JSON with the parsed route")
	if !ok {
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
	case "dev", "test", "prod", "service":
		if payload.Route.Kind == cli.KindScopedSecrets {
			s.logScopedSecretsRequest(request, payload.Route)
			response = s.secretHandler.Execute(payload.Route, payload.SecretValue)
			break
		}
		if payload.Route.Resource == "service" {
			response = cli.Error(payload.Route, "parse_error", "unsupported route", "use a documented command")
			break
		}
		response = runtime.Payload(payload.Route)
	case "ollama", "opensearch", "caddy":
		response = services.Payload(payload.Route)
	default:
		response = cli.Error(payload.Route, "parse_error", "unsupported route", "use a documented command")
	}

	s.writeJSON(writer, http.StatusOK, response)
}

func (s *Server) logScopedSecretsRequest(request *http.Request, route *cli.Route) {
	if s.logger == nil || route == nil {
		return
	}
	s.logger.Info(
		"scoped secrets request",
		"scope", route.Resource,
		"action", route.Action,
		"name", route.Subject,
		"remote_address", authRemoteKey(request.RemoteAddr),
	)
}

func (s *Server) writeJSON(writer http.ResponseWriter, status int, payload any) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = cli.WriteJSON(writer, payload)
}

func (s *Server) parseRouteRequest(writer http.ResponseWriter, request *http.Request, recovery string) (cli.RouteRequest, bool) {
	var payload cli.RouteRequest
	if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(nil, "parse_error", "invalid JSON request body", recovery))
		return cli.RouteRequest{}, false
	}
	return payload, true
}

func (s *Server) parseServiceRouteRequest(writer http.ResponseWriter, request *http.Request, action string) (*cli.Route, bool) {
	payload, ok := s.parseRouteRequest(writer, request, "send JSON with the target service")
	if !ok {
		return nil, false
	}

	service := strings.TrimSpace(payload.Service)
	route := &cli.Route{Resource: "gateway", Kind: cli.KindGatewayService, Action: action, Subject: service}
	if payload.Route != nil {
		route = payload.Route
	}
	if strings.TrimSpace(route.Subject) == "" {
		route.Subject = service
	}
	if strings.TrimSpace(route.Subject) == "" {
		s.writeJSON(writer, http.StatusBadRequest, cli.Error(route, "parse_error", "missing service name", fmt.Sprintf("use: gateway service %s <service>", action)))
		return nil, false
	}

	return route, true
}
