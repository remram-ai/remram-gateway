package orchestrator

import (
	"context"
	"crypto/x509"
	"encoding/pem"
	"os"
	"path/filepath"
	"reflect"
	"sort"
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

type fakeSecretResolver struct {
	values map[string]map[string]string
}

func (f fakeSecretResolver) Resolve(scope string, names []string) (map[string]string, error) {
	resolved := make(map[string]string, len(names))
	for _, name := range names {
		if value, ok := f.values[scope][name]; ok {
			resolved[name] = value
		}
	}
	return resolved, nil
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
	}, fakeInspector{}, &fakeRunner{}, nil)

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

func TestRenderServiceAssetsWritesScopedSecretEnvFile(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	servicesRoot := filepath.Join(root, "services-repo")
	runtimeRoot := filepath.Join(root, "runtime-repo")
	stateRoot := filepath.Join(root, "state")
	skillsRoot := filepath.Join(root, "skills-repo")

	mustWriteFile(t, filepath.Join(servicesRoot, "services", "openclaw-dev", "service.yaml"), "compose_project: openclaw-dev\ncontainer_names:\n  - openclaw-dev\nruntime_required: true\n")
	mustWriteFile(t, filepath.Join(servicesRoot, "services", "openclaw-dev", "compose.yml.template"), "services:\n  {{ service_name }}:\n    environment:\n      TOGETHER_API_KEY: \"${TOGETHER_API_KEY:-}\"\n")
	mustWriteFile(t, filepath.Join(runtimeRoot, "openclaw-dev", "openclaw.json.template"), "{}\n")
	mustWriteFile(t, filepath.Join(skillsRoot, "skills", "together-escalation", "SKILL.md"), "---\nname: together-escalation\ndescription: test\n---\n")

	manager := NewManager(appconfig.Config{
		Paths: appconfig.PathsConfig{
			StateRoot:   stateRoot,
			RuntimeRoot: filepath.Join(root, "runtime-state"),
			LogsRoot:    filepath.Join(root, "logs"),
			SecretsRoot: filepath.Join(root, "secrets"),
		},
		Repos: appconfig.ReposConfig{
			Services: appconfig.RepoConfig{URL: servicesRoot},
			Runtime:  appconfig.RepoConfig{URL: runtimeRoot},
			Skills:   appconfig.RepoConfig{URL: skillsRoot},
		},
		Gateway: appconfig.GatewayConfig{Host: "0.0.0.0", Port: 7460},
	}, fakeInspector{}, &fakeRunner{}, fakeSecretResolver{
		values: map[string]map[string]string{
			"dev": {
				"TOGETHER_API_KEY": "scoped-dev-secret",
			},
		},
	})

	definition, err := manager.LoadServiceDefinition("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadServiceDefinition() error = %v", err)
	}

	outputDir, _, err := manager.RenderServiceAssets("openclaw-dev", definition)
	if err != nil {
		t.Fatalf("RenderServiceAssets() error = %v", err)
	}

	envData, err := os.ReadFile(filepath.Join(outputDir, ".env"))
	if err != nil {
		t.Fatalf("read rendered .env: %v", err)
	}
	if !strings.Contains(string(envData), `TOGETHER_API_KEY="scoped-dev-secret"`) {
		t.Fatalf("rendered .env missing scoped secret: %s", envData)
	}

	if _, err := os.Stat(filepath.Join(root, "runtime-state", "openclaw-dev", "skills", "together-escalation", "SKILL.md")); err != nil {
		t.Fatalf("staged skill missing from runtime state: %v", err)
	}
}

