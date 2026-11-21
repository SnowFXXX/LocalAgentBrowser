"""The watch window for test runs: the Windows UI's live view following each task of a run of many (the regression
suite, the compatibility test, SimpleQA) on the left half of the screen, beside the agent's browser on the right.

The run that starts the tasks opens the window once (start); each task then writes its events to a file of its own
(task_env, or begin for a task that does work of its own first), which the window reads (winui.py --watch). The
files are kept in a folder in Temp, so a task's output, console.txt and its run folder are what they would be
without the window. It opens only on Windows, from a person's console window (a launcher), and LAB_WATCH=0 turns it
off: then the tasks also keep the test-run browser window as before, for runs whose timings must stay comparable.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

from . import settings

COUNT = {"n": 0}


def wanted():
    return (sys.platform == "win32" and os.environ.get("LAB_WATCH", "1") != "0"
            and bool(getattr(sys.stdin, "isatty", lambda: False)()))


def start(title, total=None):
    """Opens the watch window for the tasks this process and its children will run; returns its folder, or None (off,
    or already watched: the process was started by a run that has one)."""
    if os.environ.get("LAB_WATCH_DIR") or not wanted():
        return None
    folder = tempfile.mkdtemp(prefix="LocalAgentBrowser-watch-")
    os.environ["LAB_WATCH_DIR"] = folder  # Inherited by the processes that run the tasks
    write_state(folder, {"title": title, "of": total})
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (settings.APP, os.environ.get("PYTHONPATH")) if p))
    subprocess.Popen([settings.python(windowless=True), "-m", "localagentbrowser.winui", "--watch", folder],
                     cwd=folder, env=env, close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    return folder


def write_state(folder, changes):
    state = read_state(folder)
    state.update(changes)
    path = os.path.join(folder, "current.json")
    with open(path + ".new", "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(path + ".new", path)  # Whole, never half written, when the window reads it


def read_state(folder):
    try:
        with open(os.path.join(folder, "current.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def new_task(label, output, model=None):
    """The events file for the next task, made the window's current one; None when there is no window."""
    folder = os.environ.get("LAB_WATCH_DIR")
    if not folder:
        return None
    COUNT["n"] += 1
    n = int(os.environ.get("LAB_WATCH_N") or COUNT["n"])  # A child running one task is told its number
    events = os.path.join(folder, f"{n:04d}-{re.sub(r'[^A-Za-z0-9.-]+', '-', label)[:60]}.jsonl")
    open(events, "w", encoding="utf-8").close()
    write_state(folder, {"label": label, "n": n, "of": int(os.environ.get("LAB_WATCH_OF") or 0) or
                         read_state(folder).get("of"), "file": events, "folder": output, "model": model,
                         "done": False})
    return events


def task_env(env, label, output, model=None):
    """The environment of a task's process: its events go to a new file the window follows, and its browser goes on
    the right half, laid out as in a person's run. Unchanged when there is no window, or the task was begun already."""
    if env.get("LAB_EVENTS") or not os.environ.get("LAB_WATCH_DIR"):
        return env
    events = new_task(label, output, model)
    return dict(env, LAB_EVENTS=events, LAB_UI="windows") if events else env


def begin(label, output, model=None):
    """For a process that does work of its own before running a task (the compatibility test's quick checks): its
    events and the task's go to one file, and the task's process inherits it."""
    events = new_task(label, output, model)
    if events:
        os.environ.update(LAB_EVENTS=events, LAB_UI="windows")
    return events


def finish():
    """Tells the window the run is over (it stays open, on the last report, until closed)."""
    folder = os.environ.get("LAB_WATCH_DIR")
    if folder:
        write_state(folder, {"done": True})
