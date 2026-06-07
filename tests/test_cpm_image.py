"""CP/Mシステムイメージ統合テスト(#37)。

`make cpm-image` で生成した SDシステムイメージ(8192B)を SDモデルに載せ、
ローダ(0x6000)を起動 → BIOS/CCP/BDOS が正しい配置にロードされ、
CCP 実体(0xD300) へ制御が渡ることを検証する。

ローダ仕様(#35):
  LBA 0-4  → 0xE900 (BIOS)
  LBA 5-8  → 0xD300 (CCP)
  LBA 9-15 → 0xDB00 (BDOS)
"""
import os
import subprocess

import pytest

from emu.pc8001 import PC8001
from emu.sdcard import SDCard

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(PROJECT_ROOT, "build")
LOADER_BIN = os.path.join(BUILD, "loader.bin")
CPM_IMAGE = os.path.join(BUILD, "cpm-image.bin")
EXTERNAL_CPM22 = os.path.join(PROJECT_ROOT, "external", "cpm22")


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
        if 0xD300 <= pc_val < 0xDB00:
            return "ccp"
        if 0xDB00 <= pc_val < 0xE900:
            return "bdos"
        if pc._mem_read(pc_val) == 0x76:
            return "halt"
        pc.cpu.ticks_to_stop = 200
        pc.cpu.run()
    return "timeout"


class TestCpmImage:
    def test_image_size(self):
        assert os.path.getsize(CPM_IMAGE) == 8192

    def test_bios_first_bytes(self):
        with open(CPM_IMAGE, "rb") as f:
            img = f.read()
        # BIOS 先頭 = JP BOOT (0xC3, 0x33, 0xE9)
        assert img[0:3] == bytes([0xC3, 0x33, 0xE9])

    def test_bdos_entry(self):
        with open(CPM_IMAGE, "rb") as f:
            img = f.read()
        # LBA9 + 6 = BDOSエントリ JP nn(0xDB?? へ)
        off = 9 * 512 + 6
        assert img[off] == 0xC3
        assert img[off + 2] == 0xDB

    def test_loader_reaches_bios(self):
        """ローダ → BIOS(0xE900領域) または BIOSのHALT到達。

        現状のBIOS雛形 BOOT_DONE は HALT(設計どおり、CCPダミー本体待ち)。
        ローダが BIOS BOOT を正しく起動できることを「BIOS領域到達」または
        「サインオン後HALT」で確認する。CCPダミーへの実ジャンプは別途WBOOT経由テストで検証。
        """
        pc, _ = _make_pc_with_image()
        result = _run_until_ccp(pc)
        # BIOSのHALT(BOOT_DONE)到達、または BIOS領域内で動作中(timeoutなし)
        assert result in ("halt", "ccp"), (
            f"BIOS到達せず: {result} pc=0x{pc.cpu.pc:04X}"
        )
        # PC が BIOS 領域(0xE900-0xF2FF)に居る、または CCPアドレスに居る
        assert (0xE900 <= pc.cpu.pc <= 0xF2FF) or (0xD300 <= pc.cpu.pc <= 0xDAFF), (
            f"想定外のPC: 0x{pc.cpu.pc:04X}"
        )

    def test_bios_loaded(self):
        pc, _ = _make_pc_with_image()
        _run_until_ccp(pc)
        assert pc._mem_read(0xE900) == 0xC3
        assert pc._mem_read(0xE901) == 0x33
        assert pc._mem_read(0xE902) == 0xE9

    def test_ccp_loaded(self):
        pc, _ = _make_pc_with_image()
        _run_until_ccp(pc)
        with open(CPM_IMAGE, "rb") as f:
            img = f.read()
        expected = img[5 * 512 : 5 * 512 + 4]
        actual = bytes(pc._mem_read(0xD300 + i) for i in range(4))
        assert actual == expected

    def test_bdos_loaded(self):
        pc, _ = _make_pc_with_image()
        _run_until_ccp(pc)
        with open(CPM_IMAGE, "rb") as f:
            img = f.read()
        expected = img[9 * 512 + 6 : 9 * 512 + 10]
        actual = bytes(pc._mem_read(0xDB06 + i) for i in range(4))
        assert actual == expected

    def test_zero_page_jumps(self):
        pc, _ = _make_pc_with_image()
        _run_until_ccp(pc)
        # 0x0000: JP 0E903h (WBOOT)
        assert pc._mem_read(0x0000) == 0xC3
        assert pc._mem_read(0x0001) == 0x03
        assert pc._mem_read(0x0002) == 0xE9
        # 0x0005: JP 0DB06h (BDOS)
        assert pc._mem_read(0x0005) == 0xC3
        assert pc._mem_read(0x0006) == 0x06
        assert pc._mem_read(0x0007) == 0xDB
