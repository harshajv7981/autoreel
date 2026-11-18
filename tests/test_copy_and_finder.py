import json

from autoreel import copywriter
from autoreel.compose.ass import ass_color, build_ass, highlight
from autoreel.finder import find_moments
from autoreel.sources.base import SourceItem

ITEM = SourceItem("internet_archive", "id1", "The Vagabond", 1916, "https://archive.org/details/id1",
                  "", "Public domain (US)", "The Vagabond (1916) via the Internet Archive",
                  text="Charlie Chaplin; Silents; a tramp violinist rescues a girl")
GOOD = {"hook_line1": "He only wanted", "hook_line2": "a quiet violin solo", "keyword": "violin",
        "title": "Chaplin's violin solo goes wrong",
        "description": "A tramp tries to play his violin in peace. The street has other plans.",
        "beats": ["A tramp violinist plays on the street", "Charlie Chaplin made it in 1916"]}


def test_good_copy_passes():
    assert copywriter.lint(GOOD, ITEM.title + " 1916 " + ITEM.text) == []


def test_ai_tells_and_hype_rejected():
    bad = {**GOOD, "title": "This epic moment is jaw-dropping — you won't believe it!!"}
    problems = " ".join(copywriter.lint(bad, ITEM.text))
    for word in ("epic", "jaw-dropping", "you won't believe", "em dash", "exclamation"):
        assert word.split()[0] in problems


def test_invented_names_rejected():
    bad = {**GOOD, "description": "Watch as Buster Keaton steals the show in a classic gag."}
    assert any("Buster Keaton" in p for p in copywriter.lint(bad, ITEM.title + " " + ITEM.text))


class FakeLLM:
    """First draft has hype words, second draft is clean: exercises the retry loop."""
    def __init__(self):
        self.calls = []

    def available(self):
        return True

    def text(self, prompt, as_json=False):
        self.calls.append(prompt)
        if len(self.calls) == 1:
            return json.dumps({**GOOD, "title": "The ultimate epic violin moment"})
        return json.dumps(GOOD)


def test_retry_with_feedback():
    llm = FakeLLM()
    item = SourceItem(**{**ITEM.__dict__, "year": 1916})
    copy = copywriter.write(llm, item, {"what_happens": "a man plays violin"}, "dry", seed=1)
    assert copy["source"].startswith("llm (attempt 2")
    assert "rejected" in llm.calls[1] and "ultimate" in llm.calls[1]


def test_template_fallback_without_llm():
    from autoreel.llm import NullLLM
    copy = copywriter.write(NullLLM(), ITEM, {}, "dry", seed=3)
    assert copy["source"] == "template" and copy["hook_line1"]


def test_description_has_provenance():
    d = copywriter.description(GOOD, ITEM, "Calm Piano (Mixkit)", ["silent film", "classic comedy"])
    assert "Licence: Public domain (US)" in d and "archive.org/details/id1" in d
    assert "Music: Calm Piano" in d and "#SilentFilm" in d


def test_ass_highlight_and_colors():
    assert ass_color("#FFD60A") == "&H000AD6FF"
    assert "{\\c&H000AD6FF&}violin" in highlight("a quiet violin solo", "violin", "#FFD60A")
    ass = build_ass({"line1": "a", "line2": "b {x}", "keyword": "", "credit": "c"},
                    {"font": "Inter", "title_y": 240, "pop": True, "accent": "#FFFFFF"}, 1080, 1920, 10)
    assert "PlayResY: 1920" in ass and "{x}" not in ass


def test_finder_picks_action_not_title_card(film):
    moments = find_moments(film, 6, 10, top_k=2)
    best = moments[0]
    assert 16 <= best.start and best.end <= 34, best      # inside the busy 18-32s shot (+snap slack)
    assert best.features["static"] < 0.2
