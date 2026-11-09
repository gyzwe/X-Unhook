#!/usr/bin/env bash
# Adds the X Mod dex to the rebuilt base APK, aligns and signs base + all splits.
set -euo pipefail
W="$(cd "$(dirname "$0")/.." && pwd)"
export JAVA_HOME="$W/tools/jdk-21.0.13+9"; export PATH="$JAVA_HOME/bin:$PATH"
BT="$(ls -d "$W"/tools/bt/*)"
B="$W/build"
cd "$B"

# next free classesN.dex index
cp base-unsigned.apk base-work.apk
N=$(( $(unzip -Z1 base-work.apk | grep -cE '^classes[0-9]*\.dex$') + 1 ))
rm -rf dexadd && mkdir dexadd && cp "$W/mod-src/build/dex/classes.dex" "dexadd/classes$N.dex"
(cd dexadd && zip -q -0 ../base-work.apk "classes$N.dex")
echo "mod dex -> classes$N.dex"

[ -f xmod.keystore ] || keytool -genkeypair -keystore xmod.keystore -storepass xmodpass -keypass xmodpass \
  -alias xmod -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=X Mod" >/dev/null 2>&1

rm -rf out && mkdir out
"$BT/zipalign" -P 16 -f 4 base-work.apk out/base.apk
rm -f base-work.apk
cp "$W"/apkm/split_config.*.apk out/
for f in out/*.apk; do
  "$BT/apksigner" sign --ks xmod.keystore --ks-pass pass:xmodpass --ks-key-alias xmod "$f"
done
rm -f out/*.idsig
for f in out/*.apk; do "$BT/apksigner" verify "$f" >/dev/null; done
echo "signed: $(ls out | wc -l) apks"
