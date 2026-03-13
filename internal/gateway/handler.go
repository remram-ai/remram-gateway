package gateway

import (
	"fmt"

	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

func Payload(route *cli.Route) any {
	switch route.Kind {
	case cli.KindGateway:
		return cli.NotImplemented(
			route,
			fmt.Sprintf("gateway %s is not implemented in phase 1", route.Action),
			"phase 1 only boots the direct localhost gateway control channel",
		)
	case cli.KindGatewayService:
		return cli.NotImplemented(
			route,
			fmt.Sprintf("gateway service %s %s is not implemented in phase 1", route.Action, route.Subject),
			"phase 1 only provides gateway status, service status, and Docker connectivity",
		)
	default:
		return cli.Error(route, "parse_error", "unsupported gateway route", "use a documented gateway command")
	}
}
