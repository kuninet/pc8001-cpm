"""メモリ配置パラメータ(テスト用、単一情報源)。

BIOS/CCP/BDOS の配置アドレス・LBA・ゼロページ値を、唯一の調整点
BIOS_BLOCKS(BIOSが占めるSDブロック数)から導出する。

固定値: VRAM=0xF300, CCP=4ブロック(2048B), BDOS=7ブロック(3584B)。

導出規則(Makefile / bios.asm / loader.asm と同一):
  BIOS_ORG = 0xF300 - BIOS_BLOCKS*512
  BDOS_ORG = BIOS_ORG - 3584
  CCP_ORG  = BDOS_ORG - 2048
  LBA: BIOS 0..(N-1), CCP N..(N+3), BDOS (N+4)..(N+10)   (N=BIOS_BLOCKS)
  WBOOTベクタ = BIOS_ORG+3, BDOSエントリ = BDOS_ORG+6

※ Makefile の BIOS_BLOCKS デフォルトと、ここのデフォルトは必ず一致させること。
   環境変数 BIOS_BLOCKS でオーバライド可能。
"""
import os

# --- 単一パラメータ(Makefile デフォルトと一致) ---
BIOS_BLOCKS = int(os.environ.get("BIOS_BLOCKS", "9"))

# --- 固定値 ---
VRAM_BASE = 0xF300
BLOCK_SIZE = 512
CCP_BLOCKS = 4      # 2048B
BDOS_BLOCKS = 7     # 3584B

# --- 派生アドレス ---
BIOS_ADDR = VRAM_BASE - BIOS_BLOCKS * BLOCK_SIZE
BDOS_ADDR = BIOS_ADDR - BDOS_BLOCKS * BLOCK_SIZE   # = BIOS_ADDR - 3584
CCP_ADDR  = BDOS_ADDR - CCP_BLOCKS * BLOCK_SIZE    # = BDOS_ADDR - 2048

# 別名(各テストの命名揺れに対応)
BIOS_ORG = BIOS_ADDR
BDOS_ORG = BDOS_ADDR
CCP_ORG  = CCP_ADDR

# --- 終端アドレス(範囲チェック用) ---
BIOS_END = VRAM_BASE - 1
BDOS_END = BIOS_ADDR - 1
CCP_END  = BDOS_ADDR - 1

# --- LBA レイアウト ---
BIOS_LBA_START = 0
CCP_LBA_START  = BIOS_BLOCKS
BDOS_LBA_START = BIOS_BLOCKS + CCP_BLOCKS

# --- ベクタアドレス ---
# WBOOT エントリは BIOS ジャンプテーブルの JP WBOOT(=vec(1)=BIOS_ORG+3)。
WBOOT_VEC  = BIOS_ADDR + 3            # ゼロページ 0x0000 の JP 先
WBOOT_ENTRY = BIOS_ADDR + 0x45        # WBOOT 本体エントリ(origin+0x45 固定)
BDOS_ENTRY = BDOS_ADDR + 6            # ゼロページ 0x0005 の JP 先

# --- ゼロページ期待バイト列 ---
ZP_JP_WBOOT = [0xC3, WBOOT_VEC & 0xFF, (WBOOT_VEC >> 8) & 0xFF]
ZP_JP_BDOS  = [0xC3, BDOS_ENTRY & 0xFF, (BDOS_ENTRY >> 8) & 0xFF]


def vec(n: int) -> int:
    """BIOS ジャンプテーブル vec(n) = BIOS_ORG + 3*n。"""
    return BIOS_ADDR + 3 * n


def asl_origin() -> str:
    """asl の -D origin= に渡す '0XXXXh' 形式。"""
    return f"0{BIOS_ADDR:X}h"


def _hx(v: int) -> str:
    return f"0{v:X}h"


def bios_asl_defines() -> list[str]:
    """bios.asm を asl でアセンブルする際の -D 引数列(Makefile と同一)。"""
    return [
        "-D", f"origin={_hx(BIOS_ADDR)}",
        "-D", f"CCP_ORG={_hx(CCP_ADDR)}",
        "-D", f"BDOS_ORG={_hx(BDOS_ADDR)}",
        "-D", f"CCP_LBA={CCP_LBA_START}",
        "-D", f"BDOS_LBA={BDOS_LBA_START}",
    ]


def loader_asl_defines() -> list[str]:
    """loader.asm を asl でアセンブルする際の -D 引数列(Makefile と同一)。"""
    return [
        "-D", f"BIOS_ORG={_hx(BIOS_ADDR)}",
        "-D", f"CCP_ORG={_hx(CCP_ADDR)}",
        "-D", f"BDOS_ORG={_hx(BDOS_ADDR)}",
        "-D", f"BIOS_BLOCKS={BIOS_BLOCKS}",
        "-D", f"CCP_LBA={CCP_LBA_START}",
        "-D", f"BDOS_LBA={BDOS_LBA_START}",
    ]


def bios_p2bin_range() -> str:
    """bios.bin の p2bin -r レンジ '$XXXX-$YYYY'。"""
    return f"${BIOS_ADDR:X}-${BIOS_END:X}"
