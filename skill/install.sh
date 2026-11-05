#!/usr/bin/env bash
# Install this skill into a DSH skills directory.
#   ./install.sh                       # -> ${DSH_HOME:-$HOME/.dsh-home}/skills
#   ./install.sh /path/to/skills       # explicit skills root
#   SKIP_DEPS=1 ./install.sh           # do not run pip
#   SKIP_VERIFY=1 ./install.sh         # do not run scripts/selfcheck.py
#
# Re-installing keeps the old copy as <target>.bak-<timestamp> (latest 3 kept).
# NOTE: the painting/video half needs Windows x64 DLLs (scripts/native/*.dll);
#       on Linux/macOS only the SVG half is usable and selfcheck will say so.
set -euo pipefail

src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "${1:-}" != "" ]; then dest="$1"; else dest="${DSH_HOME:-$HOME/.dsh-home}/skills"; fi
target="$dest/svg-rebuild-and-paint"

# 1) Python 3.10+
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)" 2>/dev/null; then
      PY="$cand"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "X  need Python 3.10+ (python3 / python on PATH)"
  exit 2
fi
echo "python: $PY $("$PY" -c "import sys;print("%d.%d.%d"%sys.version_info[:3])")"

# 2) copy with backup
mkdir -p "$dest"
if [ -e "$target" ]; then
  bak="$target.bak-$(date +%Y%m%d-%H%M%S)"
  mv "$target" "$bak"
  echo "kept previous -> $bak"
  ls -1d "$dest"/svg-rebuild-and-paint.bak-* 2>/dev/null | sort -r | tail -n +4 | while read -r old; do
    rm -rf "$old"
  done
fi
cp -R "$src" "$target"
find "$target" -type d \( -name .git -o -name __pycache__ -o -name _kf -o -name .idea \) -prune -exec rm -rf {} + 2>/dev/null || true
find "$target" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete 2>/dev/null || true
echo "installed -> $target"

# 3) deps (this script owns them; requirements.txt is skill content)
DEPS="numpy Pillow scipy potracer opencv-python imageio-ffmpeg"
if [ "${SKIP_DEPS:-0}" != "1" ]; then
  missing=""
  for d in $DEPS; do
    mod="${d%%-*}"; [ "$d" = "Pillow" ] && mod="PIL"; [ "$d" = "opencv-python" ] && mod="cv2"; [ "$d" = "imageio-ffmpeg" ] && mod="imageio_ffmpeg"
    "$PY" -c "import $mod" 2>/dev/null || missing="$missing $d"
  done
  if [ -z "$missing" ]; then
    echo "deps: already satisfied"
  else
    echo "installing:$missing"
    "$PY" -m pip install --disable-pip-version-check -q $missing || echo "pip failed; run: $PY -m pip install$missing"
  fi
fi

# 4) verify
bad=""
for d in $DEPS; do
  mod="${d%%-*}"; [ "$d" = "Pillow" ] && mod="PIL"; [ "$d" = "opencv-python" ] && mod="cv2"; [ "$d" = "imageio-ffmpeg" ] && mod="imageio_ffmpeg"
  "$PY" -c "import $mod" 2>/dev/null || bad="$bad $d"
done
if [ -n "$bad" ]; then
  echo "X  missing deps:$bad"
  echo "   run: $PY -m pip install$bad"
  exit 3
fi
echo "deps: ok"

ok=1
if [ "${SKIP_VERIFY:-0}" != "1" ]; then
  echo
  echo "selfcheck ..."
  "$PY" "$target/scripts/selfcheck.py" || ok=0
fi

echo
if [ "$ok" = "1" ]; then
  echo "OK  installed. Restart DSH; skill id: svg-rebuild-and-paint"
else
  echo "!   installed, but selfcheck failed - see the FAIL line above"
fi
echo "Try it:"
echo "  $PY "$target/scripts/selfcheck.py""
echo "  $PY "$target/scripts/auto.py" ref.png out.html"
[ "$ok" = "1" ] || exit 1
