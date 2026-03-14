package gateway

import (
	"net/http"
	"time"

	"github.com/remram-ai/moltbox-gateway/internal/command"
	appconfig "github.com/remram-ai/moltbox-gateway/internal/config"
	"github.com/remram-ai/moltbox-gateway/internal/docker"
	"github.com/remram-ai/moltbox-gateway/internal/localexec"
	"github.com/remram-ai/moltbox-gateway/internal/mcpstdio"
	"github.com/remram-ai/moltbox-gateway/internal/orchestrator"
	"github.com/remram-ai/moltbox-gateway/internal/secrets"
	"github.com/remram-ai/moltbox-gateway/internal/tokens"
	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

type Config struct {
	AppConfig        appconfig.Config
	DockerSocketPath string
	Runner           command.Runner
}

type Server struct {
	listenAddress    string
	dockerSocketPath string
	dockerClient     *docker.Client
	appConfig        appconfig.Config
	orchestrator     *orchestrator.Manager
	secretHandler    *secrets.Handler
	tokenManager     *tokens.Manager
	mcpServer        *mcpstdio.Server
}

func NewServer(config Config) *Server {
	appCfg := config.AppConfig
	defaults := appconfig.Default()
	if appCfg.Paths.StateRoot == "" {
		appCfg.Paths.StateRoot = defaults.Paths.StateRoot
	}
	if appCfg.Paths.RuntimeRoot == "" {
		appCfg.Paths.RuntimeRoot = defaults.Paths.RuntimeRoot
	}
	if appCfg.Paths.LogsRoot == "" {
		appCfg.Paths.LogsRoot = defaults.Paths.LogsRoot
	}
	if appCfg.Paths.SecretsRoot == "" {
		appCfg.Paths.SecretsRoot = defaults.Paths.SecretsRoot
	}
	if appCfg.Gateway.Host == "" {
		appCfg.Gateway.Host = defaults.Gateway.Host
	}
	if appCfg.Gateway.Port == 0 {
		appCfg.Gateway.Port = defaults.Gateway.Port
	}
	if appCfg.CLI.Path == "" {
		appCfg.CLI.Path = defaults.CLI.Path
	}

	listenAddress := appCfg.ListenAddress()
	if listenAddress == "" {
		listenAddress = cli.DefaultGatewayListenAddr
	}

	dockerSocketPath := config.DockerSocketPath
	if dockerSocketPath == "" {
		dockerSocketPath = cli.DefaultDockerSocket
	}
	runner := config.Runner
	if runner == nil {
		defaultRunner := command.NewExecRunner()
		runner = defaultRunner
	}

	dockerClient := docker.NewClient(dockerSocketPath)
	secretHandler := secrets.NewHandler(appCfg.Paths.SecretsRoot)
	tokenManager := tokens.NewManager(appCfg.Paths.SecretsRoot)
	executor := localexec.New(appconfig.ConfigPath(), "http://127.0.0.1:7460")

	return &Server{
		listenAddress:    listenAddress,
		dockerSocketPath: dockerSocketPath,
		dockerClient:     dockerClient,
		appConfig:        appCfg,
		orchestrator:     orchestrator.NewManager(appCfg, dockerClient, runner, secretHandler),
		secretHandler:    secretHandler,
		tokenManager:     tokenManager,
		mcpServer:        mcpstdio.New(executor),
	}
}

func (s *Server) ListenAndServe() error {
	httpServer := &http.Server{
		Addr:              s.listenAddress,
		Handler:           s.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
	}

	return httpServer.ListenAndServe()
}
