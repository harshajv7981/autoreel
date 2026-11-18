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

    beats = copy.get("beats") or []
    if not isinstance(beats, list) or not 2 <= len(beats) <= 3:
        problems.append("beats must be a list of 2-3 short captions")
        beats = beats if isinstance(beats, list) else []
    for b in beats:
        if not isinstance(b, str) or not 8 <= len(b.strip()) <= 70:
            problems.append("each beat must be 8-70 characters")
    everything = f"{everything} {' '.join(map(str, beats))}"
    low = everything.lower()
    for b in BANNED:
        if any(re.search(rf"(?<![a-z]){re.escape(b)}(?![a-z])", str(x).lower()) for x in beats):
            problems.append(f"remove the phrase {b!r}")

    # Honesty: every number and year must come from the facts.
    known_numbers = set(re.findall(r"\d[\d,.]*", facts))
    for n in set(re.findall(r"\d[\d,.]*", everything)):
        if n.rstrip(".,") not in {k.rstrip(".,") for k in known_numbers}:
            problems.append(f"the number {n.rstrip('.,')!r} is not in the facts - don't invent numbers")

    # Honesty: proper names must come from the source facts, never invented.
    known = facts.lower()
    named = f"{title}. {desc}. " + ". ".join(str(b) for b in beats)
    for name in re.findall(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,})*)", named):
        words = [w for w in name.lower().split() if w not in _stopwords()]
        if words and not all(w in known for w in words):
            problems.append(f"name {name!r} is not in the source facts - don't invent names")
    return sorted(set(problems))


PROMPT = """You write the packaging and captions for a YouTube Shorts channel that posts REAL archival
public-domain footage for viewers in the US and UK. There is no voice-over: on-screen text tells the story.
Channel voice: {voice}

SOURCE FACTS (the only facts you may use; never invent names, dates, numbers or claims):
- Footage: {title} ({year})
- Archive notes: {notes}
- What happens in this clip (from frame analysis): {what}
{topic}
Packaging that works: a specific subject + a surprising true detail, so the viewer needs to see what happens.
Pattern examples (style only, not facts): "The nuclear test that accidentally vaporized an island",
"Why this bridge was built to sway", "The airship that crossed the Atlantic before planes could".

Return JSON with:
"options": 4 different packaging options, each {{"hook_line1": 2-5 words, "hook_line2": 2-5 words,
           "keyword": 1-2 words from the hook to colour, "title": 30-60 characters, sentence case}}
"description": 1-3 short sentences in the channel voice, with one real detail from the facts
"beats": 2-3 on-screen captions shown one after another (each 4-12 words): context first, then the
         surprising detail. Put any real number or year from the facts in them.

Style rules: sound like a person, not an ad. Plain words. No hype words (epic, insane, ultimate,
legendary, iconic, jaw-dropping), no "you won't believe", no em dashes, no hashtags, at most one
emoji in total, at most one exclamation mark. Don't mention AI. Never claim something the facts don't say.
{feedback}"""

CURIOSITY = ["accidentally", "almost", "nearly", "never", "first", "last", "only", "why", "how", "before",
             "after", "forgot", "wrong", "nobody", "still", "until", "lost", "the day", "what happened",
             "built to", "tried to", "nearly", "instead"]
GENERIC = ["moment", "moments", "video", "clip", "footage", "amazing", "interesting", "cool", "old film",
           "classic", "vintage", "watch this", "check out"]


def score(copy: dict, facts: str) -> float:
    """Packaging quality: specific, concrete, curious, not generic. Grounding is lint's job."""
    title = copy.get("title", "")
    hook = f"{copy.get('hook_line1', '')} {copy.get('hook_line2', '')}".lower()
    t = title.lower()
    s = 2.0 if 35 <= len(title) <= 60 else 1.0 if 25 <= len(title) <= 70 else 0.0
    if re.search(r"\d", title):
        s += 1.5                                                    # concrete number or year
    names = re.findall(r"\b[A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,})*", title[1:])
    if any(n.lower() in facts.lower() for n in names):
        s += 1.5                                                    # a specific, real subject
    s += min(2, sum(1 for w in CURIOSITY if w in t or w in hook))
    s -= 1.5 * sum(1 for w in GENERIC if re.search(rf"\b{re.escape(w)}\b", t))
    if "?" in title:
        s -= 0.5
    if len(copy.get("hook_line1", "")) <= 18 and len(copy.get("hook_line2", "")) <= 18:
        s += 0.5
    shared = set(re.findall(r"[a-z]{4,}", t)) & set(re.findall(r"[a-z]{4,}", hook))
    if len(shared) >= 3:
        s -= 0.5                                                    # hook just repeats the title
    return round(s, 2)


