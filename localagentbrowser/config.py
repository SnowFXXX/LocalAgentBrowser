"""Settings: the LM Studio connection, read from environment variables, and every limit and threshold."""
import os

BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")

MODEL = os.environ.get("LMSTUDIO_MODEL", "qwen2.5-7b-instruct")

API_KEY = os.environ.get("LMSTUDIO_API_KEY", "lm-studio")

CONTEXT_LENGTH = int(os.environ.get("LMSTUDIO_CONTEXT_LENGTH", "8192"))

SEARCH_URL = "https://html.duckduckgo.com/html/?q="

REPORT_FILE = "research_report.md"

JOURNAL_BATCH = 6        # The prompt shows 6-11 recent journal entries; old ones are dropped 6 at a time

MAX_JOURNAL_DETAIL_CHARS = 120  # Longest query/URL/request text shown per journal line

MAX_JOURNAL_REASON_CHARS = 120  # Longest refusal reason shown per journal line (the full text is in research_log.txt)

MAX_THOUGHT_CHARS = 120 # Old reasoning is trimmed in the prompt; the full text stays in research_log.txt

MAX_SEARCH_RESULTS = 8

MAX_SNIPPET_CHARS = 200

PAGE_PART_CHARS = 2500   # Pages are shown in parts of about this size; READ_MORE shows the next part

MAX_LINKS = 8           # Links listed per page; most pages are reached from search results

CONTENT_LINK_WORDS = 3   # Links with at least this many words of text are listed before shorter (menu) links

OUTLINE_MIN_PARTS = 5    # Pages with at least this many parts get an outline of what each part covers

MAX_OUTLINE_LINES = 40

MAX_OUTLINE_LABEL = 90

LONG_PAGE_UNREAD = 3     # Above this many unread parts, the leave check points to the outline instead of the count

MAX_JSON_RETRIES = 3

MAX_VISITS_PER_URL = 3   # Returning to an earlier page is allowed; this caps back-and-forth loops

GAP_REFUSAL_LIMIT = 2    # FINISH attempts sent back per run because their own reasoning lists a part as not covered

MAX_CHEAP_RETRIES = 2  # Refused replies re-asked within the same turn before the refusal costs a whole turn

MAX_FOLLOWUPS = 12       # Turns that continue one conversation before a fresh full prompt (the token budget usually ends it first)

MAX_CONVERSATION_TOKENS = int(CONTEXT_LENGTH * 0.75)  # Start fresh with room left for the next page and the reply

MAX_DISCOVERY_PROMPT_CHARS = 250  # Discoveries are shortened to this in fresh prompts; the log keeps the full text

MAX_CHECK_DISCOVERY_CHARS = 150   # Shorter still in the numbered list that comes with the finish and progress checks

GROUNDED_PAIR_SHARE = 0.7  # A DISCOVER sharing fewer of its word pairs with the page text shown is confirmed first

MIN_GROUNDING_PAIRS = 20   # Shorter discoveries are not checked: a few connecting words sink their score

GROUNDED_SENTENCE_SHARE = 0.35  # Any one sentence matching the page text shown less than this is confirmed too

MIN_SENTENCE_PAIRS = 8     # Shorter sentences are not scored on their own

UNGROUNDED_PAIR_SHARE = 0.4       # Below this (whole) or the next (any sentence) a DISCOVER is refused outright

UNGROUNDED_SENTENCE_SHARE = 0.25

MAX_CLOSEST_CHARS = 200   # Longest page sentence quoted when a DISCOVER is refused as not on the page

TURN_PAUSE_SECONDS = 1

STALL_TURNS = 4          # Turns without a new discovery before the model is asked to check its progress

COMPOSE_MAX_TOKENS = 900  # Length limit for the separately written final result

VIEW_MAX_TOKENS = 250     # Length limit for a 'My view:' paragraph added when the result left it out

OBJECTIVE_CHECK_EVERY = 8 # Turns between scheduled objective checks, which happen even while discoveries continue

MAX_CHECKLIST_ITEMS = 8   # Most parts an objective is split into; a longer split is cut, a failed one is the whole objective

MAX_CHECKLIST_ITEM_CHARS = 200

SPLIT_MAX_TOKENS = 300

COVERAGE_MAX_TOKENS = 400  # Length limit for the check of one checklist item before finishing

MAX_CONDITION_WORDS = 2    # Longer "conditions" are descriptions of the item, not properties a thing must have

MAX_THING_NAME_WORDS = 6   # Longer "things" named by the coverage check are descriptions, not names

MAX_MISSING_CLAIM_WORDS = 4  # Short "missing" claims are checked against the discoveries' words; longer ones are kept

KNOWN_LINE_THRESHOLD = 0.9
