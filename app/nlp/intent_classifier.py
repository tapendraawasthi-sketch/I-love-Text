from __future__ import annotations

import re

# ── intent labels ──────────────────────────────────────────────
# nav          → user wants WHERE to find / how to open / keyboard shortcut
# action_path  → user wants HOW TO MAKE/CREATE something (give path only)
# definition   → user wants WHAT something IS
# steps        → user wants step-by-step procedure only
# troubleshoot → user has an error or something is not working
# effect       → user wants the accounting debit/credit effect
# code         → developer question about source code / implementation
# general      → catch-all; answer concisely and only what is asked

# ── PRIORITY ORDER (tested top to bottom, first match wins) ────
# 1. code         (source/implementation/function/component/schema)
# 2. troubleshoot (error / not working / why isn't / can't / fails)
# 3. effect       (what gets debited/credited, accounting effect)
# 4. steps        (steps to / step by step / procedure / walk me through)
# 5. nav          (where is / how to open/access/find/go to / shortcut)
# 6. action_path  (how to make/create/post/record/enter/pass/generate/add)
# 7. definition   (what is / explain / tell me about / describe)
# 8. general      (fallback)

# ── 1. CODE — developer questions about source/implementation ──
_CODE_PATTERNS = re.compile(
    r"\b(code|function|component|hook|api (route|endpoint)|schema|"
    r"database|implementation|source (file|code)|where in (the )?code|"
    r"how is .+ (implemented|built|coded|stored)|developer|backend|frontend|"
    r"typescript|\.tsx?|sql query|table (schema|structure)|column|"
    r"which (file|module|class) |renders?|supabase|rpc|query)\b",
    re.IGNORECASE,
)

# ── 2. TROUBLESHOOT — errors, failures, "why isn't X" ──────────
_TROUBLESHOOT_PATTERNS = re.compile(
    r"\b(error|not working|issue|problem|failed|failing|fails|"
    r"can'?t|cannot|won'?t|doesn'?t work|"
    r"why (is|isn'?t|does|doesn'?t|can'?t|won'?t)|"
    r"not (showing|posting|saving|appearing|reflecting|working|balanced?|calculating)|"
    r"getting .+ error|throws?|exception|stuck|frozen|crash|bug)\b",
    re.IGNORECASE,
)

# ── 3. EFFECT — accounting debit/credit entry ──────────────────
_EFFECT_PATTERNS = re.compile(
    r"\b(what (gets?|is|will be) (debited|credited)|"
    r"accounting (entry|effect|treatment)|"
    r"journal entry for|debit .+ credit|credit .+ debit|"
    r"debit credit (for|of|in)|credit debit (for|of|in)|"
    r"double entry (for|of)|"
    r"which account (gets?|is|will be)|ledger effect|gl entry|"
    r"dr[./]?cr|what is (the )?entry for)\b",
    re.IGNORECASE,
)

# ── 4. STEPS — explicit step-by-step procedure request ─────────
_STEPS_PATTERNS = re.compile(
    r"\b(steps (for|to)|step[- ]by[- ]step|procedure (for|to)|process (for|of)|"
    r"guide me (through|on)|walk me through|"
    r"how (exactly|specifically) (do i|to|does)|"
    r"detailed (steps|procedure|process|instructions))\b",
    re.IGNORECASE,
)

# ── 5. ACTION_PATH — "how to make/create X" (path only) ────────
# CRITICAL: This must be tested AFTER nav (so "access/open" are nav).
# Catches "how to <action-verb>" patterns and Nepali ERP verbs.
# Note: "do" is NOT included as it would match "how do I" universally.
_ACTION_PATH_PATTERNS = re.compile(
    r"\b(how (do i |to |can i )?"
    r"(make|create|post|record|enter|add|pass|generate|cut|raise|"
    r"prepare|issue|file|submit|save|write|book|lodge))\b",
    re.IGNORECASE,
)

# ── 6. NAV — "where is X" / shortcut / how to open ─────────────
# Note: "access" is nav, not action_path. Tested BEFORE action_path
# would incorrectly match "how do I access" as an action.
_NAV_PATTERNS = re.compile(
    r"\b(where (is|do i|can i|to)|"
    r"how (do i |to |can i )?(open|access|find|go to|get to|navigate|reach|see|view)|"
    r"shortcut (for|to)|keyboard shortcut|hotkey|"
    r"press which|which key|what key|path (to|for)|menu (for|of)|location of)\b",
    re.IGNORECASE,
)

# ── 7. DEFINITION — "what is X" / explain / describe ───────────
# IMPORTANT: This is tested AFTER action_path, so "how to make X"
# will NOT fall through here even though "what is" is broad.
_DEFINITION_PATTERNS = re.compile(
    r"\b(what (is|are)|explain|tell me about|describe|meaning of|"
    r"definition of|what does .+ (do|mean)|"
    r"what'?s (a |an |the )?[a-z]|define)\b",
    re.IGNORECASE,
)

_QUESTION_WORD = re.compile(
    r"^(how|what|where|why|when|which|can|could|would|should|do|does|did|"
    r"is|are|was|were|explain|tell|show|list|steps|step)\b",
    re.IGNORECASE,
)

_ERP_TOPIC = re.compile(
    r"\b(journal|voucher|invoice|payment|receipt|contra|ledger|trial\s*balance|"
    r"day\s*book|balance\s*sheet|chart\s*of\s*accounts|party|parties|stock|"
    r"vat|tds|payroll|report|sales|purchase|credit\s*note|debit\s*note)\b",
    re.IGNORECASE,
)


def _is_bare_topic(q: str) -> bool:
    """Short feature name without question word → definition (e.g. 'journal voucher')."""
    text = q.strip()
    if not text or _QUESTION_WORD.search(text):
        return False
    words = re.sub(r"[?.,!]", "", text).split()
    if not words or len(words) > 4:
        return False
    return bool(_ERP_TOPIC.search(text))


def classify(question: str) -> str:
    """Return the intent label for a user question.

    PRIORITY ORDER (first match wins):
      1. code         → developer/source questions
      2. troubleshoot → errors, not working, why isn't
      3. effect       → accounting debit/credit entry
      4. steps        → explicit step-by-step procedure
      5. nav          → where is, how to open/access, shortcut
      6. action_path  → how to make/create (path only)
      7. definition   → what is, explain, describe
      8. general      → catch-all
    """
    q = question.strip()

    # 1. code — developer questions
    if _CODE_PATTERNS.search(q):
        return "code"

    # 2. troubleshoot — errors, failures
    if _TROUBLESHOOT_PATTERNS.search(q):
        return "troubleshoot"

    # 3. effect — accounting entry
    if _EFFECT_PATTERNS.search(q):
        return "effect"

    # 4. steps — explicit step-by-step request
    if _STEPS_PATTERNS.search(q):
        return "steps"

    # 5. nav — "where is X", shortcut, how to open/access
    # MUST be before action_path so "how do I access" → nav
    if _NAV_PATTERNS.search(q):
        return "nav"

    # 6. action_path — "how to make/create X"
    # MUST be before definition so "how to make a journal entry" → action_path
    if _ACTION_PATH_PATTERNS.search(q):
        return "action_path"

    # 7. definition — "what is X", explain, describe, bare topic
    if _DEFINITION_PATTERNS.search(q):
        return "definition"

    if _is_bare_topic(q):
        return "definition"

    # 8. general — fallback
    return "general"
