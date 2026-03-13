package orchestrator

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/remram-ai/moltbox-gateway/internal/command"
	appconfig "github.com/remram-ai/moltbox-gateway/internal/config"
	"github.com/remram-ai/moltbox-gateway/internal/docker"
	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

type fakeRunner struct {
	results  []command.Result
	commands [][]string
	dirs     []string
}

func (f *fakeRunner) Run(_ context.Context, dir string, name string, args ...string) (command.Result, error) {
	f.dirs = append(f.dirs, dir)
	f.commands = append(f.commands, append([]string{name}, args...))
	if len(f.results) == 0 {
		return command.Result{}, nil
	}
	result := f.results[0]
	f.results = f.results[1:]
	return result, nil
}

type fakeInspector struct {
	containers map[string]docker.ContainerInfo
}

func (f fakeInspector) InspectContainer(_ context.Context, name string) (docker.ContainerInfo, error) {
	info, ok := f.containers[name]
	if !ok {
		return docker.ContainerInfo{}, docker.ErrContainerNotFound
	}
	return info, nil
}

func TestRenderServiceAssetsForRuntimeService(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	servicesRoot := filepath.Join(root, "services-repo")
	runtimeRoot := filepath.Join(root, "runtime-repo")
	stateRoot := filepath.Join(root, "state")
	runtimeStateRoot := filepath.Join(root, "runtime-state")

	mustWriteFile(t, filepath.Join(servicesRoot, "services", "openclaw-dev", "service.yaml"), "compose_project: openclaw-dev\ncontainer_names:\n  - openclaw-dev\nruntime_required: true\n")
	mustWriteFile(t, filepath.Join(servicesRoot, "services", "openclaw-dev", "compose.yml.template"), "services:\n  {{ service_name }}:\n    container_name: \"{{ container_name }}\"\n    ports:\n      - \"{{ gateway_port }}:18789\"\n    volumes:\n      - \"{{ runtime_component_dir }}:/home/node/.openclaw\"\n")
	mustWriteFile(t, filepath.Join(runtimeRoot, "openclaw-dev", "openclaw.json.template"), "{\"port\": {{ gateway_port }}, \"profile\": \"{{ profile }}\"}\n")
	mustWriteFile(t, filepath.Join(runtimeRoot, "openclaw-dev", "model-runtime.yml"), "model: local\n")

	manager := NewManager(appconfig.Config{
		Paths: appconfig.PathsConfig{
			StateRoot:   stateRoot,
			RuntimeRoot: runtimeStateRoot,
			LogsRoot:    filepath.Join(root, "logs"),
		},
		Repos: appconfig.ReposConfig{
			Services: appconfig.RepoConfig{URL: servicesRoot},
			Runtime:  appconfig.RepoConfig{URL: runtimeRoot},
		},
		Gateway: appconfig.GatewayConfig{Host: "0.0.0.0", Port: 7460},
	}, fakeInspector{}, &fakeRunner{})

	definition, err := manager.LoadServiceDefinition("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadServiceDefinition() error = %v", err)
	}

	outputDir, _, err := manager.RenderServiceAssets("openclaw-dev", definition)
	if err != nil {
		t.Fatalf("RenderServiceAssets() error = %v", err)
	}

	composeData, err := os.ReadFile(filepath.Join(outputDir, "compose.yml"))
	if err != nil {
		t.Fatalf("read compose: %v", err)
	}
	if !strings.Contains(string(composeData), "18790:18789") {
		t.Fatalf("compose missing rendered runtime port: %s", composeData)
	}

	openclawData, err := os.ReadFile(filepath.Join(outputDir, "config", "openclaw-dev", "openclaw.json"))
	if err != nil {
		t.Fatalf("read openclaw.json: %v", err)
	}
	if !strings.Contains(string(openclawData), "\"port\": 18790") {
		t.Fatalf("rendered openclaw.json missing runtime port: %s", openclawData)
	}

	if _, err := os.Stat(filepath.Join(runtimeStateRoot, "openclaw-dev")); err != nil {
		t.Fatalf("runtime state dir missing: %v", err)
	}
}

func TestRenderServiceAssetsForCaddyGeneratesTLSAssets(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	servicesRoot := filepath.Join(root, "services-repo")
	runtimeRoot := filepath.Join(root, "runtime-repo")
	stateRoot := filepath.Join(root, "state")

	mustWriteFile(t, filepath.Join(servicesRoot, "services", "caddy", "service.yaml"), "compose_project: caddy\ncontainer_names:\n  - caddy\nruntime_required: true\n")
	mustWriteFile(t, filepath.Join(servicesRoot, "services", "caddy", "compose.yml.template"), "services:\n  caddy:\n    container_name: \"{{ container_name }}\"\n")
	mustWriteFile(t, filepath.Join(runtimeRoot, "caddy", "Caddyfile.template"), "https://moltbox-dev {\n  tls /etc/caddy/certs/local.crt /etc/caddy/certs/local.key\n}\n")

	manager := NewManager(appconfig.Config{
		Paths: appconfig.PathsConfig{
			StateRoot:   stateRoot,
			RuntimeRoot: filepath.Join(root, "runtime-state"),
			LogsRoot:    filepath.Join(root, "logs"),
		},
		Repos: appconfig.ReposConfig{
			Services: appconfig.RepoConfig{URL: servicesRoot},
			Runtime:  appconfig.RepoConfig{URL: runtimeRoot},
		},
		Gateway: appconfig.GatewayConfig{Host: "0.0.0.0", Port: 7460},
	}, fakeInspector{}, &fakeRunner{})

	definition, err := manager.LoadServiceDefinition("caddy")
	if err != nil {
		t.Fatalf("LoadServiceDefinition() error = %v", err)
	}

	outputDir, _, err := manager.RenderServiceAssets("caddy", definition)
	if err != nil {
		t.Fatalf("RenderServiceAssets() error = %v", err)
	}

	for _, relative := range []string{
		filepath.Join("config", "caddy", "Caddyfile"),
		filepath.Join("config", "caddy", "certs", "local.crt"),
		filepath.Join("config", "caddy", "certs", "local.key"),
	} {
		if _, err := os.Stat(filepath.Join(outputDir, relative)); err != nil {
			t.Fatalf("expected %s to exist: %v", relative, err)
		}
	}
}

