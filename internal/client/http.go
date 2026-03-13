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
			Timeout: 10 * time.Second,
		},
	}
}

func (c *HTTPClient) Execute(route *cli.Route) ([]byte, error) {
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
	default:
		return c.post("/execute", cli.RouteRequest{Route: route})
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
