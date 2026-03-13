package cli

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
)

const (
	Version                  = "0.1.0-dev"
	DefaultDockerSocket      = "/var/run/docker.sock"
	DefaultGatewayURL        = "http://127.0.0.1:7460"
	DefaultGatewayListenAddr = ":7460"

	ExitOK             = 0
	ExitFailure        = 1
	ExitParseError     = 2
	ExitNotImplemented = 3
)

const (
	KindGateway        = "gateway"
	KindGatewayService = "gateway_service"
	KindGatewayDocker  = "gateway_docker"
	KindRuntimeAction  = "runtime_action"
	KindRuntimeNative  = "runtime_openclaw"
	KindServiceNative  = "service_passthrough"
)

var retiredNamespaces = map[string]struct{}{
	"runtime":       {},
	"service":       {},
	"skill":         {},
	"tools":         {},
	"host":          {},
	"openclaw-dev":  {},
	"openclaw-test": {},
	"openclaw-prod": {},
}

var runtimeMappings = map[string]string{
	"dev":  "openclaw-dev",
	"test": "openclaw-test",
	"prod": "openclaw-prod",
}

type Route struct {
	Resource    string   `json:"resource"`
	Kind        string   `json:"kind"`
	Tokens      []string `json:"tokens,omitempty"`
	Action      string   `json:"action,omitempty"`
	Subject     string   `json:"subject,omitempty"`
	Environment string   `json:"environment,omitempty"`
	Runtime     string   `json:"runtime,omitempty"`
	NativeArgs  []string `json:"native_args,omitempty"`
}

type Envelope struct {
	OK              bool   `json:"ok"`
	Route           *Route `json:"route,omitempty"`
	ErrorType       string `json:"error_type,omitempty"`
	ErrorMessage    string `json:"error_message,omitempty"`
	RecoveryMessage string `json:"recovery_message,omitempty"`
}

type RouteRequest struct {
	Route   *Route `json:"route,omitempty"`
	Service string `json:"service,omitempty"`
}

type DockerRunRequest struct {
	Image string `json:"image"`
}

type GatewayHealthResult struct {
	OK      bool   `json:"ok"`
	Service string `json:"service"`
	Version string `json:"version"`
}

type GatewayStatusResult struct {
	OK            bool   `json:"ok"`
	Route         *Route `json:"route"`
	Service       string `json:"service"`
	Version       string `json:"version"`
	ListenAddress string `json:"listen_address"`
	DockerSocket  string `json:"docker_socket"`
}

type DockerPingResult struct {
	OK            bool   `json:"ok"`
	Route         *Route `json:"route"`
	DockerVersion string `json:"docker_version"`
	APIVersion    string `json:"api_version,omitempty"`
	MinAPIVersion string `json:"min_api_version,omitempty"`
	GitCommit     string `json:"git_commit,omitempty"`
	GoVersion     string `json:"go_version,omitempty"`
	OS            string `json:"os,omitempty"`
	Arch          string `json:"arch,omitempty"`
	KernelVersion string `json:"kernel_version,omitempty"`
}

type DockerRunResult struct {
	OK            bool   `json:"ok"`
	Route         *Route `json:"route"`
	Image         string `json:"image"`
	ContainerID   string `json:"container_id"`
	ContainerName string `json:"container_name"`
}

type ServiceStatusResult struct {
	OK             bool                     `json:"ok"`
	Route          *Route                   `json:"route"`
	Service        string                   `json:"service"`
	ComposeProject string                   `json:"compose_project,omitempty"`
	ContainerName  string                   `json:"container_name,omitempty"`
	Image          string                   `json:"image,omitempty"`
	Status         string                   `json:"status,omitempty"`
	Running        bool                     `json:"running"`
	Containers     []ServiceContainerStatus `json:"containers,omitempty"`
}

type ServiceContainerStatus struct {
	Name          string `json:"name"`
	Present       bool   `json:"present"`
	ContainerName string `json:"container_name,omitempty"`
	Image         string `json:"image,omitempty"`
	Status        string `json:"status,omitempty"`
	Running       bool   `json:"running"`
	Health        string `json:"health,omitempty"`
}

type ServiceDeployResult struct {
	OK             bool                     `json:"ok"`
	Route          *Route                   `json:"route"`
	Service        string                   `json:"service"`
	ComposeProject string                   `json:"compose_project,omitempty"`
	OutputDir      string                   `json:"output_dir,omitempty"`
	Command        []string                 `json:"command,omitempty"`
	Containers     []ServiceContainerStatus `json:"containers,omitempty"`
}

type ServiceActionResult struct {
	OK         bool                     `json:"ok"`
	Route      *Route                   `json:"route"`
	Service    string                   `json:"service"`
	Action     string                   `json:"action"`
	Command    []string                 `json:"command,omitempty"`
	Containers []ServiceContainerStatus `json:"containers,omitempty"`
}

