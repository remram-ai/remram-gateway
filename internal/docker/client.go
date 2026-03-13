package docker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

var ErrContainerNotFound = errors.New("container not found")

type Client struct {
	socketPath string
	httpClient *http.Client
}

type VersionInfo struct {
	Version       string `json:"Version"`
	APIVersion    string `json:"ApiVersion"`
	MinAPIVersion string `json:"MinAPIVersion"`
	GitCommit     string `json:"GitCommit"`
	GoVersion     string `json:"GoVersion"`
	OS            string `json:"Os"`
	Arch          string `json:"Arch"`
	KernelVersion string `json:"KernelVersion"`
}

type ContainerInfo struct {
	Name   string `json:"Name"`
	Config struct {
		Image string `json:"Image"`
	} `json:"Config"`
	State struct {
		Status  string `json:"Status"`
		Running bool   `json:"Running"`
		Health  *struct {
			Status string `json:"Status"`
		} `json:"Health,omitempty"`
	} `json:"State"`
}

type RunResult struct {
	ID   string
	Name string
}

func NewClient(socketPath string) *Client {
	transport := &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			var dialer net.Dialer
			return dialer.DialContext(ctx, "unix", socketPath)
		},
	}

	return &Client{
		socketPath: socketPath,
		httpClient: &http.Client{
			Timeout:   5 * time.Second,
			Transport: transport,
		},
	}
}

func (c *Client) Version(ctx context.Context) (VersionInfo, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://docker/version", nil)
	if err != nil {
		return VersionInfo{}, err
	}

	var info VersionInfo
	if err := c.doJSON(request, &info); err != nil {
		return VersionInfo{}, err
	}
	return info, nil
}

func (c *Client) InspectContainer(ctx context.Context, name string) (ContainerInfo, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://docker/containers/"+url.PathEscape(name)+"/json", nil)
	if err != nil {
		return ContainerInfo{}, err
	}

	response, err := c.httpClient.Do(request)
	if err != nil {
		return ContainerInfo{}, fmt.Errorf("inspect container %s over %s: %w", name, c.socketPath, err)
	}
	defer response.Body.Close()

	if response.StatusCode == http.StatusNotFound {
		return ContainerInfo{}, ErrContainerNotFound
	}
	if response.StatusCode != http.StatusOK {
		return ContainerInfo{}, fmt.Errorf("inspect container returned status %s", response.Status)
	}

	var info ContainerInfo
	if err := json.NewDecoder(response.Body).Decode(&info); err != nil {
		return ContainerInfo{}, fmt.Errorf("decode inspect container response: %w", err)
	}

	return info, nil
}

func (c *Client) RunImage(ctx context.Context, image string) (RunResult, error) {
	if err := c.pullImage(ctx, image); err != nil {
		return RunResult{}, err
	}

	name := containerNameForImage(image)
	if err := c.removeContainerIfPresent(ctx, name); err != nil {
		return RunResult{}, err
	}

	containerID, err := c.createContainer(ctx, image, name)
	if err != nil {
		return RunResult{}, err
	}

	if err := c.startContainer(ctx, containerID); err != nil {
		return RunResult{}, err
	}

	return RunResult{ID: containerID, Name: name}, nil
}

func (c *Client) doJSON(request *http.Request, target any) error {
	response, err := c.httpClient.Do(request)
	if err != nil {
		return fmt.Errorf("query docker via %s: %w", c.socketPath, err)
	}
	defer response.Body.Close()

	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("docker API returned status %s", response.Status)
	}

	if err := json.NewDecoder(response.Body).Decode(target); err != nil {
		return fmt.Errorf("decode docker API response: %w", err)
	}

	return nil
}

func (c *Client) pullImage(ctx context.Context, image string) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, "http://docker/images/create?fromImage="+url.QueryEscape(image), nil)
	if err != nil {
		return err
	}

	response, err := c.httpClient.Do(request)
	if err != nil {
		return fmt.Errorf("pull image %s over %s: %w", image, c.socketPath, err)
	}
	defer response.Body.Close()

	if response.StatusCode != http.StatusOK && response.StatusCode != http.StatusCreated {
		body, _ := io.ReadAll(response.Body)
		return fmt.Errorf("pull image returned status %s: %s", response.Status, strings.TrimSpace(string(body)))
	}

	_, _ = io.Copy(io.Discard, response.Body)
	return nil
}

func (c *Client) createContainer(ctx context.Context, image, name string) (string, error) {
	body, err := json.Marshal(map[string]string{"Image": image})
	if err != nil {
		return "", err
	}

	request, err := http.NewRequestWithContext(ctx, http.MethodPost, "http://docker/containers/create?name="+url.QueryEscape(name), strings.NewReader(string(body)))
	if err != nil {
		return "", err
	}
	request.Header.Set("Content-Type", "application/json")

	response, err := c.httpClient.Do(request)
	if err != nil {
		return "", fmt.Errorf("create container for %s over %s: %w", image, c.socketPath, err)
	}
	defer response.Body.Close()

	if response.StatusCode != http.StatusCreated {
		payload, _ := io.ReadAll(response.Body)
		return "", fmt.Errorf("create container returned status %s: %s", response.Status, strings.TrimSpace(string(payload)))
	}

	var result struct {
		ID string `json:"Id"`
	}
	if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("decode create container response: %w", err)
	}
	if result.ID == "" {
		return "", errors.New("docker create returned an empty container id")
	}

	return result.ID, nil
}

func (c *Client) startContainer(ctx context.Context, containerID string) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, "http://docker/containers/"+url.PathEscape(containerID)+"/start", nil)
	if err != nil {
		return err
	}

	response, err := c.httpClient.Do(request)
	if err != nil {
		return fmt.Errorf("start container %s over %s: %w", containerID, c.socketPath, err)
	}
	defer response.Body.Close()

	if response.StatusCode != http.StatusNoContent && response.StatusCode != http.StatusNotModified {
		payload, _ := io.ReadAll(response.Body)
		return fmt.Errorf("start container returned status %s: %s", response.Status, strings.TrimSpace(string(payload)))
	}

	return nil
}

func (c *Client) removeContainerIfPresent(ctx context.Context, name string) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodDelete, "http://docker/containers/"+url.PathEscape(name)+"?force=1", nil)
	if err != nil {
		return err
	}

	response, err := c.httpClient.Do(request)
	if err != nil {
		return fmt.Errorf("remove container %s over %s: %w", name, c.socketPath, err)
	}
	defer response.Body.Close()

	if response.StatusCode == http.StatusNotFound || response.StatusCode == http.StatusNoContent {
		return nil
	}

	payload, _ := io.ReadAll(response.Body)
	return fmt.Errorf("remove container returned status %s: %s", response.Status, strings.TrimSpace(string(payload)))
}

func containerNameForImage(image string) string {
	name := image
	if slash := strings.LastIndex(name, "/"); slash >= 0 {
		name = name[slash+1:]
	}
	if colon := strings.Index(name, ":"); colon >= 0 {
		name = name[:colon]
	}

	cleaned := strings.Map(func(value rune) rune {
		switch {
		case value >= 'a' && value <= 'z':
			return value
		case value >= 'A' && value <= 'Z':
			return value + ('a' - 'A')
		case value >= '0' && value <= '9':
			return value
		case value == '-' || value == '_' || value == '.':
			return value
		default:
			return '-'
		}
	}, name)

	cleaned = strings.Trim(cleaned, "-.")
	if cleaned == "" {
		return "container"
	}
	return cleaned
}
