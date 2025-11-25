# agent.py
import json
import textwrap
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from groq import Groq

from config import (
    GROQ_API_KEY,
    GROQ_MODEL,
    DATASET_DIR,
    MAX_STEPS,
    DOM_CHAR_LIMIT,
)
from browser import BrowserController

client = Groq(api_key=GROQ_API_KEY)


@dataclass
class AgentAction:
    action_type: str
    selector: Optional[str] = None
    text: Optional[str] = None
    wait_ms: Optional[int] = None
    screenshot_before: bool = True
    screenshot_after: bool = True
    reason: str = ""


class LLMAgent:
    def __init__(self, model: str = GROQ_MODEL):
        self.model = model

    def _system_prompt(self) -> str:
        return textwrap.dedent(
            """
            You are Agent B, a browser automation planner.

            Your job:
            - Understand the user's task
            - Inspect the web app UI (DOM + URL)
            - Decide the NEXT concrete browser action
            - Output ONLY a JSON object with the next action.

            JSON schema:

            {
              "action_type": "click" | "type" | "wait" | "done",
              "selector": "string or null",
              "text": "string or null",
              "wait_ms": 500,
              "screenshot_before": true,
              "screenshot_after": true,
              "reason": "short explanation"
            }

            Global rules:
            - NEVER attempt to create accounts.
            - If a login page is shown, assume a human may log in manually.
            - Prefer simple, stable selectors:
              * Use link/button names like "Issues", "New issue",
                "Create repository", "Submit new issue" as the selector string
                when possible. The executor will use role/text-based locators.
            - Keep each step small: one click OR one type OR a short wait.

            GitHub-specific hints:
            - On GitHub "new repository" pages (URL contains "/new" and text
              contains "Create a new repository"):
                * Fill in the repository name field.
                * Then CLICK the primary submit button to create the repository.
                * After that, you should usually be on the repository page.

            - On GitHub "new issue" pages (URL contains "/issues/new"):
                * First fill the issue title field with the requested title.
                * Optionally fill the description/body if the task mentions it.
                * Then CLICK the "Submit new issue" button.
                  Use "Submit new issue" as the selector string.
                * Do NOT mark the task as done until after you have clicked the
                  submit button at least once.
            """
        )

    def _truncate_dom(self, dom: Dict[str, Any]) -> str:
        text = dom.get("visible_text") or ""
        return text[:DOM_CHAR_LIMIT]

    def decide_next_action(
        self,
        task_description: str,
        dom_snapshot: Dict[str, Any],
        history: List[Dict[str, Any]],
    ) -> AgentAction:
        dom_text = self._truncate_dom(dom_snapshot)

        user_prompt = f"""
        Task: {task_description}

        Current URL:
        {dom_snapshot.get("url")}

        Visible text (partial):
        {dom_text}

        Recent actions:
        {history[-3:] if history else "None"}

        Output the next action as JSON only, no extra text.
        """

        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )

        raw = (response.choices[0].message.content or "").strip()

        # Handle ```json ... ``` wrappers
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()

        try:
            data = json.loads(raw)
        except Exception:
            print("⚠️ Groq JSON parse error, defaulting to 'done':", raw)
            data = {"action_type": "done"}

        return AgentAction(
            action_type=data.get("action_type", "done"),
            selector=data.get("selector"),
            text=data.get("text"),
            wait_ms=data.get("wait_ms"),
            screenshot_before=data.get("screenshot_before", True),
            screenshot_after=data.get("screenshot_after", True),
            reason=data.get("reason", ""),
        )


def slugify(text: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in text).strip("-")


# ---------- Heuristics / guardrails ----------

