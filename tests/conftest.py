"""pytest 共通設定。

全テストの実行前に一度だけビルド成果物を用意する(session-scope)。
各テストモジュールは個別に asl/make を呼ぶが、それらの一部は
`bios.lst`(シンボル表)を生成しない(-L 無し)ため、make clean 直後の
一発実行では bios.lst が欠落して sym() が失敗しうる。ここで先に
`make` を通し、bios.lst 含む全成果物を確実に揃えておく。

配置は単一パラメータ BIOS_BLOCKS から導出(tests/memmap.py / Makefile)。
環境変数 BIOS_BLOCKS で上書きする場合は make にも同じ値が伝わる。
"""
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def _prebuild_artifacts():
    has_cpm = os.path.isdir(os.path.join(ROOT, "external", "cpm22"))
    targets = ["bios", "loader"] + (["cpm", "cpm-image"] if has_cpm else [])
    for t in targets:
        r = subprocess.run(
            ["make", t], cwd=ROOT, capture_output=True, text=True
        )
        if r.returncode != 0:
            pytest.fail(f"prebuild: make {t} 失敗:\n{r.stderr or r.stdout}")
    yield
