#!/usr/bin/env bash
# Install this skill into the DSH home that is actually in use.
#
#   ./install.sh                          # auto-detect the live harness home
#   ./install.sh /path/to/.dsh            # explicit harness home -> <home>/skills
#   ./install.sh /path/to/skills --skills # explicit skills root
#   SKIP_DEPS=1 ./install.sh              # do not run pip
#   SKIP_VERIFY=1 ./install.sh            # do not run scripts/selfcheck.py
#
# Home detection order: argument, $DSH_HOME, the .dsh/.dsh-home this script sits
# inside (self-location), $DSH_PROFILE_DIR derived, ~/.dsh, ~/.dsh-home (legacy).
# DSH resolves skills as <home>/skills, so profiles are not a separate target.
# Re-installing keeps the old copy at <home>/skill-backups/<name>-<timestamp>
# (latest 3 kept, never inside skills/ - a .bak copy there would load twice).
# The painting/video half needs Windows x64 native DLLs; elsewhere selfcheck fails loudly.
set -euo pipefail

src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
arg="${1:-}"
arg2="${2:-}"

# clean up stage dirs left by interrupted runs (older than 6 h)
find "${TMPDIR:-/tmp}" -maxdepth 1 -name 'svp-stage-*' -mmin +360 -exec rm -rf {} + 2>/dev/null || true

# 1) Python 3.10+
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys;sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "X  need Python 3.10+ (python3 / python on PATH)"
  exit 2
fi
echo "python: $PY $("$PY" -c 'import sys;print(sys.version.split()[0])')"

# 2) resolve home
home=""
origin=""
dest=""
if [ -n "$arg" ] && [ "$arg2" = "--skills" ]; then
  dest="$arg"; origin="--skills"
elif [ -n "$arg" ]; then
  home="$arg"; origin="argument"
elif [ -n "${DSH_HOME:-}" ]; then
  home="$DSH_HOME"; origin="env DSH_HOME"
fi

if [ -z "$dest" ] && [ -z "$home" ]; then
  probe="$src"
  for _ in 1 2 3 4; do
    leaf="$(basename "$probe")"
    if [ "$leaf" = ".dsh-home" ] || [ "$leaf" = ".dsh" ]; then
      [ -d "$probe/skills" ] && { home="$probe"; origin="self-location"; }
      break
    fi
    parent="$(dirname "$probe")"
    [ "$parent" = "$probe" ] && break
    probe="$parent"
  done
fi

if [ -z "$dest" ] && [ -z "$home" ] && [ -n "${DSH_PROFILE_DIR:-}" ]; then
  up="$(dirname "$(dirname "$DSH_PROFILE_DIR")")"
  [ -d "$up/skills" ] && { home="$up"; origin="env DSH_PROFILE_DIR"; }
fi

if [ -z "$dest" ] && [ -z "$home" ]; then
  if [ -d "$HOME/.dsh/skills" ]; then home="$HOME/.dsh"; origin="default ~/.dsh"
  elif [ -d "$HOME/.dsh-home/skills" ]; then home="$HOME/.dsh-home"; origin="legacy ~/.dsh-home"
  else home="$HOME/.dsh"; origin="new ~/.dsh"; fi
fi

if [ -z "$dest" ]; then dest="$home/skills"; fi
echo "home: ${home:-$dest}  ($origin)"

# 3) warn about other homes that already hold a copy
norm() { local p; p="$(cd "$1" 2>/dev/null && { pwd -W 2>/dev/null || pwd; })" || p="$1"; printf '%s' "$p"; }
here="$(norm "$(dirname "$dest")")"
for h in "${DSH_HOME:-}" "$HOME/.dsh" "$HOME/.dsh-home"; do
  [ -n "$h" ] || continue
  [ "$(norm "$h")" = "$here" ] && continue
  if [ -d "$h/skills/svg-rebuild-and-paint" ]; then
    echo "!  another copy: $h/skills/svg-rebuild-and-paint  (use it with: $0 $h)"
  fi
done

# 4) stage, then swap (safe even when this script lives inside the target)
# Windows cannot rename a directory that is a process's cwd, so leave the tree first.
target="$dest/svg-rebuild-and-paint"
cd "${TMPDIR:-/tmp}" 2>/dev/null || cd /
stage="$(mktemp -d "${TMPDIR:-/tmp}/svp-stage-XXXXXX")"
cp -R "$src/." "$stage/"
find "$stage" -type d \( -name .git -o -name __pycache__ -o -name _kf -o -name .idea \) -prune -exec rm -rf {} + 2>/dev/null || true
find "$stage" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete 2>/dev/null || true

swapped=0
if [ -e "$target" ]; then
  bak_root="$(dirname "$dest")/skill-backups"
  mkdir -p "$bak_root"
  bak="$bak_root/svg-rebuild-and-paint-$(date +%Y%m%d-%H%M%S)"
  if mv "$target" "$bak" 2>/dev/null; then
    echo "previous -> $bak"
    swapped=1
  else
    # 目录被某个进程的 cwd 占着（典型：用户就站在技能目录里跑 ./install.sh），
    # Windows 不允许重命名这种目录：改为复制备份 + 原地刷新（内容允许删除/覆盖）。
    cp -R "$target" "$bak"
    echo "previous -> $bak (copied; dir is in use, refreshing in place)"
    find "$target" -mindepth 1 -delete 2>/dev/null || true
  fi
  ls -1d "$bak_root"/svg-rebuild-and-paint-* 2>/dev/null | sort -r | tail -n +4 | while IFS= read -r old; do rm -rf "$old"; done
fi

mkdir -p "$dest"
if [ -e "$target" ] && [ "$swapped" = "0" ]; then
  cp -R "$stage/." "$target/"
  rm -rf "$stage"
else
  mv "$stage" "$target"
fi
echo "installed -> $target"

# 5) deps (this script owns them; requirements.txt is skill content)
DEPS="numpy Pillow scipy potracer opencv-python imageio-ffmpeg"
mod_of() {
  case "$1" in
    Pillow) echo PIL ;;
    opencv-python) echo cv2 ;;
    imageio-ffmpeg) echo imageio_ffmpeg ;;
    potracer) echo potrace ;;
    *) echo "$1" ;;
  esac
}

if [ "${SKIP_DEPS:-0}" != "1" ]; then
  missing=""
  for d in $DEPS; do
    "$PY" -c "import $(mod_of "$d")" >/dev/null 2>&1 || missing="$missing $d"
  done
  if [ -z "$missing" ]; then
    echo "deps: already satisfied"
  else
    echo "installing:$missing"
    # shellcheck disable=SC2086
    "$PY" -m pip install --disable-pip-version-check -q $missing || echo "pip failed; run: $PY -m pip install$missing"
  fi
fi

bad=""
for d in $DEPS; do
  "$PY" -c "import $(mod_of "$d")" >/dev/null 2>&1 || bad="$bad $d"
done
if [ -n "$bad" ]; then
  echo "X  missing deps:$bad"
  echo "   run: $PY -m pip install$bad"
  exit 3
fi
echo "deps: ok"

# 6) verify
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
echo "  $PY \"$target/scripts/selfcheck.py\""
echo "  $PY \"$target/scripts/auto.py\" ref.png out.html"
[ "$ok" = "1" ] || exit 1
