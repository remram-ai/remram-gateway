package runtime

import (
	"fmt"
	"strings"

	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

func Payload(route *cli.Route) any {
	switch route.Kind {
	case cli.KindRuntimeAction:
		return cli.NotImplemented(
			route,
			fmt.Sprintf("%s %s is not implemented in phase 1", route.Environment, route.Action),
			"phase 2 will add runtime orchestration",
		)
	case cli.KindRuntimeNative:
		return cli.NotImplemented(
			route,
			fmt.Sprintf("%s openclaw passthrough is not implemented in phase 1 (requested: %s)", route.Environment, strings.Join(route.NativeArgs, " ")),
			"phase 2 will add runtime native passthrough execution",
		)
	default:
		return cli.Error(route, "parse_error", "unsupported runtime route", "use a documented environment command")
	}
}
