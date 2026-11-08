#!/bin/sh
set -eu
folder=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$applications"
escaped=$(printf '%s' "$folder" | sed 's/\\/\\\\/g; s/"/\\"/g; s/`/\\`/g; s/\$/\\$/g')
cat > "$applications/phone-screen.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Экран телефона
Comment=Экран и звук Android по локальной сети
Exec="$escaped/PhoneScreen"
Icon=$folder/_internal/vendor/scrcpy-linux-x86_64-v5.0.1/scrcpy.png
Terminal=false
Categories=Utility;
StartupNotify=false
EOF
printf 'Ярлык создан: %s\n' "$applications/phone-screen.desktop"
