package client

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

type HTTPClient struct {
	baseURL    string
	httpClient *http.Client
}

func NewHTTPClient(baseURL string) *HTTPClient {
	return &HTTPClient{
		baseURL: strings.TrimRight(baseURL, "/"),
		httpClient: &http.Client{
			Timeout: 10 * time.Minute,
		},
	}
}

func (c *HTTPClient) Execute(route *cli.Route, secretValue string) ([]byte, error) {
	switch {
	case route.Kind == cli.KindGateway && route.Action == "status":
		return c.get("/status")
	case route.Kind == cli.KindGatewayDocker && route.Action == "ping":
		return c.get("/docker/ping")
	case route.Kind == cli.KindGatewayDocker && route.Action == "run":
		return c.post("/docker/run", cli.DockerRunRequest{Image: route.Subject})
	case route.Kind == cli.KindGatewayService && route.Action == "status":
		query := url.Values{}
		query.Set("service", route.Subject)
		return c.get("/service/status?" + query.Encode())
	case route.Kind == cli.KindGatewayService && route.Action == "deploy":
		return c.post("/service/deploy", cli.RouteRequest{Route: route, Service: route.Subject})
	case route.Kind == cli.KindGatewayService && route.Action == "restart":
		return c.post("/service/restart", cli.RouteRequest{Route: route, Service: route.Subject})
	case route.Kind == cli.KindGateway && route.Action == "logs":
		return c.get("/logs")
	case route.Kind == cli.KindGateway && route.Action == "update":
		return c.post("/update", cli.RouteRequest{Route: route, Service: "gateway"})
	case route.Kind == cli.KindGatewayToken && route.Action == "create":
		return c.post("/token/create", cli.RouteRequest{Route: route})
	case route.Kind == cli.KindGatewayToken && route.Action == "list":
		return c.get("/token/list")
	case route.Kind == cli.KindGatewayToken && route.Action == "delete":
		return c.post("/token/delete", cli.RouteRequest{Route: route})
	case route.Kind == cli.KindGatewayToken && route.Action == "rotate":
		return c.post("/token/rotate", cli.RouteRequest{Route: route})
	case route.Kind == cli.KindRuntimeAction && route.Action == "reload":
		return c.post("/runtime/reload", cli.RouteRequest{Route: route})
	case route.Kind == cli.KindRuntimeAction && route.Action == "checkpoint":
		return c.post("/runtime/checkpoint", cli.RouteRequest{Route: route})
	case route.Kind == cli.KindRuntimeSkill && route.Action == "deploy":
		return c.post("/runtime/skill/deploy", cli.RouteRequest{Route: route})
	case route.Kind == cli.KindRuntimeSkill && route.Action == "rollback":
		return c.post("/runtime/skill/rollback", cli.RouteRequest{Route: route})
	case route.Kind == cli.KindRuntimeNative:
		return c.post("/runtime/openclaw", cli.RouteRequest{Route: route})
	case route.Kind == cli.KindServiceNative:
		return c.post("/service/passthrough", cli.RouteRequest{Route: route})
	default:
		return c.post("/execute", cli.RouteRequest{Route: route, SecretValue: secretValue})
	}
}

func (c *HTTPClient) get(path string) ([]byte, error) {
	request, err := http.NewRequest(http.MethodGet, c.baseURL+path, nil)
	if err != nil {
		return nil, err
	}

	return c.do(request)
}

func (c *HTTPClient) post(path string, payload any) ([]byte, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	request, err := http.NewRequest(http.MethodPost, c.baseURL+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")

	return c.do(request)
}

func (c *HTTPClient) do(request *http.Request) ([]byte, error) {
	response, err := c.httpClient.Do(request)
	if err != nil {
		return nil, fmt.Errorf("request %s %s: %w", request.Method, request.URL.String(), err)
	}
	defer response.Body.Close()

	body, err := io.ReadAll(response.Body)
	if err != nil {
		return nil, fmt.Errorf("read gateway response: %w", err)
	}
	if len(body) == 0 {
		return nil, fmt.Errorf("gateway returned an empty response for %s %s", request.Method, request.URL.Path)
	}

	return body, nil
}
