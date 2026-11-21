"""What the agent is doing, as events for a view to draw: the Windows UI's live view.

The console's own lines are unchanged. Classic mode prints them as before, and the regression suite, SimpleQA and
the compatibility test read them; events are a second way to follow the same run, and change nothing in it. A view
registers a listener; each event is a kind and a dict of plain values (text, numbers, lists), so it can be sent to a
page as JSON.

Kinds, in the order a run sends them:
- objective: objective
- checklist: items (each {"text", "discoveries"})
- turn: number, of
- waiting / replied: a model call started / ended (seconds)
- decision: action, parameter, thought
- retried: action, parameter, reason (a reply refused and asked again within the turn)
- refused: action, parameter, reason, check (refused for the turn)
- page: url, title, part, parts, outline, search (a page or search results shown to the model)
- failed: action, parameter, error
- read_more: part, parts
- discovery: number, text, source, items, checklist
- checking: (the checks before finishing)
- writing: (the final result is being written)
- result: text
- stopped: reason (None when the agent finished)
"""
import json
import os

LISTENERS = []
# A run started by the Windows UI (winui.py) is a process of its own: it prints its usual console text, and each event
# as one line starting with this mark, which the UI reads and never shows as text
MARK = "\x1eLAB_EVENT "


def listen(listener):
    """Adds a listener: a function called with (kind, data) for every event."""
    LISTENERS.append(listener)
    return listener


def stop_listening(listener):
    if listener in LISTENERS:
        LISTENERS.remove(listener)


def emit(kind, **data):
    for listener in list(LISTENERS):
        try:
            listener(kind, data)
        except Exception:  # A view must never stop or change a run
            pass


def print_line(kind, data):
    """A listener printing each event as a marked line (a run started by the Windows UI)."""
    print(MARK + json.dumps({"kind": kind, **data}, ensure_ascii=False, default=str), flush=True)


def read_line(line):
    """(kind, data) of a marked event line, or None for a line of console text."""
    if not line.startswith(MARK):
        return None
    try:
        event = json.loads(line[len(MARK):])
    except ValueError:
        return None
    return event.pop("kind", ""), event


class Stopped(BaseException):
    """The view asked the run to stop (the Windows UI's Stop button, or its time limit). Not an Exception, so no
    handler meant for a failed call or page takes it: the run ends as on Ctrl+C, and its report says why."""


def stop_requested():
    """The reason a view asked the run to stop, or None: the file LAB_STOP_FILE names exists, holding the reason."""
    path = os.environ.get("LAB_STOP_FILE")
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or "stopped"
    except OSError:
        return "stopped"
