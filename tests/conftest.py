import os
import sys

# Make agent_browser.py (repo root) and the shared fakes (this folder) importable from every test
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS_DIR))
sys.path.insert(0, TESTS_DIR)
sys.dont_write_bytecode = True
