package gateway

import (
	"net/http"
	"time"

	"github.com/remram-ai/moltbox-gateway/internal/docker"
	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

type Config struct {
	ListenAddress    string
	DockerSocketPath string
}

type Server struct {
	listenAddress    string
	dockerSocketPath string
	dockerClient     *docker.Client
}

func NewServer(config Config) *Server {
	listenAddress := config.ListenAddress
	if listenAddress == "" {
		listenAddress = cli.DefaultGatewayListenAddr
	}

	dockerSocketPath := config.DockerSocketPath
	if dockerSocketPath == "" {
		dockerSocketPath = cli.DefaultDockerSocket
	}

	return &Server{
		listenAddress:    listenAddress,
		dockerSocketPath: dockerSocketPath,
		dockerClient:     docker.NewClient(dockerSocketPath),
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