func TestRenderServiceAssetsSupportsEnvironmentAlias(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	servicesRoot := filepath.Join(root, "services-repo")
	runtimeRoot := filepath.Join(root, "runtime-repo")
	stateRoot := filepath.Join(root, "state")

	mustWriteFile(t, filepath.Join(servicesRoot, "services", "openclaw-dev", "service.yaml"), "compose_project: openclaw-dev\ncontainer_names:\n  - openclaw-dev\nruntime_required: true\n")
	mustWriteFile(t, filepath.Join(servicesRoot, "services", "openclaw-dev", "compose.yml.template"), "services:\n  {{ service_name }}:\n    container_name: \"{{ container_name }}\"\n")
	mustWriteFile(t, filepath.Join(runtimeRoot, "openclaw-dev", "openclaw.json.template"), "{}\n")

	manager := NewManager(appconfig.Config{
		Paths: appconfig.PathsConfig{
			StateRoot:   stateRoot,
			RuntimeRoot: filepath.Join(root, "runtime-state"),
			LogsRoot:    filepath.Join(root, "logs"),
			SecretsRoot: filepath.Join(root, "secrets"),
		},
		Repos: appconfig.ReposConfig{
			Services: appconfig.RepoConfig{URL: servicesRoot},
			Runtime:  appconfig.RepoConfig{URL: runtimeRoot},
		},
		Gateway: appconfig.GatewayConfig{Host: "0.0.0.0", Port: 7460},
	}, fakeInspector{}, &fakeRunner{}, nil)

	definition, err := manager.LoadServiceDefinition("dev")
	if err != nil {
		t.Fatalf("LoadServiceDefinition(dev) error = %v", err)
	}

	outputDir, _, err := manager.RenderServiceAssets("dev", definition)
	if err != nil {
		t.Fatalf("RenderServiceAssets(dev) error = %v", err)
	}

	if !strings.HasSuffix(outputDir, filepath.Join("services", "openclaw-dev")) {
		t.Fatalf("RenderServiceAssets(dev) outputDir = %q, want openclaw-dev state dir", outputDir)
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
	mustWriteFile(t, filepath.Join(runtimeRoot, "caddy", "Caddyfile.template"), "(moltbox_cli_tls) {\n  tls /etc/caddy/certs/local.crt /etc/caddy/certs/local.key {\n    client_auth {\n      mode require_and_verify\n      trust_pool file /etc/caddy/certs/local.crt\n    }\n  }\n}\n\nhttps://moltbox-cli {\n  import moltbox_cli_tls\n}\n")

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
	}, fakeInspector{}, &fakeRunner{}, nil)

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
		filepath.Join("config", "caddy", "clients", "jason-cli", "jason-cli.crt"),
		filepath.Join("config", "caddy", "clients", "jason-cli", "jason-cli.key"),
	} {
		if _, err := os.Stat(filepath.Join(outputDir, relative)); err != nil {
			t.Fatalf("expected %s to exist: %v", relative, err)
		}
	}

	caddyfileBytes, err := os.ReadFile(filepath.Join(outputDir, "config", "caddy", "Caddyfile"))
	if err != nil {
		t.Fatalf("read rendered caddyfile: %v", err)
	}
	if !strings.Contains(string(caddyfileBytes), "https://moltbox-cli") {
		t.Fatalf("rendered caddyfile missing moltbox-cli route: %s", caddyfileBytes)
	}
	if !strings.Contains(string(caddyfileBytes), "mode require_and_verify") {
		t.Fatalf("rendered caddyfile missing client_auth requirement: %s", caddyfileBytes)
	}
	if !strings.Contains(string(caddyfileBytes), "trust_pool file /etc/caddy/certs/local.crt") {
		t.Fatalf("rendered caddyfile missing client_auth trust root: %s", caddyfileBytes)
	}

	certPath := filepath.Join(outputDir, "config", "caddy", "certs", "local.crt")
	if !certificateHasDNSNames(certPath, []string{"moltbox-cli", "moltbox-dev", "moltbox-test", "moltbox-prod"}) {
		t.Fatalf("rendered caddy cert missing required SANs")
	}

	rootCertificate := mustParseCertificate(t, certPath)
	if !hasUsage(rootCertificate, x509.ExtKeyUsageServerAuth) || !hasUsage(rootCertificate, x509.ExtKeyUsageClientAuth) {
		t.Fatalf("rendered Moltbox root cert EKUs = %v, want serverAuth and clientAuth", rootCertificate.ExtKeyUsage)
	}
	clientCertificate := mustParseCertificate(t, filepath.Join(outputDir, "config", "caddy", "clients", "jason-cli", "jason-cli.crt"))
	if clientCertificate.Subject.CommonName != "jason-cli" {
		t.Fatalf("client certificate CN = %q, want jason-cli", clientCertificate.Subject.CommonName)
	}
	if !reflect.DeepEqual(clientCertificate.Subject.Organization, []string{"Moltbox"}) {
		t.Fatalf("client certificate organization = %v, want [Moltbox]", clientCertificate.Subject.Organization)
	}
	if clientCertificate.CheckSignatureFrom(rootCertificate) != nil {
		t.Fatal("client certificate is not signed by the rendered Moltbox root")
	}
	if !hasClientAuthUsage(clientCertificate) {
		t.Fatal("client certificate missing clientAuth extended key usage")
	}
}