def looks_like_login_or_oauth(dom: Dict[str, Any]) -> bool:
    """
    Detect login / OAuth pages.
    We PAUSE once to let the user log in, then continue.
    """
    url = (dom.get("url") or "").lower()
    text = (dom.get("visible_text") or "").lower()

    if "accounts.google.com" in url:
        return True
    if "github.com/login" in url:
        return True

    login_markers = [
        "sign in",
        "sign up",
        "log in",
        "log into",
        "continue with google",
        "continue with microsoft",
        "password",
        "2-step verification",
    ]
    hits = sum(1 for m in login_markers if m in text)
    return hits >= 2


def looks_like_github_repo_page(dom: Dict[str, Any], task_description: str) -> bool:
    """
    Detect when we're on a GitHub repository page
    after a 'create repo / project' style task.
    """
    url = (dom.get("url") or "").lower()
    text = (dom.get("visible_text") or "").lower()
    task = task_description.lower()

    if "github.com" not in url:
        return False

    if "/new" in url:
        return False

    if "create a new repository" in text:
        return False

    if "create" in task and ("repo" in task or "repository" in task or "project" in task):
        return True

    return False


def looks_like_github_issue_page(dom: Dict[str, Any], task_description: str) -> bool:
    """
    Detect when we're on a GitHub issue detail page
    after an 'open issue' or 'create issue' style task.
    """
    url = (dom.get("url") or "").lower()
    text = (dom.get("visible_text") or "").lower()
    task = task_description.lower()

    if "github.com" not in url:
        return False

    if "/issues/" not in url:
        return False
    if "/issues/new" in url:
        return False

    if "new issue" in text and "submit new issue" in text:
        return False

    if "issue" in task or "bug" in task:
        return True

    return False


# ---------- Main workflow loop ----------

