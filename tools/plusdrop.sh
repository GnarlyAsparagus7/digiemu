#!/bin/bash
# The WSL half of the Windows Explorer drop target.
#
# plusdrive-drop.cmd sits next to the emulator in digikit-win and hands this
# script the card image and whatever was dropped on it, as Windows paths.
# Files land in /incoming, which is the directory the firmware itself uses
# for samples transferred from a computer -- FORMAT +DRIVE creates it, which
# is why it exists on a card nobody has written to.
#
# The writing is tools/ekfsadd.py. What it knows about the filesystem came
# out of the firmware with Ghidra and is checked against an image the
# firmware formatted: the superblock checksum is reproduced exactly, and a
# format made here matches the device's byte for byte.
set -u
cd "$(dirname "$0")/.." || { echo "cannot find the repo from $0"; exit 1; }
PY=.venv/bin/python
TOOL=tools/ekfsadd.py

if [ $# -lt 2 ]; then
    echo "usage: plusdrop.sh <card image> <file>..."
    exit 2
fi

IMG=$(wslpath -a "$1") || exit 1
shift
echo "+Drive image: $IMG"
if [ ! -f "$IMG" ]; then
    echo
    echo "There is no card image there yet."
    echo "Start the emulator once and let it boot; it creates the file."
    exit 1
fi

if ! $PY -P "$TOOL" "$IMG" --check >/dev/null 2>&1; then
    echo
    echo "This card has no sample filesystem yet, so it is being formatted."
    echo "Only the sample region is touched, and it is empty."
    $PY -P "$TOOL" "$IMG" --format --check || exit 1
    echo
fi

files=()
for a in "$@"; do
    p=$(wslpath -a "$a" 2>/dev/null) || { echo "  skipping $a"; continue; }
    if [ -f "$p" ]; then
        files+=("$p")
    else
        echo "  skipping (not a file): $a"
    fi
done
if [ ${#files[@]} -eq 0 ]; then
    echo "nothing to copy"
    exit 1
fi

$PY -P "$TOOL" "$IMG" "${files[@]}" || exit 1
echo
echo "Done. Restart the emulator so the firmware re-reads the drive."
