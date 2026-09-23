#!/bin/sh
# Install a headless Ghidra on Linux/WSL, for tools/ghidra.sh.
#
# tools/ghidra.sh defaults GHIDRA= to a Homebrew path, which is a macOS
# install; this is the Linux/WSL equivalent. It fetches the official release
# from the NSA's GitHub, unpacks it, and prints the two environment variables
# the rest of the tooling wants. Nothing here is Digitakt-specific.
#
#   sudo tools/ghidra/install-linux.sh            # ~570 MB download
#   export GHIDRA_INSTALL_DIR=/opt/ghidra/ghidra_12.1.3_PUBLIC
#   export GHIDRA=$GHIDRA_INSTALL_DIR/support/analyzeHeadless
#   tools/ghidra/install-coldfire-emac.sh         # then the EMAC language
#   tools/ghidra.sh import                        # ~10 min for the 2.4 MB image
#
# VERSION must match a real release tag; Ghidra's headless analyzer needs a
# JDK (21 for the 12.x line), which is why openjdk is installed too. The
# version is pinned rather than "latest" so an import and the scripts that
# read its output cannot silently move under you.
set -e

VERSION=${GHIDRA_VERSION:-12.1.3}
BUILD=${GHIDRA_BUILD:-20260817}
DEST=${GHIDRA_DEST:-/opt/ghidra}
URL="https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${VERSION}_build/ghidra_${VERSION}_PUBLIC_${BUILD}.zip"
ZIP="${TMPDIR:-/tmp}/ghidra_${VERSION}_PUBLIC.zip"

if ! command -v java >/dev/null 2>&1; then
    echo "installing openjdk-21-jdk"
    if command -v apt-get >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openjdk-21-jdk unzip
    else
        echo "no apt-get here: install a JDK 21 and unzip yourself" >&2
        exit 1
    fi
fi
java -version

[ -s "$ZIP" ] || curl -L --retry 3 -o "$ZIP" "$URL"
mkdir -p "$DEST"
unzip -q -o "$ZIP" -d "$DEST"

HEADLESS=$(find "$DEST" -name analyzeHeadless -type f | head -1)
[ -n "$HEADLESS" ] || { echo "unpacked, but no analyzeHeadless under $DEST" >&2; exit 1; }
INSTALL_DIR=$(dirname "$(dirname "$HEADLESS")")

cat <<EOF

Ghidra $VERSION is installed. Put these in your environment:

  export GHIDRA_INSTALL_DIR=$INSTALL_DIR
  export GHIDRA=$HEADLESS

Then build the ColdFire EMAC language and import the image:

  tools/ghidra/install-coldfire-emac.sh
  GHIDRA_LANG=68000:BE:32:ColdfireEMAC tools/ghidra.sh import
EOF
