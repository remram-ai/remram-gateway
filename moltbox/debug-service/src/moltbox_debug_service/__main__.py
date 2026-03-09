from __future__ import annotations

import argparse
import json
import uuid

from .service import build_service, run_server


def emit(result: dict) -> None:
    print(json.dumps(result, indent=2))


def run_publish_flow(args: argparse.Namespace) -> int:
    service = build_service()

    try:
        if args.flow_command == "publish":
            runtime = "test" if args.mode == "test" else "prod"
            result = service._run_sync_operation(  # noqa: SLF001
                "publish_branch",
                runtime,
                lambda: service._publish_branch(  # noqa: SLF001
                    args.mode,
                    args.branch,
                    not args.no_validate,
                    args.exercise_script,
                    args.exercise_arg or [],
                    args.collect_diagnostics,
                    uuid.uuid4().hex,
                ),
            )
        elif args.flow_command == "finalize-test":
            result = service._run_sync_operation(  # noqa: SLF001
                "finalize_test_publish",
                "test",
                lambda: service._finalize_test_publish(  # noqa: SLF001
                    args.flow_id,
                    not args.no_resume_production,
                    not args.keep_test_runtime,
                    not args.no_collect_diagnostics,
                    uuid.uuid4().hex,
                ),
            )
        elif args.flow_command == "approve":
            result = service._approve_test_publish(args.flow_id, True, args.note)  # noqa: SLF001
        elif args.flow_command == "reject":
            result = service._approve_test_publish(args.flow_id, False, args.note)  # noqa: SLF001
        elif args.flow_command == "report":
            result = service._publish_report(args.flow_id)  # noqa: SLF001
        elif args.flow_command == "list":
            result = service._list_publish_runs()  # noqa: SLF001
        else:
            raise RuntimeError(f"Unsupported publish-flow command: {args.flow_command}")
    except Exception as exc:  # noqa: BLE001
        emit({"ok": False, "error": str(exc)})
        return 1

    emit(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Moltbox debug service.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the HTTP MCP debug service.")
    serve.add_argument("--host", help="Host/IP to bind.")
    serve.add_argument("--port", type=int, help="Port to bind.")

    flow = subparsers.add_parser("publish-flow", help="Run the deployment publish workflow from the host shell.")
    flow_subparsers = flow.add_subparsers(dest="flow_command", required=True)

    publish = flow_subparsers.add_parser("publish", help="Publish a git branch into the test or live workflow.")
    publish.add_argument("--mode", choices=["test", "live"], required=True, help="Publish mode.")
    publish.add_argument("--branch", required=True, help="Git branch to publish.")
    publish.add_argument("--no-validate", action="store_true", help="Skip validate.sh after bootstrap.")
    publish.add_argument("--exercise-script", help="Remote exec script to run in the test runtime after bootstrap.")
    publish.add_argument("--exercise-arg", action="append", help="Argument to pass to the remote exercise script.")
    publish.add_argument("--collect-diagnostics", action="store_true", help="Collect diagnostics during publish.")

    finalize = flow_subparsers.add_parser("finalize-test", help="Finalize a test publish run and resume prod.")
    finalize.add_argument("flow_id", help="Publish flow id.")
    finalize.add_argument("--no-resume-production", action="store_true", help="Do not resume prod after finishing the test flow.")
    finalize.add_argument("--keep-test-runtime", action="store_true", help="Stop the test stack instead of destroying it.")
    finalize.add_argument("--no-collect-diagnostics", action="store_true", help="Skip test diagnostics during finalization.")

    approve = flow_subparsers.add_parser("approve", help="Approve a finalized test publish run for live deploy.")
    approve.add_argument("flow_id", help="Publish flow id.")
    approve.add_argument("--note", default="", help="Approval note.")

    reject = flow_subparsers.add_parser("reject", help="Reject a finalized test publish run.")
    reject.add_argument("flow_id", help="Publish flow id.")
    reject.add_argument("--note", default="", help="Rejection note.")

    report = flow_subparsers.add_parser("report", help="Read the structured publish report.")
    report.add_argument("flow_id", help="Publish flow id.")

    flow_subparsers.add_parser("list", help="List persisted publish runs.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "serve":
        run_server(args)
        return
    raise SystemExit(run_publish_flow(args))


if __name__ == "__main__":
    main()
