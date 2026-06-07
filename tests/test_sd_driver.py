"""
SDブロックドライバ テスト (BIOS Z80実行)
実行: PYTHONPATH=external/z80 .venv/bin/python -m pytest tests/test_sd_driver.py -q
"""

import os
import re
import subprocess
import sys
import pytest

# プロジェクトルートをパスに追加
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emu.pc8001 import PC8001
from emu.sdcard import SDCard
from bios_syms import sym
import memmap

# ---------------------------------------------------------------
# 定数(配置は tests/memmap.py で BIOS_BLOCKS から導出)
# ---------------------------------------------------------------
BIOS_ORG  = memmap.BIOS_ADDR
BIOS_BIN  = os.path.join(PROJECT_ROOT, "build", "bios.bin")
BIOS_LST  = os.path.join(PROJECT_ROOT, "build", "bios.lst")
BIOS_SRC  = os.path.join(PROJECT_ROOT, "src", "bios", "bios.asm")
BUILD_DIR = os.path.join(PROJECT_ROOT, "build")

# ---------------------------------------------------------------
# ビルドヘルパ
# ---------------------------------------------------------------

def _build_bios() -> None:
    """BIOS をアセンブル・バイナリ化 + リスティング生成。"""
    os.makedirs(BUILD_DIR, exist_ok=True)

    p_file   = os.path.join(BUILD_DIR, "bios.p")
    bin_file = BIOS_BIN
    lst_file = BIOS_LST

    result = subprocess.run(
        ["asl", *memmap.bios_asl_defines(),
         "-L", "-olist", lst_file,
         "-o", p_file, BIOS_SRC],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"asl アセンブル失敗\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    result = subprocess.run(
        ["p2bin", p_file, bin_file],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"p2bin 変換失敗\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _load_bios(pc: PC8001) -> None:
    """ビルド済みの bios.bin を PC8001 へロードする。"""
    with open(BIOS_BIN, "rb") as f:
        data = f.read()
    pc.load(BIOS_ORG, data)


# ---------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def build_bios_once():
    """テストモジュール実行前に1回だけビルドを行う。"""
    _build_bios()


def setup_sd():
    """PC8001 + SDCard を接続してセットアップした状態で返す。"""
    pc = PC8001()
    sd = SDCard()
    pc.attach_sd(sd)
    _load_bios(pc)
    return pc, sd


def _call_addr(pc: PC8001, addr: int, max_steps: int = 2000000) -> bool:
    """
    指定アドレスを CALL して HALT まで実行。
    Z80メモリに CALL addr; HALT のコードを配置して実行する。
    戻り: HALT到達なら True
    """
    # スタック領域 (0xDF00) を使用
    pc.cpu.sp = 0xDF00
    # halted フラグをリセット (前回のHALT状態をクリア)
    pc.cpu.halted = False
    # 0xD000 に実行コード配置: CALL addr(3B) + HALT(1B)
    trampoline = 0xD000
    code = [
        0xCD,               # CALL nn
        addr & 0xFF,
        (addr >> 8) & 0xFF,
        0x76,               # HALT
    ]
    pc.load(trampoline, bytes(code))
    pc.set_pc(trampoline)
    return pc.run_until_halt(max_steps=max_steps)


# ---------------------------------------------------------------
# テスト1: SD_INIT (SDカード初期化)
# ---------------------------------------------------------------

class TestSDInit:
    """SD_INIT の動作テスト"""

    def test_sd_init_success_with_sdhc(self):
        """
        SDHC接続時に SD_INIT が成功する。
        期待: HALT到達、Fのキャリービット=0
        """
        pc, sd = setup_sd()

        halted = _call_addr(pc, sym('SD_INIT'))
        assert halted, "SD_INIT: HALT に到達しなかった"

        # A=0 (成功) かつ CY=0 を確認
        # SD_INIT 成功時は XOR A(A=0, CY=0) で返る
        a_reg = pc.cpu.a
        f_reg = pc.cpu.f
        carry = (f_reg >> 0) & 1  # bit0 = carry

        assert a_reg == 0, f"SD_INIT: A={a_reg:#04x} (expected 0)"
        assert carry == 0, f"SD_INIT: CY={carry} (expected 0)"

    def test_sd_init_sets_ccs_for_sdhc(self):
        """
        SDHCカード接続時、SD_CCS に非ゼロ値が格納される。
        (CMD58 OCR[0] bit6=CCS=1 → SD_CCS = 0x40)
        """
        pc, sd = setup_sd()

        halted = _call_addr(pc, sym('SD_INIT'))
        assert halted, "SD_INIT: HALT に到達しなかった"

        ccs = pc._mem_read(sym('SD_CCS'))
        assert ccs != 0, f"SD_CCS={ccs:#04x} (expected non-zero for SDHC)"

    def test_sd_init_no_sd_fails(self):
        """
        SD未接続時に SD_INIT は CY=1 で失敗する。
        (R1ポーリングが全て0xFFとなりタイムアウト)
        """
        pc = PC8001()  # SDなし
        _load_bios(pc)

        halted = _call_addr(pc, sym('SD_INIT'))
        assert halted, "SD_INIT(SD無し): HALT に到達しなかった"

        f_reg = pc.cpu.f
        carry = (f_reg >> 0) & 1
        assert carry == 1, f"SD_INIT(SD無し): CY={carry} (expected 1)"


# ---------------------------------------------------------------
# テスト2: SD_READ_BLOCK (ブロック読込)
# ---------------------------------------------------------------

class TestSDReadBlock:
    """SD_READ_BLOCK の動作テスト"""

    def test_read_block_returns_correct_data(self):
        """
        ブロック5に既知パターンを書き込んでから、
        Z80コードで SD_READ_BLOCK を呼び SD_BUF の内容が一致する。
        """
        pc, sd = setup_sd()

        # ブロック5にテストパターンを直接書き込む
        pattern = bytes([(i * 3 + 0x55) & 0xFF for i in range(512)])
        sd.write_block(5, pattern)

        # SD_INIT を実行してカードを初期化
        halted = _call_addr(pc, sym('SD_INIT'))
        assert halted, "SD_INIT: HALT に到達しなかった"

        # DE=0, HL=5 に設定して SD_READ_BLOCK を CALL
        # トランポリンコード: LD DE,0; LD HL,5; CALL SD_READ_BLOCK; HALT
        sd_read_vec = sym('SD_READ_BLOCK')
        trampoline = 0xD000
        code = [
            0x11, 0x00, 0x00,   # LD DE, 0x0000
            0x21, 0x05, 0x00,   # LD HL, 0x0005
            0xCD,               # CALL nn
            sd_read_vec & 0xFF,
            (sd_read_vec >> 8) & 0xFF,
            0x76,               # HALT
        ]
        pc.load(trampoline, bytes(code))
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(trampoline)
        halted = pc.run_until_halt(max_steps=5000000)
        assert halted, "SD_READ_BLOCK: HALT に到達しなかった"

        # CY=0 確認
        f_reg = pc.cpu.f
        carry = (f_reg >> 0) & 1
        assert carry == 0, f"SD_READ_BLOCK: CY={carry} (expected 0)"

        # SD_BUF の内容がパターンと一致する
        sd_buf = sym('SD_BUF')
        buf_data = bytes(pc._mem_read(sd_buf + i) for i in range(512))
        assert buf_data == pattern, (
            f"SD_BUF mismatch: "
            f"first bytes got {list(buf_data[:8])}, expected {list(pattern[:8])}"
        )

    def test_read_block_lba_zero(self):
        """LBA=0 のブロックを読込む。"""
        pc, sd = setup_sd()

        # ブロック0にマーカーを書き込む
        marker_data = bytearray(512)
        marker_data[0] = 0xAA
        marker_data[1] = 0x55
        marker_data[511] = 0x77
        sd.write_block(0, bytes(marker_data))

        halted = _call_addr(pc, sym('SD_INIT'))
        assert halted, "SD_INIT: HALT に到達しなかった"

        # DE=0, HL=0
        sd_read_vec = sym('SD_READ_BLOCK')
        trampoline = 0xD000
        code = [
            0x11, 0x00, 0x00,   # LD DE, 0x0000
            0x21, 0x00, 0x00,   # LD HL, 0x0000
            0xCD,
            sd_read_vec & 0xFF,
            (sd_read_vec >> 8) & 0xFF,
            0x76,
        ]
        pc.load(trampoline, bytes(code))
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(trampoline)
        halted = pc.run_until_halt(max_steps=5000000)
        assert halted, "SD_READ_BLOCK(LBA=0): HALT に到達しなかった"

        sd_buf = sym('SD_BUF')
        assert pc._mem_read(sd_buf + 0) == 0xAA
        assert pc._mem_read(sd_buf + 1) == 0x55
        assert pc._mem_read(sd_buf + 511) == 0x77


# ---------------------------------------------------------------
# テスト3: SD_WRITE_BLOCK (ブロック書込)
# ---------------------------------------------------------------

class TestSDWriteBlock:
    """SD_WRITE_BLOCK の動作テスト"""

    def test_write_block_writes_to_sd_image(self):
        """
        SD_BUF に既知データを設定してから SD_WRITE_BLOCK を呼び、
        sd._image にデータが書き込まれている。
        """
        pc, sd = setup_sd()

        # SD 初期化
        halted = _call_addr(pc, sym('SD_INIT'))
        assert halted, "SD_INIT: HALT に到達しなかった"

        # SD_BUF に書き込みデータを設定
        write_data = bytes([(i * 7 + 0x11) & 0xFF for i in range(512)])
        pc.load(sym('SD_BUF'), write_data)

        # DE=0, HL=7 に設定して SD_WRITE_BLOCK を CALL (ブロック7)
        sd_write_vec = sym('SD_WRITE_BLOCK')
        trampoline = 0xD000
        code = [
            0x11, 0x00, 0x00,   # LD DE, 0x0000
            0x21, 0x07, 0x00,   # LD HL, 0x0007
            0xCD,
            sd_write_vec & 0xFF,
            (sd_write_vec >> 8) & 0xFF,
            0x76,               # HALT
        ]
        pc.load(trampoline, bytes(code))
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(trampoline)
        halted = pc.run_until_halt(max_steps=10000000)
        assert halted, "SD_WRITE_BLOCK: HALT に到達しなかった"

        # CY=0 確認
        f_reg = pc.cpu.f
        carry = (f_reg >> 0) & 1
        assert carry == 0, f"SD_WRITE_BLOCK: CY={carry} (expected 0)"

        # sd._image のブロック7が書き込みデータと一致する
        image_data = bytes(sd._image[7 * 512: 7 * 512 + 512])
        assert image_data == write_data, (
            f"sd._image block7 mismatch: "
            f"first bytes got {list(image_data[:8])}, expected {list(write_data[:8])}"
        )

    def test_write_then_read_roundtrip(self):
        """
        SD_WRITE_BLOCK で書き込んだデータを SD_READ_BLOCK で読み戻す。
        """
        pc, sd = setup_sd()

        # SD 初期化
        halted = _call_addr(pc, sym('SD_INIT'))
        assert halted, "SD_INIT: HALT に到達しなかった"

        # 書き込みデータを SD_BUF に設定
        write_data = bytes([(255 - (i & 0xFF)) for i in range(512)])
        sd_buf = sym('SD_BUF')
        pc.load(sd_buf, write_data)

        # SD_WRITE_BLOCK (ブロック3)
        sd_write_vec = sym('SD_WRITE_BLOCK')
        sd_read_vec = sym('SD_READ_BLOCK')
        trampoline = 0xD000
        code = [
            0x11, 0x00, 0x00,   # LD DE, 0x0000
            0x21, 0x03, 0x00,   # LD HL, 0x0003
            0xCD,
            sd_write_vec & 0xFF,
            (sd_write_vec >> 8) & 0xFF,
            0x76,
        ]
        pc.load(trampoline, bytes(code))
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(trampoline)
        halted = pc.run_until_halt(max_steps=10000000)
        assert halted, "SD_WRITE_BLOCK(RT): HALT に到達しなかった"
        assert (pc.cpu.f & 1) == 0, "SD_WRITE_BLOCK(RT): CY=1"

        # SD_BUF をゼロクリアして上書きを確認
        pc.load(sd_buf, bytes(512))

        # SD_READ_BLOCK (ブロック3)
        code = [
            0x11, 0x00, 0x00,   # LD DE, 0x0000
            0x21, 0x03, 0x00,   # LD HL, 0x0003
            0xCD,
            sd_read_vec & 0xFF,
            (sd_read_vec >> 8) & 0xFF,
            0x76,
        ]
        pc.load(trampoline, bytes(code))
        pc.cpu.sp = 0xDF00
        pc.cpu.halted = False
        pc.set_pc(trampoline)
        halted = pc.run_until_halt(max_steps=5000000)
        assert halted, "SD_READ_BLOCK(RT): HALT に到達しなかった"
        assert (pc.cpu.f & 1) == 0, "SD_READ_BLOCK(RT): CY=1"

        # 読み返したデータが一致するか
        buf_data = bytes(pc._mem_read(sd_buf + i) for i in range(512))
        assert buf_data == write_data, (
            f"往復テスト mismatch: "
            f"first bytes got {list(buf_data[:8])}, expected {list(write_data[:8])}"
        )