def run_task_workflow(
    browser: BrowserController,
    task_description: str,
    start_url: str,
    task_id: Optional[str] = None,
) -> Path:
    """
    Main control loop:
    - Navigates to start_url
    - Repeatedly:
        * snapshots DOM
        * handles login once (manual)
        * checks for success / guardrails
        * asks LLM for next action
        * executes action
        * captures before/after screenshots
        * records metadata
    """

    if task_id is None:
        task_id = slugify(task_description)

    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir = DATASET_DIR / f"{task_id}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    browser.goto(start_url)

    agent = LLMAgent()
    history: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {
        "task_description": task_description,
        "start_url": start_url,
        "task_id": task_id,
        "timestamp": timestamp,
        "steps": [],
    }

    login_handled = False

    for step_idx in range(1, MAX_STEPS + 1):
        dom = browser.get_dom_snapshot()

        # 1) Manual login hook (only once)
        if looks_like_login_or_oauth(dom) and not login_handled:
            print(
                "\n============================================================"
                "\n[LOGIN] Login / OAuth page detected."
                "\nPlease log in manually in the browser window."
                "\nWhen you see the target app/dashboard loaded,"
                f"\n(optionally navigate to {start_url} if needed),"
                "\ncome back to this terminal and press ENTER to continue."
                "\n============================================================"
            )
            input("▶ Press ENTER *after* you have logged in successfully... ")
            login_handled = True
            # Ensure we land on the intended page after login
            browser.goto(start_url)
            login_after = run_dir / f"step_{step_idx:02d}_after_login.png"
            browser.screenshot(login_after)
            metadata["steps"].append(
                {
                    "step": step_idx,
                    "url": dom.get("url"),
                    "action": {
                        "action_type": "manual_login",
                        "reason": "User logged in manually.",
                    },
                    "before": None,
                    "after": str(login_after),
                }
            )
            continue

        # If still stuck on login even after manual login, bail
        if looks_like_login_or_oauth(dom) and login_handled:
            print(f"[STEP {step_idx}] Still on login page after manual login — stopping.")
            final_login = run_dir / f"step_{step_idx:02d}_login_stuck.png"
            browser.screenshot(final_login)
            metadata["stopped_reason"] = "stuck_on_login"
            metadata["final_url"] = dom.get("url")
            break

        # 2) Auto-stop success: GitHub repository created
        if looks_like_github_repo_page(dom, task_description):
            print(f"[STEP {step_idx}] Detected GitHub repository page — task completed.")
            final_repo = run_dir / f"step_{step_idx:02d}_final_repo.png"
            browser.screenshot(final_repo)
            metadata["stopped_reason"] = "github_repo_detected"
            metadata["final_url"] = dom.get("url")
            metadata["steps"].append(
                {
                    "step": step_idx,
                    "url": dom.get("url"),
                    "action": {
                        "action_type": "auto_done",
                        "reason": "Detected GitHub repo page (create workflow completed)",
                    },
                    "before": None,
                    "after": str(final_repo),
                }
            )
            break

        # 3) Auto-stop success: GitHub issue created
        if looks_like_github_issue_page(dom, task_description):
            print(f"[STEP {step_idx}] Detected GitHub issue page — task completed.")
            final_issue = run_dir / f"step_{step_idx:02d}_final_issue.png"
            browser.screenshot(final_issue)
            metadata["stopped_reason"] = "github_issue_detected"
            metadata["final_url"] = dom.get("url")
            metadata["steps"].append(
                {
                    "step": step_idx,
                    "url": dom.get("url"),
                    "action": {
                        "action_type": "auto_done",
                        "reason": "Detected GitHub issue page (issue workflow completed)",
                    },
                    "before": None,
                    "after": str(final_issue),
                }
            )
            break

        # 4) Ask LLM what to do next
        action = agent.decide_next_action(task_description, dom, history)
        print(f"[STEP {step_idx}] Action: {action.action_type} ({action.reason})")

        step_name = f"step_{step_idx:02d}"

        # BEFORE screenshot
        if action.screenshot_before:
            before_path = run_dir / f"{step_name}_before.png"
            browser.screenshot(before_path)
        else:
            before_path = None

        # Execute chosen action
        try:
            if action.action_type == "click" and action.selector:
                browser.click(action.selector)
            elif action.action_type == "type" and action.text:
                browser.fill(action.selector or "", action.text)
            elif action.action_type == "wait":
                browser.wait(action.wait_ms or 800)
            elif action.action_type == "done":
                final_path = run_dir / f"{step_name}_final.png"
                browser.screenshot(final_path)
                metadata["stopped_reason"] = "llm_done"
                metadata["final_url"] = dom.get("url")
                metadata["steps"].append(
                    {
                        "step": step_idx,
                        "url": dom.get("url"),
                        "action": asdict(action),
                        "before": str(before_path) if before_path else None,
                        "after": str(final_path),
                    }
                )
                break
        except Exception as e:
            error_path = run_dir / f"{step_name}_error.png"
            try:
                browser.screenshot(error_path)
            except Exception:
                error_path = None
            print(f"[STEP {step_idx}] ERROR while executing action: {e}")
            metadata["stopped_reason"] = "action_error"
            metadata["error"] = str(e)
            metadata["final_url"] = dom.get("url")
            metadata["steps"].append(
                {
                    "step": step_idx,
                    "url": dom.get("url"),
                    "action": asdict(action),
                    "before": str(before_path) if before_path else None,
                    "after": str(error_path) if error_path else None,
                    "error": str(e),
                }
            )
            break

        # Short wait after each action
        browser.wait(600)

        # AFTER screenshot
        if action.screenshot_after:
            after_path = run_dir / f"{step_name}_after.png"
            browser.screenshot(after_path)
        else:
            after_path = None

        # Record step metadata
        metadata["steps"].append(
            {
                "step": step_idx,
                "url": dom.get("url"),
                "action": asdict(action),
                "before": str(before_path) if before_path else None,
                "after": str(after_path) if after_path else None,
            }
        )

        history.append(
            {
                "url": dom.get("url"),
                "action": asdict(action),
            }
        )

    # Save metadata
    with open(run_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"Saved to: {run_dir}")
    return run_dir
