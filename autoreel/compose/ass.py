"""On-screen text as an ASS subtitle file: hook, timed story beats, credit.

Six layouts rotate per clip so a channel doesn't look like one template stamped out
over and over (YouTube's "inauthentic content" policy targets exactly that).
"""

LAYOUTS = ["top_hook", "boxed", "lower_third", "minimal", "label", "headline"]


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def esc(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def highlight(line: str, keyword: str, accent: str, base: str = "&H00FFFFFF&") -> str:
    if not keyword:
        return esc(line)
    i = line.lower().find(keyword.lower())
    if i < 0:
        return esc(line)
    j = i + len(keyword)
    return esc(line[:i]) + "{\\c" + ass_color(accent) + "&}" + esc(line[i:j]) + "{\\c" + base + "}" + esc(line[j:])


def ts(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _hook_lines(text, accent):
    kw = text.get("keyword", "")
    l1, l2 = text["line1"], text["line2"]
    if kw and kw.lower() in l2.lower():
        return esc(l1), highlight(l2, kw, accent)
    if kw and kw.lower() in l1.lower():
        return highlight(l1, kw, accent), esc(l2)
    return esc(l1), esc(l2)


def build_ass(text: dict, style: dict, W: int, H: int, length: float) -> str:
    layout = style.get("layout", "top_hook")
    font, accent, top = style["font"], style["accent"], style["title_y"]
    serif = style.get("serif_font", "DejaVu Serif")
    acc, dark = ass_color(accent), ass_color("#101010", 0x30)
    pop = "\\t(0,140,\\fscx106\\fscy106)\\t(140,300,\\fscx100\\fscy100)" if style.get("pop") else ""
    l1, l2 = _hook_lines(text, accent)
    beats = [b for b in (text.get("beats") or []) if b][:3]

    # Style lines: Name, Font, Size, Primary, Secondary, Outline, Back, Bold, Italic, U, S, ScX, ScY, Sp, Ang,
    #              BorderStyle, Outline, Shadow, Align, ML, MR, MV, Enc
    def st(name, fnt, size, prim="&H00FFFFFF", outline="&H00000000", back="&H64000000", bold=-1, italic=0,
           border=1, ow=6, shadow=2, align=8, ml=70, mr=70, mv=top):
        return (f"Style: {name},{fnt},{size},{prim},{prim},{outline},{back},{bold},{italic},0,0,100,100,0,0,"
                f"{border},{ow},{shadow},{align},{ml},{mr},{mv},1")

    hook_end = length
    if layout == "boxed":
        hook = st("Hook", font, 78, outline=dark, back=dark, border=3, ow=18, shadow=0)
        beat = st("Beat", font, 50, outline=dark, back=dark, border=3, ow=14, shadow=0, align=2, mv=340)
    elif layout == "lower_third":
        hook = st("Hook", font, 70, align=7, ml=70, mv=top)
        beat = st("Beat", font, 52, bold=0, ow=4, align=1, ml=70, mv=330)
    elif layout == "minimal":
        hook = st("Hook", font, 96, align=5, ow=8, mv=0)
        beat = st("Beat", font, 54, ow=5, align=2, mv=330)
        hook_end = min(2.8, length * 0.35)
    elif layout == "label":
        hook = st("Hook", font, 80, mv=top + 70)
        beat = st("Beat", font, 52, ow=5, align=2, mv=330)
    elif layout == "headline":
        hook = st("Hook", serif, 84, ow=5, mv=top)
        beat = st("Beat", serif, 52, bold=0, italic=-1, ow=4, align=2, mv=330)
    else:  # top_hook
        hook = st("Hook", font, 86, ow=7, mv=top)
        beat = st("Beat", font, 54, ow=5, align=2, mv=330)
    label = st("Label", font, 34, prim=acc, ow=3, shadow=0, mv=top)
    credit = st("Credit", font, 30, prim="&H30FFFFFF", bold=0, ow=2, shadow=0, align=2, mv=110)

    events = []
    prefix = "{\\c" + acc + "&}▌{\\c&H00FFFFFF&}" if layout == "lower_third" else ""
    events.append(f"Dialogue: 2,{ts(0.05)},{ts(hook_end)},Hook,,0,0,0,,{{\\fad(220,180){pop}}}{prefix}{l1}\\N{l2}")
    if layout == "label" and text.get("label"):
        events.append(f"Dialogue: 2,{ts(0)},{ts(length)},Label,,0,0,0,,{{\\fad(300,0)}}{esc(text['label']).upper()}")
    if beats:
        start = max(1.2, hook_end if layout == "minimal" else 1.2)
        span = max(1.0, (length - 0.3 - start) / len(beats))
        for i, b in enumerate(beats):
            a, z = start + i * span, start + (i + 1) * span - 0.1
            events.append(f"Dialogue: 1,{ts(a)},{ts(z)},Beat,,0,0,0,,{{\\fad(160,140)}}{prefix}{esc(b)}")
    events.append(f"Dialogue: 0,{ts(0)},{ts(length)},Credit,,0,0,0,,{esc(text.get('credit', ''))}")

    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        hook, beat, label, credit, "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events, ""])
