"""
PC-8001 CP/M 2.2 BIOS 雛形 テスト
実行: PYTHONPATH=external/z80 .venv/bin/python -m pytest tests/test_bios.py -q
"""

import os
import subprocess
import sys
import pytest

# プロジェクトルートをパスに追加
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from emu.pc8001 import PC8001

# ---------------------------------------------------------------
# BIOS 定数
# ---------------------------------------------------------------
BIOS_ORG = 0xE900
BIOS_BIN  = os.path.join(PROJECT_ROOT, "build", "bios.bin")
BIOS_SRC  = os.path.join(PROJECT_ROOT, "src", "bios", "bios.asm")
BUILD_DIR = os.path.join(PROJECT_ROOT, "build")

# ベクタアドレス: vec(n) = BIOS_ORG + 3*n
def vec(n: int) -> int:
    return BIOS_ORG + 3 * n


# ---------------------------------------------------------------
# ビルドヘルパ (module スコープで1回だけ実行)
# ---------------------------------------------------------------

def _build_bios() -> None:
    """BIOS をアセンブル・バイナリ化する。失敗時は pytest.fail。"""
    os.makedirs(BUILD_DIR, exist_ok=True)

    p_file = os.path.join(BUILD_DIR, "bios.p")
    bin_file = BIOS_BIN

    # アセンブル
    result = subprocess.run(
        ["asl", "-D", "origin=0E900h", "-o", p_file, BIOS_SRC],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"asl アセンブル失敗\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    # バイナリ変換
    result = subprocess.run(
        ["p2bin", p_file, bin_file],
        capture_output=True,
        text=True,
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


@pytest.fixture
def pc_with_bios() -> PC8001:
    """BIOS をロードした PC8001 インスタンスを返す。"""
    pc = PC8001()
    _load_bios(pc)
    return pc


# ---------------------------------------------------------------
# テスト 1: ジャンプテーブル検証
# ---------------------------------------------------------------

class TestJumpTable:
    """ジャンプテーブルの各エントリが JP 命令(0xC3)であることを確認する。"""

    def test_all_entries_are_jp(self, pc_with_bios):
        """vec(0)〜vec(16) の先頭バイトがすべて 0xC3(JP)。"""
        pc = pc_with_bios
        for n in range(17):
            addr = vec(n)
            opcode = pc._mem_read(addr)
            assert opcode == 0xC3, (
                f"vec({n})=0x{addr:04X}: 先頭バイトが 0x{opcode:02X} (expected 0xC3)"
            )

    def test_jp_targets_in_bios_range(self, pc_with_bios):
        """各 JP のオペランド(続く2バイト, LE)が BIOS範囲内の妥当アドレスを指す。"""
        pc = pc_with_bios
        # 本体コードはジャンプテーブル直後(0xE933)〜上端 0xF2FF の範囲に収まる。
        code_start = BIOS_ORG + 17 * 3   # 0xE933
        code_end = 0xF2FF
        for n in range(17):
            addr = vec(n)
            lo = pc._mem_read(addr + 1)
            hi = pc._mem_read(addr + 2)
            target = lo | (hi << 8)
            assert code_start <= target <= code_end, (
                f"vec({n})=0x{addr:04X}: JP 先 0x{target:04X} が BIOS範囲外 "
                f"(0x{code_start:04X}〜0x{code_end:04X})"
            )

    def test_jp_targets_match_build(self, pc_with_bios):
        """順序入れ替え検出: ビルド結果(build/bios.bin)の実アドレスと一致。

        BOOT/WBOOT は設計上の固定値も併せて確認する。
        """
        pc = pc_with_bios
        with open(BIOS_BIN, "rb") as f:
            data = f.read()
        # ビルド結果から各 vec の JP 先を読む
        expected = {}
        for n in range(17):
            off = n * 3
            expected[n] = data[off + 1] | (data[off + 2] << 8)
        # 設計上の固定値(順序ロック)
        assert expected[0] == 0xE933, f"BOOT(vec0) JP 先 0x{expected[0]:04X} != 0xE933"
        assert expected[1] == 0xE945, f"WBOOT(vec1) JP 先 0x{expected[1]:04X} != 0xE945"
        # ロード済みメモリの値とビルド結果が一致すること
        for n in range(17):
            addr = vec(n)
            lo = pc._mem_read(addr + 1)
            hi = pc._mem_read(addr + 2)
            target = lo | (hi << 8)
            assert target == expected[n], (
                f"vec({n}): メモリ上 0x{target:04X} != ビルド結果 0x{expected[n]:04X}"
            )


# ---------------------------------------------------------------
# テスト 2: SECTRAN 恒等変換
# ---------------------------------------------------------------

class TestSectran:
    """SECTRAN(vec16=0xE930): BC をそのまま HL に返す。"""

    def test_identity_conversion(self, pc_with_bios):
        """LD BC,0x1234 → CALL SECTRAN → HL==0x1234。"""
        pc = pc_with_bios
        # テストスタブをバンク0 RAM (0x8000) に配置
        # 01 34 12  LD BC, 0x1234
        # CD 30 E9  CALL 0xE930  (vec(16) = SECTRAN)
        # 76        HALT
        prog = bytes([0x01, 0x34, 0x12, 0xCD, 0x30, 0xE9, 0x76])
        pc.load(0x8000, prog)
        pc.load(0x8100, bytes([0x00] * 0x10))  # スタック領域を確保
        pc.cpu.sp = 0x8110
        pc.set_pc(0x8000)
        result = pc.run_until_halt()
        assert result is True, "HALT に到達しなかった"
        assert pc.cpu.hl == 0x1234, (
            f"SECTRAN: HL=0x{pc.cpu.hl:04X} (expected 0x1234)"
        )


# ---------------------------------------------------------------
# テスト 3: LISTST = 0xFF
# ---------------------------------------------------------------

class TestListst:
    """LISTST(vec15=0xE92D): A=0xFF を返す。"""

    def test_returns_0xff(self, pc_with_bios):
        """CALL LISTST → A==0xFF。"""
        pc = pc_with_bios
        # CD 2D E9  CALL 0xE92D  (vec(15) = LISTST)
        # 76        HALT
        prog = bytes([0xCD, 0x2D, 0xE9, 0x76])
        pc.load(0x8000, prog)
        pc.cpu.sp = 0x8110
        pc.set_pc(0x8000)
        result = pc.run_until_halt()
        assert result is True, "HALT に到達しなかった"
        assert pc.cpu.a == 0xFF, (
            f"LISTST: A=0x{pc.cpu.a:02X} (expected 0xFF)"
        )


# ---------------------------------------------------------------
# テスト 4: CONST = 0x00
# ---------------------------------------------------------------

class TestConst:
    """CONST(vec2=0xE906): A=0 を返す。"""

    def test_returns_0(self, pc_with_bios):
        """CALL CONST → A==0。"""
        pc = pc_with_bios
        # CD 06 E9  CALL 0xE906  (vec(2) = CONST)
        # 76        HALT
        prog = bytes([0xCD, 0x06, 0xE9, 0x76])
        pc.load(0x8000, prog)
        pc.cpu.sp = 0x8110
        pc.set_pc(0x8000)
        result = pc.run_until_halt()
        assert result is True, "HALT に到達しなかった"
        assert pc.cpu.a == 0, (
            f"CONST: A=0x{pc.cpu.a:02X} (expected 0x00)"
        )


# ---------------------------------------------------------------
# テスト 5: CONOUT が VRAM に文字を書く
# ---------------------------------------------------------------

class TestConout:
    """CONOUT(vec4=0xE90C): C の文字を VRAM カーソル位置へ書く。"""

    def test_single_char_a(self, pc_with_bios):
        """LD C,'A' → CALL CONOUT → VRAM[0]=='A'(0x41)。"""
        pc = pc_with_bios
        # 0E 41     LD C, 0x41 ('A')
        # CD 0C E9  CALL 0xE90C  (vec(4) = CONOUT)
        # 76        HALT
        prog = bytes([0x0E, 0x41, 0xCD, 0x0C, 0xE9, 0x76])
        pc.load(0x8000, prog)
        pc.cpu.sp = 0x8110
        pc.set_pc(0x8000)
        result = pc.run_until_halt()
        assert result is True, "HALT に到達しなかった"
        assert pc.read_vram(0, 1)[0] == 0x41, (
            f"CONOUT: VRAM[0]=0x{pc.read_vram(0,1)[0]:02X} (expected 0x41='A')"
        )

    def test_two_chars_ab(self, pc_with_bios):
        """'A'→'B'の順に出力すると VRAM[0:2]==b'AB'。"""
        pc = pc_with_bios
        # 0E 41     LD C, 'A'
        # CD 0C E9  CALL CONOUT
        # 0E 42     LD C, 'B'
        # CD 0C E9  CALL CONOUT
        # 76        HALT
        prog = bytes([
            0x0E, 0x41, 0xCD, 0x0C, 0xE9,
            0x0E, 0x42, 0xCD, 0x0C, 0xE9,
            0x76,
        ])
        pc.load(0x8000, prog)
        pc.cpu.sp = 0x8110
        pc.set_pc(0x8000)
        result = pc.run_until_halt()
        assert result is True, "HALT に到達しなかった"
        assert pc.read_vram(0, 2) == b'AB', (
            f"CONOUT: VRAM[0:2]={pc.read_vram(0,2)!r} (expected b'AB')"
        )

    def test_preserves_registers(self, pc_with_bios):
        """CONOUT 呼出し前後で BC/DE/HL が不変(特に BC は CP/M 規約)。"""
        pc = pc_with_bios
        # 01 42 00  LD BC, 0x0042  (C='B', B=0x00)
        # 11 34 12  LD DE, 0x1234
        # 21 78 56  LD HL, 0x5678
        # CD 0C E9  CALL 0xE90C  (vec(4) = CONOUT)
        # 76        HALT
        prog = bytes([
            0x01, 0x42, 0x00,
            0x11, 0x34, 0x12,
            0x21, 0x78, 0x56,
            0xCD, 0x0C, 0xE9,
            0x76,
        ])
        pc.load(0x8000, prog)
        pc.cpu.sp = 0x8200
        pc.set_pc(0x8000)
        result = pc.run_until_halt()
        assert result is True, "HALT に到達しなかった"
        assert pc.cpu.bc == 0x0042, f"CONOUT: BC=0x{pc.cpu.bc:04X} (expected 0x0042)"
        assert pc.cpu.de == 0x1234, f"CONOUT: DE=0x{pc.cpu.de:04X} (expected 0x1234)"
        assert pc.cpu.hl == 0x5678, f"CONOUT: HL=0x{pc.cpu.hl:04X} (expected 0x5678)"
        assert pc.read_vram(0, 1)[0] == 0x42, (
            f"CONOUT: VRAM[0]=0x{pc.read_vram(0,1)[0]:02X} (expected 0x42='B')"
        )


# ---------------------------------------------------------------
# テスト: ダミー実装ルーチン (CONIN/READER/READ/WRITE/SELDSK)
# ---------------------------------------------------------------

class TestDummyRoutines:
    """雛形のダミー実装ルーチンの戻り値を確認する。"""

    def _call_and_halt(self, pc: PC8001, addr: int, prefix: bytes = b"") -> None:
        """prefix(任意の前処理) + CALL addr + HALT を 0x8000 で実行する。"""
        lo = addr & 0xFF
        hi = (addr >> 8) & 0xFF
        prog = prefix + bytes([0xCD, lo, hi, 0x76])
        pc.load(0x8000, prog)
        pc.cpu.sp = 0x8200
        pc.set_pc(0x8000)
        result = pc.run_until_halt()
        assert result is True, "HALT に到達しなかった"

    def test_conin_with_key(self, pc_with_bios):
        """CONIN(vec3=0xE909): キー押下済みなら A==押下文字。
        ダミーマッピング: ASCII 0x41('A')は行2列1 → 行0x41=0x20+2*8+1=0x31? ... 確認。
        0x20 + row*8 + col = 0x41 → row*8+col = 0x21=33 → row=4,col=1
        """
        pc = pc_with_bios
        # 0x41 = 0x20 + 4*8 + 1: 行4, 列1 にキーを押す
        pc.set_key_matrix(4, 1, True)
        self._call_and_halt(pc, vec(3))
        pc.clear_keys()
        assert pc.cpu.a == 0x41, f"CONIN: A=0x{pc.cpu.a:02X} (expected 0x41='A')"

    def test_reader_returns_eof(self, pc_with_bios):
        """READER(vec7=0xE915): A==0x1A(EOF)。"""
        pc = pc_with_bios
        self._call_and_halt(pc, vec(7))
        assert pc.cpu.a == 0x1A, f"READER: A=0x{pc.cpu.a:02X} (expected 0x1A)"

    def test_read_returns_error(self, pc_with_bios):
        """READ(vec13=0xE927): A==1(エラー)。"""
        pc = pc_with_bios
        self._call_and_halt(pc, vec(13))
        assert pc.cpu.a == 1, f"READ: A=0x{pc.cpu.a:02X} (expected 0x01)"

    def test_write_returns_error(self, pc_with_bios):
        """WRITE(vec14=0xE92A): A==1(エラー)。"""
        pc = pc_with_bios
        self._call_and_halt(pc, vec(14))
        assert pc.cpu.a == 1, f"WRITE: A=0x{pc.cpu.a:02X} (expected 0x01)"

    def test_seldsk_valid_drive(self, pc_with_bios):
        """SELDSK(vec9=0xE91B): C=0 で HL != 0 (DPH アドレスを返す)。"""
        pc = pc_with_bios
        # 0E 00  LD C, 0  → CALL SELDSK
        self._call_and_halt(pc, vec(9), prefix=bytes([0x0E, 0x00]))
        assert pc.cpu.hl != 0, f"SELDSK: HL=0x{pc.cpu.hl:04X} (expected non-zero DPH addr)"

    def test_seldsk_invalid_drive(self, pc_with_bios):
        """SELDSK(vec9=0xE91B): C=8(範囲外) で HL==0(無効)。"""
        pc = pc_with_bios
        # 0E 08  LD C, 8  → CALL SELDSK
        self._call_and_halt(pc, vec(9), prefix=bytes([0x0E, 0x08]))
        assert pc.cpu.hl == 0, f"SELDSK: HL=0x{pc.cpu.hl:04X} (expected 0x0000)"


# ---------------------------------------------------------------
# テスト 6: BOOT がサインオン文字列を画面に出力する
# ---------------------------------------------------------------

class TestBoot:
    """BOOT(vec0=0xE900): 実行後 screen_text の1行目が 'PC-8001 CP/M 2.2 BIOS' で始まる。"""

    def test_signon_message(self):
        """pc=0xE900 で run_until_halt → 1行目が 'PC-8001 CP/M 2.2 BIOS' で始まる。"""
        pc = PC8001()
        _load_bios(pc)
        # BOOT はスタックを DF00h に設定するので SP 設定は不要
        pc.set_pc(BIOS_ORG)
        result = pc.run_until_halt(max_steps=200000)
        assert result is True, "HALT に到達しなかった"
        first_line = pc.screen_text().split('\n')[0]
        expected = "PC-8001 CP/M 2.2 BIOS"
        assert first_line.startswith(expected), (
            f"BOOT: 1行目={first_line!r} が {expected!r} で始まっていない"
        )
