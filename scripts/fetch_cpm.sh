#!/usr/bin/env bash
#
# CP/M 2.2 本体ソース(CCP/BDOS)取得スクリプト
#
# 本リポジトリには CP/M 本体ソースを含めない方針のため、外部リポジトリから
# external/cpm22 (gitignore 対象) へ取得する。
#
#   取得元: brouhaha/cpm22
#     - CP/M 2.2 の CCP/BDOS を The Macroassembler AS (asl) 用に整形したもの
#     - 実機 CP/M 2.2 バイナリと一致検証済み(CCP/BDOSの6バイトのシリアル番号を除く)
#     - ライセンス: 2022-07-07 の DRDOS, Inc. による許諾(use/distribute/modify/enhance)
#       詳細: http://www.cpm.z80.de/license.html / external/cpm22/LICENSE.txt
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CPM_REPO="${CPM_REPO:-https://github.com/brouhaha/cpm22}"
# 動作確認済みコミットに固定(再現性のため)。ブランチ/タグ/コミットSHAいずれも指定可。
CPM_REF="${CPM_REF:-01018abbccce0bdf4874b0b2ed1a048c5fcc2987}"

echo "==> CP/M 本体ソースを取得 ($CPM_REPO @ $CPM_REF)"
mkdir -p external
if [ ! -d external/cpm22/.git ]; then
  git init -q external/cpm22
  git -C external/cpm22 remote add origin "$CPM_REPO"
fi
git -C external/cpm22 fetch --depth 1 origin "$CPM_REF"
git -C external/cpm22 checkout -q FETCH_HEAD

echo "==> 取得結果"
ls external/cpm22
echo ""
echo "取得完了。CCP/BDOS のビルドは 'make cpm' を参照(配置アドレスは #4 で確定)。"
