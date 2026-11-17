"""Pi 智能判断入口、固定规则对照和本地审批。"""
from __future__ import annotations

import argparse
import json
import sys

from .config import PROJECT_ROOT
from .doctor import inspect_locks
from .logging_setup import get_logger, setup_logging
from .services import create_services
from .pi_judgment import PiJudgment
from .inbox import EventInbox


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oss-watch", description="开源值守：本地事件驱动维护助手")
    sub = parser.add_subparsers(dest="command", required=True)
    intelligent = sub.add_parser("pi", help="主入口：默认无人值守 headless 判断一轮；--interactive 进交互 TUI")
    intelligent.add_argument("event", nargs="?", help="默认模拟发版；接续时使用状态中保存的原事件")
    intelligent.add_argument("--prepare-only", action="store_true", help="只保留本轮事件和提示词，不启动模型")
    intelligent.add_argument("--resume-round", action="store_true", help="显式接续同一未完成事件，不开启新一轮")
    intelligent.add_argument("--interactive", action="store_true", help="进入可观察、可接管的交互 TUI（调试/接管用）")
    demo = sub.add_parser("demo", help="固定规则对照与回归，不是智能判断")
    demo.add_argument("event", nargs="?", default=str(PROJECT_ROOT / "fixtures" / "release-v0.3.0-rc.1.json"), help="本地发版事件 JSON")
    sub.add_parser("status", help="查看当前待拍板轮次")
    enqueue = sub.add_parser("enqueue", help="原子投递本地事件，交给已启动的 watch")
    enqueue.add_argument("event")
    watch = sub.add_parser("watch", help="扫描 SQLite 收件箱，派一轮 headless Pi 后退出")
    watch.add_argument("--once", action="store_true", help="收件箱为空时立即退出")
    watch.add_argument("--interval", type=float, default=1.0, help="收件箱扫描间隔（秒）")
    watch.add_argument("--interactive", action="store_true", help="本轮进入交互 TUI 而非 headless")
    sub.add_parser("doctor", help="诊断状态目录中的锁：持有进程是否存活；只报告不删锁")
    for decision in ("approve", "reject"):
        review = sub.add_parser(decision, help=f"先记录本地 {decision}；approve 在发送开关开启且有凭据时发送讨论区评论")
        review.add_argument("event_id")
        review.add_argument("--note", default="")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        services = create_services()
        setup_logging(services.settings.state_path.parent)
        logger = get_logger()
        logger.info("CLI command=%s", args.command)
        if args.command == "pi":
            judgment = PiJudgment(services.settings)
            active = services.state.load().get("active_judgment")
            if args.resume_round and args.event is None:
                if active is None:
                    raise ValueError("没有可接续的 Pi 判断")
                event = active["event"]
            else:
                event = services.workflow.load_event(args.event or str(PROJECT_ROOT / "fixtures" / "release-v0.3.0-rc.1.json"))
            prepared = judgment.prepare(event, resume=args.resume_round)
            print_json(prepared)
            if not args.prepare_only:
                print_json(judgment.launch(prepared, interactive=args.interactive))
        elif args.command == "demo":
            event = services.workflow.load_event(args.event)
            print_json(services.workflow.run(event))
        elif args.command == "status":
            print_json({**services.approval.inspect(), **EventInbox(services.settings).inspect()})
        elif args.command == "enqueue":
            print_json(EventInbox(services.settings).enqueue(args.event))
        elif args.command == "watch":
            print("本地值守已启动；等待 SQLite 队列事件，派一轮 headless Pi 后退出。Ctrl+C 停止等待。", flush=True)
            print_json(EventInbox(services.settings).run(once=args.once, interval=args.interval, interactive=args.interactive))
        elif args.command == "doctor":
            print_json(inspect_locks(services.settings))
        else:
            result = services.approval.review(args.event_id, args.command, args.note)
            logger.info("人工拍板 event=%s decision=%s delivery=%s", args.event_id, args.command, result.get("delivery"))
            print_json(result)
    except KeyboardInterrupt:
        print("已停止；原事件和已有草稿保留，可用 status 查看。", file=sys.stderr)
        raise SystemExit(130)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"运行失败：{error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
