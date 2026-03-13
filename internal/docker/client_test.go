package docker

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"path/filepath"
	"testing"
	"time"
)

func TestVersion(t *testing.T) {
	t.Parallel()

	socketPath := filepath.Join(t.TempDir(), "docker.sock")
	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatalf("listen unix socket: %v", err)
	}
	defer listener.Close()

	server := &http.Server{
		Handler: http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
			if request.URL.Path != "/version" {
				http.NotFound(writer, request)
				return
			}
			writer.Header().Set("Content-Type", "application/json")
			_, _ = writer.Write([]byte(`{"Version":"29.3.0","ApiVersion":"1.48","MinAPIVersion":"1.24"}`))
		}),
	}

	go func() {
		_ = server.Serve(listener)
	}()
	defer server.Close()

	client := NewClient(socketPath)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	info, err := client.Version(ctx)
	if err != nil {
		t.Fatalf("Version() error = %v", err)
	}
	if info.Version != "29.3.0" {
		t.Fatalf("info.Version = %q, want 29.3.0", info.Version)
	}
	if info.APIVersion != "1.48" {
		t.Fatalf("info.APIVersion = %q, want 1.48", info.APIVersion)
	}
}

func TestInspectContainer(t *testing.T) {
	t.Parallel()

	socketPath := filepath.Join(t.TempDir(), "docker.sock")
	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatalf("listen unix socket: %v", err)
	}
	defer listener.Close()

	server := &http.Server{
		Handler: http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
			switch request.URL.Path {
			case "/containers/gateway/json":
				writer.Header().Set("Content-Type", "application/json")
				_, _ = writer.Write([]byte(`{"Name":"/gateway","Config":{"Image":"moltbox-gateway:phase1"},"State":{"Status":"running","Running":true}}`))
			case "/containers/missing/json":
				http.NotFound(writer, request)
			default:
				http.NotFound(writer, request)
			}
		}),
	}

	go func() {
		_ = server.Serve(listener)
	}()
	defer server.Close()

	client := NewClient(socketPath)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	info, err := client.InspectContainer(ctx, "gateway")
	if err != nil {
		t.Fatalf("InspectContainer() error = %v", err)
	}
	if info.Name != "/gateway" {
		t.Fatalf("info.Name = %q, want /gateway", info.Name)
	}
	if !info.State.Running {
		t.Fatal("expected container to be running")
	}

	_, err = client.InspectContainer(ctx, "missing")
	if !errors.Is(err, ErrContainerNotFound) {
		t.Fatalf("InspectContainer(missing) error = %v, want ErrContainerNotFound", err)
	}
}

func TestRunImage(t *testing.T) {
	t.Parallel()

	socketPath := filepath.Join(t.TempDir(), "docker.sock")
	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatalf("listen unix socket: %v", err)
	}
	defer listener.Close()

	server := &http.Server{
		Handler: http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
			switch {
			case request.Method == http.MethodDelete && request.URL.Path == "/containers/hello-world":
				http.NotFound(writer, request)
			case request.Method == http.MethodPost && request.URL.Path == "/images/create":
				writer.WriteHeader(http.StatusOK)
				_, _ = writer.Write([]byte(`{"status":"Pulling from library/hello-world"}`))
			case request.Method == http.MethodPost && request.URL.Path == "/containers/create":
				if got := request.URL.Query().Get("name"); got != "hello-world" {
					t.Fatalf("container name = %q, want hello-world", got)
				}

				var payload map[string]string
				if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
					t.Fatalf("decode create payload: %v", err)
				}
				if payload["Image"] != "hello-world" {
					t.Fatalf("payload image = %q, want hello-world", payload["Image"])
				}

				writer.Header().Set("Content-Type", "application/json")
				writer.WriteHeader(http.StatusCreated)
				_, _ = writer.Write([]byte(`{"Id":"abc123"}`))
			case request.Method == http.MethodPost && request.URL.Path == "/containers/abc123/start":
				writer.WriteHeader(http.StatusNoContent)
			default:
				http.NotFound(writer, request)
			}
		}),
	}

	go func() {
		_ = server.Serve(listener)
	}()
	defer server.Close()

	client := NewClient(socketPath)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	result, err := client.RunImage(ctx, "hello-world")
	if err != nil {
		t.Fatalf("RunImage() error = %v", err)
	}
	if result.ID != "abc123" {
		t.Fatalf("result.ID = %q, want abc123", result.ID)
	}
	if result.Name != "hello-world" {
		t.Fatalf("result.Name = %q, want hello-world", result.Name)
	}
}
