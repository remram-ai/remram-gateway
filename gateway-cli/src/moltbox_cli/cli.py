from __future__ import annotations

import argparse

from .commands.version import handle_version
from .errors import MoltboxCliError
from .jsonio import emit_json
from .v2_actions import (
    component_action,
    component_config_sync_action,
    gateway_action,
    service_deploy_action,
    service_doctor_action,
    service_inspect_action,
    service_lifecycle_action,
    service_list_action,
    service_logs_action,
    service_rollback_action,
    service_status_action,
    skill_deploy_action,
)


def _emit_and_exit(payload: dict[str, object]) -> None:
    emit_json(payload)
    raise SystemExit(int(payload.get("exit_code", 0)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moltbox",
        add_help=False,
        description="MoltBox control-plane CLI.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Primary namespaces:\n"
            "  moltbox gateway ...\n"
            "  moltbox service ...\n"
            "  moltbox skill deploy <skill>\n"
            "  moltbox openclaw ...\n"
            "  moltbox openclaw-dev ...\n"
            "  moltbox openclaw-test ...\n"
            "  moltbox openclaw-prod ..."
        ),
    )
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--config-path")
    parser.add_argument("--policy-path")
    parser.add_argument("--state-root")
    parser.add_argument("--runtime-artifacts-root")
    parser.add_argument("--services-repo-url")
    parser.add_argument("--runtime-repo-url")
    parser.add_argument("--skills-repo-url")
    parser.add_argument("--internal-host")
    parser.add_argument("--internal-port", type=int)
    parser.add_argument("--cli-path")
    parser.add_argument("component", nargs="?")
    parser.add_argument("command", nargs="?")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def _gateway_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moltbox gateway")
    parser.add_argument("verb", choices=["health", "inspect", "logs", "rollback", "status", "update"])
    return parser


def _service_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moltbox service")
    subparsers = parser.add_subparsers(dest="verb", required=True)
    subparsers.add_parser("list")
    for verb in ("inspect", "status", "logs", "deploy", "start", "stop", "restart", "rollback", "doctor"):
        command = subparsers.add_parser(verb)
        command.add_argument("service")
    return parser


def _skill_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moltbox skill")
    subparsers = parser.add_subparsers(dest="verb", required=True)
    deploy = subparsers.add_parser("deploy")
    deploy.add_argument("skill")
    return parser


def _component_parser(component_name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"moltbox {component_name}")
    subparsers = parser.add_subparsers(dest="verb", required=True)
    for verb in ("status", "inspect", "logs", "start", "stop", "restart", "reload", "doctor", "monitor"):
        subparsers.add_parser(verb)
    chat = subparsers.add_parser("chat")
    chat.add_argument("--message", required=True)
    chat.add_argument("--timeout-seconds", type=int, default=30)
    config = subparsers.add_parser("config")
    config_subparsers = config.add_subparsers(dest="config_verb", required=True)
    config_subparsers.add_parser("sync")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.version:
            emit_json(handle_version())
            return

        if args.help and args.component is None:
            parser.print_help()
            return
        if args.component is None:
            parser.print_help()
            raise SystemExit(2)
        if args.help and args.component == "gateway":
            _gateway_parser().print_help()
            return
        if args.help and args.component == "service":
            _service_parser().print_help()
            return
        if args.help and args.component == "skill":
            _skill_parser().print_help()
            return
        if args.help:
            _component_parser(args.component).print_help()
            return
        if args.command is None:
            parser.print_help()
            raise SystemExit(2)

        from .config import resolve_config

        config = resolve_config(args)
        dispatch_argv = [args.command, *args.args]

        if args.component == "gateway":
            gateway_args = _gateway_parser().parse_args(dispatch_argv)
            _emit_and_exit(gateway_action(config, gateway_args.verb))
            return

        if args.component == "service":
            service_args = _service_parser().parse_args(dispatch_argv)
            if service_args.verb == "list":
                _emit_and_exit(service_list_action(config))
                return
            if service_args.verb == "inspect":
                _emit_and_exit(service_inspect_action(config, service_args.service))
                return
            if service_args.verb == "status":
                _emit_and_exit(service_status_action(config, service_args.service))
                return
            if service_args.verb == "logs":
                _emit_and_exit(service_logs_action(config, service_args.service))
                return
            if service_args.verb == "deploy":
                _emit_and_exit(service_deploy_action(config, service_args.service))
                return
            if service_args.verb in {"start", "stop", "restart"}:
                _emit_and_exit(service_lifecycle_action(config, service_args.service, service_args.verb))
                return
            if service_args.verb == "rollback":
                _emit_and_exit(service_rollback_action(config, service_args.service))
                return
            if service_args.verb == "doctor":
                _emit_and_exit(service_doctor_action(config, service_args.service))
                return

        if args.component == "skill":
            skill_args = _skill_parser().parse_args(dispatch_argv)
            if skill_args.verb == "deploy":
                _emit_and_exit(skill_deploy_action(config, skill_args.skill))
                return

        component_args = _component_parser(args.component).parse_args(dispatch_argv)
        if component_args.verb == "config":
            if component_args.config_verb == "sync":
                _emit_and_exit(component_config_sync_action(config, args.component))
                return
        if component_args.verb == "chat":
            _emit_and_exit(
                component_action(
                    config,
                    args.component,
                    component_args.verb,
                    message=component_args.message,
                    timeout_seconds=component_args.timeout_seconds,
                )
            )
            return
        _emit_and_exit(component_action(config, args.component, component_args.verb))
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
