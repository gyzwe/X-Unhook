# X Unhook

A patch for the official X (Twitter) Android app: hides ads, limits infinite scrolling, offers a
"Following: only accounts I follow" filter, hides photos and videos, can turn off the Home feed,
and has a permanent lock. The menu opens with a double tap on the X logo on Home.

It installs as a **separate app** (`com.twitter.android.unhook`, label "X Unhook") next to the official X
and updates itself from GitHub Releases.

## Build

```
python3 -I mod-src/build.py --apkm /path/to/com.twitter.android_<ver>.apkm [--bump] [--no-clone]
python3 -I mod-src/release.py --notes "what changed" [--push]
```

`build.py` runs the whole pipeline. Output goes to `build/out/`: `base.apk`, `split_*.apk`,
`X-Mod-v<N>-X<ver>.apks` and `xmod-release.json`.

Local files (in `.gitignore`):
- `tools/`: JDK 21, apktool, Android build-tools and platform
- `secrets/xmod.keystore`: signing key. Keep it, or updates will no longer install.
- `secrets/read_token`: read-only token that is built into the APK so the updater can read the private repo
- `secrets/release_token`: token used to publish releases

Settings live in `mod-src/xmod.json`: `mod_version`, `package`, `label`, `repo`.

## How it survives X updates: fingerprints

Patches do not use obfuscated names. `mod-src/xmodpatch/fingerprints.py` finds every class, field
and method from stable anchors:

| Anchor | Example |
|---|---|
| kotlinx serial names + serializer element names | `com.x.models.timelines.items.UrtTimelinePost` → `promotedMetadata`, `prerollMetadata` |
| field ↔ property mapping | the synthetic deserialization constructor (`iput pN → field`) |
| data-class `toString` prefixes | `TimelineView(timelineEntryEntity=`, `LoadMore(trigger=` |
| log strings | `Duplicate bottom cursor request. Dropped.`, `URTTimelineRepository`, `XApplication#onCreate` |
| enum constants | `FOR_YOU` / `FOLLOWING` / `VIDEO_TAB`, `OLDER_THAN` / `PULL_TO_REFRESH` |
| method shapes | `(TimelineView, LinkedHashMap, LinkedHashMap, …) → UrtTimelineItem` (the DB row → item mapper) |

The mod code (`mod-src/src/xmod/*.java`) only works with `Object`. All access to X's models goes
through `xmod.A`. `xmodpatch/accessors.py` generates `A` as smali with the real descriptors of the
current build, plus a Java stub with the same signatures for compiling. If X changes something
the mod depends on, the build stops with a clear `FINGERPRINT FAILED: …` instead of producing a
broken APK.

## Hooks (`xmodpatch/hooks.py`)

| Location (found by fingerprint) | Call |
|---|---|
| `Application.onCreate` | `Mod.init` |
| entry of the DB row → `UrtTimelineItem` mapper | `Mod.enterRow` (remembers `timeline_id` = `TimelineType.name` + suffix) |
| every call site of that mapper | `Mod.filterItem` (ads, following-only, photos/videos, Home off, pre-roll) |
| bottom pager `loadMore(LoadMore)` | `Mod.blockBottomPaging` |
| URT repository `fetch(direction, cursor)` | `Mod.blockFetch` (Home off: no network requests) |
| `FeatureSwitchesRepositoryImpl.get/peekBoolean` | `Mod.fsBool` (Google SSP ads) |
| `OnLogoClicked` handler | `Mod.onLogoClick` (double tap → menu) |
| immersive video timeline `<init>(…, pinnedPost, boundedPlaylist, …)` | `Mod.videoPlaylist` (one video only) |
| `AccountData(userId, …)` | `Mod.ownAccount` |

The patched classes and `A.smali` are moved into a new dex, because the original dexes are at the
65k reference limit.

## Clone package (`xmodpatch/axml.py`)

The binary `AndroidManifest.xml` (base and every split) and `resources.arsc` are edited directly:
- `package`, provider authorities and custom `<permission>`s are renamed. Class names under
  `com.twitter.android.*` are left alone.
- `account_type` becomes `<package>.auth.login`, so AccountManager does not collide with the official app.
- `android:label` is set to "X Unhook".
- `REQUEST_INSTALL_PACKAGES` is added for self-updates.

Unchanged strings keep their original bytes, so an untouched file round-trips byte-for-byte.

## Updates (`Updater.java`)

The app checks the latest release of the private repo with a read-only token, at most every 12 h
or from the menu. If the X version is the same, it downloads only `base.apk` and installs it with
`PackageInstaller` in `MODE_INHERIT_EXISTING`. If the X version changed, it downloads base plus the
installed config splits. The redirect to GitHub's storage is followed manually, so the token never
leaves `api.github.com`.
