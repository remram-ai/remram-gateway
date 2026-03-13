package orchestrator

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/remram-ai/moltbox-gateway/internal/command"
	"github.com/remram-ai/moltbox-gateway/internal/config"
	"github.com/remram-ai/moltbox-gateway/internal/docker"
	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

const internalNetworkName = "moltbox_internal"

type ContainerInspector interface {
	InspectContainer(ctx context.Context, name string) (docker.ContainerInfo, error)
}

type Manager struct {
	config    config.Config
	inspector ContainerInspector
	runner    command.Runner
}

type ServiceDefinition struct {
	ComposeProject string
	ContainerNames []string
	RuntimeRequired bool
	BuildOnDeploy bool
	SkipPull bool
}

func NewManager(cfg config.Config, inspector ContainerInspector, runner command.Runner) *Manager {
	return &Manager{
		config:    cfg,
		inspector: inspector,
		runner:    runner,
	}
}

func (m *Manager) DeployService(ctx context.Context, route *cli.Route, service string) (cli.ServiceDeployResult, error) {
	definition, err := m.LoadServiceDefinition(service)
	if err != nil {
		return cli.ServiceDeployResult{}, err
	}

	outputDir, commandArgs, err := m.RenderServiceAssets(service, definition)
	if err != nil {
		return cli.ServiceDeployResult{}, err
	}

	if err := m.ensureNetwork(ctx); err != nil {
		return cli.ServiceDeployResult{}, err
	}

	if !definition.SkipPull {
		pullResult, err := m.runner.Run(ctx, outputDir, "docker", "compose", "-f", filepath.Join(outputDir, "compose.yml"), "-p", definition.ComposeProject, "pull")
		if err != nil {
			return cli.ServiceDeployResult{}, err
		}
		if pullResult.ExitCode != 0 {
			return cli.ServiceDeployResult{}, fmt.Errorf("docker compose pull failed: %s", strings.TrimSpace(pullResult.Stdout))
		}
	}

	deployResult, err := m.runner.Run(ctx, outputDir, "docker", commandArgs...)
	if err != nil {
		return cli.ServiceDeployResult{}, err
	}
	if deployResult.ExitCode != 0 {
		return cli.ServiceDeployResult{}, fmt.Errorf("docker compose up failed: %s", strings.TrimSpace(deployResult.Stdout))
	}

	containers, err := m.waitForContainers(ctx, definition.ContainerNames, 2*time.Minute)
	if err != nil {
		return cli.ServiceDeployResult{}, err
	}

	return cli.ServiceDeployResult{
		OK:            true,
		Route:         route,
		Service:       service,
		ComposeProject: definition.ComposeProject,
		OutputDir:     outputDir,
		Command:       append([]string{"docker"}, commandArgs...),
		Containers:    containers,
	}, nil
}

func (m *Manager) GatewayUpdate(ctx context.Context, route *cli.Route) (cli.ServiceActionResult, error) {
	definition, err := m.LoadServiceDefinition("gateway")
	if err != nil {
		return cli.ServiceActionResult{}, err
	}

	outputDir, _, err := m.RenderServiceAssets("gateway", definition)
	if err != nil {
		return cli.ServiceActionResult{}, err
	}

	if err := m.ensureNetwork(ctx); err != nil {
		return cli.ServiceActionResult{}, err
	}

	updateScript := fmt.Sprintf(
		"sleep 2; docker rm -f gateway >/dev/null 2>&1 || true; cd %s && docker compose -f compose.yml -p %s up -d --remove-orphans",
		outputDir,
		definition.ComposeProject,
	)
	commandArgs := []string{
		"run",
		"-d",
		"--rm",
		"--name",
		fmt.Sprintf("gateway-updater-%d", time.Now().Unix()),
		"--entrypoint",
		"sh",
		"-v",
		fmt.Sprintf("%s:%s", m.config.Paths.StateRoot, m.config.Paths.StateRoot),
		"-v",
		fmt.Sprintf("%s:%s", m.config.Paths.LogsRoot, m.config.Paths.LogsRoot),
		"-v",
		"/var/run/docker.sock:/var/run/docker.sock",
		"moltbox-gateway:latest",
		"-lc",
		updateScript,
	}

	result, err := m.runner.Run(ctx, "", "docker", commandArgs...)
	if err != nil {
		return cli.ServiceActionResult{}, err
	}
	if result.ExitCode != 0 {
		return cli.ServiceActionResult{}, fmt.Errorf("gateway update helper failed: %s", strings.TrimSpace(result.Stdout))
	}

	return cli.ServiceActionResult{
		OK:      true,
		Route:   route,
		Service: "gateway",
		Action:  route.Action,
		Command: append([]string{"docker"}, commandArgs...),
	}, nil
}

