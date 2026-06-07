"""BIOS シンボルアドレス取得ヘルパ。

bios.asm は org を BIOS_ORG(0xE900)1つだけにし、内部ルーチン/変数は
すべて連続配置している。テストは固定アドレスをハードコードせず、本ヘルパで
シンボル名からアドレスを取得する(ビルド成果物 build/bios.lst を解析)。

これにより、コードサイズが変わってアドレスがシフトしてもテストが追従する。
"""
import os
import re
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(PROJECT_ROOT, "build")
BIOS_LST = os.path.join(BUILD, "bios.lst")
BIOS_BIN = os.path.join(BUILD, "bios.bin")

# asl リスティングのシンボル表行: " NAME :        0XXXX C |"(1行に複数可)
_SYM_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([0-9A-Fa-f]{1,5})\s+[CL]\b")

_cache: dict[str, int] | None = None


def _build_bios() -> None:
    r = subprocess.run(
        ["make", "bios"], cwd=PROJECT_ROOT, capture_output=True, text=True
    )
    if r.returncode != 0:
        raise RuntimeError(f"make bios 失敗: {r.stderr or r.stdout}")


def bios_symbols(rebuild: bool = False) -> dict[str, int]:
    """build/bios.lst を解析してシンボル名→アドレスの辞書を返す。"""
    global _cache
    if _cache is not None and not rebuild:
        return _cache
    if rebuild or not os.path.exists(BIOS_LST):
        _build_bios()
    syms: dict[str, int] = {}
    with open(BIOS_LST, encoding="utf-8", errors="replace") as f:
        for line in f:
            # 本体リスティング行("123/E953 :")はスキップ、シンボル表のみ拾う
            if "/" in line.split(":", 1)[0]:
                continue
            for name, hexaddr in _SYM_RE.findall(line):
                syms[name] = int(hexaddr, 16)
    if "SD_INIT" not in syms:
        raise RuntimeError("シンボル表の解析に失敗(SD_INIT が見つからない)")
    _cache = syms
    return syms


def sym(name: str) -> int:
    """単一シンボルのアドレスを返す。"""
    s = bios_symbols()
    if name not in s:
        raise KeyError(f"BIOSシンボル {name!r} が見つかりません")
    return s[name]