type CommandResult struct {
	OK            bool     `json:"ok"`
	Route         *Route   `json:"route"`
	ContainerName string   `json:"container_name,omitempty"`
	Command       []string `json:"command,omitempty"`
	Stdout        string   `json:"stdout,omitempty"`
	Stderr        string   `json:"stderr,omitempty"`
	ExitCode      int      `json:"exit_code"`
}

type ParseResult struct {
	Route    *Route
	Envelope *Envelope
	Code     int
	Help     bool
	Version  bool
}

func Parse(args []string) ParseResult {
	if len(args) == 0 {
		return ParseResult{Help: true, Code: ExitOK}
	}

	if len(args) == 1 && isHelpFlag(args[0]) {
		return ParseResult{Help: true, Code: ExitOK}
	}

	if len(args) == 1 && args[0] == "--version" {
		return ParseResult{Version: true, Code: ExitOK}
	}

	resource := args[0]
	if _, retired := retiredNamespaces[resource]; retired {
		return ParseResult{
			Envelope: Error(nil,
				"retired_namespace",
				fmt.Sprintf("'%s' is a retired top-level namespace", resource),
				"use one of: gateway, dev, test, prod, ollama, opensearch, caddy",
			),
			Code: ExitParseError,
		}
	}

	switch resource {
	case "gateway":
		return parseGateway(args)
	case "dev", "test", "prod":
		return parseRuntime(args)
	case "ollama", "opensearch", "caddy":
		return parseServicePassthrough(args)
	default:
		return ParseResult{
			Envelope: Error(nil,
				"parse_error",
				fmt.Sprintf("unknown resource '%s'", resource),
				"use one of: gateway, dev, test, prod, ollama, opensearch, caddy",
			),
			Code: ExitParseError,
		}
	}
}

func parseGateway(args []string) ParseResult {
	if len(args) < 2 {
		return ParseResult{
			Envelope: Error(nil,
				"parse_error",
				"missing gateway command",
				"use: gateway status|update|logs | gateway service <deploy|restart|status> <service> | gateway docker ping",
			),
			Code: ExitParseError,
		}
	}

	switch args[1] {
	case "status", "update", "logs":
		if len(args) != 2 {
			return ParseResult{
				Envelope: Error(nil,
					"parse_error",
					fmt.Sprintf("unexpected arguments after 'gateway %s'", args[1]),
					fmt.Sprintf("use: gateway %s", args[1]),
				),
				Code: ExitParseError,
			}
		}
		return ParseResult{
			Route: &Route{
				Resource: "gateway",
				Kind:     KindGateway,
				Tokens:   append([]string(nil), args...),
				Action:   args[1],
			},
		}
	case "service":
		if len(args) != 4 {
			return ParseResult{
				Envelope: Error(nil,
					"parse_error",
					"invalid gateway service command",
					"use: gateway service <deploy|restart|status> <service>",
				),
				Code: ExitParseError,
			}
		}
		switch args[2] {
		case "deploy", "restart", "status":
			return ParseResult{
				Route: &Route{
					Resource: "gateway",
					Kind:     KindGatewayService,
					Tokens:   append([]string(nil), args...),
					Action:   args[2],
					Subject:  args[3],
				},
			}
		default:
			return ParseResult{
				Envelope: Error(nil,
					"parse_error",
					fmt.Sprintf("unknown gateway service action '%s'", args[2]),
					"use: gateway service <deploy|restart|status> <service>",
				),
				Code: ExitParseError,
			}
		}
	case "docker":
		if len(args) == 3 && args[2] == "ping" {
			return ParseResult{
				Route: &Route{
					Resource: "gateway",
					Kind:     KindGatewayDocker,
					Tokens:   append([]string(nil), args...),
					Action:   "ping",
					Subject:  "docker",
				},
			}
		}
		if len(args) == 4 && args[2] == "run" {
			return ParseResult{
				Route: &Route{
					Resource: "gateway",
					Kind:     KindGatewayDocker,
					Tokens:   append([]string(nil), args...),
					Action:   "run",
					Subject:  args[3],
				},
			}
		}
		return ParseResult{
			Envelope: Error(nil,
				"parse_error",
				"invalid gateway docker command",
				"use: gateway docker ping | gateway docker run <image>",
			),
			Code: ExitParseError,
		}
	default:
		return ParseResult{
			Envelope: Error(nil,
				"parse_error",
				fmt.Sprintf("unknown gateway command '%s'", args[1]),
				"use: gateway status|update|logs | gateway service <deploy|restart|status> <service> | gateway docker ping | gateway docker run <image>",
			),
			Code: ExitParseError,
		}
	}
}

