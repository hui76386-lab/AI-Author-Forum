from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def command_label(command: list[str]) -> str:
    return " ".join(command[1:])


def stop_processes(processes: list[tuple[str, subprocess.Popen]]) -> None:
    for name, process in processes:
        if process.poll() is None:
            print(f"Stopping {name}...", flush=True)
            process.terminate()

    deadline = time.monotonic() + 5
    for name, process in processes:
        if process.poll() is not None:
            continue
        remaining = max(0.1, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            print(f"Force stopping {name}...", flush=True)
            process.kill()
            process.wait()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start Django, the static frontend, and Celery together."
    )
    parser.add_argument("--django-port", type=int, default=8000)
    parser.add_argument("--static-port", type=int, default=4173)
    parser.add_argument(
        "--without-worker",
        action="store_true",
        help="Do not start the local Celery worker.",
    )
    return parser.parse_args()


def main() -> int:
    options = parse_args()
    environment = os.environ.copy()
    environment.setdefault("DJANGO_SETTINGS_MODULE", "ai_author_forum.settings.dev")

    python = sys.executable
    commands = [
        (
            "Django",
            [
                python,
                "manage.py",
                "runserver",
                f"127.0.0.1:{options.django_port}",
                "--noreload",
            ],
        ),
        (
            "Static frontend",
            [
                python,
                "-m",
                "ai_author_forum.static_publish.static_server",
                "--root",
                str(PROJECT_ROOT / "published"),
                "--host",
                "127.0.0.1",
                "--port",
                str(options.static_port),
            ],
        ),
    ]
    if not options.without_worker:
        # Windows does not support Celery's prefork pool; solo provides the
        # development worker required for asynchronous static publishing.
        commands.append(
            (
                "Celery worker",
                [
                    python,
                    "-m",
                    "celery",
                    "-A",
                    "ai_author_forum",
                    "worker",
                    "--loglevel=INFO",
                    "--pool=solo",
                    "--concurrency=1",
                ],
            )
        )

    processes: list[tuple[str, subprocess.Popen]] = []
    try:
        for name, command in commands:
            print(f"Starting {name}: {command_label(command)}", flush=True)
            process = subprocess.Popen(command, cwd=PROJECT_ROOT, env=environment)
            processes.append((name, process))

        print("", flush=True)
        print(
            f"Django admin/dynamic preview: http://127.0.0.1:{options.django_port}/",
            flush=True,
        )
        print(
            f"Static frontend:             http://127.0.0.1:{options.static_port}/",
            flush=True,
        )
        print("Press Ctrl+C to stop all services.", flush=True)

        while True:
            for name, process in processes:
                return_code = process.poll()
                if return_code is not None:
                    print(
                        f"{name} exited with code {return_code}; stopping the remaining services.",
                        flush=True,
                    )
                    return return_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nShutting down all services...", flush=True)
        return 0
    finally:
        stop_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())
