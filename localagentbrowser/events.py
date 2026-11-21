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
LISTENERS = []


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
