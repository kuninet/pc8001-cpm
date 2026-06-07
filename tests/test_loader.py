"""
ブートローダ テスト (Z80エミュレータ実行)
実行: PYTHONPATH=external/z80 .venv/bin/python -m pytest tests/test_loader.py -q

テスト概要:
  - SDの先頭16ブロックにダミーパターンを書き込む
  - ローダ(loader.bin)を拡張ROM領域(0x6000)に配置して実行
  - CP/M本体(BIOS/CCP/BDOS)が正しい位置に配置されることを確認
  - ゼロページ(0x0000-0x0007)が正しく初期化されることを確認
  - PC8001-MEM E2 レジスタが 0x11 になることを確認
"""

import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from emu.pc8001 import PC8001
from emu.sdcard import SDCard

# ---------------------------------------------------------------
# 定数
# ---------------------------------------------------------------
LOADER_ORG  = 0x6000
LOADER_BIN  = os.path.join(PROJECT_ROOT, "build", "loader.bin")
LOADER_SRC  = os.path.join(PROJECT_ROOT, "src", "loader", "loader.asm")
BUILD_DIR   = os.path.join(PROJECT_ROOT, "build")

# CP/M 配置アドレス
BIOS_ADDR   = 0xE900
CCP_ADDR    = 0xD300
BDOS_ADDR   = 0xDB00

# SDレイアウト(設計02準拠)
BIOS_LBA_START  = 0
BIOS_BLOCKS     = 5
CCP_LBA_START   = 5
CCP_BLOCKS      = 4
BDOS_LBA_START  = 9
BDOS_BLOCKS     = 7
BLOCK_SIZE      = 512

# ゼロページ期待値
ZP_JP_WBOOT   = [0xC3, 0x03, 0xE9]   # JP 0xE903
ZP_JP_BDOS    = [0xC3, 0x06, 0xDB]   # JP 0xDB06


# ---------------------------------------------------------------
# ビルドヘルパ
# ---------------------------------------------------------------

