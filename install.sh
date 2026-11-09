#!/usr/bin/env bash
# Install modded X via adb. The original X must be uninstalled first (different signature).
set -e
cd "$(dirname "$0")/build/out"
adb install-multiple -r base.apk split_config.arm64_v8a.apk split_config.xxhdpi.apk split_config.xxxhdpi.apk \
  split_config.en.apk split_config.ru.apk
