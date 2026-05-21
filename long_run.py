from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent

LONG_RUN_ENV = {
    "PYTHONUNBUFFERED": "1",
    "HEADLESS": "false",
    "MANUAL_AUTH": "true",
    "SKIP_DETAIL_FETCH": "false",
    "DELAY_AFTER_OPEN_SEARCH": "8,18",
    "DELAY_BETWEEN_PAGES": "30,90",
    "DELAY_RETRY_RELOAD": "120,300",
    "DELAY_BETWEEN_TASKS": "300,900",
    "DELAY_BEFORE_NEXT_PAGE": "20,60",
    "DELAY_AFTER_NEXT_PAGE": "30,90",
    "DELAY_LONG_BREAK": "300,900",
    "DELAY_BEFORE_OPEN_DETAIL": "8,20",
    "DELAY_AFTER_OPEN_DETAIL": "15,35",
    "DELAY_BETWEEN_DETAILS": "20,60",
    "DELAY_DETAIL_RETRY": "120,300",
    "LONG_BREAK_EVERY_PAGES": "1",
    "LONG_BREAK_PROBABILITY": "0.85",
    "MAX_DETAIL_RETRIES": "2",
    "MAX_RETRY_DELAY_SECONDS": "600",
}


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_seconds_range(value: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        left, right = [int(float(part.strip())) for part in value.split(",", 1)]
    except Exception:
        return default
    if left < 0 or right < 0:
        return default
    if left > right:
        left, right = right, left
    return left, right


def pick_seconds(value: str, default: tuple[int, int]) -> int:
    left, right = parse_seconds_range(value, default)
    return random.randint(left, right)


def sleep_with_heartbeat(seconds: int, label: str) -> None:
    if seconds <= 0:
        return
    deadline = time.monotonic() + seconds
    while True:
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            return
        print(f"[{now()}] {label}，剩余约 {remaining // 60} 分 {remaining % 60} 秒。")
        time.sleep(min(60, remaining))


def stop_child(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    print(f"[{now()}] 正在结束当前抓取子进程...")
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        print(f"[{now()}] 子进程未及时退出，强制结束。")
        process.kill()
        process.wait(timeout=20)


def build_child_env(cycle: int, args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    env.update(LONG_RUN_ENV)

    if args.refetch_each_cycle:
        env["REFETCH_CRAWLED_DETAILS"] = "true"
    elif cycle == 1 and not args.no_first_refetch:
        env["REFETCH_CRAWLED_DETAILS"] = "true"
    else:
        env["REFETCH_CRAWLED_DETAILS"] = "false"

    return env


def run_one_cycle(cycle: int, args: argparse.Namespace) -> int:
    child_env = build_child_env(cycle, args)
    main_args = list(args.main_args or [])
    if main_args and main_args[0] == "--":
        main_args = main_args[1:]
    command = [sys.executable, "-u", str(ROOT / "main.py"), *main_args]

    print(f"\n[{now()}] 挂机轮次 #{cycle} 开始。")
    print(f"[{now()}] 命令：{' '.join(command)}")
    print(
        f"[{now()}] 详情补全={child_env['SKIP_DETAIL_FETCH'] == 'false'}，"
        f"首轮/本轮强制回补历史详情={child_env['REFETCH_CRAWLED_DETAILS']}"
    )

    if args.dry_run:
        print(f"[{now()}] dry-run：只展示配置，不启动抓取。")
        for key in sorted(LONG_RUN_ENV):
            print(f"{key}={child_env[key]}")
        print(f"REFETCH_CRAWLED_DETAILS={child_env['REFETCH_CRAWLED_DETAILS']}")
        return 0

    process = subprocess.Popen(command, cwd=str(ROOT), env=child_env)
    try:
        while True:
            return_code = process.poll()
            if return_code is not None:
                return return_code
            time.sleep(5)
    except KeyboardInterrupt:
        stop_child(process)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Long-running wrapper for main.py. Press Ctrl+C to stop."
    )
    parser.add_argument(
        "--cycle-delay",
        default=os.getenv("LONG_RUN_CYCLE_DELAY", "1800,3600"),
        help="成功完成一轮后的冷却秒数范围，默认 1800,3600。",
    )
    parser.add_argument(
        "--error-delay",
        default=os.getenv("LONG_RUN_ERROR_DELAY", "600,1200"),
        help="异常退出后的冷却秒数范围，默认 600,1200。",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只按挂机参数跑一轮，用于测试。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印挂机参数，不启动抓取。",
    )
    parser.add_argument(
        "--no-first-refetch",
        action="store_true",
        help="首轮也不强制回补历史详情。",
    )
    parser.add_argument(
        "--refetch-each-cycle",
        action="store_true",
        help="每一轮都强制重新打开历史详情页，通常不建议长时间使用。",
    )
    parser.add_argument(
        "main_args",
        nargs=argparse.REMAINDER,
        help="传给 main.py 的额外参数，例如：-- --max-pages 10",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cycle = 1
    try:
        while True:
            return_code = run_one_cycle(cycle, args)
            print(f"[{now()}] 挂机轮次 #{cycle} 结束，退出码：{return_code}。")

            if args.dry_run or args.once:
                return

            if return_code == 0:
                delay = pick_seconds(args.cycle_delay, (1800, 3600))
                sleep_with_heartbeat(delay, "本轮完成，进入长冷却")
            else:
                delay = pick_seconds(args.error_delay, (600, 1200))
                sleep_with_heartbeat(delay, "本轮异常，保护性等待后重试")

            cycle += 1
    except KeyboardInterrupt:
        print(f"\n[{now()}] 收到手动结束，挂机脚本已停止。")


if __name__ == "__main__":
    main()
