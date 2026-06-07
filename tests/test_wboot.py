"""
WBOOT・RST7スタブ テスト (#36)
実行: PYTHONPATH=external/z80 .venv/bin/python -m pytest tests/test_wboot.py -q

テスト概要:
  - SDイメージにCCP/BDOSダミーパターンを配置
  - WBOOT(0xE945)を実行後、CCP/BDOSが正しい位置に再ロードされることを確認
  - ゼロページ(0x0000-0x0007)が正しく再設定されることを確認
  - RST7ベクタ(0x0038)にRET(0xC9)が書き込まれることを確認
  - CUR_DMA=0x0080、CUR_DISK=0 に初期化されることを確認
  - BOOT実行後もRST7ベクタが設定されることを確認
"""

import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emu.pc8001 import PC8001
from emu.sdcard import SDCard
from bios_syms import sym

# ---------------------------------------------------------------
# 定数
# ---------------------------------------------------------------
BIOS_ORG    = 0xE900
BIOS_BIN    = os.path.join(PROJECT_ROOT, "build", "bios.bin")
BIOS_SRC    = os.path.join(PROJECT_ROOT, "src", "bios", "bios.asm")
BUILD_DIR   = os.path.join(PROJECT_ROOT, "build")

# BIOS ジャンプテーブル: vec(n) = 0xE900 + 3*n
def vec(n: int) -> int:
    return BIOS_ORG + 3 * n

# ベクタアドレス
WBOOT_ADDR      = 0xE945    # WBOOT エントリ固定アドレス

# CP/M メモリマップ
CCP_ADDR        = 0xD300    # CCP ロード先
BDOS_ADDR       = 0xDB00    # BDOS ロード先
CCP_BLOCKS      = 4         # CCP SD ブロック数
BDOS_BLOCKS     = 7         # BDOS SD ブロック数
CCP_LBA_START   = 5         # CCP 開始 LBA
BDOS_LBA_START  = 9         # BDOS 開始 LBA
BLOCK_SIZE      = 512       # SD ブロックサイズ

# ゼロページ期待値
ZP_JP_WBOOT     = [0xC3, 0x03, 0xE9]   # JP 0xE903 (WBOOT)
ZP_JP_BDOS      = [0xC3, 0x06, 0xDB]   # JP 0xDB06 (BDOS)


# ---------------------------------------------------------------
# ビルドヘルパ
# ---------------------------------------------------------------

