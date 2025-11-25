# browser.py
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Any, Optional

from playwright.sync_api import sync_playwright, Page


class BrowserController:
    """
    Browser wrapper using a *persistent Chrome profile* so login cookies are kept
    across runs (in ./chrome-profile).
    """

    def __init__(self, headless: bool = False, storage_state: Optional[str] = None):
        self.headless = headless
        self.storage_state = storage_state
        self._pw = None
        self.context = None
        self.page: Optional[Page] = None

    def start(self):
        self._pw = sync_playwright().start()

        # Dedicated profile just for this project (no conflicts with your real Chrome)
        profile_path = Path(__file__).resolve().parent / "chrome-profile"
        profile_path.mkdir(exist_ok=True)

        # Persistent context returns a BrowserContext directly
        self.context = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            channel="chrome",           # uses actual Chrome (playwright install chrome)
            headless=self.headless,
        )

        # Use existing tab or open a new one
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = self.context.new_page()

        self.page.set_viewport_size({"width": 1440, "height": 900})

    def goto(self, url: str):
        assert self.page is not None
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            self.page.goto(url, wait_until="networkidle", timeout=60000)

    def get_dom_snapshot(self) -> Dict[str, Any]:
        assert self.page is not None
        return {
            "url": self.page.url,
            "visible_text": self.page.inner_text("body"),
            "html": self.page.content(),
        }

    def screenshot(self, path: Path):
        assert self.page is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.page.screenshot(path=str(path), full_page=True)
        except Exception:
            # If page changed, pick the last tab
            if self.context and self.context.pages:
                self.page = self.context.pages[-1]
                self.page.screenshot(path=str(path), full_page=True)

    def _switch_to_new_page(self):
        if self.context and self.context.pages and len(self.context.pages) > 1:
            self.page = self.context.pages[-1]

    # ------------------ CLICK ------------------

    def click(self, selector: str):
        """
        Try multiple strategies to click something.
        Works for GitHub tabs/buttons like 'Create repository', 'Issues', 'Settings'.
        """
        assert self.page is not None
        label = (selector or "").strip()

        # 1) CSS selector if it *looks* like CSS
        if label and any(ch in label for ch in ("#", ".", "[", ">", ":")):
            try:
                self.page.click(label, timeout=3000)
                self._switch_to_new_page()
                return
            except Exception:
                pass

        # 2) Accessible name via roles
        for role in ["button", "link", "tab"]:
            try:
                self.page.get_by_role(role, name=label, exact=False).click(timeout=3000)
                self._switch_to_new_page()
                return
            except Exception:
                continue

        # 3) Visible text directly
        if label:
            try:
                self.page.get_by_text(label, exact=False).click(timeout=3000)
                self._switch_to_new_page()
                return
            except Exception:
                pass

        # 4) Manual text-based lookup on common clickable elements
        try:
            candidates = self.page.query_selector_all(
                "button, [role='button'], a, [role='link'], summary, span"
            )
            best_el = None
            best_score = 0
            label_low = label.lower()
            for el in candidates:
                try:
                    if not el.is_visible():
                        continue
                    txt = (el.inner_text() or "").strip()
                    if not txt:
                        continue
                    t_low = txt.lower()
                    score = 0
                    if t_low == label_low:
                        score = 3
                    elif label_low in t_low:
                        score = 2
                    if score > best_score:
                        best_score = score
                        best_el = el
                except Exception:
                    continue

            if best_el:
                best_el.click()
                self._switch_to_new_page()
                return
        except Exception:
            pass

        # 5) Generic fallback: first visible button / link
        try:
            candidates = self.page.query_selector_all(
                "button, [role='button'], a, [role='link']"
            )
            for el in candidates:
                try:
                    if el.is_visible():
                        el.click()
                        self._switch_to_new_page()
                        return
                except Exception:
                    continue
        except Exception:
            pass

        raise Exception(f"Could not click using selector: {selector}")

    # ------------------ FILL ------------------

    def fill(self, selector: str, text: str):
        """
        Try multiple strategies to type into an input box.
        Has special support for:
        - GitHub new repo form
        - GitHub new issue form
        """
        assert self.page is not None

        label = (selector or "").strip()
        url = (self.page.url or "").lower()
        try:
            body_text = (self.page.inner_text("body") or "").lower()
        except Exception:
            body_text = ""

        # --- Helpers to submit GitHub forms after filling ---

        def maybe_submit_github_new_repo():
            if "github.com" not in url or "/new" not in url:
                return
            if "create a new repository" not in body_text:
                return
            try:
                name_input = (
                    self.page.query_selector("input[name='repository[name]']")
                    or self.page.query_selector("#repository_name")
                )
                if not name_input:
                    return
                if not name_input.input_value().strip():
                    return
            except Exception:
                return

            self.page.wait_for_timeout(500)
            for btn_name in ["Create repository", "Create repository."]:
                try:
                    self.page.get_by_role("button", name=btn_name, exact=False).click(timeout=3000)
                    self._switch_to_new_page()
                    return
                except Exception:
                    continue
            try:
                self.page.get_by_text("Create repository", exact=False).click(timeout=3000)
                self._switch_to_new_page()
                return
            except Exception:
                pass
            try:
                btn = self.page.query_selector("form button[type='submit']")
                if btn and btn.is_visible():
                    btn.click()
                    self._switch_to_new_page()
                    return
            except Exception:
                pass

        def maybe_submit_github_new_issue():
            if "github.com" not in url or "/issues/new" not in url:
                return
            if "new issue" not in body_text:
                return
            try:
                title_input = (
                    self.page.query_selector("input[name='issue[title]']")
                    or self.page.query_selector("#issue_title")
                )
                if not title_input:
                    return
                if not title_input.input_value().strip():
                    return
            except Exception:
                return

            self.page.wait_for_timeout(500)
            for btn_name in ["Submit new issue", "Create new issue"]:
                try:
                    self.page.get_by_role("button", name=btn_name, exact=False).click(timeout=3000)
                    self._switch_to_new_page()
                    return
                except Exception:
                    continue
            try:
                self.page.get_by_text("Submit new issue", exact=False).click(timeout=3000)
                self._switch_to_new_page()
                return
            except Exception:
                pass
            try:
                btn = self.page.query_selector("form button[type='submit']")
                if btn and btn.is_visible():
                    btn.click()
                    self._switch_to_new_page()
                    return
            except Exception:
                pass

        # --- GitHub special cases first ---

        # New repository creation page
        if "github.com" in url and "/new" in url and "create a new repository" in body_text:
            for css in ["input[name='repository[name]']", "#repository_name"]:
                try:
                    self.page.wait_for_selector(css, timeout=2000)
                    self.page.fill(css, text, timeout=2000)
                    maybe_submit_github_new_repo()
                    return
                except Exception:
                    continue

        # New issue creation page
        if "github.com" in url and "/issues/new" in url:
            for css in ["input[name='issue[title]']", "#issue_title"]:
                try:
                    self.page.wait_for_selector(css, timeout=2000)
                    self.page.fill(css, text, timeout=2000)
                    maybe_submit_github_new_issue()
                    return
                except Exception:
                    continue

        # --- Generic strategies below ---

        # 1) CSS selector if it looks like CSS
        if label and any(ch in label for ch in ("#", ".", "[", ">", ":")):
            try:
                self.page.wait_for_selector(label, timeout=2000)
                self.page.fill(label, text, timeout=2000)
                maybe_submit_github_new_repo()
                maybe_submit_github_new_issue()
                return
            except Exception:
                pass

        # 2) Placeholder text
        if label:
            try:
                self.page.get_by_placeholder(label).fill(text, timeout=2000)
                maybe_submit_github_new_repo()
                maybe_submit_github_new_issue()
                return
            except Exception:
                pass

        # 3) Textbox role/name
        textbox_names = [
            label,
            "Search",
            "Search GitHub",
            "Search or jump to…",
            "Search or jump to...",
        ]
        for name in textbox_names:
            if not name:
                continue
            try:
                self.page.get_by_role("textbox", name=name, exact=False).fill(text, timeout=2000)
                maybe_submit_github_new_repo()
                maybe_submit_github_new_issue()
                return
            except Exception:
                continue

        # 4) Generic visible input / textarea
        try:
            candidates = self.page.query_selector_all(
                "input[type='search'], input[type='text'], input, textarea"
            )
            for el in candidates:
                try:
                    if el.is_visible():
                        el.click()
                        el.fill("")
                        el.type(text)
                        maybe_submit_github_new_repo()
                        maybe_submit_github_new_issue()
                        return
                except Exception:
                    continue
        except Exception:
            pass

        # 5) Fallback: type into focused element
        try:
            self.page.keyboard.type(text)
            maybe_submit_github_new_repo()
            maybe_submit_github_new_issue()
            return
        except Exception:
            pass

        raise Exception(f"Could not fill using selector: {selector}")

    # ------------------ MISC ------------------

    def wait(self, ms: int):
        assert self.page is not None
        self.page.wait_for_timeout(ms)

    def close(self):
        if self.context:
            self.context.close()
        if self._pw:
            self._pw.stop()


@contextmanager
def browser_session(headless: bool = False, storage_state: Optional[str] = None):
    ctrl = BrowserController(headless=headless, storage_state=storage_state)
    ctrl.start()
    try:
        yield ctrl
    finally:
        ctrl.close()
