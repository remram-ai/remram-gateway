package main

import (
	"fmt"
	"os"

	appconfig "github.com/remram-ai/moltbox-gateway/internal/config"
	"github.com/remram-ai/moltbox-gateway/internal/gateway"
	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

func main() {
	cfg, err := appconfig.Load(appconfig.ConfigPath())
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(cli.ExitFailure)
	}

	server := gateway.NewServer(gateway.Config{
		AppConfig:        cfg,
		DockerSocketPath: cli.DockerSocketPath(),
	})

	if err := server.ListenAndServe(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(cli.ExitFailure)
	}
}