def _build_loader() -> None:
    """loader.asm をアセンブルしてバイナリ・HEXを生成する。"""
    os.makedirs(BUILD_DIR, exist_ok=True)

    p_file   = os.path.join(BUILD_DIR, "loader.p")
    bin_file = LOADER_BIN
    hex_file = os.path.join(BUILD_DIR, "loader.hex")

    result = subprocess.run(
        ["asl", "-o", p_file, LOADER_SRC],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"asl アセンブル失敗\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    result = subprocess.run(
        ["p2bin", p_file, bin_file, "-r", "$6000-$7fff"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"p2bin 変換失敗\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    result = subprocess.run(
        ["p2hex", p_file, hex_file],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"p2hex 変換失敗\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _load_loader_to_rom(pc: PC8001) -> None:
    """ビルド済み loader.bin を PC8001 のROM(0x6000)へ書き込む。"""
    with open(LOADER_BIN, "rb") as f:
        data = f.read()
    # ROM は pc.rom (bytearray, 0x8000バイト)
    # loader.bin は p2bin -r '$6000-$7fff' で 8192バイトの固定長出力
    # 0x6000 オフセットから書き込む
    pc.rom[0x6000:0x6000 + len(data)] = data


def _make_sd_image() -> bytearray:
    """
    テスト用SDイメージを作成する。
    各ブロックの全バイトをそのブロックを識別するパターンで埋める。
      LBA 0-4  (BIOS): 各ブロック先頭バイト = 0xB0, 0xB1, ..., 0xB4
      LBA 5-8  (CCP):  0xC0, 0xC1, 0xC2, 0xC3
      LBA 9-15 (BDOS): 0xD0, 0xD1, ..., 0xD6
    BIOSの先頭(LBA0の先頭バイト)は HALT(0x76) を置く。
    ローダがBIOS BOOTへJPした後、即座にHALTしてrun_until_haltが返るようにする。
    """
    # 十分な大きさ(最低16ブロック = 8KB)
    image = bytearray(256 * BLOCK_SIZE)

    # BIOS ブロック(LBA 0-4): LBA0の先頭は HALT(0x76)
    bios_patterns = [0xB0, 0xB1, 0xB2, 0xB3, 0xB4]
    for i, pat in enumerate(bios_patterns):
        lba = BIOS_LBA_START + i
        block = bytearray([pat] * BLOCK_SIZE)
        if i == 0:
            block[0] = 0x76  # HALT: BIOS BOOTへジャンプ後すぐ停止
        image[lba * BLOCK_SIZE:(lba + 1) * BLOCK_SIZE] = block

    # CCP ブロック(LBA 5-8)
    ccp_patterns = [0xC0, 0xC1, 0xC2, 0xC3]
    for i, pat in enumerate(ccp_patterns):
        lba = CCP_LBA_START + i
        block = bytearray([pat] * BLOCK_SIZE)
        image[lba * BLOCK_SIZE:(lba + 1) * BLOCK_SIZE] = block

    # BDOS ブロック(LBA 9-15)
    bdos_patterns = [0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6]
    for i, pat in enumerate(bdos_patterns):
        lba = BDOS_LBA_START + i
        block = bytearray([pat] * BLOCK_SIZE)
        image[lba * BLOCK_SIZE:(lba + 1) * BLOCK_SIZE] = block

    return image


def _mem_read_word(pc: PC8001, addr: int) -> int:
    """指定アドレスから2バイト読み出す(リトルエンディアン)。"""
    lo = pc._mem_read(addr)
    hi = pc._mem_read(addr + 1)
    return lo | (hi << 8)


# ---------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def build_loader_once():
    """テストモジュール実行前に1回だけビルドを行う。"""
    _build_loader()


def setup_loader_env() -> tuple[PC8001, SDCard]:
    """
    テスト環境をセットアップする。
    - SDイメージを作成してSDCardに設定
    - loader.binをROM(0x6000)に配置
    - PC8001を返す
    """
    image = _make_sd_image()
    sd = SDCard(image=image)
    pc = PC8001()
    pc.attach_sd(sd)
    _load_loader_to_rom(pc)
    return pc, sd


# ---------------------------------------------------------------
# テスト: ブートシーケンス全体
# ---------------------------------------------------------------

class TestLoaderBoot:
    """ローダのブートシーケンス統合テスト"""

    def _run_loader(self, pc: PC8001, max_steps: int = 5000000) -> bool:
        """
        0x6000 からローダを実行し、HALT まで待つ。
        BIOSの先頭(0xE900)は HALT なので、ローダが正常に動けば止まる。
        """
        pc.set_pc(LOADER_ORG)
        return pc.run_until_halt(max_steps=max_steps)

    def test_loader_bios_placed(self):
        """
        ローダ実行後、BIOSダミーが 0xE900 に配置されていること。
        LBA0のブロックは先頭バイト=HALT(0x76)、残りは 0xB0。
        """
        pc, sd = setup_loader_env()
        halted = self._run_loader(pc)
        assert halted, "ローダ: HALT に到達しなかった"

        # BIOS先頭(0xE900)はHALT(0x76)
        val = pc._mem_read(BIOS_ADDR)
        assert val == 0x76, f"BIOS先頭 0xE900={val:#04x} (expected 0x76 HALT)"

        # LBA0の2バイト目以降(0xE901〜): 0xB0 パターン
        val2 = pc._mem_read(BIOS_ADDR + 1)
        assert val2 == 0xB0, f"BIOS 0xE901={val2:#04x} (expected 0xB0)"

        # LBA1の先頭(0xE900+512=0xEB00): 0xB1
        val3 = pc._mem_read(BIOS_ADDR + BLOCK_SIZE)
        assert val3 == 0xB1, f"BIOS 0xEB00={val3:#04x} (expected 0xB1)"

    def test_loader_ccp_placed(self):
        """
        ローダ実行後、CCPダミーが 0xD300 に配置されていること。
        LBA5のブロックは全バイト 0xC0。
        """
        pc, sd = setup_loader_env()
        self._run_loader(pc)

        # CCP先頭(0xD300): LBA5パターン 0xC0
        val = pc._mem_read(CCP_ADDR)
        assert val == 0xC0, f"CCP先頭 0xD300={val:#04x} (expected 0xC0)"

        # LBA6の先頭(0xD300+512=0xD500): 0xC1
        val2 = pc._mem_read(CCP_ADDR + BLOCK_SIZE)
        assert val2 == 0xC1, f"CCP 0xD500={val2:#04x} (expected 0xC1)"

    def test_loader_bdos_placed(self):
        """
        ローダ実行後、BDOSダミーが 0xDB00 に配置されていること。
        LBA9のブロックは全バイト 0xD0。
        """
        pc, sd = setup_loader_env()
        self._run_loader(pc)

        # BDOS先頭(0xDB00): LBA9パターン 0xD0
        val = pc._mem_read(BDOS_ADDR)
        assert val == 0xD0, f"BDOS先頭 0xDB00={val:#04x} (expected 0xD0)"

        # LBA10の先頭(0xDB00+512=0xDD00): 0xD1
        val2 = pc._mem_read(BDOS_ADDR + BLOCK_SIZE)
        assert val2 == 0xD1, f"BDOS 0xDD00={val2:#04x} (expected 0xD1)"

    def test_loader_zero_page_wboot(self):
        """
        ローダ実行後、0x0000-0x0002 が JP 0xE903 (WBOOT) になっていること。
        """
        pc, sd = setup_loader_env()
        self._run_loader(pc)

        # 0x0000-0x0002: JP 0xE903
        for i, expected in enumerate(ZP_JP_WBOOT):
            val = pc._mem_read(i)
            assert val == expected, (
                f"ゼロページ 0x{i:04X}={val:#04x} (expected {expected:#04x})"
            )

    def test_loader_zero_page_bdos(self):
        """
        ローダ実行後、0x0005-0x0007 が JP 0xDB06 (BDOSエントリ) になっていること。
        """
        pc, sd = setup_loader_env()
        self._run_loader(pc)

        # 0x0005-0x0007: JP 0xDB06
        for i, expected in enumerate(ZP_JP_BDOS):
            addr = 0x0005 + i
            val = pc._mem_read(addr)
            assert val == expected, (
                f"ゼロページ 0x{addr:04X}={val:#04x} (expected {expected:#04x})"
            )

    def test_loader_e2_is_hide_rom(self):
        """
        ローダ実行後、PC8001-MEM E2 レジスタが 0x11 になっていること。
        (bit0=1: ROM隠蔽, bit4=1: 拡張RAM書込許可)
        """
        pc, sd = setup_loader_env()
        self._run_loader(pc)

        assert pc.e2 == 0x11, f"E2={pc.e2:#04x} (expected 0x11)"

    def test_loader_zero_page_in_bank_ram(self):
        """
        ゼロページ(0x0000-0x0007)はバンクRAM(bank0)に書かれていること。
        E2 bit0=1 で拡張RAMが読めるので _mem_read で確認。
        """
        pc, sd = setup_loader_env()
        self._run_loader(pc)

        # E2=0x11なのでRAMが見えている → _mem_readで読める
        val0 = pc._mem_read(0x0000)
        assert val0 == 0xC3, f"ZP[0x0000]={val0:#04x} (expected 0xC3 JP)"

    def test_loader_bios_all_blocks(self):
        """
        BIOSの全ブロック(LBA 0-4, 2560B)が正しく配置されていること。
        各ブロックのパターンバイト確認。
        """
        pc, sd = setup_loader_env()
        self._run_loader(pc)

        patterns = [0xB0, 0xB1, 0xB2, 0xB3, 0xB4]
        for i, pat in enumerate(patterns):
            addr = BIOS_ADDR + i * BLOCK_SIZE
            if i == 0:
                # LBA0先頭はHALT(0x76), 2バイト目からパターン
                val = pc._mem_read(addr + 1)
                expected = 0xB0
            else:
                val = pc._mem_read(addr)
                expected = pat
            assert val == expected, (
                f"BIOS LBA{i}: addr=0x{addr:04X}+{1 if i==0 else 0} "
                f"= {val:#04x} (expected {expected:#04x})"
            )

    def test_loader_bdos_all_blocks(self):
        """
        BDOSの全ブロック(LBA 9-15, 3584B)が正しく配置されていること。
        """
        pc, sd = setup_loader_env()
        self._run_loader(pc)

        patterns = [0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6]
        for i, pat in enumerate(patterns):
            addr = BDOS_ADDR + i * BLOCK_SIZE
            val = pc._mem_read(addr)
            assert val == pat, (
                f"BDOS LBA{BDOS_LBA_START + i}: "
                f"addr=0x{addr:04X} = {val:#04x} (expected {pat:#04x})"
            )
