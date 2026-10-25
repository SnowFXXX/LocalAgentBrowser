"""Run the agent against the real model and browser with a fixed objective and a time limit, logging
the timing and token counts of every model call.

Usage:
    python tools/live_test.py "Your objective here" [--minutes 25] [--out run_output]

Writes into the output folder (created if needed):
    research_log.txt   the agent's journal, as in a normal run
    prompt_trace.txt   the last message of every model request and the full reply, for checking what the model saw and said
Progress and per-call timings are printed to the console.
"""
import argparse
import asyncio
import builtins
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agent_browser as ab  # noqa: E402
from localagentbrowser import agent  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("objective", help="the objective to give the agent")
    parser.add_argument("--minutes", type=float, default=25, help="stop the run after this many minutes (default 25)")
    parser.add_argument("--out", default="run_output", help="folder for the logs (default ./run_output)")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    os.chdir(args.out)
    builtins.input = lambda prompt="": (print(prompt + "<objective from command line>"), args.objective)[1]

    original_client = agent.AsyncOpenAI

    def traced_client(**kwargs):
        # Every model call, including the objective split before turn 1, logs its timing, token counts and newest message
        client = original_client(**kwargs)
        create = client.chat.completions.create

        async def timed_create(*a, **kw):
            with open("prompt_trace.txt", "a", encoding="utf-8") as f:
                f.write(f"\n\n######## PROMPT ########\n{kw['messages'][-1]['content']}")
            started = time.time()
            response = await create(*a, **kw)
            # The full reply as well: the journal keeps only the start of a refused action
            with open("prompt_trace.txt", "a", encoding="utf-8") as f:
                f.write(f"\n\n######## REPLY ########\n{response.choices[0].message.content}")
            print(f"   (model call {time.time() - started:.1f}s, prompt={response.usage.prompt_tokens} tok, "
                  f"completion={response.usage.completion_tokens} tok)")
            return response

        client.chat.completions.create = timed_create
        return client

    agent.AsyncOpenAI = traced_client  # main() creates its client in the agent module

    async def run():
        try:
            await asyncio.wait_for(ab.main(), timeout=args.minutes * 60)
        except asyncio.TimeoutError:
            print("\n⏹️ TEST TIME LIMIT REACHED")

    asyncio.run(run())


if __name__ == "__main__":
    main()
