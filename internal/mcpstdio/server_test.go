package mcpstdio

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"testing"

	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

type fakeExecutor struct {
	args        []string
	secretValue string
	payload     []byte
	code        int
	err         error
}

func (f *fakeExecutor) ExecuteArgs(args []string, secretValue string) ([]byte, int, error) {
	f.args = append([]string(nil), args...)
	f.secretValue = secretValue
	return f.payload, f.code, f.err
}

func TestServerListsTools(t *testing.T) {
	t.Parallel()

	output := runServer(t, `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`, &fakeExecutor{})
	response := decodeResponse(t, output)

	result, ok := response["result"].(map[string]any)
	if !ok {
		t.Fatalf("result = %#v, want map", response["result"])
	}
	tools, ok := result["tools"].([]any)
	if !ok || len(tools) != 2 {
		t.Fatalf("tools = %#v, want 2 tools", result["tools"])
	}
}

func TestServerInitializeAdvertisesLoggingCapability(t *testing.T) {
	t.Parallel()

	output := runServer(t, `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"vscode","version":"1.0"}}}`, &fakeExecutor{})
	response := decodeResponse(t, output)

	result, ok := response["result"].(map[string]any)
	if !ok {
		t.Fatalf("result = %#v, want map", response["result"])
	}
	capabilities, ok := result["capabilities"].(map[string]any)
	if !ok {
		t.Fatalf("capabilities = %#v, want map", result["capabilities"])
	}
	if _, ok := capabilities["logging"].(map[string]any); !ok {
		t.Fatalf("logging capability missing from %#v", capabilities)
	}
}

func TestServerAcceptsLoggingSetLevel(t *testing.T) {
	t.Parallel()

	output := runServer(t, `{"jsonrpc":"2.0","id":4,"method":"logging/setLevel","params":{"level":"debug"}}`, &fakeExecutor{})
	response := decodeResponse(t, output)

	if got, ok := response["result"].(map[string]any); !ok || len(got) != 0 {
		t.Fatalf("result = %#v, want empty object", response["result"])
	}
}

func TestServerRunsCLIThroughExecutor(t *testing.T) {
	t.Parallel()

	executor := &fakeExecutor{
		payload: []byte("{\n  \"ok\": true\n}\n"),
		code:    cli.ExitOK,
	}
	output := runServer(t, `{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"moltbox_run","arguments":{"args":["gateway","status"],"stdin":"secret-value"}}}`, executor)
	response := decodeResponse(t, output)

	if got := strings.Join(executor.args, " "); got != "gateway status" {
		t.Fatalf("executor args = %q, want gateway status", got)
	}
	if executor.secretValue != "secret-value" {
		t.Fatalf("executor secretValue = %q, want secret-value", executor.secretValue)
	}

	result, ok := response["result"].(map[string]any)
	if !ok {
		t.Fatalf("result = %#v, want map", response["result"])
	}
	if result["isError"] != false {
		t.Fatalf("isError = %#v, want false", result["isError"])
	}
}

func TestServerReturnsToolErrorsAsIsError(t *testing.T) {
	t.Parallel()

	executor := &fakeExecutor{
		payload: []byte("{\n  \"ok\": false,\n  \"error_type\": \"not_implemented\"\n}\n"),
		code:    cli.ExitNotImplemented,
	}
	output := runServer(t, `{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"moltbox_run","arguments":{"args":["dev","checkpoint"]}}}`, executor)
	response := decodeResponse(t, output)

	result := response["result"].(map[string]any)
	if result["isError"] != true {
		t.Fatalf("isError = %#v, want true", result["isError"])
	}
}

func runServer(t *testing.T, requestJSON string, executor Executor) string {
	t.Helper()

	stdinReader, stdinWriter := io.Pipe()
	var stdout bytes.Buffer
	done := make(chan error, 1)

	go func() {
		done <- New(executor).Run(stdinReader, &stdout)
	}()

	if _, err := fmt.Fprintf(stdinWriter, "Content-Length: %d\r\n\r\n%s", len(requestJSON), requestJSON); err != nil {
		t.Fatalf("write request: %v", err)
	}
	_ = stdinWriter.Close()

	if err := <-done; err != nil {
		t.Fatalf("Run() error = %v", err)
	}
	return stdout.String()
}

func decodeResponse(t *testing.T, output string) map[string]any {
	t.Helper()

	reader := bufio.NewReader(strings.NewReader(output))
	header, err := reader.ReadString('\n')
	if err != nil {
		t.Fatalf("read header: %v", err)
	}
	if !strings.HasPrefix(header, "Content-Length: ") {
		t.Fatalf("header = %q, want Content-Length", header)
	}
	if _, err := reader.ReadString('\n'); err != nil {
		t.Fatalf("read header separator: %v", err)
	}
	body, err := io.ReadAll(reader)
	if err != nil {
		t.Fatalf("read body: %v", err)
	}
	var payload map[string]any
	if err := json.Unmarshal(body, &payload); err != nil {
		t.Fatalf("decode body: %v", err)
	}
	return payload
}
