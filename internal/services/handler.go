package services

import (
	"fmt"
	"strings"

	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

func Payload(route *cli.Route) any {
	if route.Kind != cli.KindServiceNative {
		return cli.Error(route, "parse_error", "unsupported service route", "use a documented native service command")
	}

	return cli.NotImplemented(
		route,
		fmt.Sprintf("%s passthrough is not implemented in phase 1 (requested: %s)", route.Resource, strings.Join(route.NativeArgs, " ")),
		"phase 2 will add managed service passthrough execution",
	)
}