def template_copy(item, rng: random.Random) -> dict:
    who = item.title.split("(")[0].strip()[:40]
    pairs = [("Wait for", "the ending"), ("Nobody saw", "this coming"), ("Watch the", "background"),
             ("Timing is", "everything"), ("One wrong", "step later")]
    l1, l2 = rng.choice(pairs)
    beats = [f"{who} ({item.year})" if item.year else who, "Real archival footage, public domain"]
    return {"hook_line1": l1, "hook_line2": l2, "keyword": l2.split()[-1], "beats": beats,
            "title": f"A moment from {who}"[:70] if len(who) > 4 else "A moment from a silent classic",
            "description": f"A clip from {item.title}{f' ({item.year})' if item.year else ''}."}


def topic_block(topic) -> str:
    if not topic:
        return ""
    return (f"- Why this matters today: {topic.why}\n- Today's topic: {topic.title}. {topic.angle}\n"
            f"- Topic facts (Wikipedia): {topic.facts[:900]}\n")


def write(llm, item, judged: dict, voice: str, seed: int, attempts: int = 3, topic=None) -> dict:
    """Draft -> expand options -> lint each -> score -> best. Retries with lint feedback."""
    rng = random.Random(seed)
    facts = f"{item.title} {item.year or ''} {item.text} {judged.get('what_happens', '')}"
    if topic:
        facts += f" {topic.title} {topic.why} {topic.facts}"
    feedback = ""
    for i in range(attempts):
        raw = llm.text(PROMPT.format(voice=voice, title=item.title, year=item.year or "unknown",
                                     notes=item.text[:600] or "none",
                                     what=judged.get("what_happens") or "not available",
                                     topic=topic_block(topic), feedback=feedback), as_json=True)
        data = parse_json(raw)
        if not data:
            break
        shared = {"description": data.get("description", ""), "beats": data.get("beats") or []}
        options = data.get("options") or [{k: data.get(k, "") for k in ("hook_line1", "hook_line2", "keyword", "title")}]
        passing, problems_all = [], []
        for opt in options[:6]:
            if not isinstance(opt, dict):
                continue
            cand = {**shared, **{k: str(opt.get(k, "")) for k in ("hook_line1", "hook_line2", "keyword", "title")}}
            problems = lint(cand, facts)
            if problems:
                problems_all += problems
            else:
                passing.append((score(cand, facts), cand))
        if passing:
            passing.sort(key=lambda x: x[0], reverse=True)
            best = dict(passing[0][1])
            best["score"] = passing[0][0]
            best["alternates"] = [{"title": c["title"], "hook": f"{c['hook_line1']} / {c['hook_line2']}",
                                   "score": sc} for sc, c in passing[1:]]
            best["source"] = f"llm (attempt {i + 1}, {len(passing)}/{len(options)} options passed)"
            return best
        feedback = ("Your last draft was rejected. Fix these problems:\n- " +
                    "\n- ".join(sorted(set(problems_all))[:12]))
        log.info("copy draft %d rejected: %s", i + 1, "; ".join(sorted(set(problems_all))[:6]))
    copy = template_copy(item, rng)
    copy["source"] = "template"
    copy["score"] = score(copy, facts)
    copy["alternates"] = []
    return copy


def description(copy: dict, item, music_credit: str | None, tags: list[str], topic_url: str | None = None) -> str:
    """Final description: the human-written blurb + honest provenance (helps with Content ID disputes)."""
    lines = [copy["description"].strip(), "",
             f"Background: {topic_url}" if topic_url else None,
             f"From: {item.title}{f' ({item.year})' if item.year else ''}",
             f"Licence: {item.licence}",
             f"Source: {item.page_url}" if item.page_url else "",
             item.credit]
    if music_credit:
        lines.append(f"Music: {music_credit}")
    lines.append("Footage is original archival film; edited for this channel (cut, reframed, captioned).")
    tag_line = " ".join("#" + re.sub(r"\W", "", t.title()) for t in tags[:3])
    return "\n".join(x for x in lines if x is not None).strip() + ("\n\n" + tag_line if tag_line else "")
