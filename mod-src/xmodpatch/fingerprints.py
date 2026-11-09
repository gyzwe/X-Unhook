"""Resolve obfuscated X classes / fields / methods from stable anchors.

Anchors used (all survive R8 renaming):
  * kotlinx.serialization serial names, e.g. "com.x.models.timelines.items.UrtTimelinePost",
    and the element names of their generated serializers;
  * data-class toString() prefixes, e.g. "UrtTimelineCursor(";
  * log / analytics string literals, e.g. "Duplicate bottom cursor request. Dropped.";
  * method shapes (parameter / return types built from the classes above).
"""
import math
import re

from .smali import SmaliTree, split_types


class FingerprintError(Exception):
    pass


def need(cond, what):
    if not cond:
        raise FingerprintError(what)


def one(items, what):
    items = list(dict.fromkeys(items))
    need(len(items) == 1, f"{what}: expected 1 match, got {len(items)}: {items[:5]}")
    return items[0]


class Resolver:
    def __init__(self, root):
        self.t = SmaliTree(root)
        self.m = {}          # logical name -> value

    # ------------------------------------------------------------------ primitives
    def serial(self, serial_name):
        """{element: (field, type)} + model class for a @Serializable class, via its generated serializer."""
        files = self.t.grep(f'"{serial_name}"')
        for p in files:
            lines = p.read_text(encoding="utf-8").split("\n")
            regs, elems = {}, []
            for ln in lines:
                s = ln.strip()
                mc = re.match(r'const-string(?:/jumbo)? ([vp]\d+), "((?:[^"\\]|\\.)*)"', s)
                if mc:
                    regs[mc.group(1)] = mc.group(2)
                    continue
                mi = re.match(r"invoke-virtual \{([^}]*)\}, L[^;]+;->\w+\(Ljava/lang/String;Z\)V", s)
                if mi:
                    args = [a.strip() for a in mi.group(1).split(",")]
                    if len(args) >= 2 and args[1] in regs:
                        elems.append(regs[args[1]])
            if not elems:
                continue
            n_masks = max(1, math.ceil(len(elems) / 32))
            text = "\n".join(lines)
            for mi in re.finditer(r"invoke-direct(?:/range)? \{[^}]*\}, (L[^;]+;)-><init>\(((?:I){%d}[^)]*)\)V" % n_masks, text):
                cls, params = mi.group(1), split_types(mi.group(2))
                if len(params) < n_masks + len(elems):
                    continue
                mapping = self._ctor_param_fields(cls, mi.group(2))
                if mapping is None:
                    continue
                res = {}
                for i, e in enumerate(elems):
                    f = mapping.get(n_masks + i)
                    if f:
                        res[e] = f
                return cls, res
        raise FingerprintError(f"serializer for {serial_name} not found")

    def _ctor_param_fields(self, cls, params_desc):
        c = self.t.cls(cls)
        if c is None:
            return None
        ctor = c.method("<init>", sig=f"<init>({params_desc})V")
        if not ctor:
            return None
        params = split_types(params_desc)
        preg, regmap = 1, {}
        for i, ty in enumerate(params):
            regmap[f"p{preg}"] = i
            preg += 2 if ty in ("J", "D") else 1
        synthetic = {n for n, t, f in c.fields if "synthetic" in f}
        cands = {}
        written = set()
        # R8 reuses parameter registers as temporaries: only trust a parameter register until it is
        # first overwritten, and prefer real (non-synthetic) fields over Kotlin delegate fields.
        for line in ctor[0].body:
            s = line.strip()
            mi = re.match(r"iput(?:-\w+)? (p\d+), p0, " + re.escape(cls) + r"->(\w+):(\S+)", s)
            if mi:
                reg = mi.group(1)
                if reg in regmap and reg not in written:
                    cands.setdefault(regmap[reg], []).append((mi.group(2), mi.group(3)))
                continue
            mw = re.match(r"(?:move\S*|const\S*|iget\S*|sget\S*|aget\S*|new-instance|check-cast|\S+-int\S*|\S+-long\S*|"
                          r"\S+-float\S*|\S+-double\S*|neg-\S+|not-\S+|\S+-to-\S+|instance-of|array-length|new-array|"
                          r"move-result\S*) (p\d+)", s)
            if mw and not s.startswith("check-cast"):
                written.add(mw.group(1))
        res = {}
        for idx, fl in cands.items():
            real = [f for f in fl if f[0] not in synthetic]
            res[idx] = (real or fl)[0]
        return res

    def by_tostring(self, prefix):
        return one([c.name for c in self.t.by_tostring(prefix)], f"toString {prefix!r}")

    def field_of_type(self, cls, typ):
        c = self.t.cls(cls)
        return one([n for n, t in c.instance_fields() if t == typ], f"field of {typ} in {cls}")

    def enum_with(self, *consts):
        cands = None
        for k in consts:
            files = set(self.t.grep(f'"{k}"'))
            cands = files if cands is None else cands & files
        res = [self.t.cls(p).name for p in cands if self.t.cls(p).is_enum]
        return one(res, f"enum with {consts}")

    def method_with_string(self, s):
        hits = []
        for c in self.t.by_string(s):
            for m in c.methods:
                if f'"{s}"' in m.text():
                    hits.append((c.name, m.sig))
        return one(hits, f"method with string {s!r}")

    # ------------------------------------------------------------------ resolution
    def resolve(self):
        m, t = self.m, self.t
        I = "com.x.models.timelines.items."

        # --- timeline items (UrtTimelineItem subclasses) -------------------------------
        for key, sname, fields in [
            ("Post", I + "UrtTimelinePost", ["postResult", "entryId", "promotedMetadata", "prerollMetadata"]),
            ("User", I + "UrtTimelineUser", ["entryId", "promotedMetadata"]),
            ("Trend", I + "UrtTimelineTrend", ["timelineTrend", "entryId"]),
            ("Event", I + "UrtTimelineEventSummary", ["eventSummary", "entryId"]),
            ("Module", I + "UrtTimelineModule", ["innerContent", "entryId"]),
            ("ModuleItem", I + "UrtTimelineModuleItem", ["item"]),
            ("RtbAd", I + "UrtTimelineRtbImageAd", ["entryId"]),
            ("Cursor", I + "UrtTimelineCursor", ["entryId"]),
            ("ContextualPost", "com.x.models.ContextualPost", ["canonicalPost", "quotedPost", "rePostedPost"]),
            ("RePostedPost", "com.x.models.RePostedPost", ["canonicalPost", "quotedPost"]),
            ("CanonicalPost", "com.x.models.CanonicalPost", ["media", "author"]),
            ("MinimalUser", "com.x.models.MinimalUser", ["id", "friendship"]),
            ("Friendship", "com.x.models.Friendship", ["isFollowedByMe"]),
            ("TimelineTrend", "com.x.models.TimelineTrend", ["promotedMetadata"]),
            ("Video", "com.x.models.MediaContent.MediaContentVideo", []),
            ("Image", "com.x.models.MediaContent.MediaContentImage", []),
            ("Playlist", "com.x.navigation.VideoTabArgs.BoundedPlaylist", []),
            ("VideoTabArgs", "com.x.navigation.VideoTabArgs", ["pinnedPost", "boundedPlaylist"]),
        ]:
            try:
                cls, fm = self.serial(sname)
            except FingerprintError:
                cls, fm = self._serial_fallback(key)
            m[key] = cls
            for f in fields:
                need(f in fm, f"{key}.{f}")
                m[f"{key}.{f}"] = fm[f]

        # TimelinePromotedMetadata; EventSummary (not @Serializable) -> its field of that type
        m["Promoted"] = self.by_tostring("TimelinePromotedMetadata(")
        need(m["Post.promotedMetadata"][1] == m["Promoted"], "Post.promotedMetadata type")
        ev = m["Event.eventSummary"][1]
        m["EventSummary"] = ev
        m["EventSummary.promotedMetadata"] = (self.field_of_type(ev, m["Promoted"]), m["Promoted"])

        # UserIdentifier: type of MinimalUser.id; its only long field is the numeric id
        m["UserId"] = m["MinimalUser.id"][1]
        m["UserId.id"] = (one([n for n, ty in t.cls(m["UserId"]).instance_fields() if ty == "J"], "UserId long"), "J")

        # PostIdentifier: VideoTabArgs.pinnedPost is an interface with a single abstract getter of the id
        m["PinnedPost"] = m["VideoTabArgs.pinnedPost"][1]
        pp = t.cls(m["PinnedPost"])
        getter = one([x for x in pp.methods if "abstract" in x.flags and not x.params and x.ret.startswith("L")],
                     "PinnedPost id getter")
        m["PinnedPost.id"] = getter.sig
        m["PostId"] = getter.ret
        pl = t.cls(m["Playlist"])
        m["Playlist.ctor"] = one([x.sig for x in pl.methods if x.name == "<init>" and "synthetic" not in x.flags
                                  and x.params == ["Ljava/util/List;", m["PostId"]]], "BoundedPlaylist(List, id)")

        # UrtTimelineItem interface = common interface of the item classes
        common = None
        for k in ("Post", "User", "Trend", "Module", "RtbAd", "Cursor"):
            ifs = set(t.cls(m[k]).interfaces)
            common = ifs if common is None else common & ifs
        m["Item"] = one(common, "UrtTimelineItem interface")

        # Post copy constructor (all instance fields, declaration order)
        post = t.cls(m["Post"])
        nfields = len(post.instance_fields())
        ctors = [x for x in post.methods if x.name == "<init>" and "synthetic" not in x.flags
                 and len(x.params) == nfields]
        ctor = one([x.sig for x in ctors], "UrtTimelinePost primary ctor")
        pmap = self._ctor_param_fields(m["Post"], ctor[len("<init>("):ctor.index(")")])
        need(pmap and len(pmap) == nfields, "UrtTimelinePost ctor field map")
        m["Post.ctor"] = ctor
        m["Post.ctorFields"] = [pmap[i] for i in range(nfields)]

        # --- timeline DB row -> item mapper ---------------------------------------------
        m["TimelineView"] = self.by_tostring("TimelineView(timelineEntryEntity=")
        m["TimelineEntry"] = self.by_tostring("TimelineEntryEntity(timeline_id=")
        m["TimelineId"] = self.by_tostring("TimelineIdentifier(value=")
        m["TimelineView.entry"] = (self.field_of_type(m["TimelineView"], m["TimelineEntry"]), m["TimelineEntry"])
        m["TimelineEntry.id"] = (self.field_of_type(m["TimelineEntry"], m["TimelineId"]), m["TimelineId"])
        m["TimelineId.value"] = (self.field_of_type(m["TimelineId"], "Ljava/lang/String;"), "Ljava/lang/String;")

        decl = t.grep_lines(rf".method public static final \w+\({re.escape(m['TimelineView'])}Ljava/util/LinkedHashMap;Ljava/util/LinkedHashMap;L[^;]+;\){re.escape(m['Item'])}$", regex=True)
        need(len(decl) == 1, f"row mapper: {len(decl)} candidates")
        path, _, text = decl[0]
        cls = t.cls(path).name
        sig = text.strip().split()[-1]
        m["Mapper"] = cls
        m["Mapper.sig"] = sig

        # --- timeline type / repositories / paging ---------------------------------------
        m["TimelineType"] = self.enum_with("FOR_YOU", "FOLLOWING", "HOME_SUBSCRIBED", "VIDEO_TAB")
        bp_cls, bp_sig = self.method_with_string("Duplicate bottom cursor request. Dropped.")
        m["BottomPager"], m["BottomPager.loadMore"] = bp_cls, bp_sig
        m["LoadMore"] = self.by_tostring("LoadMore(trigger=")
        bpc = t.cls(bp_cls)
        ctor = one([x for x in bpc.methods if x.name == "<init>"], "BottomPager ctor")
        repo_iface = ctor.params[1]
        m["Repo"] = repo_iface
        m["BottomPager.repo"] = (self.field_of_type(bp_cls, repo_iface), repo_iface)
        m["BottomPager.count"] = (one([n for n, ty in bpc.instance_fields() if ty == "I"], "BottomPager int"), "I")
        ri = t.cls(repo_iface)
        m["Repo.type"] = one([x.sig for x in ri.methods if not x.params and x.ret == m["TimelineType"]],
                             "Repo timeline type getter")

        direction = self.enum_with("OLDER_THAN", "PULL_TO_REFRESH", "NEWER_THAN")
        cands = []
        for c in t.by_string("URTTimelineRepository"):
            fetch = [x.sig for x in c.methods if x.params == [direction, m["Cursor"]] and x.ret == "V"]
            types = [n for n, ty in c.instance_fields() if ty == m["TimelineType"]]
            if len(fetch) == 1 and len(types) == 1 and m["Repo"] in c.interfaces:
                cands.append((c.name, fetch[0], types[0]))
        urt_cls, fetch, tfield = one(cands, "URT timeline repository")
        m["UrtRepo"], m["UrtRepo.fetch"] = urt_cls, fetch
        m["UrtRepo.type"] = (tfield, m["TimelineType"])

        # --- immersive video viewer ---------------------------------------------------------
        hits = t.grep_lines(f"constructor <init>\\(L[^;]+;{re.escape(m['PinnedPost'])}{re.escape(m['Playlist'])}", regex=True)
        need(len(hits) == 1, f"video viewer timeline ctor: {len(hits)}")
        m["VideoTimeline"] = t.cls(hits[0][0]).name
        m["VideoTimeline.ctor"] = hits[0][2].strip().split()[-1]

        # --- account / app / feature switches / logo ----------------------------------------
        acc = [c for c in t.by_tostring("AccountData(userId=")
               if any(ty == m["UserId"] for _, ty in c.instance_fields())]
        m["AccountData"] = one([c.name for c in acc], "AccountData")
        m["AccountData.ctor"] = one([x.sig for x in t.cls(m["AccountData"]).methods
                                     if x.name == "<init>" and x.params and x.params[0] == m["UserId"]], "AccountData ctor")

        app, sig = self.method_with_string("XApplication#onCreate")
        need(sig == "onCreate()V", "Application.onCreate")
        m["App"] = app

        fs = [t.cls(p) for p in t.grep(".method public peekBoolean(Ljava/lang/String;Z)Z")]
        fs = [c for c in fs if c.method(sig="getBoolean(Ljava/lang/String;Z)Z") and not c.is_interface
              and "Fake" not in c.name and "NoOp" not in c.name]
        m["FeatureSwitches"] = one([c.name for c in fs], "FeatureSwitchesRepositoryImpl")

        m["LogoClicked"] = self.by_tostring("OnLogoClicked")
        return m

    def _serial_fallback(self, key):
        raise FingerprintError(f"no serializer for {key}")


def resolve(root):
    return Resolver(root).resolve()