func TestRenderServiceAssetsForCaddyUsesExactCanonicalSANSet(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	servicesRoot := filepath.Join(root, "services-repo")
	runtimeRoot := filepath.Join(root, "runtime-repo")
	stateRoot := filepath.Join(root, "state")

	mustWriteFile(t, filepath.Join(servicesRoot, "services", "caddy", "service.yaml"), "compose_project: caddy\ncontainer_names:\n  - caddy\nruntime_required: true\n")
	mustWriteFile(t, filepath.Join(servicesRoot, "services", "caddy", "compose.yml.template"), "services:\n  caddy:\n    container_name: \"{{ container_name }}\"\n")
	mustWriteFile(t, filepath.Join(runtimeRoot, "caddy", "Caddyfile.template"), "(moltbox_cli_tls) {\n  tls /etc/caddy/certs/local.crt /etc/caddy/certs/local.key {\n    client_auth {\n      mode require_and_verify\n      trust_pool file /etc/caddy/certs/local.crt\n    }\n  }\n}\n\nhttps://moltbox-cli {\n  import moltbox_cli_tls\n}\n")

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
	}, fakeInspector{}, &fakeRunner{}, nil)

	definition, err := manager.LoadServiceDefinition("caddy")
	if err != nil {
		t.Fatalf("LoadServiceDefinition() error = %v", err)
	}

	outputDir, _, err := manager.RenderServiceAssets("caddy", definition)
	if err != nil {
		t.Fatalf("RenderServiceAssets() error = %v", err)
	}

	certificate := mustParseCertificate(t, filepath.Join(outputDir, "config", "caddy", "certs", "local.crt"))
	got := append([]string(nil), certificate.DNSNames...)
	want := []string{"moltbox-cli", "moltbox-dev", "moltbox-test", "moltbox-prod"}
	sort.Strings(got)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("caddy cert SANs = %v, want %v", got, want)
	}
	for _, unexpected := range []string{"moltbox-prime"} {
		for _, name := range certificate.DNSNames {
			if name == unexpected {
				t.Fatalf("caddy cert SANs unexpectedly include %q: %v", unexpected, certificate.DNSNames)
			}
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
	}, runner, nil)

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
	if got := strings.Join(runner.commands[1], " "); !strings.Contains(got, "compose") || !strings.Contains(got, "--build") || !strings.Contains(got, "--force-recreate") {
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
			Gateway:  appconfig.RepoConfig{URL: filepath.Join(root, "gateway-repo")},
			Services: appconfig.RepoConfig{URL: servicesRoot},
			Runtime:  appconfig.RepoConfig{URL: runtimeRoot},
		},
		Gateway: appconfig.GatewayConfig{Host: "0.0.0.0", Port: 7460},
		CLI: appconfig.CLIConfig{
			Path:       filepath.Join(root, "home", "jpekovitch", ".local", "bin", "moltbox"),
			ConfigPath: filepath.Join(root, "home", "jpekovitch", ".config", "moltbox", "config.yaml"),
		},
	}, fakeInspector{}, runner, nil)

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
	if !strings.Contains(got, "run -d --rm") || !strings.Contains(got, "moltbox-gateway:latest") || !strings.Contains(got, "golang:1.23-bookworm") || !strings.Contains(got, "/usr/local/go/bin/go build -buildvcs=false -o /out/moltbox") || !strings.Contains(got, "remote get-url origin") || !strings.Contains(got, "cp \"$STAGING_ROOT/moltbox\" \"$CLI_PATH\"") || !strings.Contains(got, "chown -R \"$CLI_OWNER\" \"$SECRETS_ROOT\"") {
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

func mustParseCertificate(t *testing.T, path string) *x509.Certificate {
	t.Helper()

	certificates := mustParseCertificates(t, path)
	return certificates[0]
}

func mustParseCertificates(t *testing.T, path string) []*x509.Certificate {
	t.Helper()

	pemBytes, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read cert %s: %v", path, err)
	}
	certificates := make([]*x509.Certificate, 0, 2)
	for len(pemBytes) > 0 {
		block, rest := pem.Decode(pemBytes)
		pemBytes = rest
		if block == nil {
			break
		}
		if block.Type != "CERTIFICATE" {
			continue
		}
		certificate, parseErr := x509.ParseCertificate(block.Bytes)
		if parseErr != nil {
			t.Fatalf("parse cert %s: %v", path, parseErr)
		}
		certificates = append(certificates, certificate)
	}
	if len(certificates) == 0 {
		t.Fatalf("decode cert %s: no PEM blocks", path)
	}
	return certificates
}

func hasClientAuthUsage(certificate *x509.Certificate) bool {
	return hasUsage(certificate, x509.ExtKeyUsageClientAuth)
}

func hasUsage(certificate *x509.Certificate, expected x509.ExtKeyUsage) bool {
	for _, usage := range certificate.ExtKeyUsage {
		if usage == expected {
			return true
		}
	}
	return false
}
