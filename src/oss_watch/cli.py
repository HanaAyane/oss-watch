"""一条命令运行演示，或查看、记录本地审批。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import PROJECT_ROOT
from .services import create_services


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oss-watch", description="开源值守：本地事件驱动维护助手")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="运行模拟发版适配，生成草稿并停在待拍板")
    demo.add_argument("event", nargs="?", default=str(PROJECT_ROOT / "fixtures" / "release-v0.3.0-rc.1.json"), help="本地发版事件 JSON")
    sub.add_parser("status", help="查看当前待拍板轮次")
    for decision in ("approve", "reject"):
        review = sub.add_parser(decision, help=f"本地{decision}，不会对外发送")
        review.add_argument("event_id")
        review.add_argument("--note", default="")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        services = create_services()
        if args.command == "demo":
            event = services.workflow.load_event(args.event)
            print_json(services.workflow.run(event))
        elif args.command == "status":
            print_json(services.approval.inspect())
        else:
            print_json(services.state.review(args.event_id, args.command, args.note))
    except (ValueError, FileNotFoundError, PermissionError, json.JSONDecodeError) as error:
        print(f"运行失败：{error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
