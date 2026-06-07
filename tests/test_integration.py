"""エンドツーエンド統合テスト(#39)。

sd_tool で実用的なブート用SDイメージを作り、エミュレータ + SDモデルに渡して
ローダ(0x6000)からの起動が BIOS到達まで通ることを検証する。
"""
import os
import subprocess

import pytest

from emu.pc8001 import PC8001
from emu.sdcard import SDCard

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(PROJECT_ROOT, "build")
SCRIPTS = os.path.join(PROJECT_ROOT, "scripts")
LOADER_BIN = os.path.join(BUILD, "loader.bin")
BIOS_BIN = os.path.join(BUILD, "bios.bin")
CCP_BIN = os.path.join(BUILD, "ccp.bin")
BDOS_BIN = os.path.join(BUILD, "bdos.bin")
PY = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")
EXTERNAL_CPM22 = os.path.join(PROJECT_ROOT, "external", "cpm22")


def _build_all() -> None:
    if not os.path.isdir(EXTERNAL_CPM22):
        pytest.skip("external/cpm22 が未取得")
    for t in ("bios", "cpm", "loader"):
        r = subprocess.run(["make", t], cwd=PROJECT_ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            pytest.fail(f"make {t} 失敗: {r.stderr or r.stdout}")


def _run_sd_tool(*args: str) -> None:
    r = subprocess.run(
        [PY, os.path.join(SCRIPTS, "sd_tool.py"), *args],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    if r.returncode != 0:
        pytest.fail(f"sd_tool {args}: {r.stderr or r.stdout}")


@pytest.fixture(scope="module", autouse=True)
def build_once():
    _build_all()


def _make_bootable_image(tmp_path) -> str:
    img = str(tmp_path / "boot.img")
    _run_sd_tool("mkimage", "--out", img)
    _run_sd_tool("sys", "--image", img, "--bios", BIOS_BIN, "--ccp", CCP_BIN, "--bdos", BDOS_BIN)
    hello = tmp_path / "HELLO.COM"
    hello.write_bytes(b"\x3E\x41\x76")  # LD A,'A'; HALT
    _run_sd_tool("put", "--image", img, "--drive", "A", "--file", str(hello))
    return img


def _boot(image_path: str):
    with open(LOADER_BIN, "rb") as f:
        loader = f.read()
    with open(image_path, "rb") as f:
        sd_data = f.read()
    pc = PC8001()
    pc.rom[0x6000 : 0x6000 + len(loader)] = loader
    sd = SDCard(image=bytearray(sd_data))
    pc.attach_sd(sd)
    pc.set_pc(0x6000)
    pc.cpu.halted = False
    return pc, sd


def _run(pc, max_steps: int = 6_000_000) -> str:
    for _ in range(max_steps):
        if pc.cpu.halted:
            return "halt"
        pcv = pc.cpu.pc
        if 0xD300 <= pcv < 0xDB00:
            return "ccp"
        if pc._mem_read(pcv) == 0x76:
            return "halt"
        pc.cpu.ticks_to_stop = 200
        pc.cpu.run()
    return "timeout"


class TestEndToEnd:
    def test_bootable_image_size(self, tmp_path):
        img = _make_bootable_image(tmp_path)
        assert os.path.getsize(img) == 33024 * 512

    def test_loader_boots_to_bios(self, tmp_path):
        img = _make_bootable_image(tmp_path)
        pc, _ = _boot(img)
        result = _run(pc)
        assert result in ("halt", "ccp"), f"BIOS到達せず: {result} pc=0x{pc.cpu.pc:04X}"
        assert (0xE900 <= pc.cpu.pc <= 0xF2FF) or (0xD300 <= pc.cpu.pc <= 0xDAFF)

    def test_bios_at_e900(self, tmp_path):
        img = _make_bootable_image(tmp_path)
        pc, _ = _boot(img)
        _run(pc)
        assert pc._mem_read(0xE900) == 0xC3
        assert pc._mem_read(0xE901) == 0x33
        assert pc._mem_read(0xE902) == 0xE9

    def test_ccp_at_d300(self, tmp_path):
        img = _make_bootable_image(tmp_path)
        pc, _ = _boot(img)
        _run(pc)
        with open(CCP_BIN, "rb") as f:
            ccp = f.read()
        actual = bytes(pc._mem_read(0xD300 + i) for i in range(4))
        assert actual == ccp[:4]

    def test_bdos_at_db00(self, tmp_path):
        img = _make_bootable_image(tmp_path)
        pc, _ = _boot(img)
        _run(pc)
        with open(BDOS_BIN, "rb") as f:
            bdos = f.read()
        actual = bytes(pc._mem_read(0xDB00 + i) for i in range(8))
        assert actual == bdos[:8]

    def test_zero_page_jumps(self, tmp_path):
        img = _make_bootable_image(tmp_path)
        pc, _ = _boot(img)
        _run(pc)
        assert pc._mem_read(0x0000) == 0xC3
        assert pc._mem_read(0x0001) == 0x03
        assert pc._mem_read(0x0002) == 0xE9
        assert pc._mem_read(0x0005) == 0xC3
        assert pc._mem_read(0x0006) == 0x06
        assert pc._mem_read(0x0007) == 0xDB

    def test_e2_after_boot(self, tmp_path):
        img = _make_bootable_image(tmp_path)
        pc, _ = _boot(img)
        _run(pc)
        assert (pc.e2 & 0x11) == 0x11

    def test_hello_com_in_dir(self, tmp_path):
        img = _make_bootable_image(tmp_path)
        with open(img, "rb") as f:
            data = f.read()
        dir_off = 32 * 512
        found = False
        for i in range(0, 16384, 32):
            entry = data[dir_off + i : dir_off + i + 32]
            if entry[0] == 0xE5:
                continue
            name = bytes(b & 0x7F for b in entry[1:9]).rstrip().decode("ascii", "replace")
            ext = bytes(b & 0x7F for b in entry[9:12]).rstrip().decode("ascii", "replace")
            if name == "HELLO" and ext == "COM":
                found = True
                break
        assert found, "HELLO.COM 未検出"
