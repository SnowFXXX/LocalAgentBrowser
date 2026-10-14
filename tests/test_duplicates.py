"""Duplicate discovery detection: repeats are refused however they are reformatted, while anything
that adds genuinely new information is accepted."""
import pytest

import agent_browser as ab
from fakes import discoveries

FILE_LAYOUT = "repo/\n.github/\nspecs/\ntests/\nplaywright.config.ts\nnpx playwright init-agents --loop=vscode\nseed.spec.ts"
FILE_LAYOUT_REORDERED = "npx playwright init-agents --loop=vscode\nrepo/\n.github/\nspecs/\ntests/\nplaywright.config.ts\nseed.spec.ts"

# The same configuration recorded twice: once squeezed onto one line, once laid out with a comment per option
CONFIG_ONE_LINE = ("import { defineConfig } from '@playwright/test', testDir: 'tests', fullyParallel: true, "
                   "retries: process.env.CI ? 2 : 0, reporter: 'html', use: { baseURL: 'http://localhost:3000', "
                   "trace: 'on-first-retry' }")
CONFIG_COMMENTED = """import { defineConfig } from '@playwright/test';
export default defineConfig({
  // Look for test files in this directory.
  testDir: 'tests',
  // Run every test file in parallel.
  fullyParallel: true,
  // Retry twice, on CI only.
  retries: process.env.CI ? 2 : 0,
  // Write an HTML report.
  reporter: 'html',
  use: {
    // Base URL for page.goto('/').
    baseURL: 'http://localhost:3000',
    // Keep a trace when a test is retried.
    trace: 'on-first-retry',
  },
});"""


@pytest.mark.parametrize("new, recorded, expected", [
    (FILE_LAYOUT_REORDERED, [FILE_LAYOUT], True),
    ("npx playwright init-agents --loop=vscode", [FILE_LAYOUT], True),
    ("  NPX playwright   init-agents --loop=vscode ", [FILE_LAYOUT], True),
    ("npx playwright init-agents --loop=vscode\nnpx playwright init-agents --loop=claude", [FILE_LAYOUT], False),
    ("Cats purr at 25-150 Hz.", [FILE_LAYOUT], False),
    ("npx playwright test --ui", ["npx playwright test --debug", "run in ui mode"], False),
    (CONFIG_ONE_LINE + "\nretries can also be set per project", [CONFIG_ONE_LINE], False),
    ("A clear request (e.g., “Generate a plan.”)", ["A clear request (e.g., 'Generate a plan.')"], True),
    ("", [FILE_LAYOUT], False),
], ids=[
    "same lines reordered", "one recorded line", "case and spacing", "recorded line plus a new variant",
    "entirely new", "new command built from familiar words", "duplicate block plus one new line",
    "curly vs straight quotes", "empty",
])
def test_known_discovery(new, recorded, expected):
    assert ab.is_known_discovery(new, discoveries(*recorded)) is expected


def test_commented_config_adds_information_over_one_line_version():
    assert not ab.is_known_discovery(CONFIG_COMMENTED, discoveries(CONFIG_ONE_LINE))


def test_one_line_config_after_commented_version_is_duplicate():
    assert ab.is_known_discovery(CONFIG_ONE_LINE, discoveries(CONFIG_COMMENTED))


def test_repeated_commented_config_is_duplicate():
    assert ab.is_known_discovery(CONFIG_COMMENTED, discoveries(CONFIG_ONE_LINE, CONFIG_COMMENTED))


def test_commented_config_rejoined_on_one_line_is_duplicate():
    assert ab.is_known_discovery(" ".join(CONFIG_COMMENTED.splitlines()), discoveries(CONFIG_COMMENTED))


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_blank_text_has_no_discovery_lines(text):
    assert not ab.discovery_lines(text)