func (m *Manager) RestartService(ctx context.Context, route *cli.Route, service string) (cli.ServiceActionResult, error) {
	definition, err := m.LoadServiceDefinition(service)
	if err != nil {
		return cli.ServiceActionResult{}, err
	}

	commandArgs := append([]string{"restart"}, definition.ContainerNames...)
	restartResult, err := m.runner.Run(ctx, "", "docker", commandArgs...)
	if err != nil {
		return cli.ServiceActionResult{}, err
	}
	if restartResult.ExitCode != 0 {
		return cli.ServiceActionResult{}, fmt.Errorf("docker restart failed: %s", strings.TrimSpace(restartResult.Stdout))
	}

	containers, err := m.waitForContainers(ctx, definition.ContainerNames, 45*time.Second)
	if err != nil {
		return cli.ServiceActionResult{}, err
	}

	return cli.ServiceActionResult{
		OK:         true,
		Route:      route,
		Service:    service,
		Action:     route.Action,
		Command:    append([]string{"docker"}, commandArgs...),
		Containers: containers,
	}, nil
}

func (m *Manager) ServiceStatus(ctx context.Context, route *cli.Route, service string) (cli.ServiceStatusResult, error) {
	definition, err := m.LoadServiceDefinition(service)
	if err != nil {
		return cli.ServiceStatusResult{}, err
	}

	containers, err := m.inspectContainers(ctx, definition.ContainerNames)
	if err != nil {
		return cli.ServiceStatusResult{}, err
	}

	result := cli.ServiceStatusResult{
		OK:            true,
		Route:         route,
		Service:       service,
		ComposeProject: definition.ComposeProject,
		Containers:    containers,
	}
	if len(containers) > 0 {
		result.ContainerName = containers[0].ContainerName
		result.Image = containers[0].Image
		result.Status = containers[0].Status
		result.Running = containers[0].Running
	}
	return result, nil
}

func (m *Manager) GatewayLogs(ctx context.Context, route *cli.Route) (cli.CommandResult, error) {
	routeCopy := *route
	commandArgs := []string{"logs", "--tail", "200", "gateway"}
	result, err := m.runner.Run(ctx, "", "docker", commandArgs...)
	if err != nil {
		return cli.CommandResult{}, err
	}

	return cli.CommandResult{
		OK:          result.ExitCode == 0,
		Route:       &routeCopy,
		ContainerName: "gateway",
		Command:     append([]string{"docker"}, commandArgs...),
		Stdout:      result.Stdout,
		Stderr:      result.Stderr,
		ExitCode:    result.ExitCode,
	}, nil
}

func (m *Manager) ServicePassthrough(ctx context.Context, route *cli.Route) (cli.CommandResult, error) {
	commandArgs := append([]string{"exec", route.Resource, route.Resource}, route.NativeArgs...)
	result, err := m.runner.Run(ctx, "", "docker", commandArgs...)
	if err != nil {
		return cli.CommandResult{}, err
	}

	return cli.CommandResult{
		OK:            result.ExitCode == 0,
		Route:         route,
		ContainerName: route.Resource,
		Command:       append([]string{"docker"}, commandArgs...),
		Stdout:        result.Stdout,
		Stderr:        result.Stderr,
		ExitCode:      result.ExitCode,
	}, nil
}

func (m *Manager) RuntimeOpenClaw(ctx context.Context, route *cli.Route) (cli.CommandResult, error) {
	commandArgs := append([]string{"exec", route.Runtime, "openclaw"}, route.NativeArgs...)
	result, err := m.runner.Run(ctx, "", "docker", commandArgs...)
	if err != nil {
		return cli.CommandResult{}, err
	}

	return cli.CommandResult{
		OK:            result.ExitCode == 0,
		Route:         route,
		ContainerName: route.Runtime,
		Command:       append([]string{"docker"}, commandArgs...),
		Stdout:        result.Stdout,
		Stderr:        result.Stderr,
		ExitCode:      result.ExitCode,
	}, nil
}

