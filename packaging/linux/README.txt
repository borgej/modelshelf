ModelShelf for Linux
====================

Try it without installing:

    ./ModelShelf

Install it for your user (adds it to the application menu, no root needed):

    ./install.sh

Remove it again:

    ./install.sh --uninstall


What it needs
-------------

A 64-bit desktop Linux with glibc 2.35 or newer (Ubuntu 22.04, Debian 12,
Fedora 36, Linux Mint 21 and later) and a graphics driver with OpenGL 3.3.

ModelShelf brings its own Qt. A few small system libraries are expected to be
present, as they are on a normal desktop install. If the program does not
start, install them:

    Debian, Ubuntu, Mint:
        sudo apt install libxcb-cursor0 libegl1 libgl1 libxkbcommon-x11-0

    Fedora:
        sudo dnf install xcb-util-cursor libglvnd-egl libglvnd-glx libxkbcommon-x11

    Arch:
        sudo pacman -S xcb-util-cursor libglvnd libxkbcommon-x11

To see why it does not start, run it from a terminal: ./ModelShelf


Where your data lives
---------------------

The library database, thumbnails and settings are in ~/.local/share/ModelShelf
(or $XDG_DATA_HOME/ModelShelf). Your model files are only ever read.

Project page: https://github.com/borgej/modelshelf
