"""
ディスク層 BIOS テスト (SELDSK/SETTRK/SETSEC/SETDMA/READ/WRITE)
実行: PYTHONPATH=external/z80 .venv/bin/python -m pytest tests/test_disk.py -q
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
BIOS_ORG  = 0xE900
BIOS_BIN  = os.path.join(PROJECT_ROOT, "build", "bios.bin")
BIOS_SRC  = os.path.join(PROJECT_ROOT, "src", "bios", "bios.asm")
BUILD_DIR = os.path.join(PROJECT_ROOT, "build")

# BIOS ジャンプテーブル: vec(n) = 0xE900 + 3*n
def vec(n: int) -> int:
    return BIOS_ORG + 3 * n

# ディスク層ワーク変数アドレス
CUR_DISK_ADDR    = 0xF001
CUR_TRACK_ADDR   = 0xF002
CUR_SECTOR_ADDR  = 0xF004
CUR_DMA_ADDR     = 0xF006
BUF_LBA_ADDR     = 0xF008
BUF_DIRTY_ADDR   = 0xF00C

# DPHテーブル先頭 (ドライブA=0がDPH0)
DPH_TABLE_ADDR   = 0xF0B0

# SD ドライバ固定アドレス
SD_INIT_VEC  = 0xEC00
SD_BUF_ADDR  = 0xEE00

# ---------------------------------------------------------------
# LBA 計算ヘルパ (BIOS の CALC_LBA と同じ計算)
# ---------------------------------------------------------------
OFF = 2    # システム予約トラック数
SPT = 64   # 1トラックのレコード数
DRIVE_BLOCKS = 4128  # 1ドライブのSDブロック数

def calc_lba(drive: int, track: int, sector: int):
    """論理(drive, track, sector)→物理(lba, offset)を計算する。"""
    track_adj = track + OFF
    rec = track_adj * SPT + sector
    offset128 = (rec & 3) * 128
    rec_div4 = rec >> 2
    lba = drive * DRIVE_BLOCKS + rec_div4
    return lba, offset128


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
    with open(BIOS_BIN, "rb") as f:
        data = f.read()
    pc.load(BIOS_ORG, data)


# ---------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def build_bios_once():
    _build_bios()


def setup_disk():
    """PC8001 + SDCard を接続し BIOS をロードして返す。"""
    pc = PC8001()
    sd = SDCard()
    pc.attach_sd(sd)
    _load_bios(pc)
    # SD 初期化
    _call_addr(pc, SD_INIT_VEC)
    return pc, sd


def _call_addr(pc: PC8001, addr: int, max_steps: int = 3000000) -> bool:
    """指定アドレスを CALL して HALT まで実行。"""
    pc.cpu.sp = 0xDF00
    pc.cpu.halted = False
    trampoline = 0xD000
    code = [0xCD, addr & 0xFF, (addr >> 8) & 0xFF, 0x76]
    pc.load(trampoline, bytes(code))
    pc.set_pc(trampoline)
    return pc.run_until_halt(max_steps=max_steps)


def _call_with_regs(pc: PC8001, addr: int, bc: int = 0,
                    max_steps: int = 3000000) -> bool:
    """BC を設定してから指定アドレスを CALL して HALT まで実行。"""
    pc.cpu.sp = 0xDF00
    pc.cpu.halted = False
    trampoline = 0xD000
    # LD BC, nn; CALL addr; HALT
    code = [
        0x01, bc & 0xFF, (bc >> 8) & 0xFF,  # LD BC, nn
        0xCD, addr & 0xFF, (addr >> 8) & 0xFF,
        0x76,
    ]
    pc.load(trampoline, bytes(code))
    pc.set_pc(trampoline)
    return pc.run_until_halt(max_steps=max_steps)


def _mem_read16(pc: PC8001, addr: int) -> int:
    """Z80メモリからリトルエンディアン16bit値を読む。"""
    lo = pc._mem_read(addr)
    hi = pc._mem_read(addr + 1)
    return lo | (hi << 8)


def _mem_read32(pc: PC8001, addr: int) -> int:
    """Z80メモリからリトルエンディアン32bit値を読む。"""
    b0 = pc._mem_read(addr)
    b1 = pc._mem_read(addr + 1)
    b2 = pc._mem_read(addr + 2)
    b3 = pc._mem_read(addr + 3)
    return b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)


# ---------------------------------------------------------------
# テスト1: SELDSK
# ---------------------------------------------------------------

class TestSeldsk:
    """SELDSK(vec9): ドライブ選択"""

    def test_drive_a_returns_dph(self):
        """C=0 で DPH_TABLE 先頭アドレスが HL に返る。"""
        pc, _ = setup_disk()
        halted = _call_with_regs(pc, vec(9), bc=0x0000)  # C=0
        assert halted, "HALT に到達しなかった"
        assert pc.cpu.hl == DPH_TABLE_ADDR, (
            f"SELDSK(A): HL=0x{pc.cpu.hl:04X} (expected 0x{DPH_TABLE_ADDR:04X})"
        )

    def test_drive_h_returns_dph(self):
        """C=7 (ドライブH) で DPH_TABLE+7*16 が HL に返る。"""
        pc, _ = setup_disk()
        halted = _call_with_regs(pc, vec(9), bc=0x0007)  # C=7
        assert halted, "HALT に到達しなかった"
        expected = DPH_TABLE_ADDR + 7 * 16
        assert pc.cpu.hl == expected, (
            f"SELDSK(H): HL=0x{pc.cpu.hl:04X} (expected 0x{expected:04X})"
        )

    def test_invalid_drive_returns_0(self):
        """C=8 (範囲外) で HL=0 が返る。"""
        pc, _ = setup_disk()
        halted = _call_with_regs(pc, vec(9), bc=0x0008)  # C=8
        assert halted, "HALT に到達しなかった"
        assert pc.cpu.hl == 0, f"SELDSK(invalid): HL=0x{pc.cpu.hl:04X} (expected 0)"

    def test_cur_disk_updated(self):
        """C=3 で CUR_DISK ワーク変数が 3 に更新される。"""
        pc, _ = setup_disk()
        _call_with_regs(pc, vec(9), bc=0x0003)
        cur_disk = pc._mem_read(CUR_DISK_ADDR)
        assert cur_disk == 3, f"CUR_DISK={cur_disk} (expected 3)"


# ---------------------------------------------------------------
# テスト2: SETTRK / SETSEC / SETDMA
# ---------------------------------------------------------------

class TestSetters:
    """SETTRK/SETSEC/SETDMA のワーク変数更新テスト"""

    def test_settrk_updates_cur_track(self):
        """SETTRK(vec10): BC=0x0005 → CUR_TRACK=5。"""
        pc, _ = setup_disk()
        _call_with_regs(pc, vec(10), bc=0x0005)
        val = _mem_read16(pc, CUR_TRACK_ADDR)
        assert val == 5, f"CUR_TRACK={val} (expected 5)"

    def test_setsec_updates_cur_sector(self):
        """SETSEC(vec11): BC=0x003F → CUR_SECTOR=63。"""
        pc, _ = setup_disk()
        _call_with_regs(pc, vec(11), bc=0x003F)
        val = _mem_read16(pc, CUR_SECTOR_ADDR)
        assert val == 63, f"CUR_SECTOR={val} (expected 63)"

    def test_setdma_updates_cur_dma(self):
        """SETDMA(vec12): BC=0x8100 → CUR_DMA=0x8100。"""
        pc, _ = setup_disk()
        _call_with_regs(pc, vec(12), bc=0x8100)
        val = _mem_read16(pc, CUR_DMA_ADDR)
        assert val == 0x8100, f"CUR_DMA=0x{val:04X} (expected 0x8100)"


# ---------------------------------------------------------------
# テスト3: READ
# ---------------------------------------------------------------

class TestRead:
    """READ(vec13): SDからCP/Mレコードを読み込む"""

    def test_read_drive_a_track0_sec0(self):
        """
        ドライブA(d=0), track=0, sector=0 の READ テスト。
        lba = 0*4128 + (2*64+0)>>2 = 32, offset = 0
        SD image の LBA32 先頭128バイトに既知パターンを書き込み、
        READ 後 DMA バッファと一致することを確認する。
        """
        pc, sd = setup_disk()

        # 期待パターン生成 (LBA32 のブロック全体)
        lba, offset = calc_lba(0, 0, 0)
        assert lba == 32, f"lba={lba} (expected 32)"
        assert offset == 0, f"offset={offset} (expected 0)"

        pattern_block = bytearray(512)
        for i in range(128):
            pattern_block[i] = (i * 3 + 0xAA) & 0xFF
        sd.write_block(lba, bytes(pattern_block))

        # SETDMA(0x8100), SELDSK(0), SETTRK(0), SETSEC(0)
        DMA_ADDR = 0x8100
        _call_with_regs(pc, vec(12), bc=DMA_ADDR)   # SETDMA
        _call_with_regs(pc, vec(9),  bc=0x0000)      # SELDSK drive A
        _call_with_regs(pc, vec(10), bc=0x0000)      # SETTRK 0
        _call_with_regs(pc, vec(11), bc=0x0000)      # SETSEC 0

        # READ
        halted = _call_addr(pc, vec(13))
        assert halted, "READ: HALT に到達しなかった"
        assert pc.cpu.a == 0, f"READ: A={pc.cpu.a} (expected 0=success)"

        # DMA バッファの内容確認
        got = bytes(pc._mem_read(DMA_ADDR + i) for i in range(128))
        expected = bytes(pattern_block[:128])
        assert got == expected, (
            f"READ: DMA data mismatch: got {list(got[:8])!r}, "
            f"expected {list(expected[:8])!r}"
        )

    def test_read_sector_offset(self):
        """
        track=0, sector=1 → offset=128 のレコードを読む。
        lba = 32 (同じブロック), offset = 128
        """
        pc, sd = setup_disk()

        lba, offset = calc_lba(0, 0, 1)
        assert lba == 32
        assert offset == 128

        pattern_block = bytearray(512)
        for i in range(128, 256):
            pattern_block[i] = (i * 7 + 0x55) & 0xFF
        sd.write_block(lba, bytes(pattern_block))

        DMA_ADDR = 0x8200
        _call_with_regs(pc, vec(12), bc=DMA_ADDR)
        _call_with_regs(pc, vec(9),  bc=0x0000)
        _call_with_regs(pc, vec(10), bc=0x0000)
        _call_with_regs(pc, vec(11), bc=0x0001)  # sector=1

        halted = _call_addr(pc, vec(13))
        assert halted
        assert pc.cpu.a == 0, f"READ: A={pc.cpu.a} (expected 0)"

        got = bytes(pc._mem_read(DMA_ADDR + i) for i in range(128))
        expected = bytes(pattern_block[128:256])
        assert got == expected, (
            f"READ(offset=128): mismatch: got {list(got[:8])}, "
            f"expected {list(expected[:8])}"
        )


# ---------------------------------------------------------------
# テスト4: WRITE
# ---------------------------------------------------------------

class TestWrite:
    """WRITE(vec14): CP/Mレコードをバッファに書き込む"""

    def test_write_dir_flushes_immediately(self):
        """
        WRITE タイプ=1 (ディレクトリ) は即時フラッシュされる。
        DMA に既知データを置いて WRITE(C=1) 後、SD image に書き込まれることを確認。
        """
        pc, sd = setup_disk()

        DMA_ADDR = 0x8300
        write_pattern = bytes([(i * 11 + 0x33) & 0xFF for i in range(128)])
        pc.load(DMA_ADDR, write_pattern)

        _call_with_regs(pc, vec(12), bc=DMA_ADDR)  # SETDMA
        _call_with_regs(pc, vec(9),  bc=0x0000)     # SELDSK drive A
        _call_with_regs(pc, vec(10), bc=0x0000)     # SETTRK 0
        _call_with_regs(pc, vec(11), bc=0x0000)     # SETSEC 0

        # WRITE タイプ=1 (ディレクトリ): C=1
        pc.cpu.halted = False
        pc.cpu.sp = 0xDF00
        trampoline = 0xD000
        code = [
            0x0E, 0x01,                              # LD C, 1
            0xCD, vec(14) & 0xFF, (vec(14) >> 8) & 0xFF,
            0x76,
        ]
        pc.load(trampoline, bytes(code))
        pc.set_pc(trampoline)
        halted = pc.run_until_halt(max_steps=5000000)
        assert halted, "WRITE: HALT に到達しなかった"
        assert pc.cpu.a == 0, f"WRITE: A={pc.cpu.a} (expected 0=success)"

        # BUF_DIRTY が 0 (即時フラッシュ済み)
        dirty = pc._mem_read(BUF_DIRTY_ADDR)
        assert dirty == 0, f"BUF_DIRTY={dirty} (expected 0 after dir write)"

        # SD image の LBA32 先頭128バイトが write_pattern と一致
        lba, offset = calc_lba(0, 0, 0)
        block = sd.read_block(lba)
        assert bytes(block[:128]) == write_pattern, (
            f"WRITE(dir): SD image mismatch: "
            f"got {list(block[:8])!r}, expected {list(write_pattern[:8])!r}"
        )

    def test_write_normal_marks_dirty(self):
        """
        WRITE タイプ=0 (通常) は遅延書込み: BUF_DIRTY=1 になり
        SD image はまだ更新されない(後でフラッシュされる)。
        """
        pc, sd = setup_disk()

        DMA_ADDR = 0x8400
        write_pattern = bytes([(i * 5 + 0x11) & 0xFF for i in range(128)])
        pc.load(DMA_ADDR, write_pattern)

        _call_with_regs(pc, vec(12), bc=DMA_ADDR)
        _call_with_regs(pc, vec(9),  bc=0x0000)
        _call_with_regs(pc, vec(10), bc=0x0000)
        _call_with_regs(pc, vec(11), bc=0x0000)

        # WRITE タイプ=0: C=0
        pc.cpu.halted = False
        pc.cpu.sp = 0xDF00
        trampoline = 0xD000
        code = [
            0x0E, 0x00,
            0xCD, vec(14) & 0xFF, (vec(14) >> 8) & 0xFF,
            0x76,
        ]
        pc.load(trampoline, bytes(code))
        pc.set_pc(trampoline)
        halted = pc.run_until_halt(max_steps=5000000)
        assert halted
        assert pc.cpu.a == 0, f"WRITE(normal): A={pc.cpu.a}"

        # BUF_DIRTY = 1
        dirty = pc._mem_read(BUF_DIRTY_ADDR)
        assert dirty == 1, f"BUF_DIRTY={dirty} (expected 1 for delayed write)"


# ---------------------------------------------------------------
# テスト5: ラウンドトリップ (WRITE→READ で内容一致)
# ---------------------------------------------------------------

class TestRoundtrip:
    """WRITE した内容を READ で読み戻す往復テスト"""

    def test_write_read_roundtrip(self):
        """
        ドライブA track=0 sector=0 に書き込み後、同アドレスを READ して一致を確認。
        ディレクトリ書込み(C=1)で即時フラッシュを使用。
        """
        pc, sd = setup_disk()

        DMA_WRITE = 0x8500
        DMA_READ  = 0x8600
        write_data = bytes([(i * 13 + 0x77) & 0xFF for i in range(128)])
        pc.load(DMA_WRITE, write_data)

        # WRITE
        _call_with_regs(pc, vec(12), bc=DMA_WRITE)
        _call_with_regs(pc, vec(9),  bc=0x0000)
        _call_with_regs(pc, vec(10), bc=0x0000)
        _call_with_regs(pc, vec(11), bc=0x0000)

        pc.cpu.halted = False
        pc.cpu.sp = 0xDF00
        trampoline = 0xD000
        code = [
            0x0E, 0x01,  # WRITE タイプ=1(ディレクトリ、即時フラッシュ)
            0xCD, vec(14) & 0xFF, (vec(14) >> 8) & 0xFF,
            0x76,
        ]
        pc.load(trampoline, bytes(code))
        pc.set_pc(trampoline)
        halted = pc.run_until_halt(max_steps=5000000)
        assert halted, "WRITE(RT): HALT に到達しなかった"
        assert pc.cpu.a == 0, f"WRITE(RT): A={pc.cpu.a}"

        # DMA_READ バッファをゼロクリア
        pc.load(DMA_READ, bytes(128))

        # READ (同じ位置)
        _call_with_regs(pc, vec(12), bc=DMA_READ)
        _call_with_regs(pc, vec(9),  bc=0x0000)
        _call_with_regs(pc, vec(10), bc=0x0000)
        _call_with_regs(pc, vec(11), bc=0x0000)

        halted = _call_addr(pc, vec(13))
        assert halted, "READ(RT): HALT に到達しなかった"
        assert pc.cpu.a == 0, f"READ(RT): A={pc.cpu.a}"

        # 読み戻しデータが書き込みデータと一致
        got = bytes(pc._mem_read(DMA_READ + i) for i in range(128))
        assert got == write_data, (
            f"往復テスト mismatch: got {list(got[:8])!r}, "
            f"expected {list(write_data[:8])!r}"
        )

    def test_multiple_sectors_roundtrip(self):
        """
        同一LBAブロック内の複数レコード(sector=0,1,2,3)を書き込み・読み戻し。
        """
        pc, sd = setup_disk()

        patterns = [
            bytes([(i + s * 50) & 0xFF for i in range(128)])
            for s in range(4)
        ]

        # sector 0-3 を順番に WRITE(type=0: 遅延)
        for sec in range(4):
            dma = 0x8700 + sec * 0x80
            pc.load(dma, patterns[sec])
            _call_with_regs(pc, vec(12), bc=dma)
            _call_with_regs(pc, vec(9),  bc=0x0000)
            _call_with_regs(pc, vec(10), bc=0x0000)
            _call_with_regs(pc, vec(11), bc=sec)
            # 最後のセクタのみディレクトリ書込み(即時フラッシュ)
            write_type = 1 if sec == 3 else 0
            pc.cpu.halted = False
            pc.cpu.sp = 0xDF00
            code = [
                0x0E, write_type,
                0xCD, vec(14) & 0xFF, (vec(14) >> 8) & 0xFF,
                0x76,
            ]
            pc.load(0xD000, bytes(code))
            pc.set_pc(0xD000)
            halted = pc.run_until_halt(max_steps=5000000)
            assert halted, f"WRITE(sec={sec}): HALT に到達しなかった"
            assert pc.cpu.a == 0, f"WRITE(sec={sec}): A={pc.cpu.a}"

        # sector 0-3 を順番に READ して内容確認
        for sec in range(4):
            dma = 0x8B00 + sec * 0x80
            pc.load(dma, bytes(128))  # クリア
            _call_with_regs(pc, vec(12), bc=dma)
            _call_with_regs(pc, vec(9),  bc=0x0000)
            _call_with_regs(pc, vec(10), bc=0x0000)
            _call_with_regs(pc, vec(11), bc=sec)

            halted = _call_addr(pc, vec(13))
            assert halted, f"READ(sec={sec}): HALT に到達しなかった"
            assert pc.cpu.a == 0, f"READ(sec={sec}): A={pc.cpu.a}"

            got = bytes(pc._mem_read(dma + i) for i in range(128))
            assert got == patterns[sec], (
                f"多セクタRT sec={sec}: got {list(got[:4])!r}, "
                f"expected {list(patterns[sec][:4])!r}"
            )
