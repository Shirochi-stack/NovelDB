#!/bin/sh
# a.sh <adb shell args...>
timeout 20 "$LOCALAPPDATA/Android/Sdk/platform-tools/adb.exe" -s emulator-5562 shell "$@"