func parseRuntime(args []string) ParseResult {
	if len(args) < 2 {
		return ParseResult{
			Envelope: Error(nil,
				"parse_error",
				fmt.Sprintf("missing command for environment '%s'", args[0]),
				fmt.Sprintf("use: %s reload|checkpoint|openclaw <command>", args[0]),
			),
			Code: ExitParseError,
		}
	}

	route := &Route{
		Resource:    args[0],
		Tokens:      append([]string(nil), args...),
		Environment: args[0],
		Runtime:     runtimeMappings[args[0]],
	}

	switch args[1] {
	case "reload", "checkpoint":
		if len(args) != 2 {
			return ParseResult{
				Envelope: Error(nil,
					"parse_error",
					fmt.Sprintf("unexpected arguments after '%s %s'", args[0], args[1]),
					fmt.Sprintf("use: %s %s", args[0], args[1]),
				),
				Code: ExitParseError,
			}
		}
		route.Kind = KindRuntimeAction
		route.Action = args[1]
		return ParseResult{Route: route}
	case "openclaw":
		if len(args) < 3 {
			return ParseResult{
				Envelope: Error(nil,
					"parse_error",
					fmt.Sprintf("missing native OpenClaw command for '%s'", args[0]),
					fmt.Sprintf("use: %s openclaw <command>", args[0]),
				),
				Code: ExitParseError,
			}
		}
		route.Kind = KindRuntimeNative
		route.Action = "openclaw"
		route.NativeArgs = append([]string(nil), args[2:]...)
		return ParseResult{Route: route}
	default:
		return ParseResult{
			Envelope: Error(nil,
				"parse_error",
				fmt.Sprintf("unknown environment command '%s'", args[1]),
				fmt.Sprintf("use: %s reload|checkpoint|openclaw <command>", args[0]),
			),
			Code: ExitParseError,
		}
	}
}

func parseServicePassthrough(args []string) ParseResult {
	if len(args) < 2 {
		return ParseResult{
			Envelope: Error(nil,
				"parse_error",
				fmt.Sprintf("missing native command for service '%s'", args[0]),
				fmt.Sprintf("use: %s <native command>", args[0]),
			),
			Code: ExitParseError,
		}
	}

	return ParseResult{
		Route: &Route{
			Resource:   args[0],
			Kind:       KindServiceNative,
			Tokens:     append([]string(nil), args...),
			Action:     "passthrough",
			NativeArgs: append([]string(nil), args[1:]...),
		},
	}
}

func Error(route *Route, errorType, errorMessage, recoveryMessage string) *Envelope {
	return &Envelope{
		OK:              false,
		Route:           route,
		ErrorType:       errorType,
		ErrorMessage:    errorMessage,
		RecoveryMessage: recoveryMessage,
	}
}

func NotImplemented(route *Route, errorMessage, recoveryMessage string) *Envelope {
	return Error(route, "not_implemented", errorMessage, recoveryMessage)
}

func WriteJSON(out io.Writer, payload any) error {
	encoder := json.NewEncoder(out)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	return encoder.Encode(payload)
}

func WriteHelp(out io.Writer) error {
	_, err := io.WriteString(out, strings.TrimLeft(helpText, "\n"))
	return err
}

func WriteVersion(out io.Writer) error {
	_, err := fmt.Fprintf(out, "moltbox %s\n", Version)
	return err
}

func DockerSocketPath() string {
	if value := strings.TrimSpace(os.Getenv("MOLTBOX_DOCKER_SOCKET")); value != "" {
		return value
	}
	return DefaultDockerSocket
}

func GatewayURL() string {
	if value := strings.TrimSpace(os.Getenv("MOLTBOX_GATEWAY_URL")); value != "" {
		return strings.TrimRight(value, "/")
	}
	return DefaultGatewayURL
}

func GatewayListenAddress() string {
	if value := strings.TrimSpace(os.Getenv("MOLTBOX_GATEWAY_LISTEN_ADDR")); value != "" {
		return value
	}
	return DefaultGatewayListenAddr
}

func ExitCodeFromPayload(payload []byte) int {
	var envelope Envelope
	if err := json.Unmarshal(payload, &envelope); err != nil {
		return ExitFailure
	}

	if envelope.OK {
		return ExitOK
	}

	switch envelope.ErrorType {
	case "not_implemented":
		return ExitNotImplemented
	case "parse_error", "retired_namespace":
		return ExitParseError
	default:
		return ExitFailure
	}
}

func isHelpFlag(value string) bool {
	return value == "-h" || value == "--help"
}

const helpText = `
moltbox <resource> <command>

Resources:
  gateway
    status
    update
    logs
    service deploy <service>
    service restart <service>
    service status <service>
    docker ping
    docker run <image>

  dev|test|prod
    reload
    checkpoint
    openclaw <command>

  ollama|opensearch|caddy
    <native command>

Retired namespaces fail explicitly:
  runtime, service, skill, tools, host, openclaw-dev, openclaw-test, openclaw-prod
`
