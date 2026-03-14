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
	"regexp"
	"sort"
	"strconv"
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

type SecretResolver interface {
	Resolve(scope string, names []string) (map[string]string, error)
}

type Manager struct {
	config         config.Config
	inspector      ContainerInspector
	runner         command.Runner
	secretResolver SecretResolver
}

type ServiceDefinition struct {
	ComposeProject  string
	ContainerNames  []string
	RuntimeRequired bool
	BuildOnDeploy   bool
	SkipPull        bool
}

func NewManager(cfg config.Config, inspector ContainerInspector, runner command.Runner, secretResolver SecretResolver) *Manager {
	return &Manager{
		config:         cfg,
		inspector:      inspector,
		runner:         runner,
		secretResolver: secretResolver,
	}
}

func (m *Manager) DeployService(ctx context.Context, route *cli.Route, service string) (cli.ServiceDeployResult, error) {
	canonicalService := canonicalServiceName(service)
	definition, err := m.LoadServiceDefinition(canonicalService)
	if err != nil {
		return cli.ServiceDeployResult{}, err
	}

	outputDir, commandArgs, err := m.RenderServiceAssets(canonicalService, definition)
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
		OK:             true,
		Route:          route,
		Service:        service,
		ComposeProject: definition.ComposeProject,
		OutputDir:      outputDir,
		Command:        append([]string{"docker"}, commandArgs...),
		Containers:     containers,
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

	repoRoot := m.config.GatewayRepoRoot()
	if strings.TrimSpace(repoRoot) == "" {
		return cli.ServiceActionResult{}, fmt.Errorf("gateway update requires repos.gateway.url in gateway config")
	}

	cliPath := strings.TrimSpace(m.config.CLI.Path)
	if !filepath.IsAbs(cliPath) {
		return cli.ServiceActionResult{}, fmt.Errorf("gateway update requires cli.path to be an absolute host path")
	}

	cliConfigPath := strings.TrimSpace(m.config.CLI.ConfigPath)
	if cliConfigPath == "" {
		cliConfigPath = defaultHostCLIConfigPath(cliPath)
	}
	if !filepath.IsAbs(cliConfigPath) {
		return cli.ServiceActionResult{}, fmt.Errorf("gateway update requires cli.config_path to be an absolute host path")
	}

	stagingRoot := filepath.Join(m.config.Paths.StateRoot, "updates", "gateway")
	configSource := filepath.Join(outputDir, "config", "gateway", "config.yaml")
	updateScript := buildGatewayUpdateScript(repoRoot, stagingRoot, cliPath, cliConfigPath, configSource, outputDir, definition.ComposeProject, m.config.Paths.SecretsRoot)
	commandArgs := gatewayUpdateHelperCommand(m.config, repoRoot, cliPath, cliConfigPath, updateScript)

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

func gatewayUpdateHelperCommand(cfg config.Config, repoRoot, cliPath, cliConfigPath, updateScript string) []string {
	cliWrapperPath := "/usr/local/bin/moltbox-cli-wrapper"
	bootstrapWrapperPath := "/usr/local/bin/moltbox-bootstrap-wrapper"
	systemConfigPath := "/etc/moltbox/config.yaml"
	commandArgs := []string{
		"run",
		"-d",
		"--rm",
		"--name",
		fmt.Sprintf("gateway-updater-%d", time.Now().Unix()),
		"--entrypoint",
		"sh",
	}

	for _, mount := range uniqueMountRoots(
		cfg.Paths.StateRoot,
		cfg.Paths.LogsRoot,
		cfg.Paths.SecretsRoot,
		repoRoot,
		filepath.Dir(cliPath),
		filepath.Dir(cliConfigPath),
		filepath.Dir(cliWrapperPath),
		filepath.Dir(bootstrapWrapperPath),
		filepath.Dir(systemConfigPath),
	) {
		commandArgs = append(commandArgs, "-v", fmt.Sprintf("%s:%s", mount, mount))
	}

	commandArgs = append(commandArgs,
		"-v", "/var/run/docker.sock:/var/run/docker.sock",
		"moltbox-gateway:latest",
		"-lc",
		updateScript,
	)
	return commandArgs
}

func buildGatewayUpdateScript(repoRoot, stagingRoot, cliPath, cliConfigPath, configSource, gatewayOutputDir, composeProject, secretsRoot string) string {
	cliWrapperSource := filepath.Join(repoRoot, "scripts", "moltbox-cli-wrapper.sh")
	cliWrapperPath := "/usr/local/bin/moltbox-cli-wrapper"
	bootstrapWrapperSource := filepath.Join(repoRoot, "scripts", "moltbox-bootstrap-wrapper.sh")
	bootstrapWrapperPath := "/usr/local/bin/moltbox-bootstrap-wrapper"
	sharedCLIPath := "/usr/local/bin/moltbox"
	systemConfigPath := "/etc/moltbox/config.yaml"
	return strings.Join([]string{
		"set -eu",
		fmt.Sprintf("REPO=%s", shellQuote(repoRoot)),
		fmt.Sprintf("STAGING_ROOT=%s", shellQuote(stagingRoot)),
		fmt.Sprintf("CLI_PATH=%s", shellQuote(cliPath)),
		fmt.Sprintf("CLI_CONFIG_PATH=%s", shellQuote(cliConfigPath)),
		fmt.Sprintf("CONFIG_SOURCE=%s", shellQuote(configSource)),
		fmt.Sprintf("GATEWAY_OUTPUT_DIR=%s", shellQuote(gatewayOutputDir)),
		fmt.Sprintf("COMPOSE_PROJECT=%s", shellQuote(composeProject)),
		fmt.Sprintf("SECRETS_ROOT=%s", shellQuote(secretsRoot)),
		fmt.Sprintf("CLI_WRAPPER_SOURCE=%s", shellQuote(cliWrapperSource)),
		fmt.Sprintf("CLI_WRAPPER_PATH=%s", shellQuote(cliWrapperPath)),
		fmt.Sprintf("BOOTSTRAP_WRAPPER_SOURCE=%s", shellQuote(bootstrapWrapperSource)),
		fmt.Sprintf("BOOTSTRAP_WRAPPER_PATH=%s", shellQuote(bootstrapWrapperPath)),
		fmt.Sprintf("SHARED_CLI_PATH=%s", shellQuote(sharedCLIPath)),
		fmt.Sprintf("SYSTEM_CONFIG_PATH=%s", shellQuote(systemConfigPath)),
		`mkdir -p "$STAGING_ROOT" "$(dirname "$CLI_PATH")" "$(dirname "$CLI_CONFIG_PATH")" "$(dirname "$SYSTEM_CONFIG_PATH")"`,
		`mkdir -p "$SECRETS_ROOT"`,
		`if [ -d "$REPO/.git" ] && git -C "$REPO" remote get-url origin >/dev/null 2>&1; then git -C "$REPO" fetch --all --tags --prune && git -C "$REPO" pull --ff-only; fi`,
		`docker run --rm -v "$REPO:/src" -v "$STAGING_ROOT:/out" -w /src golang:1.23-bookworm sh -lc 'set -eu; /usr/local/go/bin/go build -buildvcs=false -o /out/moltbox ./cmd/moltbox && /usr/local/go/bin/go build -buildvcs=false -o /out/gateway ./cmd/gateway'`,
		`cp "$STAGING_ROOT/moltbox" "$CLI_PATH"`,
		`chmod 0755 "$CLI_PATH"`,
		`cp "$STAGING_ROOT/moltbox" "$SHARED_CLI_PATH"`,
		`chmod 0755 "$SHARED_CLI_PATH"`,
		`cp "$CONFIG_SOURCE" "$CLI_CONFIG_PATH"`,
		`chmod 0644 "$CLI_CONFIG_PATH"`,
		`cp "$CONFIG_SOURCE" "$SYSTEM_CONFIG_PATH"`,
		`chmod 0644 "$SYSTEM_CONFIG_PATH"`,
		`sed "s|__MOLTBOX_CLI_PATH__|$SHARED_CLI_PATH|g" "$CLI_WRAPPER_SOURCE" > "$CLI_WRAPPER_PATH"`,
		`chmod 0755 "$CLI_WRAPPER_PATH"`,
		`sed "s|__MOLTBOX_CLI_PATH__|$SHARED_CLI_PATH|g" "$BOOTSTRAP_WRAPPER_SOURCE" > "$BOOTSTRAP_WRAPPER_PATH"`,
		`chmod 0755 "$BOOTSTRAP_WRAPPER_PATH"`,
		`CLI_OWNER="$(stat -c '%u:%g' "$(dirname "$CLI_PATH")")"`,
		`chown -R "$CLI_OWNER" "$SECRETS_ROOT"`,
		`find "$SECRETS_ROOT" -type d -exec chmod 0700 {} +`,
		`find "$SECRETS_ROOT" -type f -name '*.json' -exec chmod 0600 {} +`,
		`if [ -f "$SECRETS_ROOT/master.key" ]; then chmod 0600 "$SECRETS_ROOT/master.key"; fi`,
		`docker build -t moltbox-gateway:latest "$REPO"`,
		`docker rm -f gateway >/dev/null 2>&1 || true`,
		`cd "$GATEWAY_OUTPUT_DIR" && docker compose -f compose.yml -p "$COMPOSE_PROJECT" up -d --remove-orphans`,
	}, "; ")
}

func (m *Manager) RestartService(ctx context.Context, route *cli.Route, service string) (cli.ServiceActionResult, error) {
	deployResult, err := m.DeployService(ctx, route, service)
	if err != nil {
		return cli.ServiceActionResult{}, err
	}

	return cli.ServiceActionResult{
		OK:         true,
		Route:      route,
		Service:    service,
		Action:     route.Action,
		Command:    deployResult.Command,
		Containers: deployResult.Containers,
	}, nil
}

func (m *Manager) ServiceStatus(ctx context.Context, route *cli.Route, service string) (cli.ServiceStatusResult, error) {
	canonicalService := canonicalServiceName(service)
	definition, err := m.LoadServiceDefinition(canonicalService)
	if err != nil {
		return cli.ServiceStatusResult{}, err
	}

	containers, err := m.inspectContainers(ctx, definition.ContainerNames)
	if err != nil {
		return cli.ServiceStatusResult{}, err
	}

	result := cli.ServiceStatusResult{
		OK:             true,
		Route:          route,
		Service:        service,
		ComposeProject: definition.ComposeProject,
		Containers:     containers,
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
		OK:            result.ExitCode == 0,
		Route:         &routeCopy,
		ContainerName: "gateway",
		Command:       append([]string{"docker"}, commandArgs...),
		Stdout:        result.Stdout,
		Stderr:        result.Stderr,
		ExitCode:      result.ExitCode,
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
	deployResult, err := m.DeployService(ctx, route, service)
	if err != nil {
		return cli.ServiceActionResult{}, err
	}

	return cli.ServiceActionResult{
		OK:         true,
		Route:      route,
		Service:    service,
		Action:     route.Action,
		Command:    deployResult.Command,
		Containers: deployResult.Containers,
	}, nil
}

func (m *Manager) LoadServiceDefinition(service string) (ServiceDefinition, error) {
	service = canonicalServiceName(service)
	path := filepath.Join(m.config.ServicesRepoRoot(), "services", service, "service.yaml")
	data, err := os.ReadFile(path)
	if err != nil {
		return ServiceDefinition{}, fmt.Errorf("read service definition for %s: %w", service, err)
	}

	var (
		definition   ServiceDefinition
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
	service = canonicalServiceName(service)
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
	if err := m.renderComposeEnvFile(service, outputDir); err != nil {
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
		"--force-recreate",
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
		return ensureCaddyTLSAssets(
			filepath.Join(outputDir, "config", "caddy", "certs"),
		)
	case "ollama":
		modelsDir := filepath.Join(outputDir, "shared", "models")
		return os.MkdirAll(modelsDir, 0o755)
	case "openclaw-dev", "openclaw-test", "openclaw-prod":
		if err := m.renderRuntimeTree(service, filepath.Join(m.config.RuntimeRepoRoot(), service), filepath.Join(outputDir, "config", service)); err != nil {
			return err
		}
		return m.stageRuntimeSkills(service)
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

func (m *Manager) renderComposeEnvFile(service, outputDir string) error {
	if m.secretResolver == nil {
		return nil
	}

	composePath := filepath.Join(outputDir, "compose.yml")
	data, err := os.ReadFile(composePath)
	if err != nil {
		return fmt.Errorf("read rendered compose for %s: %w", service, err)
	}

	matches := regexp.MustCompile(`\$\{([A-Z0-9_]+)(?::-[^}]*)?\}`).FindAllStringSubmatch(string(data), -1)
	secretNames := make([]string, 0, len(matches))
	seen := make(map[string]struct{}, len(matches))
	for _, match := range matches {
		if len(match) < 2 {
			continue
		}
		name := match[1]
		if _, ok := seen[name]; ok {
			continue
		}
		seen[name] = struct{}{}
		secretNames = append(secretNames, name)
	}
	sort.Strings(secretNames)

	resolved, err := m.secretResolver.Resolve(secretScopeForService(service), secretNames)
	if err != nil {
		return fmt.Errorf("resolve compose secrets for %s: %w", service, err)
	}

	lines := make([]string, 0, len(secretNames))
	for _, name := range secretNames {
		value, ok := resolved[name]
		if !ok {
			continue
		}
		lines = append(lines, name+"="+strconv.Quote(value))
	}
	content := strings.Join(lines, "\n")
	if content != "" {
		content += "\n"
	}
	return os.WriteFile(filepath.Join(outputDir, ".env"), []byte(content), 0o600)
}

func (m *Manager) stageRuntimeSkills(service string) error {
	skillsRoot := filepath.Join(m.config.SkillsRepoRoot(), "skills")
	if strings.TrimSpace(m.config.SkillsRepoRoot()) == "" {
		return nil
	}

	entries, err := os.ReadDir(skillsRoot)
	if err != nil {
		if errorsIsNotExist(err) {
			return nil
		}
		return fmt.Errorf("read skills repo: %w", err)
	}

	destinationRoot := filepath.Join(m.config.RuntimeComponentDir(service), "skills")
	if err := os.MkdirAll(destinationRoot, 0o755); err != nil {
		return fmt.Errorf("create runtime skills dir for %s: %w", service, err)
	}

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		sourceDir := filepath.Join(skillsRoot, entry.Name())
		skillFile := filepath.Join(sourceDir, "SKILL.md")
		if info, err := os.Stat(skillFile); err != nil || info.IsDir() {
			if err == nil {
				continue
			}
			if errorsIsNotExist(err) {
				continue
			}
			return fmt.Errorf("stat skill %s: %w", entry.Name(), err)
		}

		if err := copyTree(sourceDir, filepath.Join(destinationRoot, entry.Name())); err != nil {
			return fmt.Errorf("stage skill %s for %s: %w", entry.Name(), service, err)
		}
	}

	return nil
}

func (m *Manager) renderContext(service string) map[string]string {
	profile := strings.TrimPrefix(service, "openclaw-")
	context := map[string]string{
		"service_name":           service,
		"profile":                profile,
		"gateway_port":           fmt.Sprintf("%d", runtimeGatewayPort(service)),
		"state_root":             m.config.Paths.StateRoot,
		"runtime_root":           m.config.Paths.RuntimeRoot,
		"logs_root":              m.config.Paths.LogsRoot,
		"secrets_root":           m.config.Paths.SecretsRoot,
		"runtime_component_dir":  m.config.RuntimeComponentDir(service),
		"internal_network_name":  internalNetworkName,
		"gateway_container_name": "gateway",
		"gateway_container_port": fmt.Sprintf("%d", m.config.Gateway.Port),
		"shared_root":            filepath.Join(m.config.ServiceStateDir(service), "shared"),
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

func canonicalServiceName(service string) string {
	switch service {
	case "dev":
		return "openclaw-dev"
	case "test":
		return "openclaw-test"
	case "prod":
		return "openclaw-prod"
	default:
		return service
	}
}

func secretScopeForService(service string) string {
	switch service {
	case "openclaw-dev":
		return "dev"
	case "openclaw-test":
		return "test"
	case "openclaw-prod":
		return "prod"
	default:
		return "service"
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

func defaultHostCLIConfigPath(cliPath string) string {
	cleaned := filepath.Clean(cliPath)
	suffix := filepath.Join(".local", "bin", "moltbox")
	if strings.HasSuffix(cleaned, suffix) {
		prefix := strings.TrimSuffix(cleaned, suffix)
		return filepath.Join(prefix, ".config", "moltbox", "config.yaml")
	}
	return filepath.Join(filepath.Dir(cleaned), "moltbox-config.yaml")
}

func uniqueMountRoots(paths ...string) []string {
	seen := map[string]struct{}{}
	ordered := make([]string, 0, len(paths))
	for _, value := range paths {
		cleaned := strings.TrimSpace(filepath.Clean(value))
		if cleaned == "" || cleaned == "." {
			continue
		}
		if _, ok := seen[cleaned]; ok {
			continue
		}
		seen[cleaned] = struct{}{}
		ordered = append(ordered, cleaned)
	}
	return ordered
}

func shellQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", `'"'"'`) + "'"
}

func ensureCaddyTLSAssets(certsDir string) error {
	certPath := filepath.Join(certsDir, "local.crt")
	keyPath := filepath.Join(certsDir, "local.key")
	requiredDNSNames := []string{
		"moltbox-cli",
		"moltbox-dev",
		"moltbox-test",
		"moltbox-prod",
	}

	if fileExists(certPath) && fileExists(keyPath) && certificateHasDNSNames(certPath, requiredDNSNames) {
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
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageClientAuth},
		BasicConstraintsValid: true,
		IsCA:                  true,
		DNSNames:              requiredDNSNames,
	}

	derBytes, err := x509.CreateCertificate(rand.Reader, template, template, privateKey.Public(), privateKey)
	if err != nil {
		return fmt.Errorf("generate caddy tls certificate: %w", err)
	}

	certBytes := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: derBytes})
	if err := os.WriteFile(certPath, certBytes, 0o644); err != nil {
		return err
	}
	if err := writeECDSAPrivateKey(keyPath, privateKey); err != nil {
		return err
	}
	return nil
}

func certificateHasDNSNames(certPath string, requiredNames []string) bool {
	pemData, err := os.ReadFile(certPath)
	if err != nil {
		return false
	}
	block, _ := pem.Decode(pemData)
	if block == nil {
		return false
	}
	certificate, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return false
	}
	if !certificate.IsCA {
		return false
	}
	if !hasExtendedKeyUsage(certificate, x509.ExtKeyUsageServerAuth) || !hasExtendedKeyUsage(certificate, x509.ExtKeyUsageClientAuth) {
		return false
	}
	available := make(map[string]struct{}, len(certificate.DNSNames))
	for _, name := range certificate.DNSNames {
		available[name] = struct{}{}
	}
	if len(available) != len(requiredNames) {
		return false
	}
	for _, name := range requiredNames {
		if _, ok := available[name]; !ok {
			return false
		}
	}
	return true
}

func loadCertificate(path string) (*x509.Certificate, error) {
	certificates, err := loadCertificates(path)
	if err != nil {
		return nil, err
	}
	if len(certificates) == 0 {
		return nil, fmt.Errorf("decode certificate %s: no PEM block", path)
	}
	return certificates[0], nil
}

func loadCertificates(path string) ([]*x509.Certificate, error) {
	pemData, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	certificates := make([]*x509.Certificate, 0, 2)
	for len(pemData) > 0 {
		var block *pem.Block
		block, pemData = pem.Decode(pemData)
		if block == nil {
			break
		}
		if block.Type != "CERTIFICATE" {
			continue
		}
		certificate, parseErr := x509.ParseCertificate(block.Bytes)
		if parseErr != nil {
			return nil, parseErr
		}
		certificates = append(certificates, certificate)
	}
	if len(certificates) == 0 {
		return nil, fmt.Errorf("decode certificate %s: no PEM block", path)
	}
	return certificates, nil
}

func writeECDSAPrivateKey(path string, privateKey *ecdsa.PrivateKey) error {
	privateKeyBytes, err := x509.MarshalECPrivateKey(privateKey)
	if err != nil {
		return fmt.Errorf("marshal ecdsa key: %w", err)
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: privateKeyBytes})
	return os.WriteFile(path, keyPEM, 0o600)
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

func hasExtendedKeyUsage(certificate *x509.Certificate, expected x509.ExtKeyUsage) bool {
	for _, usage := range certificate.ExtKeyUsage {
		if usage == expected {
			return true
		}
	}
	return false
}

func copyTree(source, destination string) error {
	return filepath.Walk(source, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}

		relative, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		target := filepath.Join(destination, relative)

		if info.IsDir() {
			return os.MkdirAll(target, 0o755)
		}

		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		return os.WriteFile(target, data, 0o644)
	})
}

func errorsIsNotExist(err error) bool {
	return err != nil && os.IsNotExist(err)
}
