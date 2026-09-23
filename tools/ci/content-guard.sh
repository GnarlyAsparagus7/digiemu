#!/usr/bin/env bash
# Refuse Elektron firmware and anything derived from it, whatever .gitignore
# says: `git add -f` gets past .gitignore, this does not. Also refuses any
# file over 1 MB, which nothing legitimate in this repository comes near.
# (tests/test_symbols_hashed.py separately refuses literal signature bytes.)
set -euo pipefail
cd "$(dirname "$0")/../.."
status=0

bad=$(git ls-files | grep -E -i '\.(syx|snap|img|bin|wav|zip|pdf)$|(^|/)(sections|snapshots|out)/' || true)
if [[ -n $bad ]]; then
  echo "error: firmware-derived or binary media files are tracked:"
  printf '  %s\n' $bad
  status=1
fi

while IFS= read -r -d '' f; do
  size=$(stat -c %s "$f")
  if ((size > 1048576)); then
    echo "error: $f is $size bytes (limit 1 MB)"
    status=1
  fi
done < <(git ls-files -z)

if ((status == 0)); then
  echo "content guard: ok ($(git ls-files | wc -l) tracked files)"
fi
exit $status
