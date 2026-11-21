"""The person's choices between runs, and the folders and programs the two modes share.

Kept in settings.json in the user's own settings folder (%APPDATA%\\LocalAgentBrowser on Windows): the mode the
shortcuts open ("windows", the Windows UI, or "classic", the console), and the start screen's model, time limit and
reports folder. Setup's own choices stay where setup writes them (the program folder's settings\\*.cmd), and are read
here as the defaults. Standard library only.
"""
import ctypes
import json
import os
import re
import subprocess
import sys

PACKAGE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(PACKAGE)  # The program folder (an install or a clone): agent_browser.py, tools\, settings\
CLASSIC_LAUNCHER = os.path.join(APP, "tools", "windows", "LocalAgentBrowser.cmd")


def settings_folder():
    base = os.environ.get("APPDATA") if sys.platform == "win32" else os.environ.get("XDG_CONFIG_HOME")
    return os.path.join(base or os.path.join(os.path.expanduser("~"), ".config"), "LocalAgentBrowser")


def defaults():
    # The Windows UI where it runs; everywhere else (pip install on Linux or macOS) the console, which is all there is
    # The second kind of model is never kept: only its own shortcut ticks it, so nothing leaves the PC unless that
    # shortcut was the one opened, as with the console launcher
    return {"ui": "windows" if sys.platform == "win32" else "classic", "model": None, "minutes": 0,
            "folder": runs_folder()}


def load():
    choices = defaults()
    try:
        with open(os.path.join(settings_folder(), "settings.json"), encoding="utf-8") as f:
            saved = json.load(f)
        choices.update({k: v for k, v in saved.items() if k in choices})
    except (OSError, ValueError):
        pass
    return choices


def save(**changes):
    choices = load()
    choices.update(changes)
    os.makedirs(settings_folder(), exist_ok=True)
    with open(os.path.join(settings_folder(), "settings.json"), "w", encoding="utf-8") as f:
        json.dump(choices, f, indent=1)
    return choices


def documents_folder():
    """Windows' own Documents folder (it may be in OneDrive), as the console launcher and setup's "Past reports"
    shortcut use it."""
    if sys.platform == "win32":
        path = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, path) == 0:  # CSIDL_PERSONAL
            return path.value
    return os.path.join(os.path.expanduser("~"), "Documents")


def runs_folder():
    return os.path.join(documents_folder(), "LocalAgentBrowser runs")


SET_LINE = re.compile(r'^\s*set\s+"([A-Z_]+)=(.*)"\s*$', re.I)


def setup_choices(name):
    """The variables a setup file in the program folder's settings folder sets (model.cmd: 'set "LMSTUDIO_MODEL=..."'),
    as a dict; empty if there is none (a clone, or a pip install)."""
    found = {}
    try:
        with open(os.path.join(APP, "settings", name), encoding="utf-8", errors="replace") as f:
            for line in f:
                match = SET_LINE.match(line)
                if match:
                    found[match.group(1)] = match.group(2)
    except OSError:
        pass
    return found


def python(windowless):
    """This Python, as python.exe (a console program: a run's own process) or pythonw.exe (no console: the UI)."""
    folder, name = os.path.split(sys.executable)
    wanted = ("pythonw" if windowless else "python") + os.path.splitext(name)[1]
    candidate = os.path.join(folder, wanted)
    return candidate if os.path.exists(candidate) else sys.executable


def open_windows_ui():
    """Starts the Windows UI on its own, without a console window."""
    subprocess.Popen([python(windowless=True), "-m", "localagentbrowser.winui"], cwd=documents_folder(),
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True)


def open_classic(arguments=()):
    """Starts classic mode in a console window of its own: the console launcher where there is one (an install or a
    clone), with its arguments, else the console command."""
    if os.path.exists(CLASSIC_LAUNCHER):
        command = ["cmd", "/c", "start", "LocalAgentBrowser", CLASSIC_LAUNCHER, *arguments]
    else:
        command = ["cmd", "/c", "start", "LocalAgentBrowser", python(windowless=False), "-m", "localagentbrowser"]
    subprocess.Popen(command, cwd=documents_folder(), close_fds=True)
