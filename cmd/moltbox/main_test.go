package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

func TestCLIForwardsToGateway(t *testing.T) {
	testCases := []struct {
		name       string
		args       []string
		wantMethod string
		wantPath   string
		wantCode   int
		handler    func(t *testing.T, writer http.ResponseWriter, request *http.Request)
	}{
		{
			name:       "gateway status",
			args:       []string{"gateway", "status"},
			wantMethod: http.MethodGet,
			wantPath:   "/status",
			wantCode:   cli.ExitOK,
			handler: func(t *testing.T, writer http.ResponseWriter, request *http.Request) {
				t.Helper()
				_ = json.NewEncoder(writer).Encode(cli.GatewayStatusResult{
					OK:            true,
					Route:         &cli.Route{Resource: "gateway", Kind: cli.KindGateway, Action: "status"},
					Service:       "gateway",
					Version:       cli.Version,
					ListenAddress: ":7460",
					DockerSocket:  cli.DefaultDockerSocket,
				})
			},
		},
		{
			name:       "gateway docker ping",
			args:       []string{"gateway", "docker", "ping"},
			wantMethod: http.MethodGet,
			wantPath:   "/docker/ping",
			wantCode:   cli.ExitOK,
			handler: func(t *testing.T, writer http.ResponseWriter, request *http.Request) {
				t.Helper()
				_ = json.NewEncoder(writer).Encode(cli.DockerPingResult{
					OK:            true,
					Route:         &cli.Route{Resource: "gateway", Kind: cli.KindGatewayDocker, Action: "ping", Subject: "docker"},
					DockerVersion: "29.3.0",
				})
			},
		},
		{
			name:       "gateway docker run",
			args:       []string{"gateway", "docker", "run", "hello-world"},
			wantMethod: http.MethodPost,
			wantPath:   "/docker/run",
			wantCode:   cli.ExitOK,
			handler: func(t *testing.T, writer http.ResponseWriter, request *http.Request) {
				t.Helper()
				var payload cli.DockerRunRequest
				if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
					t.Fatalf("decode request: %v", err)
				}
				if payload.Image != "hello-world" {
					t.Fatalf("payload.image = %q, want hello-world", payload.Image)
				}
				_ = json.NewEncoder(writer).Encode(cli.DockerRunResult{
					OK:            true,
					Route:         &cli.Route{Resource: "gateway", Kind: cli.KindGatewayDocker, Action: "run", Subject: "hello-world"},
					Image:         "hello-world",
					ContainerID:   "abc123",
					ContainerName: "hello-world",
				})
			},
		},
		{
			name:       "gateway service deploy",
			args:       []string{"gateway", "service", "deploy", "opensearch"},
			wantMethod: http.MethodPost,
			wantPath:   "/service/deploy",
			wantCode:   cli.ExitNotImplemented,
			handler: func(t *testing.T, writer http.ResponseWriter, request *http.Request) {
				t.Helper()
				var payload cli.RouteRequest
				if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
					t.Fatalf("decode request: %v", err)
				}
				if payload.Service != "opensearch" {
					t.Fatalf("payload.service = %q, want opensearch", payload.Service)
				}
				_ = json.NewEncoder(writer).Encode(cli.NotImplemented(
					&cli.Route{Resource: "gateway", Kind: cli.KindGatewayService, Action: "deploy", Subject: "opensearch"},
					"gateway service deploy opensearch is not implemented in phase 1",
					"phase 1 only boots the direct localhost control channel",
				))
			},
		},
		{
			name:       "gateway service restart",
			args:       []string{"gateway", "service", "restart", "opensearch"},
			wantMethod: http.MethodPost,
			wantPath:   "/service/restart",
			wantCode:   cli.ExitOK,
			handler: func(t *testing.T, writer http.ResponseWriter, request *http.Request) {
				t.Helper()
				var payload cli.RouteRequest
				if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
					t.Fatalf("decode request: %v", err)
				}
				if payload.Service != "opensearch" {
					t.Fatalf("payload.service = %q, want opensearch", payload.Service)
				}
				_ = json.NewEncoder(writer).Encode(cli.ServiceActionResult{
					OK:      true,
					Route:   &cli.Route{Resource: "gateway", Kind: cli.KindGatewayService, Action: "restart", Subject: "opensearch"},
					Service: "opensearch",
					Action:  "restart",
				})
			},
		},
		{
			name:       "gateway logs",
			args:       []string{"gateway", "logs"},
			wantMethod: http.MethodGet,
			wantPath:   "/logs",
			wantCode:   cli.ExitOK,
			handler: func(t *testing.T, writer http.ResponseWriter, request *http.Request) {
				t.Helper()
				_ = json.NewEncoder(writer).Encode(cli.CommandResult{
					OK:            true,
					Route:         &cli.Route{Resource: "gateway", Kind: cli.KindGateway, Action: "logs"},
					ContainerName: "gateway",
					ExitCode:      0,
					Stdout:        "gateway log line",
				})
			},
		},
		{
			name:       "gateway update",
			args:       []string{"gateway", "update"},
			wantMethod: http.MethodPost,
			wantPath:   "/update",
			wantCode:   cli.ExitOK,
			handler: func(t *testing.T, writer http.ResponseWriter, request *http.Request) {
				t.Helper()
				_ = json.NewEncoder(writer).Encode(cli.ServiceDeployResult{
					OK:      true,
					Route:   &cli.Route{Resource: "gateway", Kind: cli.KindGateway, Action: "update", Subject: "gateway"},
					Service: "gateway",
				})
			},
		},
		{
			name:       "runtime action",
			args:       []string{"dev", "reload"},
			wantMethod: http.MethodPost,
			wantPath:   "/runtime/reload",
			wantCode:   cli.ExitOK,
			handler: func(t *testing.T, writer http.ResponseWriter, request *http.Request) {
				t.Helper()
				var payload cli.RouteRequest
				if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
					t.Fatalf("decode request: %v", err)
				}
				if payload.Route == nil || payload.Route.Environment != "dev" {
					t.Fatalf("payload.route = %#v, want dev runtime route", payload.Route)
				}
				_ = json.NewEncoder(writer).Encode(cli.ServiceActionResult{
					OK:      true,
					Route:   payload.Route,
					Service: "openclaw-dev",
					Action:  "reload",
				})
			},
		},
		{
			name:       "runtime checkpoint",
			args:       []string{"dev", "checkpoint"},
			wantMethod: http.MethodPost,
			wantPath:   "/runtime/checkpoint",
			wantCode:   cli.ExitNotImplemented,
			handler: func(t *testing.T, writer http.ResponseWriter, request *http.Request) {
				t.Helper()
				var payload cli.RouteRequest
				if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
					t.Fatalf("decode request: %v", err)
				}
				_ = json.NewEncoder(writer).Encode(cli.NotImplemented(
					payload.Route,
					"dev checkpoint is not implemented yet",
					"checkpoint orchestration lands after runtime deployment",
				))
			},
		},
		{
			name:       "runtime openclaw passthrough",
			args:       []string{"dev", "openclaw", "plugins", "list"},
			wantMethod: http.MethodPost,
			wantPath:   "/runtime/openclaw",
			wantCode:   cli.ExitOK,
			handler: func(t *testing.T, writer http.ResponseWriter, request *http.Request) {
				t.Helper()
				var payload cli.RouteRequest
				if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
					t.Fatalf("decode request: %v", err)
				}
				_ = json.NewEncoder(writer).Encode(cli.CommandResult{
					OK:            true,
					Route:         payload.Route,
					ContainerName: "openclaw-dev",
					ExitCode:      0,
					Stdout:        "plugin-a\nplugin-b\n",
				})
			},
		},
		{
			name:       "service passthrough",
			args:       []string{"ollama", "list"},
			wantMethod: http.MethodPost,
			wantPath:   "/service/passthrough",
			wantCode:   cli.ExitOK,
			handler: func(t *testing.T, writer http.ResponseWriter, request *http.Request) {
				t.Helper()
				var payload cli.RouteRequest
				if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
					t.Fatalf("decode request: %v", err)
				}
				_ = json.NewEncoder(writer).Encode(cli.CommandResult{
					OK:            true,
					Route:         payload.Route,
					ContainerName: "ollama",
					ExitCode:      0,
					Stdout:        "qwen3:8b\n",
				})
			},
		},
	}

	for _, testCase := range testCases {
		testCase := testCase
		t.Run(testCase.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
				if request.Method != testCase.wantMethod {
					t.Fatalf("method = %s, want %s", request.Method, testCase.wantMethod)
				}
				if request.URL.Path != testCase.wantPath {
					t.Fatalf("path = %s, want %s", request.URL.Path, testCase.wantPath)
				}
				testCase.handler(t, writer, request)
			}))
			defer server.Close()

			t.Setenv("MOLTBOX_GATEWAY_URL", server.URL)

			var output strings.Builder
			code := run(testCase.args, &output, ioDiscard{})
			if code != testCase.wantCode {
				t.Fatalf("exit code = %d, want %d", code, testCase.wantCode)
			}

			if output.Len() == 0 {
				t.Fatal("expected gateway response output")
			}
		})
	}
}

