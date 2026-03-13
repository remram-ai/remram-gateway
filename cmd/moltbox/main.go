package main

import (
	"fmt"
	"io"
	"os"

	"github.com/remram-ai/moltbox-gateway/internal/client"
	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, _ io.Writer) int {
	result := cli.Parse(args)

	switch {
	case result.Help:
		_ = cli.WriteHelp(stdout)
		return cli.ExitOK
	case result.Version:
		_ = cli.WriteVersion(stdout)
		return cli.ExitOK
	case result.Envelope != nil:
		_ = cli.WriteJSON(stdout, result.Envelope)
		return result.Code
	}

	gatewayClient := client.NewHTTPClient(cli.GatewayURL())
	payload, err := gatewayClient.Execute(result.Route)
	if err != nil {
		_ = cli.WriteJSON(stdout, cli.Error(
			result.Route,
			"gateway_unreachable",
			fmt.Sprintf("failed to contact gateway at %s", cli.GatewayURL()),
			"verify the gateway container is running and the localhost control port is reachable",
		))
		return cli.ExitFailure
	}

	if _, err := stdout.Write(payload); err != nil {
		return cli.ExitFailure
	}

	return cli.ExitCodeFromPayload(payload)
}
