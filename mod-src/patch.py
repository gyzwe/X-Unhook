#!/usr/bin/env python3
"""Insert X Mod hooks into apktool smali output. Usage: patch.py <apktool-out-dir>"""
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
MARK = "# xmod-hook"


def find(cls):
    hits = list(ROOT.glob(f"smali*/{cls}.smali"))
    assert len(hits) == 1, (cls, hits)
    return hits[0]


def patch(cls, fn, sig):
    """sig: unique Mod call inserted by fn; its presence means the hook is already applied."""
    p = find(cls)
    src = p.read_text()
    if sig in src:
        print(f"[skip] {cls}: {sig}")
        return
    out = fn(src)
    assert out != src, f"no change in {cls}"
    p.write_text(out)
    print(f"[ok]   {cls}")


def after_method_header(src, header, code):
    """Insert code right after '.locals N' of the method whose line starts with header."""
    i = src.index(header)
    m = re.compile(r"\n    \.locals \d+\n").search(src, i)
    assert m and src.rfind(".method", 0, m.start()) == i
    return src[:m.end()] + code + src[m.end():]


# 1. Application init
def p_app(src):
    return after_method_header(src, ".method public final onCreate()V", f"""
    {MARK}
    invoke-static/range {{p0 .. p0}}, Lxmod/Mod;->init(Landroid/app/Application;)V
""")


# 2. Bottom paging (infinite scroll)
def p_bottom(src):
    return after_method_header(src, ".method public final a(Lcom/x/urt/paging/f;)V", f"""
    {MARK}
    instance-of v0, p1, Lcom/x/urt/paging/d;
    if-eqz v0, :xmod_cont
    invoke-static {{p0}}, Lxmod/Mod;->blockBottomPaging(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :xmod_cont
    return-void
    :xmod_cont
""")


# 3. Feature switches (Google SSP ads)
def p_fs(src):
    for name in ("getBoolean", "peekBoolean"):
        src = after_method_header(src, f".method public {name}(Ljava/lang/String;Z)Z", f"""
    {MARK}
    invoke-static {{p1}}, Lxmod/Mod;->fsBool(Ljava/lang/String;)I
    move-result v0
    if-ltz v0, :xmod_{name}
    return v0
    :xmod_{name}
""")
    return src


# 4. Logo click on Home -> menu on double tap
def p_logo(src):
    anchor = "    sget-object v3, Lcom/x/home/d0;->a:Lcom/x/home/d0;\n"
    i = src.index(anchor)
    j = src.index("    if-eqz v3, :cond_5\n", i) + len("    if-eqz v3, :cond_5\n")
    assert j - i < 300
    code = f"""
    {MARK}
    invoke-static {{}}, Lxmod/Mod;->onLogoClick()Z
    move-result v3
    if-eqz v3, :xmod_logo
    return-void
    :xmod_logo
"""
    return src[:j] + code + src[j:]


# 5. Timeline item mapper: filter every UrtTimelineItem produced by util.b.W(...)
W_CALL = "Lorg/chromium/support_lib_boundary/util/b;->W(Lcom/x/database/core/impl/view/a;Ljava/util/LinkedHashMap;Ljava/util/LinkedHashMap;Lcom/x/models/g4;)Lcom/x/models/timelines/items/o0;"
MR = re.compile(r"(\n    invoke-static[^\n]*" + re.escape(W_CALL) + r"\n\n    move-result-object ([vp]\d+)\n)")


def p_mapper(src):
    def rep(m):
        r = m.group(2)
        return m.group(1) + f"""
    {MARK}
    invoke-static/range {{{r} .. {r}}}, Lxmod/Mod;->filterItem(Ljava/lang/Object;)Ljava/lang/Object;
    move-result-object {r}
    check-cast {r}, Lcom/x/models/timelines/items/o0;
"""
    out, n = MR.subn(rep, src)
    print(f"       W call sites patched: {n}")
    assert n >= 1
    return out


# 6. Remember timeline_id of the DB row at W entry (used by timeline-specific filters)
def p_w_entry(src):
    return after_method_header(src, ".method public static final W(Lcom/x/database/core/impl/view/a;", f"""
    {MARK}
    invoke-static/range {{p0 .. p0}}, Lxmod/Mod;->enterRow(Ljava/lang/Object;)V
""")


# 7. Immersive video viewer opened from a post: replace with a bounded playlist of that one post
def p_video(src):
    return after_method_header(src, ".method public constructor <init>(Lcom/x/urt/c0;Lcom/x/navigation/qt;Lcom/x/navigation/gt;", f"""
    {MARK}
    invoke-static/range {{p2 .. p3}}, Lxmod/Mod;->videoPlaylist(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;
    move-result-object p3
    check-cast p3, Lcom/x/navigation/gt;
""")


# 8. AccountData(userId, ...) -> remember own account ids
def p_account(src):
    return after_method_header(src, ".method public constructor <init>(Lcom/x/models/gh;", f"""
    {MARK}
    invoke-static {{p1}}, Lxmod/Mod;->ownAccount(Ljava/lang/Object;)V
""")


patch("com/x/android/XApplication", p_app, "Lxmod/Mod;->init(")
patch("com/x/urt/paging/bottom/h", p_bottom, "Lxmod/Mod;->blockBottomPaging(")
patch("com/x/featureswitches/FeatureSwitchesRepositoryImpl", p_fs, "Lxmod/Mod;->fsBool(")
patch("com/x/home/tabbed/r", p_logo, "Lxmod/Mod;->onLogoClick(")
patch("org/chromium/support_lib_boundary/util/b", p_mapper, "Lxmod/Mod;->filterItem(")
patch("ai/x/grok/bots/calls/impl/j1", p_mapper, "Lxmod/Mod;->filterItem(")
patch("org/chromium/support_lib_boundary/util/b", p_w_entry, "Lxmod/Mod;->enterRow(")
patch("com/x/video/tab/h2", p_video, "Lxmod/Mod;->videoPlaylist(")
patch("com/x/account/b0", p_account, "Lxmod/Mod;->ownAccount(")


# 9. Every network fetch of a URT timeline: urt.b0.p(direction, cursor) -> skip for a disabled Home feed
def p_fetch(src):
    return after_method_header(src, ".method public final p(Lcom/x/models/timelines/d;Lcom/x/models/timelines/items/j0;)V", f"""
    {MARK}
    invoke-static {{p0}}, Lxmod/Mod;->blockFetch(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :xmod_fetch
    return-void
    :xmod_fetch
""")


patch("com/x/repositories/urt/b0", p_fetch, "Lxmod/Mod;->blockFetch(")
