"""Run the agent against the real model and browser with a fixed objective and a time limit, logging
the timing and token counts of every model call.

Usage:
    python tools/live_test.py "Your objective here" [--minutes 25] [--out run_output]

Writes into the output folder (created if needed):
    research_log.txt   the agent's journal, as in a normal run
    prompt_trace.txt   the last message of every model request, for checking what the model saw
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


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("objective", help="the objective to give the agent")
    parser.add_argument("--minutes", type=float, default=25, help="stop the run after this many minutes (default 25)")
    parser.add_argument("--out", default="run_output", help="folder for the logs (default ./run_output)")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    os.chdir(args.out)
    builtins.input = lambda prompt="": (print(prompt + "<objective from command line>"), args.objective)[1]

    wrapped = set()
    original_turn = ab.run_agent_turn

    async def traced_turn(client, page, objective, journal, *turn_args, **turn_kwargs):
        # Wrap the client once so every model call logs its timing, token counts and newest message
        if id(client) not in wrapped:
            wrapped.add(id(client))
            create = client.chat.completions.create

            async def timed_create(*a, **kw):
                with open("prompt_trace.txt", "a", encoding="utf-8") as f:
                    f.write(f"\n\n######## PROMPT (journal entries: {len(journal)}) ########\n{kw['messages'][-1]['content']}")
                started = time.time()
                response = await create(*a, **kw)
                print(f"   (model call {time.time() - started:.1f}s, prompt={response.usage.prompt_tokens} tok, "
                      f"completion={response.usage.completion_tokens} tok)")
                return response

            client.chat.completions.create = timed_create
        return await original_turn(client, page, objective, journal, *turn_args, **turn_kwargs)

    ab.run_agent_turn = traced_turn

    async def run():
        try:
            await asyncio.wait_for(ab.main(), timeout=args.minutes * 60)
        except asyncio.TimeoutError:
            print("\n⏹️ TEST TIME LIMIT REACHED")

    asyncio.run(run())


if __name__ == "__main__":
    main()
