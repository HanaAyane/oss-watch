"""Pi 智能判断入口、固定规则对照和本地审批。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import PROJECT_ROOT
from .services import create_services
from .pi_judgment import PiJudgment


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oss-watch", description="开源值守：本地事件驱动维护助手")
    sub = parser.add_subparsers(dest="command", required=True)
    intelligent = sub.add_parser("pi", help="主入口：在当前终端启动交互 Pi，智能判断一轮后退出")
    intelligent.add_argument("event", nargs="?", default=str(PROJECT_ROOT / "fixtures" / "release-v0.3.0-rc.1.json"))
    intelligent.add_argument("--prepare-only", action="store_true", help="只保留本轮事件和提示词，不启动模型")
    intelligent.add_argument("--resume-round", action="store_true", help="显式接续同一未完成事件，不开启新一轮")
    demo = sub.add_parser("demo", help="固定规则对照与回归，不是智能判断")
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
        if args.command == "pi":
            judgment = PiJudgment(services.settings)
            event = services.workflow.load_event(args.event)
            prepared = judgment.prepare(event, resume=args.resume_round)
            print_json(prepared)
            if not args.prepare_only:
                print_json(judgment.launch(prepared))
        elif args.command == "demo":
            event = services.workflow.load_event(args.event)
            print_json(services.workflow.run(event))
        elif args.command == "status":
            print_json(services.approval.inspect())
        else:
            print_json(services.state.review(args.event_id, args.command, args.note))
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"运行失败：{error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
