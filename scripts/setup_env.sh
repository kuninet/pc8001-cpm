#!/usr/bin/env bash
#
# 開発・テスト環境セットアップスクリプト
#
# 行うこと:
#   1. Python 仮想環境 (.venv) を作成 (python3.13 を使用)
#   2. ビルドに必要な setuptools / wheel を導入
#   3. Z80 CPU エミュレータコア (kosarev/z80, MIT) を external/ に取得しビルド
#
# 注意:
#   - Python 3.14 では kosarev/z80 のビルド (setup.py) が inspect 周りで失敗するため
#     python3.13 を明示的に使用する。
#   - `pip install z80` は PEP517 ビルド分離下で同じ失敗をするため、clone して
#     `setup.py build_ext --inplace` で直接ビルドする。
#
# 前提コマンド: python3.13, git, (C++ コンパイラ = macOS の clang 等)
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3.13}"
Z80_REPO="${Z80_REPO:-https://github.com/kosarev/z80}"
# 動作確認済みコミットに固定(再現性のため)。ブランチ/タグ/コミットSHAいずれも指定可。
Z80_REF="${Z80_REF:-a3847b55ed3e7c09d4be63c8363c96f12570dbd6}"

echo "==> Python 仮想環境を作成 (.venv, $PYTHON)"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "エラー: $PYTHON が見つかりません。python3.13 を導入してください (brew install python@3.13)。" >&2
  exit 1
fi
[ -d .venv ] || "$PYTHON" -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python --version

echo "==> ビルド依存を導入 (setuptools, wheel)"
pip install --quiet --upgrade pip setuptools wheel

echo "==> Z80 エミュレータコアを取得 ($Z80_REPO @ $Z80_REF)"
# ブランチ/タグ/コミットSHA を一律に扱うため fetch-by-ref で浅く取得する
# (GitHub は want-sha に対応しているためコミットSHAも浅く取得可能)。
mkdir -p external
if [ ! -d external/z80/.git ]; then
  git init -q external/z80
  git -C external/z80 remote add origin "$Z80_REPO"
fi
git -C external/z80 fetch --depth 1 origin "$Z80_REF"
git -C external/z80 checkout -q FETCH_HEAD

echo "==> Z80 拡張モジュールをビルド (build_ext --inplace)"
( cd external/z80 && python setup.py build_ext --inplace >/dev/null )

echo "==> 導入確認"
PYTHONPATH="external/z80" python - <<'PY'
import z80
m = z80.Z80Machine()
print("  z80 import OK / Z80Machine 生成OK")
PY

echo ""
echo "セットアップ完了。"
echo "  - 仮想環境:  . .venv/bin/activate"
echo "  - エミュコア: PYTHONPATH=external/z80 で import z80 可能"
echo "  - 動作確認:  make smoke"
