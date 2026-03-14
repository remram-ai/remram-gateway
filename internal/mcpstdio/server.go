package mcpstdio

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"strconv"
	"strings"

	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

const protocolVersion = "2025-03-26"

type Server struct {
	executor Executor
}

type Executor interface {
	ExecuteArgs(args []string, secretValue string) ([]byte, int, error)
}

type requestEnvelope struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

type responseEnvelope struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Result  any             `json:"result,omitempty"`
	Error   *responseError  `json:"error,omitempty"`
}

type responseError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

type toolsCallParams struct {
	Name      string          `json:"name"`
	Arguments json.RawMessage `json:"arguments,omitempty"`
}

type runCLIArguments struct {
	Args  []string `json:"args"`
	Stdin string   `json:"stdin,omitempty"`
}

func New(executor Executor) *Server {
	return &Server{executor: executor}
}

func (s *Server) Run(stdin io.Reader, stdout io.Writer) error {
	reader := bufio.NewReader(stdin)
	for {
		message, err := readMessage(reader)
		if err != nil {
			if err == io.EOF {
				return nil
			}
			return err
		}

		response, ok, err := s.HandleMessage(message)
		if err != nil {
			if err := writeMessage(stdout, responseEnvelope{
				JSONRPC: "2.0",
				Error: &responseError{
					Code:    -32700,
					Message: "parse error",
				},
			}); err != nil {
				return err
			}
			continue
		}
		if !ok {
			continue
		}
		if err := writeMessage(stdout, response); err != nil {
			return err
		}
	}
}

func (s *Server) HandleMessage(message []byte) (responseEnvelope, bool, error) {
	var request requestEnvelope
	if err := json.Unmarshal(message, &request); err != nil {
		return responseEnvelope{}, false, err
	}
	response, ok := s.handleRequest(request)
	return response, ok, nil
}

func (s *Server) handleRequest(request requestEnvelope) (responseEnvelope, bool) {
	switch request.Method {
	case "initialize":
		return responseEnvelope{
			JSONRPC: "2.0",
			ID:      request.ID,
			Result: map[string]any{
				"protocolVersion": protocolVersion,
				"capabilities": map[string]any{
					"logging": map[string]any{},
					"tools": map[string]any{
						"listChanged": false,
					},
				},
				"serverInfo": map[string]any{
					"name":    "moltbox",
					"version": cli.Version,
				},
			},
		}, true
	case "notifications/initialized":
		return responseEnvelope{}, false
	case "logging/setLevel":
		return responseEnvelope{
			JSONRPC: "2.0",
			ID:      request.ID,
			Result:  map[string]any{},
		}, true
	case "ping":
		return responseEnvelope{
			JSONRPC: "2.0",
			ID:      request.ID,
			Result:  map[string]any{},
		}, true
	case "tools/list":
		return responseEnvelope{
			JSONRPC: "2.0",
			ID:      request.ID,
			Result: map[string]any{
				"tools": []map[string]any{
					{
						"name":        "moltbox_help",
						"description": "Return the Moltbox CLI help text for the appliance control plane.",
						"inputSchema": map[string]any{
							"type":                 "object",
							"properties":           map[string]any{},
							"additionalProperties": false,
						},
					},
					{
						"name":        "moltbox_run",
						"description": "Execute the Moltbox appliance CLI. Provide tokens after 'moltbox' in args and optionally a single-line stdin secret value.",
						"inputSchema": map[string]any{
							"type": "object",
							"properties": map[string]any{
								"args": map[string]any{
									"type":        "array",
									"description": "CLI tokens after the 'moltbox' executable.",
									"items": map[string]any{
										"type": "string",
									},
								},
								"stdin": map[string]any{
									"type":        "string",
									"description": "Optional single-line stdin payload, primarily for secrets set commands.",
								},
							},
							"required":             []string{"args"},
							"additionalProperties": false,
						},
					},
				},
			},
		}, true
	case "tools/call":
		var params toolsCallParams
		if err := json.Unmarshal(request.Params, &params); err != nil {
			return errorResponse(request.ID, -32602, "invalid tools/call params"), true
		}
		result, isError := s.handleToolCall(params)
		return responseEnvelope{
			JSONRPC: "2.0",
			ID:      request.ID,
			Result: map[string]any{
				"content": []map[string]any{
					{
						"type": "text",
						"text": result,
					},
				},
				"isError": isError,
			},
		}, true
	default:
		return errorResponse(request.ID, -32601, "method not found"), true
	}
}

func (s *Server) handleToolCall(params toolsCallParams) (string, bool) {
	switch params.Name {
	case "moltbox_help":
		payload, _, err := s.executor.ExecuteArgs([]string{"--help"}, "")
		if err != nil {
			return err.Error(), true
		}
		return string(payload), false
	case "moltbox_run":
		var arguments runCLIArguments
		if len(params.Arguments) == 0 {
			return "missing arguments for moltbox_run", true
		}
		if err := json.Unmarshal(params.Arguments, &arguments); err != nil {
			return fmt.Sprintf("invalid moltbox_run arguments: %v", err), true
		}
		payload, exitCode, err := s.executor.ExecuteArgs(arguments.Args, arguments.Stdin)
		if err != nil {
			return err.Error(), true
		}
		return string(payload), exitCode != cli.ExitOK
	default:
		return fmt.Sprintf("unknown tool %q", params.Name), true
	}
}

func readMessage(reader *bufio.Reader) ([]byte, error) {
	contentLength := 0
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			return nil, err
		}
		line = strings.TrimRight(line, "\r\n")
		if line == "" {
			if contentLength == 0 {
				continue
			}
			break
		}
		name, value, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		if strings.EqualFold(strings.TrimSpace(name), "Content-Length") {
			length, err := strconv.Atoi(strings.TrimSpace(value))
			if err != nil {
				return nil, fmt.Errorf("parse content length: %w", err)
			}
			contentLength = length
		}
	}
	if contentLength <= 0 {
		return nil, fmt.Errorf("missing Content-Length header")
	}
	message := make([]byte, contentLength)
	if _, err := io.ReadFull(reader, message); err != nil {
		return nil, err
	}
	return message, nil
}

func writeMessage(writer io.Writer, payload responseEnvelope) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	if _, err := fmt.Fprintf(writer, "Content-Length: %d\r\n\r\n", len(body)); err != nil {
		return err
	}
	_, err = writer.Write(body)
	return err
}

func errorResponse(id json.RawMessage, code int, message string) responseEnvelope {
	return responseEnvelope{
		JSONRPC: "2.0",
		ID:      id,
		Error: &responseError{
			Code:    code,
			Message: message,
		},
	}
}