func (m *Manager) RuntimeReload(ctx context.Context, route *cli.Route) (cli.ServiceActionResult, error) {
	service := route.Runtime
	return m.RestartService(ctx, route, service)
}

func (m *Manager) LoadServiceDefinition(service string) (ServiceDefinition, error) {
	path := filepath.Join(m.config.ServicesRepoRoot(), "services", service, "service.yaml")
	data, err := os.ReadFile(path)
	if err != nil {
		return ServiceDefinition{}, fmt.Errorf("read service definition for %s: %w", service, err)
	}

	var (
		definition ServiceDefinition
		inContainers bool
	)

	for _, raw := range strings.Split(string(data), "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "container_names:") {
			inContainers = true
			continue
		}
		if inContainers && strings.HasPrefix(line, "- ") {
			definition.ContainerNames = append(definition.ContainerNames, strings.TrimSpace(strings.TrimPrefix(line, "- ")))
			continue
		}
		inContainers = false
		key, value, ok := splitYAMLLine(line)
		if !ok {
			continue
		}
		switch key {
		case "compose_project":
			definition.ComposeProject = value
		case "runtime_required":
			definition.RuntimeRequired = parseBool(value)
		case "build_on_deploy":
			definition.BuildOnDeploy = parseBool(value)
		case "skip_pull":
			definition.SkipPull = parseBool(value)
		}
	}

	if definition.ComposeProject == "" {
		return ServiceDefinition{}, fmt.Errorf("service %s is missing compose_project", service)
	}
	if len(definition.ContainerNames) == 0 {
		return ServiceDefinition{}, fmt.Errorf("service %s is missing container_names", service)
	}

	return definition, nil
}

func (m *Manager) RenderServiceAssets(service string, definition ServiceDefinition) (string, []string, error) {
	serviceRoot := filepath.Join(m.config.ServicesRepoRoot(), "services", service)
	outputDir := m.config.ServiceStateDir(service)
	if err := os.MkdirAll(outputDir, 0o755); err != nil {
		return "", nil, fmt.Errorf("create service state dir for %s: %w", service, err)
	}

	if err := m.copyServiceAssets(serviceRoot, outputDir); err != nil {
		return "", nil, err
	}
	if err := m.renderConfigAssets(service, outputDir); err != nil {
		return "", nil, err
	}
	if err := m.renderCompose(service, definition, serviceRoot, outputDir); err != nil {
		return "", nil, err
	}

	args := []string{
		"compose",
		"-f",
		filepath.Join(outputDir, "compose.yml"),
		"-p",
		definition.ComposeProject,
		"up",
		"-d",
		"--remove-orphans",
	}
	if definition.BuildOnDeploy {
		args = append(args, "--build")
	}

	return outputDir, args, nil
}

func (m *Manager) copyServiceAssets(serviceRoot, outputDir string) error {
	return filepath.Walk(serviceRoot, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			return nil
		}

		relative, err := filepath.Rel(serviceRoot, path)
		if err != nil {
			return err
		}
		if relative == "service.yaml" || relative == "compose.yml.template" {
			return nil
		}

		destination := filepath.Join(outputDir, relative)
		if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
			return err
		}

		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(destination, data, 0o644)
	})
}

