#!/bin/sh
# Installs ModelShelf for the current user (no root needed):
#   program    -> ~/.local/bin/modelshelf
#   icon       -> ~/.local/share/icons/hicolor/256x256/apps/modelshelf.png
#   menu entry -> ~/.local/share/applications/modelshelf.desktop
# Run "./install.sh --uninstall" to remove them again. Your library data in
# ~/.local/share/ModelShelf is never touched by this script.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
bin="$HOME/.local/bin"
icons="$HOME/.local/share/icons/hicolor/256x256/apps"
apps="$HOME/.local/share/applications"

if [ "$1" = "--uninstall" ]; then
    rm -f "$bin/modelshelf" "$icons/modelshelf.png" "$apps/modelshelf.desktop"
    echo "ModelShelf removed. Your library data in ~/.local/share/ModelShelf was kept."
    exit 0
fi

mkdir -p "$bin" "$icons" "$apps"
install -m 755 "$here/ModelShelf" "$bin/modelshelf"
install -m 644 "$here/modelshelf.png" "$icons/modelshelf.png"
sed "s|^Exec=.*|Exec=$bin/modelshelf|" "$here/modelshelf.desktop" > "$apps/modelshelf.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$apps" >/dev/null 2>&1 || true

echo "ModelShelf is installed. Find it in your application menu, or run: $bin/modelshelf"
case ":$PATH:" in
    *":$bin:"*) ;;
    *) echo "Note: $bin is not on your PATH, so the short command 'modelshelf' will not work in a terminal." ;;
esac