func TestRetiredNamespacesFailExplicitly(t *testing.T) {
	t.Parallel()

	retired := []string{
		"runtime",
		"service",
		"skill",
		"tools",
		"host",
		"openclaw-dev",
		"openclaw-test",
		"openclaw-prod",
	}

	for _, value := range retired {
		value := value
		t.Run(value, func(t *testing.T) {
			t.Parallel()

			var output strings.Builder
			code := run([]string{value}, &output, ioDiscard{})
			if code != cli.ExitParseError {
				t.Fatalf("exit code = %d, want %d", code, cli.ExitParseError)
			}

			var payload cli.Envelope
			if err := json.Unmarshal([]byte(output.String()), &payload); err != nil {
				t.Fatalf("decode payload: %v", err)
			}
			if payload.ErrorType != "retired_namespace" {
				t.Fatalf("error_type = %q, want retired_namespace", payload.ErrorType)
			}
		})
	}
}

func TestUnknownResourceFails(t *testing.T) {
	t.Parallel()

	var output strings.Builder
	code := run([]string{"unknown"}, &output, ioDiscard{})
	if code != cli.ExitParseError {
		t.Fatalf("exit code = %d, want %d", code, cli.ExitParseError)
	}

	var payload cli.Envelope
	if err := json.Unmarshal([]byte(output.String()), &payload); err != nil {
		t.Fatalf("decode payload: %v", err)
	}
	if payload.ErrorType != "parse_error" {
		t.Fatalf("error_type = %q, want parse_error", payload.ErrorType)
	}
}

