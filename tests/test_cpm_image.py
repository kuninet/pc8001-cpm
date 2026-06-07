"""CP/Mシステムイメージ統合テスト(#37)。

`make cpm-image` で生成した SDシステムイメージを SDモデルに載せ、
ローダ(0x6000)を起動 → BIOS/CCP/BDOS が正しい配置にロードされ、
CCP 実体へ制御が渡ることを検証する。

配置・LBA は単一パラメータ BIOS_BLOCKS から導出(tests/memmap.py)。
  LBA 0..(N-1)     → BIOS_ADDR
  LBA N..(N+3)     → CCP_ADDR
  LBA (N+4)..(N+10)→ BDOS_ADDR     (N=BIOS_BLOCKS)
"""
import os
import subprocess

import pytest

from emu.pc8001 import PC8001
from emu.sdcard import SDCard
from bios_syms import sym
from memmap import (
    BIOS_ADDR, CCP_ADDR, BDOS_ADDR, BDOS_ENTRY,
    CCP_LBA_START, BDOS_LBA_START,
    BIOS_BLOCKS, CCP_BLOCKS, BDOS_BLOCKS, BLOCK_SIZE,
    ZP_JP_WBOOT, ZP_JP_BDOS,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(PROJECT_ROOT, "build")
LOADER_BIN = os.path.join(BUILD, "loader.bin")
CPM_IMAGE = os.path.join(BUILD, "cpm-image.bin")
EXTERNAL_CPM22 = os.path.join(PROJECT_ROOT, "external", "cpm22")

TOTAL_BLOCKS = BIOS_BLOCKS + CCP_BLOCKS + BDOS_BLOCKS


def _boot_jp_bytes():
    """BIOS 先頭の JP BOOT = [0xC3, lo, hi]。"""
    boot = sym("BOOT")
    return bytes([0xC3, boot & 0xFF, (boot >> 8) & 0xFF])


def _build_all() -> None:
    if not os.path.isdir(EXTERNAL_CPM22):
        pytest.skip("external/cpm22 が未取得(make fetch-cpm が必要)")
    for target in ("bios", "cpm", "cpm-image", "loader"):
        r = subprocess.run(
            ["make", target], cwd=PROJECT_ROOT, capture_output=True, text=True
        )
        if r.returncode != 0:
            pytest.fail(f"make {target} が失敗: {r.stderr or r.stdout}")


@pytest.fixture(scope="module", autouse=True)
def build_once():
    _build_all()


def _make_pc_with_image():
    with open(LOADER_BIN, "rb") as f:
        loader = f.read()
    with open(CPM_IMAGE, "rb") as f:
        image = f.read()
    pc = PC8001()
    pc.rom[0x6000 : 0x6000 + len(loader)] = loader
    sd_image = bytearray(33024 * 512)
    sd_image[0 : len(image)] = image
    sd = SDCard(image=sd_image)
    pc.attach_sd(sd)
    return pc, sd


def _run_until_ccp(pc, max_steps=5_000_000):
    pc.set_pc(0x6000)
    pc.cpu.halted = False
    for _ in range(max_steps):
        if pc.cpu.halted:
            return "halt"
        pc_val = pc.cpu.pc
        if CCP_ADDR <= pc_val < BDOS_ADDR:
            return "ccp"
        if BDOS_ADDR <= pc_val < BIOS_ADDR:
            return "bdos"
        if pc._mem_read(pc_val) == 0x76:
            return "halt"
        pc.cpu.ticks_to_stop = 200
        pc.cpu.run()
    return "timeout"


class TestCpmImage:
    def test_image_size(self):
        assert os.path.getsize(CPM_IMAGE) == TOTAL_BLOCKS * BLOCK_SIZE

    def test_bios_first_bytes(self):
        with open(CPM_IMAGE, "rb") as f:
            img = f.read()
        # BIOS 先頭 = JP BOOT
        assert img[0:3] == _boot_jp_bytes()

    def test_bdos_entry(self):
        with open(CPM_IMAGE, "rb") as f:
            img = f.read()
        # BDOS先頭LBA + 6 = BDOSエントリ JP nn(BDOS領域内へ)
        off = BDOS_LBA_START * BLOCK_SIZE + 6
        assert img[off] == 0xC3
        assert img[off + 2] == (BDOS_ADDR >> 8) & 0xFF

    def test_loader_reaches_bios(self):
        """ローダ → BIOS BOOT → CCP起動チェーン到達。

        BOOT_DONE は CCP(CCP_ADDR)へジャンプする。ローダが BIOS BOOT を
        正しく起動し、CCP→BDOS の起動チェーンに制御が渡ることを確認する。
        (_run_until_ccp は200tickバッチ実行のスナップショット判定のため、
         CCP領域を踏み越えてBDOS領域で検出されることがある)
        """
        pc, _ = _make_pc_with_image()
        result = _run_until_ccp(pc)
        # CCP もしくは BDOS 領域へ制御が渡っている(起動チェーン成立)
        assert result in ("ccp", "bdos"), (
            f"CCP起動チェーン未到達: {result} pc=0x{pc.cpu.pc:04X}"
        )
        # PC が CCP/BDOS 領域(CCP_ADDR..BIOS_ADDR-1)に居る
        assert CCP_ADDR <= pc.cpu.pc < BIOS_ADDR, (
            f"想定外のPC: 0x{pc.cpu.pc:04X}"
        )

    def test_bios_loaded(self):
        pc, _ = _make_pc_with_image()
        _run_until_ccp(pc)
        actual = bytes(pc._mem_read(BIOS_ADDR + i) for i in range(3))
        assert actual == _boot_jp_bytes()

    def test_ccp_loaded(self):
        pc, _ = _make_pc_with_image()
        _run_until_ccp(pc)
        with open(CPM_IMAGE, "rb") as f:
            img = f.read()
        off = CCP_LBA_START * BLOCK_SIZE
        expected = img[off : off + 4]
        actual = bytes(pc._mem_read(CCP_ADDR + i) for i in range(4))
        assert actual == expected

    def test_bdos_loaded(self):
        pc, _ = _make_pc_with_image()
        _run_until_ccp(pc)
        with open(CPM_IMAGE, "rb") as f:
            img = f.read()
        off = BDOS_LBA_START * BLOCK_SIZE + 6
        expected = img[off : off + 4]
        actual = bytes(pc._mem_read(BDOS_ENTRY + i) for i in range(4))
        assert actual == expected

    def test_zero_page_jumps(self):
        pc, _ = _make_pc_with_image()
        _run_until_ccp(pc)
        # 0x0000: JP WBOOT
        assert [pc._mem_read(0x0000 + i) for i in range(3)] == ZP_JP_WBOOT
        # 0x0005: JP BDOS
        assert [pc._mem_read(0x0005 + i) for i in range(3)] == ZP_JP_BDOS
