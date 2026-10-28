#!/usr/bin/env bash
#
# search.sh - Search KiCad symbol libraries for symbols or libraries.
#
# Usage:
#   ./scripts/search.sh --search <pattern>        # search all symbol libs
#   ./scripts/search.sh --search esp              # everything matching "esp"
#   ./scripts/search.sh --search "Device:R"       # exact symbol across libs
#   ./scripts/search.sh --lib Device --search R   # only inside Device.kicad_sym
#   ./scripts/search.sh --list-libs               # list installed symbol libs
#
# Output: lib_id (Library:Symbol) and library path so you can copy it into a
# project spec (projects/<slug>/<slug>.py).
#
SYMBOL_DIR="${KICAD9_SYMBOL_DIR:-/usr/share/kicad/symbols}"

usage() {
  cat <<EOF
Usage: $0 --search <pattern> [--lib <library>]
       $0 --list-libs

Options:
  --search PATTERN   Search symbol libraries for symbols/libs matching PATTERN
  --lib LIBRARY      Limit search to a single library (e.g. Device, Amplifier_Operational)
  --list-libs        List all installed symbol libraries
  -h, --help         Show this help

Examples:
  $0 --search esp
  $0 --search "Device:R"
  $0 --lib Device --search R
  $0 --list-libs
EOF
  exit 0
}

SEARCH_PATTERN=""
LIB_FILTER=""
LIST_LIBS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --search)
      SEARCH_PATTERN="${2:-}"; shift 2 ;;
    --lib)
      LIB_FILTER="${2:-}"; shift 2 ;;
    --list-libs)
      LIST_LIBS=1; shift ;;
    -h|--help)
      usage ;;
    *)
      echo "error: unknown option: $1" >&2; usage ;;
  esac
done

if [ ! -d "$SYMBOL_DIR" ]; then
  echo "error: symbol directory not found: $SYMBOL_DIR" >&2
  echo "       set KICAD9_SYMBOL_DIR to your KiCad symbols path" >&2
  exit 1
fi

if [ "$LIST_LIBS" -eq 1 ]; then
  echo "Installed symbol libraries in $SYMBOL_DIR:"
  ls -1 "$SYMBOL_DIR"/*.kicad_sym 2>/dev/null | sed 's|.*/||' | sort || true
  exit 0
fi

if [ -z "$SEARCH_PATTERN" ]; then
  echo "error: --search <pattern> or --list-libs is required" >&2
  usage
fi

# Build the list of libraries to search
if [ -n "$LIB_FILTER" ]; then
  LIBS=("$SYMBOL_DIR/${LIB_FILTER}.kicad_sym")
  if [ ! -f "${LIBS[0]}" ]; then
    echo "error: library not found: ${LIBS[0]}" >&2
    exit 1
  fi
else
  LIBS=("$SYMBOL_DIR"/*.kicad_sym)
fi

FOUND=0

for lib in "${LIBS[@]}"; do
  [ -f "$lib" ] || continue
  libname=$(basename "$lib" .kicad_sym)

  # Each symbol in a .kicad_sym starts with:  (symbol "NAME"
  while IFS= read -r symname; do
    [ -z "$symname" ] && continue
    lib_id="${libname}:${symname}"
    echo "$lib_id"
    echo "  library: $lib"
    echo "  symbol:  $symname"
    echo ""
    FOUND=1
  done < <(grep '(symbol "' "$lib" 2>/dev/null | sed -n 's/.*(symbol "\([^"]*\)".*/\1/p' | grep -i "$SEARCH_PATTERN" || true)
done

if [ "$FOUND" -eq 0 ]; then
  echo "No symbols matching '$SEARCH_PATTERN' found."
  exit 0
fi

echo "Done. Search complete."