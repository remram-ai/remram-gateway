package main

import (
	"fmt"
	"os"

	"github.com/remram-ai/moltbox-gateway/internal/gateway"
	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

func main() {
	server := gateway.NewServer(gateway.Config{
		ListenAddress:    cli.GatewayListenAddress(),
		DockerSocketPath: cli.DockerSocketPath(),
	})

	if err := server.ListenAndServe(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(cli.ExitFailure)
	}
}
