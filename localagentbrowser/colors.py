"""Classic mode's colours: the console's action tags, warnings and discoveries in the website's palette.

Only in a person's console window. Output piped to a file (the regression suite's console.txt, SimpleQA, the
compatibility test, a run started by the Windows UI) is plain text as before, and NO_COLOR (no-color.org) or
LAB_COLOR=0 turns colours off. The text itself is never changed: known line starts are wrapped in colour codes.
"""
import os
import re
import sys

MINT, BLUE, AMBER, MUTED = "95;211;166", "127;179;230", "240;168;96", "129;145;165"
RESET = "\x1b[0m"


def paint(rgb, text, bold=False):
    return f"\x1b[{'1;' if bold else ''}38;2;{rgb}m{text}{RESET}"


ACTION_COLOURS = {"SEARCH": BLUE, "NAVIGATE": BLUE, "READ_MORE": BLUE, "DISCOVER": MINT, "FINISH": MINT}
RULES = [
    (re.compile(r"(?m)^(\[TURN \d+/\d+\])"), lambda m: paint(BLUE, m.group(1), bold=True)),
    (re.compile(r"(?m)^(Chosen Action: )\[([A-Z_]+)\]"),
     lambda m: m.group(1) + paint(ACTION_COLOURS.get(m.group(2), MUTED), f"[{m.group(2)}]", bold=True)),
    (re.compile(r"(?m)^(Agent Thoughts:)"), lambda m: paint(MUTED, m.group(1))),
    (re.compile(r"(?m)^(Logging critical discovery element:)"), lambda m: paint(MINT, m.group(1), bold=True)),
    (re.compile(r"(?m)^(Agent has signaled mission complete\.)"), lambda m: paint(MINT, m.group(1), bold=True)),
    (re.compile(r"(?m)^(Objective checklist:)"), lambda m: paint(BLUE, m.group(1), bold=True)),
    (re.compile(r"(?m)^(Warning:|Error:|Action refused:|Cheap retry \d+/\d+:|Stopped:)"),
     lambda m: paint(AMBER, m.group(1), bold=True)),
]


def wanted(stream):
    if os.environ.get("NO_COLOR") or os.environ.get("LAB_COLOR") == "0":
        return False
    return bool(getattr(stream, "isatty", lambda: False)()) and terminal_takes_colours()


def terminal_takes_colours():
    """Windows consoles show colour codes only once asked to (virtual terminal processing); elsewhere they do."""
    if sys.platform != "win32":
        return True
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    return bool(mode.value & 4) or bool(kernel32.SetConsoleMode(handle, mode.value | 4))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


class Colored:
    """The console stream, with known line starts coloured."""

    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        for pattern, colour in RULES:
            text = pattern.sub(colour, text)
        return self.stream.write(text)

    def __getattr__(self, name):  # flush, isatty, encoding, reconfigure, ...
        return getattr(self.stream, name)


def colour_console():
    """Classic mode's console in colour, when it is a person's console window."""
    if wanted(sys.stdout) and not isinstance(sys.stdout, Colored):
        sys.stdout = Colored(sys.stdout)