func TestGatewayUnavailable(t *testing.T) {
	t.Setenv("MOLTBOX_GATEWAY_URL", "http://127.0.0.1:1")

	var output strings.Builder
	code := run([]string{"gateway", "status"}, &output, ioDiscard{})
	if code != cli.ExitFailure {
		t.Fatalf("exit code = %d, want %d", code, cli.ExitFailure)
	}

	var payload cli.Envelope
	if err := json.Unmarshal([]byte(output.String()), &payload); err != nil {
		t.Fatalf("decode payload: %v", err)
	}
	if payload.ErrorType != "gateway_unreachable" {
		t.Fatalf("error_type = %q, want gateway_unreachable", payload.ErrorType)
	}
}

func TestHelpAndVersion(t *testing.T) {
	t.Parallel()

	var helpOutput strings.Builder
	if code := run([]string{"--help"}, &helpOutput, ioDiscard{}); code != cli.ExitOK {
		t.Fatalf("help exit code = %d, want %d", code, cli.ExitOK)
	}
	if !strings.Contains(helpOutput.String(), "moltbox <resource> <command>") {
		t.Fatalf("help output missing grammar: %q", helpOutput.String())
	}

	var versionOutput strings.Builder
	if code := run([]string{"--version"}, &versionOutput, ioDiscard{}); code != cli.ExitOK {
		t.Fatalf("version exit code = %d, want %d", code, cli.ExitOK)
	}
	if !strings.Contains(versionOutput.String(), cli.Version) {
		t.Fatalf("version output missing version: %q", versionOutput.String())
	}
}

type ioDiscard struct{}

func (ioDiscard) Write(p []byte) (int, error) {
	return len(p), nil
}