func TestDeployServiceRunsComposeAndInspectsContainers(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	servicesRoot := filepath.Join(root, "services-repo")
	runtimeRoot := filepath.Join(root, "runtime-repo")
	stateRoot := filepath.Join(root, "state")

	mustWriteFile(t, filepath.Join(servicesRoot, "services", "gateway", "service.yaml"), "compose_project: gateway\ncontainer_names:\n  - gateway\nbuild_on_deploy: true\nskip_pull: true\n")
	mustWriteFile(t, filepath.Join(servicesRoot, "services", "gateway", "compose.yml.template"), "services:\n  gateway:\n    container_name: \"{{ container_name }}\"\n")
	mustWriteFile(t, filepath.Join(runtimeRoot, "gateway", "config.yaml"), "gateway:\n  host: 0.0.0.0\n  port: 7460\n")

	runner := &fakeRunner{
		results: []command.Result{
			{ExitCode: 0},
			{ExitCode: 0},
		},
	}
	containerInfo := docker.ContainerInfo{}
	containerInfo.Name = "/gateway"
	containerInfo.Config.Image = "moltbox-gateway:latest"
	containerInfo.State.Status = "running"
	containerInfo.State.Running = true
	containerInfo.State.Health = &struct {
		Status string `json:"Status"`
	}{Status: "healthy"}

	manager := NewManager(appconfig.Config{
		Paths: appconfig.PathsConfig{
			StateRoot:   stateRoot,
			RuntimeRoot: filepath.Join(root, "runtime-state"),
			LogsRoot:    filepath.Join(root, "logs"),
		},
		Repos: appconfig.ReposConfig{
			Services: appconfig.RepoConfig{URL: servicesRoot},
			Runtime:  appconfig.RepoConfig{URL: runtimeRoot},
		},
		Gateway: appconfig.GatewayConfig{Host: "0.0.0.0", Port: 7460},
	}, fakeInspector{
		containers: map[string]docker.ContainerInfo{
			"gateway": containerInfo,
		},
	}, runner)

	result, err := manager.DeployService(context.Background(), &cli.Route{Resource: "gateway", Kind: cli.KindGatewayService, Action: "deploy", Subject: "gateway"}, "gateway")
	if err != nil {
		t.Fatalf("DeployService() error = %v", err)
	}
	if !result.OK {
		t.Fatal("expected successful deploy result")
	}
	if len(runner.commands) < 2 {
		t.Fatalf("expected docker commands, got %d", len(runner.commands))
	}
	if got := strings.Join(runner.commands[1], " "); !strings.Contains(got, "compose") || !strings.Contains(got, "--build") {
		t.Fatalf("compose up command = %q, want compose build command", got)
	}
}

func TestGatewayUpdateStartsHelperContainer(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	servicesRoot := filepath.Join(root, "services-repo")
	runtimeRoot := filepath.Join(root, "runtime-repo")
	stateRoot := filepath.Join(root, "state")

	mustWriteFile(t, filepath.Join(servicesRoot, "services", "gateway", "service.yaml"), "compose_project: gateway\ncontainer_names:\n  - gateway\nruntime_required: true\nskip_pull: true\n")
	mustWriteFile(t, filepath.Join(servicesRoot, "services", "gateway", "compose.yml.template"), "services:\n  gateway:\n    container_name: \"{{ container_name }}\"\n")
	mustWriteFile(t, filepath.Join(runtimeRoot, "gateway", "config.yaml"), "gateway:\n  host: 0.0.0.0\n  port: 7460\n")

	runner := &fakeRunner{
		results: []command.Result{
			{ExitCode: 1},
			{ExitCode: 0},
			{ExitCode: 0},
		},
	}

	manager := NewManager(appconfig.Config{
		Paths: appconfig.PathsConfig{
			StateRoot:   stateRoot,
			RuntimeRoot: filepath.Join(root, "runtime-state"),
			LogsRoot:    filepath.Join(root, "logs"),
		},
		Repos: appconfig.ReposConfig{
			Services: appconfig.RepoConfig{URL: servicesRoot},
			Runtime:  appconfig.RepoConfig{URL: runtimeRoot},
		},
		Gateway: appconfig.GatewayConfig{Host: "0.0.0.0", Port: 7460},
	}, fakeInspector{}, runner)

	result, err := manager.GatewayUpdate(context.Background(), &cli.Route{Resource: "gateway", Kind: cli.KindGateway, Action: "update", Subject: "gateway"})
	if err != nil {
		t.Fatalf("GatewayUpdate() error = %v", err)
	}
	if !result.OK {
		t.Fatal("expected successful gateway update result")
	}
	if len(runner.commands) != 3 {
		t.Fatalf("expected network inspect/create + helper run, got %d commands", len(runner.commands))
	}
	got := strings.Join(runner.commands[2], " ")
	if !strings.Contains(got, "run -d --rm") || !strings.Contains(got, "moltbox-gateway:latest") {
		t.Fatalf("gateway update helper command = %q", got)
	}
}

func mustWriteFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir %s: %v", path, err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("write %s: %v", path, err)
	}
}
