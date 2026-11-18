"""ASS subtitle file for the on-screen hook, keyword highlight and credit line."""


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def highlight(line: str, keyword: str, accent: str) -> str:
    if not keyword:
        return esc(line)
    i = line.lower().find(keyword.lower())
    if i < 0:
        return esc(line)
    j = i + len(keyword)
    return (esc(line[:i]) + "{\\c" + ass_color(accent) + "&}" + esc(line[i:j]) +
            "{\\c&H00FFFFFF&}" + esc(line[j:]))


def ts(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def build_ass(text: dict, style: dict, W: int, H: int, length: float) -> str:
    font = style["font"]
    top = style["title_y"]
    pop = "\\t(0,140,\\fscx106\\fscy106)\\t(140,300,\\fscx100\\fscy100)" if style["pop"] else ""
    l1 = esc(text["line1"])
    l2 = highlight(text["line2"], text.get("keyword", ""), style["accent"])
    if text.get("keyword") and text["keyword"].lower() in text["line1"].lower() \
            and text["keyword"].lower() not in text["line2"].lower():
        l1 = highlight(text["line1"], text["keyword"], style["accent"])
        l2 = esc(text["line2"])
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,{font},86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,8,70,70,{top},1
Style: Credit,{font},30,&H30FFFFFF,&H30FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,60,60,110,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 1,{ts(0.05)},{ts(length)},Hook,,0,0,0,,{{\\fad(220,0){pop}}}{l1}\\N{l2}
Dialogue: 0,{ts(0)},{ts(length)},Credit,,0,0,0,,{esc(text.get("credit", ""))}
"""
