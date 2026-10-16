from __future__ import annotations

import argparse
from pathlib import Path

from . import ROOT
from .awake import stay_awake
from .campaign import measured
from .docker import prepare_runtime
from .publish import publish
from .report import report
from .tasks import load_suite


def main(argv=None):
    parser = argparse.ArgumentParser(prog="harness")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a paired local campaign")
    run_p.add_argument("--suite", default="classic")
    run_p.add_argument("--run-dir", type=Path, required=True)
    run_p.add_argument("--engine", type=Path, default=ROOT.parent / "engine")
    run_p.add_argument("--tasks", nargs="*")
    run_p.add_argument("--parallel", type=int, default=2, help="1 serializes the two systems")

    sub.add_parser("report").add_argument("--run-dir", type=Path, required=True)

    pub = sub.add_parser("publish")
    pub.add_argument("--run-dir", type=Path, required=True)
    pub.add_argument("--pages", type=Path, required=True)

    prep = sub.add_parser("prepare-runtime")
    prep.add_argument("--engine", type=Path, default=ROOT.parent / "engine")

    listed = sub.add_parser("tasks")
    listed.add_argument("--suite", default="classic")

    mon = sub.add_parser("monitor", help="Live local UI for a run directory")
    mon.add_argument("--run-dir", type=Path)
    mon.add_argument("--host", default="127.0.0.1")
    mon.add_argument("--port", type=int, default=8765)
    mon.add_argument("--no-open", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "run":
        with stay_awake():
            summary = measured(args.suite, args.run_dir, args.engine, args.tasks, args.parallel)
        print(summary["status"], summary["run_id"])
        return 0
    if args.command == "report":
        summary = report(args.run_dir)
        print(summary["status"], summary["run_id"])
        return 0
    if args.command == "publish":
        summary = publish(args.run_dir, args.pages)
        print("published", summary["run_id"])
        return 0
    if args.command == "prepare-runtime":
        record = prepare_runtime(args.engine)
        print(record["tag"])
        return 0
    if args.command == "tasks":
        for task in load_suite(args.suite):
            print(f"{task['id']}\t{task['difficulty']}\t{task['category']}\t{task['title']}")
        return 0
    if args.command == "monitor":
        from .monitor import serve

        return serve(args.run_dir, host=args.host, port=args.port, open_browser=not args.no_open)
    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
