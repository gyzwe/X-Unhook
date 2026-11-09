# X 12.41.0 mod: ad blocking and a limit on the infinite feed

## How the feed works (decompilation findings)
- The app is the new KMP/Compose client (`com.x.android.main.MainActivity`, `com.x.android.XApplication`, decompose components).
- URT feed: `com.x.urt.c0` (DefaultUrtTimelineComponent) holds the pagers:
  - `com.x.urt.paging.bottom.h`: bottom pager. The UI sends `LoadMore` (`com.x.urt.paging.d`, trigger `LAZY_LIST_SCROLL`) near the end of the list.
    `h.a()` then loads older posts by the bottom cursor (`UrtTimelineCursor` = `items.j0`, `w.Bottom`). This is the "infinite" scrolling.
  - `h.b : com.x.repositories.urt.c2`, `c2.c()` → timeline type `com.x.models.timelines.v` (FOR_YOU, FOLLOWING, RANKED_FOLLOWING, HOME_SUBSCRIBED, EXPLORE_FOR_YOU, VIDEO_TAB…).
  - `h.n`: current number of items in the list.
- Data path: network → Room DB (`com.x.database.core.impl`) → `org.chromium.support_lib_boundary.util.b.W(row, …)` (mapper merged by R8) → `UrtTimelineItem` (`items.o0`).
  If `W` returns null, the item is skipped everywhere, including inside modules/carousels.
- Ads in the model:
  - `j1` (UrtTimelinePost): `e` = promotedMetadata (`gf`), `f` = prerollMetadata (`i7`)
  - `b2` (UrtTimelineUser): `h` = promotedMetadata
  - `y1` (Trend): `a.j` = promotedMetadata
  - `m0` (EventSummary, "promoted_hero"): `a.i` = promotedMetadata
  - `p1` (UrtTimelineRtbImageAd) = Google SSP ad slot
  - Feature flag `android_x_lite_ssp_ads_enabled` gates the Google SSP SDK.

## Hooks (`mod-src/patch.py`)
| Location | Hook |
|---|---|
| `XApplication.onCreate` | `Mod.init(app)`: settings, activity tracking |
| 3 call sites of `util.b.W(...)` | `Mod.filterItem(item)`: removes promoted items and pre-roll |
| `urt.paging.bottom.h.a(LoadMore)` | `Mod.blockBottomPaging(this)`: feed limit |
| `FeatureSwitchesRepositoryImpl.getBoolean/peekBoolean` | `Mod.fsBool(key)`: turns off SSP ads |
| `home.tabbed.r.e(OnLogoClicked)` | `Mod.onLogoClick()`: double tap on the X logo opens the menu |
| entry of `util.b.W(row, …)` | `Mod.enterRow(row)`: remembers `timeline_id` (= `TimelineType.name` + suffix) for timeline-specific filters |
| `video.tab.h2.<init>(c0, pinnedPost, boundedPlaylist, …)` | `Mod.videoPlaylist()`: a video opened from a post becomes `BoundedPlaylist` of one post (pagers become no-op `video.tab.e.a`) |
| `account.b0.<init>(userId, …)` (AccountData) | `Mod.ownAccount()`: own ids, so your own posts stay in "Following" |

Following-only: `g5.h` = `Friendship`, `.a` = isFollowedByMe; repost = `ContextualPost(s1).d != null`, author = `s1.b.f`.
Video: `CanonicalPost(v0).d` = media list, `MediaContentVideo` = `com.x.models.y4`.
Lock: prefs `locked_<key>`, `locked_limit_idx`, `lock_mode`; `load()` forces locked = true.

## Build
```
java -jar tools/apktool.jar d -r -f -o apktool-out apkm/base.apk
python3 -I mod-src/patch.py apktool-out
# classes6/7/1 are close to the 64K-reference limit; move packages into a new dex:
mkdir -p apktool-out/smali_classes16/{com/x,androidx,bo}
mv apktool-out/smali_classes6/com/x/composer apktool-out/smali_classes16/com/x/
mv apktool-out/smali_classes7/androidx/camera apktool-out/smali_classes16/androidx/
mv apktool-out/smali/bo/app apktool-out/smali_classes16/bo/
java -jar tools/apktool.jar b -f -o build/base-unsigned.apk apktool-out
mod-src/build-mod.sh && mod-src/package.sh   # → build/out/*.apk (signed with build/xmod.keystore)
```
