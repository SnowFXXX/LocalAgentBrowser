"""The Windows UI: LocalAgentBrowser in a window of its own, in the project website's colours, instead of a console.

The window is the agent's own browser (Chromium, through Playwright) showing a local page (winui.html) in app mode:
no tabs, no address bar, nothing fetched. Its start screen takes the objective and the choices (model, time limit,
reports folder); each research then runs as a process of its own, as one console run is, with the program's usual
console text saved as console.txt in its run folder, and its events (events.py) drawn as the live view. When the
run ends, the report appears in place. The console window is never shown: "Classic console" switches the shortcuts
to classic mode and opens it; typing /ui as the objective there switches back.

Run with `pythonw -m localagentbrowser.winui` (the Windows shortcuts) or `localagentbrowser-ui` (pip). Elsewhere than
Windows, classic mode runs instead.
"""
import asyncio
import datetime
import html
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import webbrowser

from . import settings, windows
from .config import BASE_URL, CHROMIUM_ARGS
from .events import read_line
from .reportpage import page as report_page, to_html
from .tray import Tray

EDITION = "LocalAgentBrowser Clean Room"
# cloud:start
EDITION = "LocalAgentBrowser"
# cloud:end
TITLE = "LocalAgentBrowser"  # The UI window's title (with Chromium's window class, how it is found to be placed)
PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "winui.html")
STOP_WAIT_SECONDS = 90  # After Stop, the run finishes its turn and writes its report; past this it is ended
RECENT_REPORTS = 8
CREATE_NO_WINDOW = 0x08000000  # A run's process gets no console window


def server_status():
    """(ready, models, message): whether the model server answers, and the models it offers."""
    try:
        with urllib.request.urlopen(BASE_URL.rstrip("/") + "/models", timeout=5) as response:
            listed = json.load(response).get("data", [])
        models = [m["id"] for m in listed if "embed" not in m.get("id", "")]
        return True, models, ""
    except Exception:
        return False, [], (f"LM Studio is not answering at {BASE_URL.rsplit('/v1', 1)[0]}. Open LM Studio, load a "
                           "model, and start its local server (the Developer tab, Status: Running). Then click Retry.")