func (m *Manager) renderConfigAssets(service, outputDir string) error {
	switch service {
	case "gateway":
		source := filepath.Join(m.config.RuntimeRepoRoot(), "gateway", "config.yaml")
		return copyFile(source, filepath.Join(outputDir, "config", "gateway", "config.yaml"))
	case "opensearch":
		targetRoot := filepath.Join(outputDir, "config", "opensearch")
		if err := os.MkdirAll(targetRoot, 0o755); err != nil {
			return err
		}
		if err := copyFile(filepath.Join(m.config.RuntimeRepoRoot(), "opensearch", "opensearch.yml"), filepath.Join(targetRoot, "opensearch.yml")); err != nil {
			return err
		}
		if err := copyFile(filepath.Join(m.config.RuntimeRepoRoot(), "opensearch", "container.env"), filepath.Join(targetRoot, "container.env")); err != nil {
			return err
		}
		return os.WriteFile(filepath.Join(targetRoot, ".env"), []byte(""), 0o644)
	case "caddy":
		context := m.renderContext(service)
		source := filepath.Join(m.config.RuntimeRepoRoot(), "caddy", "Caddyfile.template")
		destination := filepath.Join(outputDir, "config", "caddy", "Caddyfile")
		if err := renderFile(source, destination, context); err != nil {
			return err
		}
		return ensureCaddyTLSAssets(filepath.Join(outputDir, "config", "caddy", "certs"))
	case "ollama":
		modelsDir := filepath.Join(outputDir, "shared", "models")
		return os.MkdirAll(modelsDir, 0o755)
	case "openclaw-dev", "openclaw-test", "openclaw-prod":
		return m.renderRuntimeTree(service, filepath.Join(m.config.RuntimeRepoRoot(), service), filepath.Join(outputDir, "config", service))
	default:
		return nil
	}
}

func (m *Manager) renderRuntimeTree(service, sourceRoot, destinationRoot string) error {
	context := m.renderContext(service)
	if err := os.MkdirAll(destinationRoot, 0o755); err != nil {
		return err
	}
	if err := os.MkdirAll(m.config.RuntimeComponentDir(service), 0o755); err != nil {
		return err
	}

	return filepath.Walk(sourceRoot, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			return nil
		}
		relative, err := filepath.Rel(sourceRoot, path)
		if err != nil {
			return err
		}
		destination := filepath.Join(destinationRoot, relative)
		if strings.HasSuffix(destination, ".template") {
			destination = strings.TrimSuffix(destination, ".template")
			return renderFile(path, destination, context)
		}
		return copyFile(path, destination)
	})
}

func (m *Manager) renderCompose(service string, definition ServiceDefinition, serviceRoot, outputDir string) error {
	context := m.renderContext(service)
	context["container_name"] = definition.ContainerNames[0]
	context["selected_artifact"] = "latest"
	templatePath := filepath.Join(serviceRoot, "compose.yml.template")
	return renderFile(templatePath, filepath.Join(outputDir, "compose.yml"), context)
}

func (m *Manager) renderContext(service string) map[string]string {
	profile := strings.TrimPrefix(service, "openclaw-")
	context := map[string]string{
		"service_name":            service,
		"profile":                 profile,
		"gateway_port":            fmt.Sprintf("%d", runtimeGatewayPort(service)),
		"state_root":              m.config.Paths.StateRoot,
		"runtime_root":            m.config.Paths.RuntimeRoot,
		"logs_root":               m.config.Paths.LogsRoot,
		"runtime_component_dir":   m.config.RuntimeComponentDir(service),
		"internal_network_name":   internalNetworkName,
		"gateway_container_name":  "gateway",
		"gateway_container_port":  fmt.Sprintf("%d", m.config.Gateway.Port),
		"shared_root":             filepath.Join(m.config.ServiceStateDir(service), "shared"),
	}
	return context
}

func (m *Manager) ensureNetwork(ctx context.Context) error {
	result, err := m.runner.Run(ctx, "", "docker", "network", "inspect", internalNetworkName)
	if err != nil {
		return err
	}
	if result.ExitCode == 0 {
		return nil
	}
	createResult, err := m.runner.Run(ctx, "", "docker", "network", "create", internalNetworkName)
	if err != nil {
		return err
	}
	if createResult.ExitCode != 0 {
		return fmt.Errorf("create docker network %s failed: %s", internalNetworkName, createResult.Stdout)
	}
	return nil
}

func (m *Manager) waitForContainers(ctx context.Context, names []string, timeout time.Duration) ([]cli.ServiceContainerStatus, error) {
	deadline := time.Now().Add(timeout)
	for {
		containers, err := m.inspectContainers(ctx, names)
		if err != nil {
			return nil, err
		}
		ready := len(containers) > 0
		for _, container := range containers {
			if !container.Present || !container.Running {
				ready = false
				break
			}
			if container.Health != "" && container.Health != "healthy" {
				ready = false
				break
			}
		}
		if ready {
			return containers, nil
		}
		if time.Now().After(deadline) {
			return containers, fmt.Errorf("containers not ready before timeout")
		}
		select {
		case <-ctx.Done():
			return containers, ctx.Err()
		case <-time.After(2 * time.Second):
		}
	}
}

