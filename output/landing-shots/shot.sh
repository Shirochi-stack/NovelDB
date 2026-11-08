#!/bin/sh
# shot.sh NAME : screencap emulator-5562 to m/NAME.png plus a small preview m/NAME-s.png
adb="$LOCALAPPDATA/Android/Sdk/platform-tools/adb.exe"
o="C:/Users/ADMIN/Projects/NpiaDownloader67/output/landing-shots/m"
mkdir -p "$o"
timeout 20 "$adb" -s emulator-5562 exec-out screencap -p > "$o/$1.png"
python -c "
from PIL import Image; im=Image.open('$o/$1.png'); im.resize((324,720)).save('$o/$1-s.png')"
