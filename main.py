# main.py
import argparse

from agent import run_task_workflow
from browser import browser_session


def parse_args():
    parser = argparse.ArgumentParser(
        description="Softlight Agent B - UI State Capture"
    )
    parser.add_argument(
        "--task",
        required=True,
        help="High-level task description, e.g. 'Create a project in Linear' or 'Create a repo in GitHub'",
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Start URL for the web app, e.g. https://github.com/new",
    )
    parser.add_argument(
        "--task-id",
        default=None,
        help="Optional short ID for organizing dataset folders",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (no visible window)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print()
    print("================= AGENT WORKFLOW MODE =================")
    print(f"Task          : {args.task}")
    print(f"Start URL     : {args.url}")
    print(f"Task ID       : {args.task_id}")
    print(f"Headless      : {args.headless}")
    print("=======================================================")
    print()

    with browser_session(headless=args.headless) as browser:
        run_task_workflow(
            browser=browser,
            task_description=args.task,
            start_url=args.url,
            task_id=args.task_id,
        )


if __name__ == "__main__":
    main()