func (m *Manager) inspectContainers(ctx context.Context, names []string) ([]cli.ServiceContainerStatus, error) {
	containers := make([]cli.ServiceContainerStatus, 0, len(names))
	for _, name := range names {
		info, err := m.inspector.InspectContainer(ctx, name)
		if err != nil {
			if err == docker.ErrContainerNotFound {
				containers = append(containers, cli.ServiceContainerStatus{Name: name, Present: false, ContainerName: name})
				continue
			}
			return nil, fmt.Errorf("inspect container %s: %w", name, err)
		}

		status := cli.ServiceContainerStatus{
			Name:          name,
			Present:       true,
			ContainerName: strings.TrimPrefix(info.Name, "/"),
			Image:         info.Config.Image,
			Status:        info.State.Status,
			Running:       info.State.Running,
		}
		if info.State.Health != nil {
			status.Health = info.State.Health.Status
		}
		containers = append(containers, status)
	}
	return containers, nil
}

func copyFile(source, destination string) error {
	data, err := os.ReadFile(source)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return err
	}
	return os.WriteFile(destination, data, 0o644)
}

func renderFile(source, destination string, context map[string]string) error {
	data, err := os.ReadFile(source)
	if err != nil {
		return err
	}
	rendered := string(data)
	for key, value := range context {
		rendered = strings.ReplaceAll(rendered, "{{ "+key+" }}", value)
		rendered = strings.ReplaceAll(rendered, "{{"+key+"}}", value)
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return err
	}
	return os.WriteFile(destination, []byte(rendered), 0o644)
}

func runtimeGatewayPort(service string) int {
	switch service {
	case "openclaw-dev":
		return 18790
	case "openclaw-test":
		return 28789
	case "openclaw-prod":
		return 38789
	default:
		return 7460
	}
}

func splitYAMLLine(line string) (string, string, bool) {
	index := strings.Index(line, ":")
	if index < 0 {
		return "", "", false
	}
	key := strings.TrimSpace(line[:index])
	value := strings.Trim(strings.TrimSpace(line[index+1:]), `"'`)
	return key, value, key != ""
}

func parseBool(value string) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func ensureCaddyTLSAssets(certsDir string) error {
	certPath := filepath.Join(certsDir, "local.crt")
	keyPath := filepath.Join(certsDir, "local.key")

	if fileExists(certPath) && fileExists(keyPath) {
		return nil
	}
	if err := os.MkdirAll(certsDir, 0o755); err != nil {
		return err
	}

	privateKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return fmt.Errorf("generate caddy tls key: %w", err)
	}

	serialNumberLimit := new(big.Int).Lsh(big.NewInt(1), 128)
	serialNumber, err := rand.Int(rand.Reader, serialNumberLimit)
	if err != nil {
		return fmt.Errorf("generate caddy tls serial: %w", err)
	}

	now := time.Now().UTC()
	template := &x509.Certificate{
		SerialNumber: serialNumber,
		Subject: pkix.Name{
			CommonName:   "moltbox local tls",
			Organization: []string{"Moltbox"},
		},
		NotBefore:             now.Add(-1 * time.Hour),
		NotAfter:              now.AddDate(5, 0, 0),
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
		DNSNames: []string{
			"moltbox-cli",
			"moltbox-dev",
			"moltbox-test",
			"moltbox-prod",
		},
	}

	derBytes, err := x509.CreateCertificate(rand.Reader, template, template, privateKey.Public(), privateKey)
	if err != nil {
		return fmt.Errorf("generate caddy tls certificate: %w", err)
	}

	certBytes := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: derBytes})
	privateKeyBytes, err := x509.MarshalECPrivateKey(privateKey)
	if err != nil {
		return fmt.Errorf("marshal caddy tls key: %w", err)
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: privateKeyBytes})

	if err := os.WriteFile(certPath, certBytes, 0o644); err != nil {
		return err
	}
	if err := os.WriteFile(keyPath, keyPEM, 0o600); err != nil {
		return err
	}
	return nil
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}
