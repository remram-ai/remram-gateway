package main

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/remram-ai/moltbox-gateway/internal/client"
	appconfig "github.com/remram-ai/moltbox-gateway/internal/config"
	"github.com/remram-ai/moltbox-gateway/internal/secrets"
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

	if result.Route != nil && result.Route.Kind == cli.KindScopedSecrets {
		cfg, err := appconfig.Load(appconfig.ConfigPath())
		if err != nil {
			_ = cli.WriteJSON(stdout, cli.Error(
				result.Route,
				"config_load_failed",
				fmt.Sprintf("failed to load gateway config from %s", appconfig.ConfigPath()),
				err.Error(),
			))
			return cli.ExitFailure
		}

		secretValue := ""
		if result.Route.Action == "set" {
			secretValue, err = loadSecretValue(os.Stdin)
			if err != nil {
				_ = cli.WriteJSON(stdout, cli.Error(
					result.Route,
					"secret_input_missing",
					"failed to read secret input",
					err.Error(),
				))
				return cli.ExitFailure
			}
		}

		handler := secrets.NewHandler(cfg.Paths.SecretsRoot)
		payload := handler.Execute(result.Route, secretValue)
		var buffer bytes.Buffer
		if err := cli.WriteJSON(&buffer, payload); err != nil {
			return cli.ExitFailure
		}
		if _, err := stdout.Write(buffer.Bytes()); err != nil {
			return cli.ExitFailure
		}
		return cli.ExitCodeFromPayload(buffer.Bytes())
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

func loadSecretValue(stdin io.Reader) (string, error) {
	if value, ok := os.LookupEnv("MOLTBOX_SECRET_VALUE"); ok && value != "" {
		return value, nil
	}

	reader := bufio.NewReader(stdin)
	data, err := reader.ReadString('\n')
	if err != nil && err != io.EOF {
		return "", err
	}
	value := strings.TrimRight(data, "\r\n")
	if value == "" {
		return "", fmt.Errorf("pipe the secret value on stdin or set MOLTBOX_SECRET_VALUE")
	}
	return value, nil
}
