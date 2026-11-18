"""Hook text, title and description that read like a person wrote them.

The LLM drafts, `lint()` rejects anything with machine tells or made-up facts, and the
draft is retried with the lint feedback. If the model can't pass, a plain template is used.
"""
import random
import re

from . import log
from .llm import parse_json

# Words and patterns that make captions read as machine-written or as cheap clickbait.
BANNED = [
    "delve", "unleash", "embark", "testament", "tapestry", "elevate", "realm", "unlock",
    "in this video", "you won't believe", "mind-blowing", "mind blowing", "game-changer",
    "game changer", "buckle up", "dive into", "let's dive", "ever wondered", "look no further",
    "a must-watch", "must watch", "jaw-dropping", "jaw dropping", "epic", "ultimate", "insane",
    "absolutely", "truly", "captivating", "whimsical", "timeless classic", "hilarious moment",
    "sit back", "get ready", "nothing short of", "showcases", "showcasing", "iconic", "legendary",
    "ai", "artificial intelligence", "generated",
]
MAX_EMOJI = 1
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def _stopwords():
    return {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with", "his", "her",
            "this", "that", "when", "what", "who", "how", "why", "i", "it", "is", "was", "be", "my",
            "no", "not", "one", "just", "then", "every", "watch", "wait", "nobody", "somebody", "film"}


def lint(copy: dict, facts: str) -> list[str]:
    """Return a list of problems (empty = good)."""
    problems = []
    l1, l2 = copy.get("hook_line1", "").strip(), copy.get("hook_line2", "").strip()
    title, desc = copy.get("title", "").strip(), copy.get("description", "").strip()
    kw = copy.get("keyword", "").strip()

    if not (l1 and l2 and title and desc):
        problems.append("missing field")
    for name, text, lo, hi in (("hook_line1", l1, 3, 24), ("hook_line2", l2, 3, 24),
                               ("title", title, 18, 70), ("description", desc, 40, 600)):
        if text and not lo <= len(text) <= hi:
            problems.append(f"{name} must be {lo}-{hi} characters (got {len(text)})")
    if kw and kw.lower() not in f"{l1} {l2}".lower():
        problems.append("keyword must appear in the hook lines")

    everything = f"{l1} {l2} {title} {desc}"
    low = everything.lower()
    for b in BANNED:
        if re.search(rf"(?<![a-z]){re.escape(b)}(?![a-z])", low):
            problems.append(f"remove the phrase {b!r}")
    if "—" in everything or " -- " in everything:
        problems.append("no em dashes")
    if len(EMOJI.findall(everything)) > MAX_EMOJI:
        problems.append("at most one emoji in total")
    if everything.count("!") > 1:
        problems.append("at most one exclamation mark")
    if len(re.findall(r"\b[A-Z]{4,}\b", f"{title} {desc}")) > 1:
        problems.append("no shouting in caps")
    if "#" in title:
        problems.append("no hashtags in the title")

    # Honesty: proper names must come from the source facts, never invented.
    known = facts.lower()
    for name in re.findall(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,})*)", f"{title}. {desc}"):
        words = [w for w in name.lower().split() if w not in _stopwords()]
        if words and not all(w in known for w in words):
            problems.append(f"name {name!r} is not in the source facts - don't invent names")
    return sorted(set(problems))


PROMPT = """You write captions for a YouTube Shorts channel that posts clips from public-domain films.
Channel voice: {voice}

SOURCE FACTS (the only facts you may use; never invent names, dates or claims):
- Film: {title} ({year})
- Notes: {notes}
- What happens in this clip (from frame analysis): {what}

Write JSON with exactly these keys:
"hook_line1": first line of on-screen text, 2-5 words, sets up the moment
"hook_line2": second line, 2-5 words, the payoff or tension
"keyword": one or two words from the hook lines to highlight in colour
"title": YouTube title, 25-60 characters, sentence case, specific to this clip
"description": 1-3 short sentences in the channel voice, with one real detail from the facts

Style rules: sound like a person, not an ad. Plain words. No hype words (epic, insane, ultimate,
legendary, iconic, jaw-dropping), no "you won't believe", no em dashes, no hashtags, at most one
emoji in total, at most one exclamation mark. Don't mention AI.
{feedback}"""


def template_copy(item, rng: random.Random) -> dict:
    who = item.title.split("(")[0].strip()[:40]
    pairs = [("Wait for", "the ending"), ("Nobody saw", "this coming"), ("Watch the", "background"),
             ("Timing is", "everything"), ("One wrong", "step later")]
    l1, l2 = rng.choice(pairs)
    return {"hook_line1": l1, "hook_line2": l2, "keyword": l2.split()[-1],
            "title": f"A moment from {who}"[:70] if len(who) > 4 else "A moment from a silent classic",
            "description": f"A clip from {item.title}{f' ({item.year})' if item.year else ''}."}


def write(llm, item, judged: dict, voice: str, seed: int, attempts: int = 3) -> dict:
    rng = random.Random(seed)
    facts = f"{item.title} {item.year or ''} {item.text} {judged.get('what_happens', '')}"
    feedback = ""
    for i in range(attempts):
        raw = llm.text(PROMPT.format(voice=voice, title=item.title, year=item.year or "unknown",
                                     notes=item.text[:600] or "none",
                                     what=judged.get("what_happens") or "not available",
                                     feedback=feedback), as_json=True)
        copy = parse_json(raw)
        if not copy:
            break
        problems = lint(copy, facts)
        if not problems:
            copy["source"] = f"llm (attempt {i + 1})"
            return copy
        feedback = "Your last draft was rejected. Fix these problems:\n- " + "\n- ".join(problems)
        log.info("copy draft %d rejected: %s", i + 1, "; ".join(problems))
    copy = template_copy(item, rng)
    copy["source"] = "template"
    return copy


def description(copy: dict, item, music_credit: str | None, tags: list[str]) -> str:
    """Final description: the human-written blurb + honest provenance (helps with Content ID disputes)."""
    lines = [copy["description"].strip(), "",
             f"From: {item.title}{f' ({item.year})' if item.year else ''}",
             f"Licence: {item.licence}",
             f"Source: {item.page_url}" if item.page_url else "",
             item.credit]
    if music_credit:
        lines.append(f"Music: {music_credit}")
    lines.append("Footage is original archival film; edited for this channel (cut, reframed, captioned).")
    tag_line = " ".join("#" + re.sub(r"\W", "", t.title()) for t in tags[:3])
    return "\n".join(x for x in lines if x is not None).strip() + ("\n\n" + tag_line if tag_line else "")
