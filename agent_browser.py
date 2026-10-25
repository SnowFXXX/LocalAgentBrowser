"""LocalAgentBrowser: an autonomous web agent driven by a local language model.

Run:  python agent_browser.py   (or `localagentbrowser` once installed; it asks for the objective)

The code lives in the localagentbrowser package (see its __init__.py for how it is organized). This file keeps
`python agent_browser.py` working from a clone, and `import agent_browser` working for the tests and tools: it
gives access to every name in the package. To replace a part of the agent in a test, patch the module that
uses it (for example localagentbrowser.agent.AsyncOpenAI), not this file.
"""
from localagentbrowser.config import *  # noqa: F403
from localagentbrowser.prompts import *  # noqa: F403
from localagentbrowser.text import *  # noqa: F403
from localagentbrowser.pages import *  # noqa: F403
from localagentbrowser.grounding import *  # noqa: F403
from localagentbrowser.checklist import *  # noqa: F403
from localagentbrowser.coverage import *  # noqa: F403
from localagentbrowser.checks import *  # noqa: F403
from localagentbrowser.report import *  # noqa: F403
from localagentbrowser.agent import *  # noqa: F403
from localagentbrowser.agent import main, run  # noqa: F401

if __name__ == "__main__":
    run()
