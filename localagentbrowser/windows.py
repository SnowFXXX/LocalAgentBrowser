"""Side-by-side windows on Windows: the console on the left half of the screen and the browser on the right, so a run
can be watched without switching between them. Only when a person runs LocalAgentBrowser in a console window (not in
test runs, whose output is piped), and LAB_SIDE_BY_SIDE is not "0". Does nothing on other systems."""
import ctypes
import os
import sys
import tempfile

SW_RESTORE, SW_MINIMIZE = 9, 6
GA_ROOTOWNER = 3
SPI_GETWORKAREA = 0x0030


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def enabled():
    return (sys.platform == "win32" and os.environ.get("LAB_SIDE_BY_SIDE", "1") != "0"
            and bool(getattr(sys.stdin, "isatty", lambda: False)()))


def work_area():
    """(left, top, width, height) of the screen without the taskbar."""
    area = RECT()
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(area), 0)
    return area.left, area.top, area.right - area.left, area.bottom - area.top


CONSOLE = {}

def has_size(hwnd):
    r = RECT()
    return bool(hwnd) and ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)) and r.right > r.left


def console_title():
    buffer = ctypes.create_unicode_buffer(512)
    ctypes.windll.kernel32.GetConsoleTitleW(buffer, 512)
    return buffer.value


def window_titled(title):
    """A visible top-level window whose title is the given one (Windows Terminal shows the console's title), or 0."""
    found = []
    get_text = ctypes.windll.user32.GetWindowTextW

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def each(hwnd, _):
        text = ctypes.create_unicode_buffer(512)
        get_text(hwnd, text, 512)
        if text.value == title and ctypes.windll.user32.IsWindowVisible(hwnd) and has_size(hwnd):
            found.append(hwnd)
            return False
        return True

    ctypes.windll.user32.EnumWindows(each, 0)
    return found[0] if found else 0


def console_window():
    """The window the console is shown in. A classic console window is the console's own; in Windows Terminal that
    handle is an invisible placeholder (0 by 0 on BB2), so the terminal window showing the console's title is used, or
    failing that the window in front when the run started. Not the window in front alone: from the second run in one
    console window, the report page from the run before is in front."""
    if "hwnd" not in CONSOLE:
        own = ctypes.windll.kernel32.GetConsoleWindow()
        owner = ctypes.windll.user32.GetAncestor(own, GA_ROOTOWNER) if own else 0
        CONSOLE["hwnd"] = (next((h for h in (owner, own) if has_size(h) and ctypes.windll.user32.IsWindowVisible(h)), 0)
                           or (window_titled(console_title()) if console_title() else 0)
                           or ctypes.windll.user32.GetForegroundWindow())
    return CONSOLE["hwnd"]


def half(side):
    """(x, y, width, height) of the left or right half of the work area."""
    left, top, width, height = work_area()
    return (left if side == "left" else left + width // 2), top, width // 2, height


def place(hwnd, side):
    if not hwnd:
        return
    ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
    ctypes.windll.user32.MoveWindow(hwnd, *half(side), True)


def maximize_console():
    """At the start of a person's first run: the console full size, until the browser opens. From the second run in
    the same window (LAB_RUN_COUNT, set by the Windows launcher) it stays on the left, beside the last report."""
    if enabled() and int(os.environ.get("LAB_RUN_COUNT") or 1) <= 1:
        hwnd = console_window()
        if hwnd:
            # Sized to the whole work area: Windows Terminal ignored a request to maximize from another process
            ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
            ctypes.windll.user32.MoveWindow(hwnd, *work_area(), True)


LAYOUT_WIDTH = 1280  # The page width every run lays pages out at (the browser context's viewport)

def browser_args():
    """Chromium arguments putting its window on the right half, with the console moved to the left half; [] if off.
    The page is zoomed out to fit, so it is still laid out 1,280 pixels wide, as in every test run."""
    if not enabled():
        return []
    minimize_previous_report()
    place(console_window(), "left")
    x, y, width, height = half("right")
    # Chromium reads its window's position and size in zoomed units too
    scale = width / LAYOUT_WIDTH
    return [f"--window-position={round(x / scale)},{round(y / scale)}",
            f"--window-size={round(width / scale)},{round(height / scale)}", f"--force-device-scale-factor={scale:.4f}"]


def place_foreground_right(console_too=True):
    """After the report opens in the default browser, in front: that window to the right half, the console left."""
    if not enabled():
        return
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    console = console_window()
    if hwnd and hwnd != console:
        place(hwnd, "right")
        try:
            with open(report_window_file(), "w") as f:
                f.write(str(hwnd))
        except OSError:
            pass
    if console_too:
        place(console, "left")


def report_window_file():
    """Where the report window's handle is kept between runs in one console window."""
    return os.path.join(tempfile.gettempdir(), f"LocalAgentBrowser-report-window-{console_window()}.txt")


def minimize_previous_report():
    """From the second run in one console window: the report window from the run before is minimized, so the new
    browser window is seen. Windows does not bring a new window in front of the one the person is looking at, and on
    the second run of a live test Chromium opened behind the first report and was never seen working."""
    try:
        with open(report_window_file()) as f:
            hwnd = int(f.read().strip())
        os.remove(report_window_file())
    except (OSError, ValueError):
        return
    if hwnd and ctypes.windll.user32.IsWindowVisible(hwnd):
        ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE)
