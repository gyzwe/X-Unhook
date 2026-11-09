#!/usr/bin/env bash
# Install the X Mod clone (package com.twitter.android.xmod) next to the official X app.
set -e
cd "$(dirname "$0")/build/out"
splits=$(ls split_config.*.apk)
adb install-multiple -r base.apk $splits