def recent_reports(folder, limit=RECENT_REPORTS):
    """The newest run folders with a report: {"folder", "objective", "when"} each."""
    found = []
    try:
        names = sorted((n for n in os.listdir(folder) if os.path.isdir(os.path.join(folder, n))), reverse=True)
    except OSError:
        return []
    for name in names:
        report = os.path.join(folder, name, "research_report.md")
        if not os.path.exists(report):
            continue
        objective = ""
        try:
            with open(report, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("**Objective:**"):
                        objective = line.split("**Objective:**", 1)[1].strip()
                        break
        except OSError:
            pass
        found.append({"folder": os.path.join(folder, name), "objective": objective or name,
                      "when": name.replace("_", " ")})
        if len(found) >= limit:
            break
    return found


def new_run_folder(folder):
    """A folder of its own for each run, named for when it started, as the console launcher makes them."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path, n = os.path.join(folder, stamp), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(folder, f"{stamp}_{n}")
    os.makedirs(path)
    return path


def report_page_for(folder):
    """The run's report as a page (also saved as research_report.html, as the console launcher does), or None."""
    report = os.path.join(folder, "research_report.md")
    if not os.path.exists(report):
        return None
    with open(report, encoding="utf-8", errors="replace") as f:
        body = to_html(f.read())
    whole = report_page(html.escape(f"Research report ({os.path.basename(folder)})"), body, html.escape(report))
    try:
        with open(os.path.join(folder, "research_report.html"), "w", encoding="utf-8") as f:
            f.write(whole)
    except OSError:
        pass
    return whole


def run_environment(choices, stop_file):
    """The environment of one run's process: the chosen model, and the switches that make it a UI run."""
    env = dict(os.environ)
    env.update(settings.setup_choices("model.cmd"))
    if choices.get("model"):
        env["LMSTUDIO_MODEL"] = choices["model"]
    # cloud:start
    if choices.get("remote"):
        env.update(settings.setup_choices("cloud.cmd"))
        env["LMSTUDIO_API_KEY"] = os.environ.get("OPENROUTER_API_KEY", "")
    # cloud:end
    env.update(LAB_UI="windows", LAB_EVENTS="stdout", LAB_STOP_FILE=stop_file, PYTHONUNBUFFERED="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", PYTHONPATH=os.pathsep.join(p for p in (settings.APP, env.get("PYTHONPATH")) if p))
    return env


def remote_option():
    """The second kind of model the start screen offers besides LM Studio's, or None (Clean Room has none)."""
    option = None
    # cloud:start
    chosen = settings.setup_choices("cloud.cmd")
    if chosen:
        ready = bool(os.environ.get("OPENROUTER_API_KEY"))
        option = {"label": "Use a cloud model through OpenRouter (your own key)", "model": chosen.get("LMSTUDIO_MODEL", ""),
                  "ready": ready, "note": "" if ready else ("The OPENROUTER_API_KEY environment variable is not set: set it "
                                                           "to your OpenRouter key, then start LocalAgentBrowser again."),
                  "warning": "The prompts and the pages read go to OpenRouter."}
    # cloud:end
    return option


class Run:
    def __init__(self, folder, process, stop_file, choices):
        self.folder, self.process, self.stop_file, self.choices = folder, process, stop_file, choices
        self.stopping = None


class App:
    def __init__(self, page):
        self.page, self.run, self.done, self.tray = page, None, asyncio.Event(), None
        self.choices = settings.load()

    async def tray_command(self, command):
        """A choice from the tray icon's menu (or a click on the icon: "open")."""
        if command == "open":
            windows.show(ui_window())
        elif command == "stop":
            self.request_stop("stopped by you")
        elif command == "folder":
            open_outside(self.choices.get("folder") or settings.runs_folder())
        elif command == "quit":
            await self.quit()

    async def send(self, kind, **data):
        try:
            await self.page.evaluate("e => window.lab && window.lab.receive(e)", {"kind": kind, **data})
        except Exception:  # The window was closed
            pass

    def on_message(self, source, message):
        """A message from the page (window.labSend), handled in a task of its own: the page is not kept waiting."""
        asyncio.ensure_future(self.handle(message or {}))

    async def handle(self, message):
        kind = message.get("type")
        if kind == "hello":
            await self.send("setup", **self.setup_state())
        elif kind == "check":
            ready, models, note = server_status()
            await self.send("server", ready=ready, models=models, note=note)
        elif kind == "start" and not self.run:
            await self.start(message)
        elif kind == "stop":
            self.request_stop("stopped by you")
        elif kind == "show":
            whole = report_page_for(message.get("folder", ""))
            if whole:
                await self.send("report", page=whole, folder=message["folder"], past=True)
        elif kind == "open":
            open_outside(message.get("path") or message.get("url") or "")
        elif kind == "classic":
            settings.save(ui="classic")
            settings.open_classic()
            await self.quit()

    def setup_state(self):
        ready, models, note = server_status()
        model = self.choices.get("model") or settings.setup_choices("model.cmd").get("LMSTUDIO_MODEL")
        return {"edition": EDITION, "ready": ready, "models": models, "note": note, "model": model,
                "minutes": self.choices.get("minutes") or 0, "folder": self.choices.get("folder") or settings.runs_folder(),
                "remote": remote_option(), "remote_chosen": bool(self.choices.get("remote")),
                "recent": recent_reports(self.choices.get("folder") or settings.runs_folder())}

    async def start(self, message):
        objective = " ".join(str(message.get("objective", "")).split())
        if not objective:
            return
        self.choices = settings.save(model=message.get("model") or self.choices.get("model"),
                                     minutes=int(message.get("minutes") or 0),
                                     folder=message.get("folder") or self.choices.get("folder") or settings.runs_folder(),
                                     remote=bool(message.get("remote")))
        folder = new_run_folder(self.choices["folder"])
        stop_file = os.path.join(tempfile.gettempdir(), f"LocalAgentBrowser-stop-{os.getpid()}-{os.path.basename(folder)}")
        if os.path.exists(stop_file):
            os.remove(stop_file)
        process = await asyncio.create_subprocess_exec(
            settings.python(windowless=False), "-u", "-m", "localagentbrowser", cwd=folder,
            env=run_environment(self.choices, stop_file), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        process.stdin.write((objective + "\n").encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()
        self.run = Run(folder, process, stop_file, self.choices)
        place_window("left")
        await self.send("started", objective=objective, folder=folder, model=self.choices.get("model") or "")
        asyncio.ensure_future(self.follow(self.run))
        if self.choices.get("minutes"):
            asyncio.ensure_future(self.time_limit(self.run, self.choices["minutes"]))

    async def follow(self, run):
        """The run's output: event lines to the page, console text to console.txt and the page's console view."""
        with open(os.path.join(run.folder, "console.txt"), "w", encoding="utf-8") as log:
            while True:
                raw = await run.process.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                event = read_line(line)
                if event:
                    kind, data = event
                    if kind == "result":
                        data["html"] = to_html(data.get("text") or "")
                    await self.send(kind, **data)
                else:
                    log.write(line + "\n")
                    log.flush()
                    await self.send("line", text=line)
        code = await run.process.wait()
        if os.path.exists(run.stop_file):
            os.remove(run.stop_file)
        # No report means the run ended before its first turn (the model server failed, or it was stopped while the
        # objective was split): its folder is kept, with console.txt saying why
        whole = report_page_for(run.folder)
        if whole:
            await self.send("report", page=whole, folder=run.folder, past=False)
        await self.send("exited", code=code, folder=run.folder, report=bool(whole))
        self.run = None

    async def time_limit(self, run, minutes):
        await asyncio.sleep(minutes * 60)
        if self.run is run:
            self.request_stop(f"the time limit ({minutes} minutes)")

    def request_stop(self, reason):
        run = self.run
        if not run or run.stopping:
            return
        run.stopping = reason
        with open(run.stop_file, "w", encoding="utf-8") as f:
            f.write(reason)
        asyncio.ensure_future(self.end_if_still_running(run))

    async def end_if_still_running(self, run):
        try:
            await asyncio.wait_for(run.process.wait(), STOP_WAIT_SECONDS)
        except asyncio.TimeoutError:
            run.process.kill()

    async def quit(self):
        if self.run:
            self.request_stop("the window was closed")
            try:
                await asyncio.wait_for(self.run.process.wait(), STOP_WAIT_SECONDS + 5)
            except asyncio.TimeoutError:
                pass
        self.done.set()


def open_outside(target):
    """A link or folder from the page, opened outside the UI window: links in the default browser."""
    if target.startswith(("http://", "https://")):
        webbrowser.open(target)
    elif target and os.path.exists(target) and sys.platform == "win32":
        os.startfile(target)


WINDOW = {}
TRAY_CHECK_SECONDS = 0.5  # How often the UI window is checked for being minimized (it then goes to the tray)


def ui_window():
    """The UI window's handle, found once by its title and window class (a hidden window cannot be found so)."""
    if sys.platform == "win32" and not WINDOW.get("hwnd"):
        WINDOW["hwnd"] = windows.window_titled(TITLE, class_name=windows.CHROMIUM_WINDOW_CLASS)
    return WINDOW.get("hwnd") or 0


def place_window(side):
    """The UI window on the left half (beside the agent's browser on the right), or centered before a run."""
    if sys.platform != "win32":
        return
    if side == "left":
        windows.place(ui_window(), "left")
    else:
        windows.center(ui_window())


async def to_tray_when_minimized(app):
    """A minimized UI window leaves the taskbar for the tray, where a click brings it back."""
    while not app.done.is_set():
        if app.tray and windows.minimized(ui_window()):
            windows.hide(ui_window())
        await asyncio.sleep(TRAY_CHECK_SECONDS)


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix="LocalAgentBrowser-ui-"), headless=False, no_viewport=True,
            args=["--app=about:blank", *CHROMIUM_ARGS])
        page = context.pages[0] if context.pages else await context.new_page()
        app = App(page)
        await context.expose_binding("labSend", app.on_message)
        page.on("close", lambda _: asyncio.ensure_future(app.quit()))
        with open(PAGE_FILE, encoding="utf-8") as f:
            await page.set_content(f.read())
        place_window("center")
        loop = asyncio.get_running_loop()
        app.tray = Tray(EDITION, lambda command: loop.call_soon_threadsafe(
            asyncio.ensure_future, app.tray_command(command)), running=lambda: app.run is not None)
        if not app.tray.start():
            app.tray = None  # No tray (no taskbar, as in a service session): minimizing works as usual
        asyncio.ensure_future(to_tray_when_minimized(app))
        await app.done.wait()
        if app.tray:
            app.tray.stop()
        try:
            await context.close()
        except Exception:
            pass


def run():
    """Entry point (`localagentbrowser-ui`, and the Windows shortcuts). Classic mode where the setting asks for it, or
    where there is no Windows."""
    if sys.platform != "win32":
        from .agent import run as classic
        return classic()
    if settings.load().get("ui") == "classic":
        return settings.open_classic()
    asyncio.run(main())


if __name__ == "__main__":
    run()
