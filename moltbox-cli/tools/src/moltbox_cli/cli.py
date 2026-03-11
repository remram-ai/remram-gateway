from __future__ import annotations

import argparse

from .commands.version import handle_version
from .errors import MoltboxCliError, TargetNotFoundError
from .jsonio import emit_json
from .target_resolution import HOST_TARGETS, RUNTIME_TARGETS, resolve_target_identifier


RUNTIME_VERBS = {"deploy", "rollback", "status", "inspect", "logs", "start", "stop", "restart"}
HOST_VERBS = {"deploy", "rollback", "status", "inspect", "logs", "start", "stop", "restart"}
TOOLS_VERBS = {"version", "health", "serve", "status", "inspect", "update", "rollback", "logs"}


def _emit_and_exit(payload: dict[str, object]) -> None:
    emit_json(payload)
    raise SystemExit(int(payload.get("exit_code", 0)))


def _resolve_host_target(target: str) -> str:
    resolved = resolve_target_identifier(target)
    if resolved not in HOST_TARGETS:
        raise TargetNotFoundError(target)
    return resolved


def _resolve_runtime_target(environment: str) -> str:
    resolved = resolve_target_identifier(environment)
    if resolved not in RUNTIME_TARGETS:
        raise TargetNotFoundError(environment)
    return resolved


def _dispatch_target_action(config, target: str, verb: str) -> None:  # noqa: ANN001
    if verb in {"status", "inspect"}:
        from .commands.status import handle_status

        _emit_and_exit(handle_status(config, target))
    if verb in {"start", "stop", "restart"}:
        from .deployment_service import host_lifecycle

        _emit_and_exit(host_lifecycle(config, target, verb))
    if verb in {"deploy", "update"}:
        from .commands.deploy import handle_deploy

        _emit_and_exit(handle_deploy(config, target))
    if verb == "rollback":
        from .commands.rollback import handle_rollback

        _emit_and_exit(handle_rollback(config, target))
    if verb == "logs":
        from .deployment_service import read_target_logs

        _emit_and_exit(read_target_logs(config, target))
    raise MoltboxCliError(
        error_type="unsupported_command",
        error_message=f"unsupported verb '{verb}'",
        recovery_message="run `moltbox --help` to see available commands",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moltbox", description="Canonical MoltBox operator CLI.")
    parser.add_argument("--config-path")
    parser.add_argument("--policy-path")
    parser.add_argument("--state-root")
    parser.add_argument("--runtime-artifacts-root")
    parser.add_argument("--internal-host")
    parser.add_argument("--internal-port", type=int)
    parser.add_argument("--cli-path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    tools = subparsers.add_parser("tools", help="Operate the MoltBox CLI tooling.")
    tools.add_argument("verb", choices=sorted(TOOLS_VERBS), help="Tooling verb.")

    host = subparsers.add_parser("host", help="Operate host services on the MoltBox machine.")
    host.add_argument("service", help="Host service such as ssl, ollama, or opensearch.")
    host.add_argument("verb", choices=sorted(HOST_VERBS), help="Host-service verb.")

    runtime = subparsers.add_parser("runtime", help="Operate OpenClaw runtime environments.")
    runtime.add_argument("environment", choices=sorted(RUNTIME_TARGETS), help="Runtime environment.")
    runtime.add_argument("verb", choices=sorted(RUNTIME_VERBS), help="Runtime verb.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        from .config import resolve_config

        config = resolve_config(args)

        if args.command == "tools":
            if args.verb == "version":
                emit_json(handle_version())
                return
            if args.verb == "health":
                from .commands.health import handle_health

                emit_json(handle_health(config))
                return
            if args.verb == "serve":
                from .commands.serve import handle_serve

                handle_serve(config)
                return
            if args.verb == "inspect":
                from .commands.targets import handle_list_targets

                emit_json(handle_list_targets(config))
                return
            _dispatch_target_action(config, "tools", args.verb)
            return

        if args.command == "host":
            resolved = _resolve_host_target(args.service)
            _dispatch_target_action(config, resolved, args.verb)
            return

        if args.command == "runtime":
            from .commands.runtime import handle_runtime

            resolved = _resolve_runtime_target(args.environment)
            if args.verb in {"start", "stop", "restart"}:
                _emit_and_exit(handle_runtime(config, resolved, args.verb))
                return
            _dispatch_target_action(config, resolved, args.verb)
            return

        raise MoltboxCliError(
            error_type="unsupported_command",
            error_message=f"unsupported command '{args.command}'",
            recovery_message="run `moltbox --help` to see available commands",
        )
    except MoltboxCliError as exc:
        emit_json(exc.to_payload())
        raise SystemExit(exc.exit_code) from exc
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001
        emit_json(
            MoltboxCliError(
                error_type="execution_failure",
                error_message=str(exc),
                recovery_message="inspect the MoltBox CLI logs and rerun the command",
            ).to_payload()
        )
        raise SystemExit(1) from exc