def _build_bios() -> None:
    """BIOS をアセンブル・バイナリ化する。"""
    os.makedirs(BUILD_DIR, exist_ok=True)
    p_file   = os.path.join(BUILD_DIR, "bios.p")
    bin_file = BIOS_BIN

    result = subprocess.run(
        ["asl", "-D", "origin=0E900h", "-o", p_file, BIOS_SRC],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"asl アセンブル失敗\n{result.stdout}\n{result.stderr}")

    result = subprocess.run(
        ["p2bin", p_file, bin_file, "-r", "$e900-$f2ff"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"p2bin 変換失敗\n{result.stdout}\n{result.stderr}")


def _load_bios(pc: PC8001) -> None:
    """ビルド済みの bios.bin を PC8001 へロードする。"""
    with open(BIOS_BIN, "rb") as f:
        data = f.read()
    pc.load(BIOS_ORG, data)


def _make_sd_image() -> bytearray:
    """
    テスト用SDイメージを作成する。
      LBA 5-8  (CCP):  各ブロック全バイト = 0xC0, 0xC1, 0xC2, 0xC3
      LBA 9-15 (BDOS): 各ブロック全バイト = 0xD0, 0xD1, ..., 0xD6
      CCPの先頭バイト(LBA5の先頭)は HALT(0x76) にする:
        WBOOT後にCCP(0xD300)へJPするので、CCPの先頭がHALTなら
        run_until_haltが正常に返る。
    """
    image = bytearray(256 * BLOCK_SIZE)

    # CCP ブロック(LBA 5-8)
    ccp_patterns = [0xC0, 0xC1, 0xC2, 0xC3]
    for i, pat in enumerate(ccp_patterns):
        lba = CCP_LBA_START + i
        block = bytearray([pat] * BLOCK_SIZE)
        if i == 0:
            block[0] = 0x76  # HALT: WBOOT後にCCPへジャンプ時にすぐ停止
        image[lba * BLOCK_SIZE:(lba + 1) * BLOCK_SIZE] = block

    # BDOS ブロック(LBA 9-15)
    bdos_patterns = [0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6]
    for i, pat in enumerate(bdos_patterns):
        lba = BDOS_LBA_START + i
        block = bytearray([pat] * BLOCK_SIZE)
        image[lba * BLOCK_SIZE:(lba + 1) * BLOCK_SIZE] = block

    return image


def _call_addr(pc: PC8001, addr: int, max_steps: int = 5000000) -> bool:
    """指定アドレスを CALL して HALT まで実行する。"""
    pc.cpu.sp = 0xDF00
    pc.cpu.halted = False
    trampoline = 0xD000
    code = [0xCD, addr & 0xFF, (addr >> 8) & 0xFF, 0x76]
    pc.load(trampoline, bytes(code))
    pc.set_pc(trampoline)
    return pc.run_until_halt(max_steps=max_steps)


def setup_wboot_env() -> tuple[PC8001, SDCard]:
    """
    WBOOT テスト環境をセットアップする。
    - SDイメージ作成・接続
    - BIOS ロード
    - SD_INIT を事前実行してCCS等を確定
    """
    image = _make_sd_image()
    sd = SDCard(image=image)
    pc = PC8001()
    pc.attach_sd(sd)
    # BIOS をロード
    _load_bios(pc)
    # SD_INIT を事前実行 (CCS を確定させる)
    _call_addr(pc, sym('SD_INIT'))
    return pc, sd


# ---------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def build_bios_once():
    """テストモジュール実行前に1回だけビルドを行う。"""
    _build_bios()


# ---------------------------------------------------------------
# テスト1: WBOOT 後の CCP/BDOS 再ロード確認
# ---------------------------------------------------------------

class TestWbootLoad:
    """WBOOT 実行後に CCP/BDOS が正しい位置にロードされることを確認する。"""

    def test_wboot_ccp_placed(self):
        """
        WBOOT 実行後、CCP ダミー(LBA5 パターン)が 0xD300 に配置される。
        LBA5 先頭バイトは HALT(0x76)。
        """
        pc, sd = setup_wboot_env()
        # WBOOT を set_pc で直接実行 (スタック設定済み)
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(WBOOT_ADDR)
        halted = pc.run_until_halt(max_steps=5000000)
        assert halted, "WBOOT: HALT に到達しなかった"

        # CCP先頭(0xD300)はHALT(0x76)
        val = pc._mem_read(CCP_ADDR)
        assert val == 0x76, f"CCP先頭 0xD300={val:#04x} (expected 0x76 HALT)"

        # LBA5の2バイト目以降は 0xC0 パターン
        val2 = pc._mem_read(CCP_ADDR + 1)
        assert val2 == 0xC0, f"CCP 0xD301={val2:#04x} (expected 0xC0)"

        # LBA6 先頭(0xD500)は 0xC1
        val3 = pc._mem_read(CCP_ADDR + BLOCK_SIZE)
        assert val3 == 0xC1, f"CCP LBA6先頭 0xD500={val3:#04x} (expected 0xC1)"

    def test_wboot_bdos_placed(self):
        """
        WBOOT 実行後、BDOS ダミー(LBA9 パターン)が 0xDB00 に配置される。
        """
        pc, sd = setup_wboot_env()
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(WBOOT_ADDR)
        halted = pc.run_until_halt(max_steps=5000000)
        assert halted, "WBOOT: HALT に到達しなかった"

        # BDOS先頭(0xDB00): LBA9パターン 0xD0
        val = pc._mem_read(BDOS_ADDR)
        assert val == 0xD0, f"BDOS先頭 0xDB00={val:#04x} (expected 0xD0)"

        # LBA10の先頭(0xDB00+512=0xDD00): 0xD1
        val2 = pc._mem_read(BDOS_ADDR + BLOCK_SIZE)
        assert val2 == 0xD1, f"BDOS LBA10先頭 0xDD00={val2:#04x} (expected 0xD1)"

    def test_wboot_ccp_all_blocks(self):
        """
        CCP 全4ブロック(LBA5-8)の先頭バイトパターン確認。
        """
        pc, sd = setup_wboot_env()
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(WBOOT_ADDR)
        pc.run_until_halt(max_steps=5000000)

        patterns = [0xC1, 0xC2, 0xC3]  # LBA6-8 (LBA5先頭はHALT)
        for i, pat in enumerate(patterns):
            addr = CCP_ADDR + (i + 1) * BLOCK_SIZE
            val = pc._mem_read(addr)
            assert val == pat, (
                f"CCP LBA{CCP_LBA_START+i+1}: "
                f"addr=0x{addr:04X}={val:#04x} (expected {pat:#04x})"
            )

    def test_wboot_bdos_all_blocks(self):
        """
        BDOS 全7ブロック(LBA9-15)の先頭バイトパターン確認。
        """
        pc, sd = setup_wboot_env()
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(WBOOT_ADDR)
        pc.run_until_halt(max_steps=5000000)

        patterns = [0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6]
        for i, pat in enumerate(patterns):
            addr = BDOS_ADDR + i * BLOCK_SIZE
            val = pc._mem_read(addr)
            assert val == pat, (
                f"BDOS LBA{BDOS_LBA_START+i}: "
                f"addr=0x{addr:04X}={val:#04x} (expected {pat:#04x})"
            )


# ---------------------------------------------------------------
# テスト2: ゼロページ再設定確認
# ---------------------------------------------------------------

class TestWbootZeroPage:
    """WBOOT 実行後のゼロページ設定を確認する。"""

    def test_zero_page_wboot_vector(self):
        """
        WBOOT 後、0x0000-0x0002 が JP 0xE903 (WBOOT) になっている。
        ゼロページはバンクRAM(拡張RAM)にあるため、e2=0x11でアクセスする。
        """
        pc, sd = setup_wboot_env()
        pc.e2 = 0x11   # 拡張RAM可視(bit0=1) + 書込許可(bit4=1)
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(WBOOT_ADDR)
        pc.run_until_halt(max_steps=5000000)

        for i, expected in enumerate(ZP_JP_WBOOT):
            val = pc._mem_read(i)
            assert val == expected, (
                f"ゼロページ 0x{i:04X}={val:#04x} (expected {expected:#04x})"
            )

    def test_zero_page_bdos_vector(self):
        """
        WBOOT 後、0x0005-0x0007 が JP 0xDB06 (BDOS エントリ) になっている。
        ゼロページはバンクRAM(拡張RAM)にあるため、e2=0x11でアクセスする。
        """
        pc, sd = setup_wboot_env()
        pc.e2 = 0x11   # 拡張RAM可視(bit0=1) + 書込許可(bit4=1)
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(WBOOT_ADDR)
        pc.run_until_halt(max_steps=5000000)

        for i, expected in enumerate(ZP_JP_BDOS):
            addr = 0x0005 + i
            val = pc._mem_read(addr)
            assert val == expected, (
                f"ゼロページ 0x{addr:04X}={val:#04x} (expected {expected:#04x})"
            )

    def test_zero_page_in_bank_ram(self):
        """
        ゼロページ(0x0000-0x0002)がバンクRAM(拡張RAM)に書かれていること。
        e2 bit0=1 の状態で _mem_read が正しく読めることを確認。
        """
        pc, sd = setup_wboot_env()
        # e2=0x11 (拡張RAM可視+書込許可)を設定してWBOOTを実行
        pc.e2 = 0x11
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(WBOOT_ADDR)
        pc.run_until_halt(max_steps=5000000)

        # e2 bit0=1 なのでbank_ramが見える
        val = pc._mem_read(0x0000)
        assert val == 0xC3, f"ZP[0x0000]={val:#04x} (expected 0xC3 JP)"


# ---------------------------------------------------------------
# テスト3: RST7スタブ確認
# ---------------------------------------------------------------

class TestRst7Stub:
    """RST7ベクタ(0x0038)の設定を確認する。"""

    def test_wboot_rst7_set_to_ret(self):
        """
        WBOOT 実行後、0x0038 に RET(0xC9) が書き込まれている。
        """
        pc, sd = setup_wboot_env()
        pc.e2 = 0x11   # 拡張RAM可視+書込許可
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(WBOOT_ADDR)
        pc.run_until_halt(max_steps=5000000)

        val = pc._mem_read(0x0038)
        assert val == 0xC9, (
            f"RST7ベクタ 0x0038={val:#04x} (expected 0xC9 RET)"
        )

    def test_boot_rst7_set_to_ret(self):
        """
        BOOT 実行後にも 0x0038 に RET(0xC9) が書き込まれている。
        (BOOT_BODY の CALL INSTALL_RST7_STUB による)
        """
        sd = SDCard()
        pc = PC8001()
        pc.attach_sd(sd)
        _load_bios(pc)
        # e2=0x11 で拡張RAM書込を有効化
        pc.e2 = 0x11
        # BOOT は最後に JP 0xD300(CCP) へジャンプするので、0xD300 に HALT を置く
        pc.load(CCP_ADDR, bytes([0x76]))
        # BOOT (0xE900) を実行 → CCP の HALT で停止
        pc.set_pc(BIOS_ORG)
        halted = pc.run_until_halt(max_steps=200000)
        assert halted, "BOOT: HALT に到達しなかった"

        val = pc._mem_read(0x0038)
        assert val == 0xC9, (
            f"BOOT後 RST7ベクタ 0x0038={val:#04x} (expected 0xC9 RET)"
        )


# ---------------------------------------------------------------
# テスト4: ワーク変数の初期化確認
# ---------------------------------------------------------------

class TestWbootWorkInit:
    """WBOOT 実行後のワーク変数初期化を確認する。"""

    def test_cur_disk_is_zero(self):
        """
        WBOOT 後、CUR_DISK=0 (ドライブA) に初期化されている。
        """
        pc, sd = setup_wboot_env()
        # まず別ドライブを選択しておく
        pc._mem_write(sym('CUR_DISK'), 3)
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(WBOOT_ADDR)
        pc.run_until_halt(max_steps=5000000)

        val = pc._mem_read(sym('CUR_DISK'))
        assert val == 0, f"CUR_DISK={val} (expected 0=drive A)"

    def test_cur_dma_is_0x0080(self):
        """
        WBOOT 後、CUR_DMA=0x0080 (デフォルトDMA) に初期化されている。
        """
        pc, sd = setup_wboot_env()
        # まず別アドレスに設定しておく
        cur_dma = sym('CUR_DMA')
        pc._mem_write(cur_dma, 0x00)
        pc._mem_write(cur_dma + 1, 0x90)
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(WBOOT_ADDR)
        pc.run_until_halt(max_steps=5000000)

        lo = pc._mem_read(cur_dma)
        hi = pc._mem_read(cur_dma + 1)
        dma = lo | (hi << 8)
        assert dma == 0x0080, f"CUR_DMA=0x{dma:04X} (expected 0x0080)"


# ---------------------------------------------------------------
# テスト5: WBOOT → CCP ジャンプ確認
# ---------------------------------------------------------------

class TestWbootJumpToCcp:
    """WBOOT 実行後に CCP(0xD300) へジャンプすることを確認する。"""

    def test_wboot_jumps_to_ccp(self):
        """
        WBOOT 実行後、CCP(0xD300) の先頭バイト(HALT=0x76)で停止する。
        CCP ダミーの先頭を HALT にしているので、run_until_halt が返る。
        """
        pc, sd = setup_wboot_env()
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(WBOOT_ADDR)
        halted = pc.run_until_halt(max_steps=5000000)
        assert halted, "WBOOT: CCP(0xD300)のHALTに到達しなかった"

        # HALT 到達後、PC は CCP 先頭 (0xD300) 付近のはず
        # CCP先頭(0xD300) に HALT が書かれていることを確認
        val = pc._mem_read(CCP_ADDR)
        assert val == 0x76, (
            f"CCP先頭 0xD300={val:#04x} (expected HALT 0x76)"
        )


# ---------------------------------------------------------------
# テスト6: ダーティバッファのフラッシュ確認
# ---------------------------------------------------------------

class TestWbootDirtyFlush:
    """WBOOT 前にダーティバッファがフラッシュされることを確認する。"""

    def test_dirty_buffer_flushed_before_load(self):
        """
        BUF_DIRTY=1 の状態で WBOOT を実行すると、WBOOT 後に BUF_DIRTY=0 になる。
        """
        pc, sd = setup_wboot_env()

        # BUF_DIRTY を 1 に設定 (ダーティ状態にする)
        # BUF_LBA も有効な値に設定しておく (0 = LBA0)
        buf_lba = sym('BUF_LBA')
        buf_dirty = sym('BUF_DIRTY')
        pc._mem_write(buf_dirty, 1)    # BUF_DIRTY = 1
        pc._mem_write(buf_lba, 0)      # BUF_LBA = 0
        pc._mem_write(buf_lba+1, 0)
        pc._mem_write(buf_lba+2, 0)
        pc._mem_write(buf_lba+3, 0)

        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(WBOOT_ADDR)
        pc.run_until_halt(max_steps=5000000)

        # WBOOT 後は BUF_DIRTY = 0 になっているはず
        dirty = pc._mem_read(buf_dirty)
        assert dirty == 0, f"BUF_DIRTY={dirty} (expected 0 after WBOOT flush)"
