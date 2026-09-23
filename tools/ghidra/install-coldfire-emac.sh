#!/bin/sh
# Compile the ColdfireEMAC language and link it into Ghidra's user Extensions.
#
# The module stays in the repo (tools/ghidra/ColdfireEMAC, see its README).
# Ghidra follows a symlink in <UserSettings>/Extensions in both analyzeHeadless
# and pyghidra, so a Homebrew upgrade does not remove the module. The settings
# folder name contains the Ghidra version, so re-run this after an upgrade.
set -e
GHIDRA="${GHIDRA_INSTALL_DIR:-/opt/homebrew/Cellar/ghidra/12.1.3/libexec}"
[ -d "$GHIDRA" ] || { echo "no Ghidra at $GHIDRA (set GHIDRA_INSTALL_DIR=)" >&2; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
MOD="$HERE/ColdfireEMAC"
PROPS="$GHIDRA/Ghidra/application.properties"
VER=$(sed -n 's/^application.version=//p' "$PROPS")
REL=$(sed -n 's/^application.release.name=//p' "$PROPS")
"$GHIDRA/support/sleigh" "$MOD/data/languages/coldfire_emac.slaspec"
cat > "$MOD/extension.properties" <<EOF
name=ColdfireEMAC
description=ColdFire language with MOVCLR and corrected EMAC instructions
author=digitakt2
createdOn=
version=$VER
EOF
# Where Ghidra keeps per-user settings differs by platform: macOS uses
# ~/Library/ghidra, Linux follows XDG (~/.config/ghidra) and older builds
# still read ~/.ghidra. Link into every one that applies -- a symlink into a
# directory this Ghidra does not consult is harmless, a missing one is not.
case "$(uname -s)" in
  Darwin) EXTS="$HOME/Library/ghidra/ghidra_${VER}_${REL}/Extensions" ;;
  *)      EXTS="${XDG_CONFIG_HOME:-$HOME/.config}/ghidra/ghidra_${VER}_${REL}/Extensions
$HOME/.ghidra/.ghidra_${VER}_${REL}/Extensions" ;;
esac
echo "$EXTS" | while read -r EXT; do
    [ -n "$EXT" ] || continue
    mkdir -p "$EXT"
    ln -sfn "$MOD" "$EXT/ColdfireEMAC"
    echo "linked $EXT/ColdfireEMAC -> $MOD (68000:BE:32:ColdfireEMAC)"
done
