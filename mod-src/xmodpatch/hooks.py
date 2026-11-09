"""Insert X Mod hooks into a decoded smali tree, using the fingerprint mapping."""
import re
from pathlib import Path

from .fingerprints import FingerprintError, need
from .smali import SmaliTree, split_types

MARK = "# xmod-hook"
MOD = "Lxmod/Mod;"


class Patcher:
    def __init__(self, root, mapping):
        self.t = SmaliTree(root)
        self.m = mapping
        self.touched = set()      # smali files changed by hooks

    # ------------------------------------------------------------------ helpers
    def _file(self, cls):
        p = self.t.path_of(cls)
        need(p is not None, f"class file for {cls}")
        return p

    def _method_span(self, src, cls, sig):
        """(start, end) character offsets of the method body (after the .method line)."""
        pat = re.compile(r"^\.method (?:[\w-]+ )*" + re.escape(sig) + r"$", re.M)
        hits = list(pat.finditer(src))
        need(len(hits) == 1, f"method {cls}->{sig}: {len(hits)} found")
        start = hits[0].end()
        end = src.index("\n.end method", start)
        return start, end

    def _ensure_locals(self, src, start, n):
        """Make sure the method starting at `start` has at least n locals; returns (src, insert_pos)."""
        m = re.compile(r"\n    \.(locals|registers) (\d+)\n").search(src, start)
        need(m is not None and m.start() < src.index("\n.end method", start), "locals line")
        if m.group(1) == "registers":
            raise FingerprintError(".registers methods are not supported")
        if int(m.group(2)) < n:
            src = src[:m.start()] + f"\n    .locals {n}\n" + src[m.end():]
            m = re.compile(r"\n    \.locals \d+\n").search(src, start)
        return src, m.end()

    def _patch_method_head(self, cls, sig, code, need_locals=0):
        p = self._file(cls)
        src = p.read_text(encoding="utf-8")
        start, _ = self._method_span(src, cls, sig)
        if MARK in src[start:src.index("\n.end method", start)]:
            return
        src, pos = self._ensure_locals(src, start, need_locals)
        src = src[:pos] + f"\n    {MARK}\n" + code.strip("\n") + "\n" + src[pos:]
        p.write_text(src, encoding="utf-8")
        self.touched.add(p)

    @staticmethod
    def _preg(params, index, static=False):
        """p-register name of parameter `index` (0-based, excluding `this`)."""
        r = 0 if static else 1
        for i, t in enumerate(params):
            if i == index:
                return f"p{r}"
            r += 2 if t in ("J", "D") else 1
        raise IndexError(index)

    # ------------------------------------------------------------------ hooks
    def apply(self):
        m = self.m
        self._patch_method_head(m["App"], "onCreate()V",
                                f"    invoke-static/range {{p0 .. p0}}, {MOD}->init(Landroid/app/Application;)V")

        self._patch_method_head(m["BottomPager"], m["BottomPager.loadMore"], f"""
    move-object/from16 v0, p1
    instance-of v0, v0, {m["LoadMore"]}
    if-eqz v0, :xmod_paging
    invoke-static/range {{p0 .. p0}}, {MOD}->blockBottomPaging(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :xmod_paging
    return-void
    :xmod_paging""", need_locals=1)

        for name in ("getBoolean", "peekBoolean"):
            self._patch_method_head(m["FeatureSwitches"], f"{name}(Ljava/lang/String;Z)Z", f"""
    invoke-static/range {{p1 .. p1}}, {MOD}->fsBool(Ljava/lang/String;)I
    move-result v0
    if-ltz v0, :xmod_{name}
    return v0
    :xmod_{name}""", need_locals=1)

        self._patch_method_head(m["Mapper"], m["Mapper.sig"],
                                f"    invoke-static/range {{p0 .. p0}}, {MOD}->enterRow(Ljava/lang/Object;)V")
        self._patch_mapper_calls()

        sig = m["VideoTimeline.ctor"]
        params = split_types(sig[sig.index("(") + 1:sig.index(")")])
        qi = params.index(m["PinnedPost"])
        need(params[qi + 1] == m["Playlist"], "VideoTimeline params adjacent")
        rq, rg = self._preg(params, qi), self._preg(params, qi + 1)
        self._patch_method_head(m["VideoTimeline"], sig, f"""
    invoke-static/range {{{rq} .. {rg}}}, {MOD}->videoPlaylist(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;
    move-result-object {rg}
    check-cast {rg}, {m["Playlist"]}""")

        self._patch_method_head(m["AccountData"], m["AccountData.ctor"],
                                f"    invoke-static/range {{p1 .. p1}}, {MOD}->ownAccount(Ljava/lang/Object;)V")

        self._patch_method_head(m["UrtRepo"], m["UrtRepo.fetch"], f"""
    invoke-static/range {{p0 .. p0}}, {MOD}->blockFetch(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :xmod_fetch
    return-void
    :xmod_fetch""", need_locals=1)

        self._patch_logo()
        return sorted(self.touched)

    def _patch_mapper_calls(self):
        m = self.m
        call = f"{m['Mapper']}->{m['Mapper.sig']}"
        pat = re.compile(r"(\n    invoke-static(?:/range)? \{[^}]*\}, " + re.escape(call) +
                         r"\n\n    move-result-object ([vp]\d+)\n)(?!\n    " + re.escape(MARK) + ")")
        n = 0
        for p in self.t.grep(call):
            src = p.read_text(encoding="utf-8")

            def rep(mt):
                r = mt.group(2)
                return mt.group(1) + f"""
    {MARK}
    invoke-static/range {{{r} .. {r}}}, {MOD}->filterItem(Ljava/lang/Object;)Ljava/lang/Object;
    move-result-object {r}
    check-cast {r}, {m["Item"]}
"""
            new, k = pat.subn(rep, src)
            if k:
                p.write_text(new, encoding="utf-8")
                self.touched.add(p)
                n += k
        need(n >= 1, "mapper call sites")
        self.mapper_calls = n

    def _patch_logo(self):
        logo = self.m["LogoClicked"]
        c = self.t.cls(logo)
        inst = one_static_self(c)
        get = f"sget-object ([vp]\\d+), {re.escape(logo)}->{inst}:{re.escape(logo)}"
        pat = re.compile(r"\n    " + get + r"\n\n    invoke-static \{(?:[vp]\d+, \1|\1, [vp]\d+)\}, Lkotlin/jvm/internal/Intrinsics;->areEqual"
                         r"\(Ljava/lang/Object;Ljava/lang/Object;\)Z\n\n    move-result ([vp]\d+)\n\n    if-eqz \2, (:\w+)\n")
        hits = []
        for p in self.t.grep(f"{logo}->{inst}:{logo}"):
            if p == c.path:
                continue
            src = p.read_text(encoding="utf-8")
            for mt in pat.finditer(src):
                hits.append((p, mt))
        need(len(hits) == 1, f"logo click handler: {len(hits)}")
        p, mt = hits[0]
        src = p.read_text(encoding="utf-8")
        if MARK in src[mt.end():mt.end() + 200]:
            return
        r = mt.group(2)
        code = f"""
    {MARK}
    invoke-static {{}}, {MOD}->onLogoClick()Z
    move-result {r}
    if-eqz {r}, :xmod_logo
    return-void
    :xmod_logo
"""
        meth_start = src.rfind("\n.method ", 0, mt.start())
        need(src[meth_start:src.index("\n", meth_start + 1)].endswith(")V"), "logo handler returns void")
        src = src[:mt.end()] + code + src[mt.end():]
        p.write_text(src, encoding="utf-8")
        self.touched.add(p)


def one_static_self(c):
    fs = [n for n, t, f in c.fields if "static" in f and t == c.name]
    need(len(fs) == 1, f"singleton field of {c.name}")
    return fs[0]
