# X Unhook

**X (Twitter) for Android without the parts that keep you scrolling.**

X Unhook patches the official X Android app. It is not a website wrapper or an accessibility
service. It removes ads and recommendations, can stop the feed from loading forever, and leaves
search, profiles, notifications and messages working as usual. It installs **next to** the
official X app and updates itself from this repository's releases.

> Unofficial. Not affiliated with X Corp. See the [disclaimer](#disclaimer).

## Features

| | |
|---|---|
| **No ads** | Promoted posts, accounts, trends and event cards, Google ads, video pre-rolls |
| **Finite feed** | Stop loading older posts after the first page or after ~50 / 100 / 200 items. Pull down to refresh. |
| **Following, for real** | Only posts from accounts you follow (and your own): no "Who to follow", topics or suggestions. Optionally no reposts. |
| **One video at a time** | A video opened from a post plays on its own; swiping does not lead to more recommended videos |
| **Hide videos / photos** | In For you, Following, Explore, Lists and Communities. Profiles, search and replies are not affected. |
| **Turn off Home** | For you and Following stay empty and nothing is loaded |
| **Permanent lock** | Features you lock stay on until you reinstall the app; everything else stays changeable |
| **Self-update** | Checks this repo's releases and installs updates in place |

Open the menu by **double-tapping the X logo** on the Home screen.

## Install

1. Download `X-Unhook-v<N>-X<version>.apks` from the [latest release](https://github.com/gyzwe/X-Unhook/releases/latest).
2. Install it with [SAI (Split APKs Installer)](https://github.com/Aefyr/SAI), or with
   `adb install-multiple base.apk split_config.*.apk` using the individual APKs from the release.
3. Log in as usual. The app is called **X Unhook** (`com.twitter.android.unhook`) and does not replace the official app.

Requirements: Android 10+, arm64.

Known limitations:
- Push notifications may not work, because Firebase is bound to the official package name.
- Login depends on X's servers accepting a re-signed client.

## Build it yourself

You need an X `.apkm` bundle (for example from APKMirror), Python 3, and these tools in `tools/`:
JDK 21, apktool, Android build-tools and an `android-35` platform.

```
python3 -I mod-src/build.py --apkm com.twitter.android_<version>.apkm
```

The build writes `build/out/` with the signed `base.apk`, the splits, an `.apks` bundle and
`xmod-release.json`. A signing key is generated in `secrets/` on first run. Keep that key:
updates only install over a build signed with the same key.

## How it works

**Fingerprints, not obfuscated names.** X is obfuscated with R8, so class names such as `j1` or
`util.b` change with every release. `mod-src/xmodpatch/fingerprints.py` locates every class,
field and method it needs from anchors that survive obfuscation:

| Anchor | Example |
|---|---|
| kotlinx serial names and serializer element names | `com.x.models.timelines.items.UrtTimelinePost` → `promotedMetadata` |
| field ↔ property mapping | the synthetic deserialization constructor (`iput pN → field`) |
| data-class `toString` prefixes | `TimelineView(timelineEntryEntity=`, `LoadMore(trigger=` |
| log strings | `Duplicate bottom cursor request. Dropped.`, `URTTimelineRepository` |
| enum constants and method shapes | `FOR_YOU`/`FOLLOWING`, the DB row → `UrtTimelineItem` mapper |

The mod code (`mod-src/src/xmod`) only works with `Object`. All access to X's models goes through
a generated accessor class (`xmodpatch/accessors.py`) that carries the real descriptors of the
current build. If X changes something the mod relies on, the build stops with
`FINGERPRINT FAILED: …` instead of producing a broken APK.

**Hooks** (`xmodpatch/hooks.py`) are inserted in these places:
- `Application.onCreate`;
- the timeline DB row → item mapper, at its entry and at every call site;
- the bottom pager's `loadMore`;
- the URT repository's network fetch;
- feature switches;
- the Home logo click;
- the immersive video timeline;
- the `AccountData` constructor.

**Clone package** (`xmodpatch/axml.py`): the build edits the binary `AndroidManifest.xml` (base and
splits) and `resources.arsc` directly. It renames the package, provider authorities, custom
permissions and the account type, sets the label, and adds `REQUEST_INSTALL_PACKAGES` for self-updates.

**Updates** (`Updater.java`): the app checks `releases/latest`. If the X version is unchanged, it
downloads only `base.apk` and installs it with `PackageInstaller` in `MODE_INHERIT_EXISTING`.

## Disclaimer

X Unhook is an unofficial, independent project. It is not affiliated with, endorsed by or
sponsored by X Corp. "X" and "Twitter" are trademarks of their respective owners.

It modifies the official app and is installed outside Google Play. Using a modified client may
violate X's Terms of Service and may get your account restricted. **Use it at your own risk.**

X Unhook does not collect, store or send your data or credentials. Its only network request of
its own is the update check against this repository's GitHub releases.
