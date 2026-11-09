#!/usr/bin/env bash
# Compiles xmod.Mod against android.jar + stubs of obfuscated X classes into build/dex/classes.dex
set -euo pipefail
M="$(cd "$(dirname "$0")" && pwd)"; W="$(dirname "$M")"
export JAVA_HOME="$W/tools/jdk-21.0.13+9"; export PATH="$JAVA_HOME/bin:$PATH"
BT="$(ls -d "$W"/tools/bt/*)"; AJ="$W/tools/plat/android-35/android.jar"
cd "$M"; rm -rf build; mkdir -p build/stubs build/classes build/dex
javac -nowarn -source 8 -target 8 -bootclasspath "$AJ" -d build/stubs $(find stubs -name '*.java') 2>&1 | grep -v warning || true
javac -nowarn -source 8 -target 8 -bootclasspath "$AJ:$BT/core-lambda-stubs.jar" -cp build/stubs -d build/classes src/xmod/Mod.java 2>&1 | grep -v warning || true
test -f build/classes/xmod/Mod.class
java -cp "$BT/lib/d8.jar" com.android.tools.r8.D8 --release --min-api 29 --lib "$AJ" --classpath build/stubs \
  --output build/dex $(find build/classes -name '*.class')
echo "built $(stat -c %s build/dex/classes.dex) bytes"
