"""The Windows UI's icon in the notification area (the tray): the UI window goes there when minimized, a click brings
it back, and its menu opens the window, stops the run, opens the reports folder, or quits. Plain Windows calls
(ctypes), in a thread of its own with its own message loop; each menu choice is handed to the UI's event loop.
Nothing is shown when a run ends. Does nothing elsewhere than Windows.
"""
import ctypes
import os
import sys
import threading

if sys.platform == "win32":
    from ctypes import wintypes

    user32, shell32, kernel32 = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                       wintypes.HINSTANCE, wintypes.LPVOID]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadIconW.restype = wintypes.HANDLE
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      wintypes.HWND, wintypes.LPVOID]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                    ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                    ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

WM_DESTROY, WM_CLOSE, WM_LBUTTONUP, WM_RBUTTONUP, WM_APP = 0x0002, 0x0010, 0x0202, 0x0205, 0x8000
NIM_ADD, NIM_DELETE, NIF_MESSAGE, NIF_ICON, NIF_TIP = 0, 2, 1, 2, 4
MF_STRING, MF_GRAYED, MF_SEPARATOR = 0, 1, 0x800
TPM_RETURNCMD, TPM_RIGHTBUTTON = 0x100, 0x2
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE, IDI_APPLICATION = 1, 0x10, 0x40, 32512
CALLBACK = WM_APP + 1
# The menu, in order: (command, label); None is a separator. "stop" is greyed while no run is going
MENU = [("open", "Open LocalAgentBrowser"), ("stop", "Stop the run"), ("folder", "Open the reports folder"), None,
        ("quit", "Quit")]
ICON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "windows", "localagentbrowser.ico")


class Tray:
    """The tray icon. on_command(name) is called in the tray's thread with "open", "stop", "folder" or "quit"; the
    UI hands it to its own loop. running() says whether "stop" applies."""

    def __init__(self, tooltip, on_command, running=lambda: False):
        self.tooltip, self.on_command, self.running = tooltip, on_command, running
        self.hwnd, self.thread, self.ready = None, None, threading.Event()

    def start(self):
        if sys.platform != "win32":
            return False
        self.thread = threading.Thread(target=self.loop, name="tray", daemon=True)
        self.thread.start()
        self.ready.wait(5)
        return bool(self.hwnd)

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        if self.thread:
            self.thread.join(5)

    def loop(self):
        try:
            self.create()
        finally:
            self.ready.set()
        if not self.hwnd:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def create(self):
        instance = kernel32.GetModuleHandleW(None)
        self.proc = WNDPROC(self.window_proc)  # Kept: Windows calls it for as long as the window lives
        cls = WNDCLASSW(lpfnWndProc=self.proc, hInstance=instance, lpszClassName=f"LocalAgentBrowserTray{os.getpid()}")
        user32.RegisterClassW(ctypes.byref(cls))
        self.hwnd = user32.CreateWindowExW(0, cls.lpszClassName, self.tooltip, 0, 0, 0, 0, 0, None, None, instance, None)
        if not self.hwnd:
            return
        icon = (user32.LoadImageW(None, ICON, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
                if os.path.exists(ICON) else None) or user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
        self.data = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self.hwnd, uID=1,
                                    uFlags=NIF_MESSAGE | NIF_ICON | NIF_TIP, uCallbackMessage=CALLBACK, hIcon=icon,
                                    szTip=self.tooltip[:127])
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.data)):
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None

    def window_proc(self, hwnd, message, wparam, lparam):
        if message == CALLBACK:
            if lparam == WM_LBUTTONUP:
                self.on_command("open")
            elif lparam == WM_RBUTTONUP:
                self.menu()
            return 0
        if message == WM_CLOSE:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.data))
            user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def menu(self):
        menu = user32.CreatePopupMenu()
        for number, item in enumerate(MENU, 1):
            if item is None:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            else:
                greyed = item[0] == "stop" and not self.running()
                user32.AppendMenuW(menu, MF_STRING | (MF_GRAYED if greyed else 0), number, item[1])
        point = POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(self.hwnd)  # Without it the menu does not close when clicked away from
        chosen = user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, point.x, point.y, 0, self.hwnd, None)
        user32.DestroyMenu(menu)
        if chosen and MENU[chosen - 1]:
            self.on_command(MENU[chosen - 1][0])
