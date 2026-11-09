"""Generate xmod.A: a tiny accessor layer between the mod (plain Java, Object-typed) and the
obfuscated X classes. A.smali carries the real descriptors for this build; A.java is the
compile-time stub with identical signatures."""
from .smali import split_types

OBJ = "Ljava/lang/Object;"


def _iget(t):
    if t in ("J", "D"):
        return "iget-wide"
    return {"Z": "iget-boolean", "B": "iget-byte", "C": "iget-char", "S": "iget-short",
            "I": "iget", "F": "iget"}.get(t, "iget-object")


def generate(m):
    """Returns (smali_text, java_text)."""
    sm, jv = [], []

    def instof(name, cls):
        sm.append(f""".method public static {name}(Ljava/lang/Object;)Z
    .registers 2
    instance-of v0, p0, {cls}
    return v0
.end method
""")
        jv.append(f"    public static boolean {name}(Object o) {{ throw new RuntimeException(); }}")

    def getter(name, cls, field):
        fname, ftype = field
        if ftype in ("J", "D"):
            jt, ret, rt = "long" if ftype == "J" else "double", "return-wide v0", ftype
            regs = 3
        elif ftype == "Z":
            jt, ret, rt, regs = "boolean", "return v0", "Z", 2
        elif ftype == "I":
            jt, ret, rt, regs = "int", "return v0", "I", 2
        else:
            jt, ret, rt, regs = "Object", "return-object v0", OBJ, 2
        sm.append(f""".method public static {name}(Ljava/lang/Object;){rt}
    .registers {regs}
    check-cast p0, {cls}
    {_iget(ftype)} v0, p0, {cls}->{fname}:{ftype}
    {ret}
.end method
""")
        jv.append(f"    public static {jt} {name}(Object o) {{ throw new RuntimeException(); }}")

    def iface_call(name, cls, sig):
        ret = sig[sig.index(")") + 1:]
        sm.append(f""".method public static {name}(Ljava/lang/Object;)Ljava/lang/Object;
    .registers 2
    check-cast p0, {cls}
    invoke-interface {{p0}}, {cls}->{sig}
    move-result-object v0
    return-object v0
.end method
""")
        jv.append(f"    public static Object {name}(Object o) {{ throw new RuntimeException(); }}")

    for name, key in [("isPost", "Post"), ("isUser", "User"), ("isTrend", "Trend"), ("isEvent", "Event"),
                      ("isModule", "Module"), ("isModuleItem", "ModuleItem"), ("isRtbAd", "RtbAd"),
                      ("isCursor", "Cursor"), ("isContextualPost", "ContextualPost"),
                      ("isRePostedPost", "RePostedPost"), ("isCanonicalPost", "CanonicalPost"),
                      ("isMinimalUser", "MinimalUser"), ("isVideo", "Video"), ("isImage", "Image"),
                      ("isPinnedPost", "PinnedPost")]:
        instof(name, m[key])

    for name, key in [("postResult", "Post.postResult"), ("postEntryId", "Post.entryId"),
                      ("postPromoted", "Post.promotedMetadata"), ("postPreroll", "Post.prerollMetadata"),
                      ("userEntryId", "User.entryId"), ("userPromoted", "User.promotedMetadata"),
                      ("trendTrend", "Trend.timelineTrend"), ("trendEntryId", "Trend.entryId"),
                      ("timelineTrendPromoted", "TimelineTrend.promotedMetadata"),
                      ("eventSummary", "Event.eventSummary"), ("eventEntryId", "Event.entryId"),
                      ("eventSummaryPromoted", "EventSummary.promotedMetadata"),
                      ("moduleItems", "Module.innerContent"), ("moduleEntryId", "Module.entryId"),
                      ("moduleItemItem", "ModuleItem.item"),
                      ("ctxCanonical", "ContextualPost.canonicalPost"), ("ctxQuoted", "ContextualPost.quotedPost"),
                      ("ctxRepost", "ContextualPost.rePostedPost"),
                      ("repostCanonical", "RePostedPost.canonicalPost"), ("repostQuoted", "RePostedPost.quotedPost"),
                      ("canonicalMedia", "CanonicalPost.media"), ("canonicalAuthor", "CanonicalPost.author"),
                      ("userId", "MinimalUser.id"), ("userFriendship", "MinimalUser.friendship"),
                      ("friendshipFollowed", "Friendship.isFollowedByMe"), ("userIdValue", "UserId.id"),
                      ("rowEntry", "TimelineView.entry"), ("entryTimelineId", "TimelineEntry.id"),
                      ("timelineIdValue", "TimelineId.value"),
                      ("pagerRepo", "BottomPager.repo"), ("pagerCount", "BottomPager.count"),
                      ("urtRepoType", "UrtRepo.type")]:
        cls = m[key.split(".")[0]] if key.split(".")[0] in m else None
        owner = {"TimelineView": m["TimelineView"], "TimelineEntry": m["TimelineEntry"],
                 "TimelineId": m["TimelineId"], "BottomPager": m["BottomPager"], "UrtRepo": m["UrtRepo"],
                 "UserId": m["UserId"], "EventSummary": m["EventSummary"],
                 "TimelineTrend": m["TimelineTrend"]}.get(key.split(".")[0], cls)
        getter(name, owner, m[key])

    iface_call("repoType", m["Repo"], m["Repo.type"])
    iface_call("pinnedPostId", m["PinnedPost"], m["PinnedPost.id"])

    # BoundedPlaylist(listOf(id), id)
    pl, pid = m["Playlist"], m["PostId"]
    sm.append(f""".method public static newPlaylist(Ljava/lang/Object;)Ljava/lang/Object;
    .registers 4
    invoke-static {{p0}}, Ljava/util/Collections;->singletonList(Ljava/lang/Object;)Ljava/util/List;
    move-result-object v1
    check-cast p0, {pid}
    new-instance v0, {pl}
    invoke-direct {{v0, v1, p0}}, {pl}->{m["Playlist.ctor"]}
    return-object v0
.end method
""")
    jv.append("    public static Object newPlaylist(Object postId) { throw new RuntimeException(); }")

    # copy of a UrtTimelinePost without prerollMetadata
    post, fields = m["Post"], m["Post.ctorFields"]
    preroll = m["Post.prerollMetadata"][0]
    width = sum(2 if t in ("J", "D") else 1 for _, t in fields)
    regs = 1 + width + 1
    lines, r = [], 1
    for fname, ftype in fields:
        if fname == preroll:
            lines.append(f"    const/4 v{r}, 0x0")
        else:
            lines.append(f"    {_iget(ftype)} v{r}, p0, {post}->{fname}:{ftype}")
        r += 2 if ftype in ("J", "D") else 1
    body = "\n".join(lines)
    sm.append(f""".method public static postWithoutPreroll(Ljava/lang/Object;)Ljava/lang/Object;
    .registers {regs}
    check-cast p0, {post}
    new-instance v0, {post}
{body}
    invoke-direct/range {{v0 .. v{r - 1}}}, {post}->{m["Post.ctor"]}
    return-object v0
.end method
""")
    jv.append("    public static Object postWithoutPreroll(Object post) { throw new RuntimeException(); }")

    smali = ".class public final Lxmod/A;\n.super Ljava/lang/Object;\n.source \"A.java\"\n\n" + "\n".join(sm)
    java = ("package xmod;\n\n/** Compile-time stub; the real implementation is generated smali (xmodpatch/accessors.py). */\n"
            "public final class A {\n" + "\n".join(jv) + "\n}\n")
    return smali, java
