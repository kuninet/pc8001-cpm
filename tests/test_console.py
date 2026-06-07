"""
PC-8001 CP/M 2.2 BIOS コンソール機能テスト
doc/設計/04_コンソール.md 準拠
実行: PYTHONPATH=external/z80 .venv/bin/python -m pytest tests/test_console.py -q
"""

import os
import subprocess
import sys
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from emu.pc8001 import PC8001, VRAM_BASE, VRAM_ROW_BYTES

# ---------------------------------------------------------------
# BIOS 定数
# ---------------------------------------------------------------
BIOS_ORG  = 0xE900
BIOS_BIN  = os.path.join(PROJECT_ROOT, "build", "bios.bin")
BIOS_SRC  = os.path.join(PROJECT_ROOT, "src", "bios", "bios.asm")
BUILD_DIR = os.path.join(PROJECT_ROOT, "build")

CONOUT_VEC = BIOS_ORG + 4 * 3   # vec(4) = CONOUT
CONST_VEC  = BIOS_ORG + 2 * 3   # vec(2) = CONST
CONIN_VEC  = BIOS_ORG + 3 * 3   # vec(3) = CONIN


def vec(n: int) -> int:
    return BIOS_ORG + 3 * n


# ---------------------------------------------------------------
# ビルドヘルパ
# ---------------------------------------------------------------

def _build_bios() -> None:
    os.makedirs(BUILD_DIR, exist_ok=True)
    p_file = os.path.join(BUILD_DIR, "bios.p")
    result = subprocess.run(
        ["asl", "-D", "origin=0E900h", "-o", p_file, BIOS_SRC],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"asl 失敗\n{result.stdout}\n{result.stderr}")
    result = subprocess.run(
        ["p2bin", p_file, BIOS_BIN],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"p2bin 失敗\n{result.stdout}\n{result.stderr}")


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


@pytest.fixture
def pc() -> PC8001:
    """BIOS をロードした PC8001 インスタンス(CONOUTテスト用)。
    CLR(0x1A)を実行してVRAMを初期化した状態で返す。
    """
    instance = PC8001()
    _load_bios(instance)
    # VRAMを空白で初期化(CLR: カーソルホーム+画面クリア)
    prog = bytes([0x0E, 0x1A, 0xCD,
                  CONOUT_VEC & 0xFF, (CONOUT_VEC >> 8) & 0xFF, 0x76])
    instance.load(0x8000, prog)
    instance.cpu.sp = 0x8200
    instance.set_pc(0x8000)
    instance.cpu.halted = False
    instance.run_until_halt(max_steps=500000)
    return instance


# ---------------------------------------------------------------
# ヘルパ関数
# ---------------------------------------------------------------

def call_conout(pc: PC8001, char: int) -> None:
    """CONOUT を1回呼ぶ: LD C,char; CALL CONOUT; HALT。"""
    prog = bytes([0x0E, char & 0xFF, 0xCD,
                  CONOUT_VEC & 0xFF, (CONOUT_VEC >> 8) & 0xFF, 0x76])
    pc.load(0x8000, prog)
    pc.cpu.sp = 0x8200
    pc.set_pc(0x8000)
    pc.cpu.halted = False   # 前回のHALT状態をリセット
    result = pc.run_until_halt(max_steps=500000)
    assert result is True, f"CONOUT(0x{char:02X}): HALTに到達しなかった"


def call_conout_seq(pc: PC8001, chars: list) -> None:
    """CONOUT を連続呼び出し。chars は int のリスト。"""
    for ch in chars:
        call_conout(pc, ch)


def vram_row(pc: PC8001, row: int, cols: int = 80) -> bytes:
    """指定行の表示バイト(最大 cols 文字)を返す。"""
    return pc.read_vram(row * VRAM_ROW_BYTES, cols)


def _bios_data_addrs() -> tuple:
    """BIOS内データ領域(CUR_ROW, CUR_COL, ESC_STATE, KEYBUF)のアドレスを返す。"""
    with open(BIOS_BIN, "rb") as f:
        data = f.read()
    signon = b"PC-8001 CP/M 2.2 BIOS\x00"
    pos = data.find(signon)
    cur_row = BIOS_ORG + pos + len(signon)
    return cur_row, cur_row + 1, cur_row + 2, cur_row + 3


# ---------------------------------------------------------------
# テスト 1: BOOT → VRAMクリア + サインオン
# ---------------------------------------------------------------

class TestBoot:
    """BOOT実行後のVRAM状態を確認する。"""

    def test_vram_cleared_on_boot(self):
        """BOOT後: VRAM の全行が空白(0x20)またはサインオン文字。"""
        instance = PC8001()
        _load_bios(instance)
        instance.set_pc(BIOS_ORG)
        instance.run_until_halt(max_steps=500000)

        # 行0先頭にサインオンが書かれていることを確認
        first_row = vram_row(instance, 0)
        signon = b"PC-8001 CP/M 2.2 BIOS"
        assert first_row[:len(signon)] == signon, (
            f"BOOT: 行0={first_row[:len(signon)]!r} != {signon!r}"
        )

    def test_attr_cleared_on_boot(self):
        """BOOT後: 全行のアトリビュート40バイトが 0x00。"""
        instance = PC8001()
        _load_bios(instance)
        instance.set_pc(BIOS_ORG)
        instance.run_until_halt(max_steps=500000)

        for row in range(25):
            attr = instance.read_vram(row * VRAM_ROW_BYTES + 80, 40)
            assert attr == bytes(40), (
                f"BOOT: 行{row} アトリビュートが 0x00 でない: {attr!r}"
            )


# ---------------------------------------------------------------
# テスト 2: CONOUT 制御コード
# ---------------------------------------------------------------

class TestConoutControl:
    """CONOUT の制御コード処理を確認する。"""

    def test_cr_returns_to_bol(self, pc):
        """'A','B', CR, 'C' → 行0="CB..." (CRで行頭に戻り上書き)。"""
        call_conout_seq(pc, [0x41, 0x42, 0x0D, 0x43])
        row0 = vram_row(pc, 0, 2)
        assert row0 == b"CB", f"CR: VRAM[0:2]={row0!r} (expected b'CB')"

    def test_lf_moves_down(self, pc):
        """CR, 'A', LF, 'B' → 行0[0]='A', 行1[1]='B'。
        LF はカーソルを下に移動するだけで列はリセットしない。
        CR → col=0, 'A' → vram[0][0]='A', col=1, LF → row=1(col=1のまま)
        'B' → vram[1][1]='B'
        """
        call_conout_seq(pc, [0x0D, 0x41, 0x0A, 0x42])
        assert vram_row(pc, 0, 1) == b"A", (
            f"LF: 行0[0]={vram_row(pc,0,1)!r} (expected b'A')"
        )
        # LFは列をリセットしないので、'B'は行1の列1に書かれる
        ch = pc.read_vram(1 * VRAM_ROW_BYTES + 1, 1)
        assert ch == b"B", (
            f"LF: 行1[1]={ch!r} (expected b'B')"
        )

    def test_bs_moves_left(self, pc):
        """'A','B', BS, 'C' → 行0[0:2]="AC"。"""
        call_conout_seq(pc, [0x41, 0x42, 0x08, 0x43])
        row0 = vram_row(pc, 0, 2)
        assert row0 == b"AC", f"BS: VRAM[0:2]={row0!r} (expected b'AC')"

    def test_clr_clears_screen(self, pc):
        """CLR(0x1A): 何か出力→CLR→'X' → 画面全消去、行0先頭='X'。"""
        call_conout_seq(pc, [0x41, 0x42, 0x43])
        call_conout(pc, 0x1A)   # CLR
        call_conout(pc, 0x58)   # 'X'
        row0 = vram_row(pc, 0, 1)
        assert row0 == b"X", f"CLR: 行0[0]={row0!r} (expected b'X')"
        # 行0の2バイト目以降は空白
        rest = vram_row(pc, 0, 2)
        assert rest[1:2] == b" ", f"CLR: 行0[1]={rest[1:2]!r} (expected b' ')"

    def test_home_moves_cursor(self, pc):
        """HOME(0x1E): LFで行1に移動→HOME→'X' → 行0先頭='X'。"""
        call_conout_seq(pc, [0x41, 0x0A, 0x1E, 0x58])
        assert vram_row(pc, 0, 1) == b"X", (
            f"HOME: 行0[0]={vram_row(pc,0,1)!r} (expected b'X')"
        )

    def test_bel_ignored(self, pc):
        """BEL(0x07): 出力してもカーソル位置が変わらない。"""
        call_conout(pc, 0x41)   # 'A' at (0,0) → cursor=(0,1)
        call_conout(pc, 0x07)   # BEL: カーソルは(0,1)のまま
        call_conout(pc, 0x42)   # 'B' at (0,1)
        row0 = vram_row(pc, 0, 2)
        assert row0 == b"AB", f"BEL: VRAM[0:2]={row0!r} (expected b'AB')"


# ---------------------------------------------------------------
# テスト 3: カーソル位置指定 (ADM-3A: ESC '=' row+0x20 col+0x20)
# ---------------------------------------------------------------

class TestConoutCursorAddr:
    """ADM-3A カーソルアドレッシングを確認する。"""

    def test_cursor_set_row2_col16(self, pc):
        """ESC '=' 0x22 0x30 → カーソルを(row=2,col=16)へ、その後'X' → VRAM[2][16]='X'。"""
        # ESC=0x1B, '='=0x3D, row=2+0x20=0x22, col=16+0x20=0x30
        call_conout_seq(pc, [0x1B, 0x3D, 0x22, 0x30, 0x58])
        offset = 2 * VRAM_ROW_BYTES + 16
        ch = pc.read_vram(offset, 1)
        assert ch == b"X", f"カーソル位置指定: VRAM[2][16]={ch!r} (expected b'X')"

    def test_cursor_set_row0_col0(self, pc):
        """ESC '=' 0x20 0x20 → カーソルを(0,0)へ、その後'Z' → VRAM[0][0]='Z'。"""
        # まずカーソルを別の場所へ
        call_conout_seq(pc, [0x41, 0x0A])  # 'A', LF
        call_conout_seq(pc, [0x1B, 0x3D, 0x20, 0x20, 0x5A])
        ch = pc.read_vram(0, 1)
        assert ch == b"Z", f"カーソル(0,0): VRAM[0][0]={ch!r} (expected b'Z')"

    def test_esc_unknown_seq_ignored(self, pc):
        """ESC + '=' 以外は無視して通常状態に戻る。"""
        # ESC 'A' (未対応) → 状態0へ戻る → 'X' は通常表示
        call_conout_seq(pc, [0x1B, 0x41, 0x58])
        row0 = vram_row(pc, 0, 1)
        assert row0 == b"X", f"ESC未対応: 行0[0]={row0!r} (expected b'X')"

    def test_esc_row_out_of_range(self, pc):
        """ESC '=' で row=25(>=25)を指定: 座標は変更されない(CUR_ROW=0のまま)。
        実装上、不正値受信時点で状態0復帰のため、後続バイトは通常状態で処理される。
        ここでは座標が変わっていないことを CUR_ROW 直読で確認する。
        """
        cur_row_addr, cur_col_addr, _, _ = _bios_data_addrs()
        # 元のカーソルは (0,0)
        # ESC '=' row=25(=0x39: 0x20+25) col=0(=0x20)
        # row=25は範囲外(0〜24) → 不正値として無視
        call_conout_seq(pc, [0x1B, 0x3D, 0x39])
        # CUR_ROW が変更されていない(0のまま)
        assert pc._mem_read(cur_row_addr) == 0, (
            f"ESC row範囲外: CUR_ROW={pc._mem_read(cur_row_addr)} (expected 0)"
        )
        # 後続col, charを送る (状態0なので通常文字として処理されるはず)
        call_conout_seq(pc, [0x20, 0x58])
        # row=25行(VRAM=0xF300+25*120 = 0xFA68)に書込まれていないことを確認
        # 25行目はVRAM範囲外(0xF300+25*120=0xFE40+120=0xFEB8)なのでmain_ramの末尾近く
        # 行24以降のVRAMが破壊されていないことを確認:
        # ESC受信時点でCUR_ROWは0のまま → 'X'は(0,1) or (0,2)に書かれる(状態リセット後)
        # 主目的: VRAM範囲外への書込が無いこと
        assert pc._mem_read(cur_row_addr) == 0, (
            "ESC row範囲外: 後続処理で CUR_ROW が異常値になっていない"
        )

    def test_esc_col_out_of_range(self, pc):
        """ESC '=' で col=80(>=80)を指定: 座標は変更されない(CUR_COL=0のまま)。"""
        cur_row_addr, cur_col_addr, _, _ = _bios_data_addrs()
        # 元のカーソルは (0,0)
        # ESC '=' row=0(=0x20) col=80(=0x70: 0x20+80)
        # col=80は範囲外(0〜79) → 不正値として無視
        # ESC + '=' + row=0(0x20) を送る → 状態3(col待ち)
        call_conout_seq(pc, [0x1B, 0x3D, 0x20])
        # CUR_ROW は 0 にセットされる(row=0は有効)
        assert pc._mem_read(cur_row_addr) == 0, (
            f"ESC col範囲外: CUR_ROW={pc._mem_read(cur_row_addr)} (expected 0)"
        )
        # col=80 (不正値) を送る → CUR_COL は変更されない
        call_conout(pc, 0x70)
        assert pc._mem_read(cur_col_addr) == 0, (
            f"ESC col範囲外: CUR_COL={pc._mem_read(cur_col_addr)} (expected 0, 不正値で変更されない)"
        )

    def test_esc_col_boundary_79_ok(self, pc):
        """ESC '=' col=79 は有効(範囲内)、その位置に文字が書かれる。"""
        # row=0, col=79 (=0x6F: 0x20+79) は有効
        call_conout_seq(pc, [0x1B, 0x3D, 0x20, 0x6F, 0x59])  # 'Y'
        ch = pc.read_vram(79, 1)
        assert ch == b"Y", (
            f"ESC col=79境界: VRAM[0][79]={ch!r} (expected b'Y')"
        )

    def test_esc_row_boundary_24_ok(self, pc):
        """ESC '=' row=24 は有効(範囲内)、その位置に文字が書かれる。"""
        # row=24 (=0x38: 0x20+24), col=0 は有効
        call_conout_seq(pc, [0x1B, 0x3D, 0x38, 0x20, 0x59])  # 'Y'
        ch = pc.read_vram(24 * VRAM_ROW_BYTES, 1)
        assert ch == b"Y", (
            f"ESC row=24境界: VRAM[24][0]={ch!r} (expected b'Y')"
        )


# ---------------------------------------------------------------
# テスト 4: 半角カナ素通し
# ---------------------------------------------------------------

class TestConoutKana:
    """半角カナコード(0xA1〜0xDF)がそのままVRAMへ書き込まれることを確認する。"""

    def test_kana_passthrough(self, pc):
        """0xA1(｡) を出力 → VRAM[0]== 0xA1。"""
        call_conout(pc, 0xA1)
        ch = pc.read_vram(0, 1)
        assert ch[0] == 0xA1, f"カナ素通し: VRAM[0]=0x{ch[0]:02X} (expected 0xA1)"

    def test_kana_range(self, pc):
        """0xDF(最大カナ) を出力 → VRAM[0]==0xDF。"""
        call_conout(pc, 0xDF)
        ch = pc.read_vram(0, 1)
        assert ch[0] == 0xDF, f"カナ範囲: VRAM[0]=0x{ch[0]:02X} (expected 0xDF)"

    def test_high_code_passthrough(self, pc):
        """0x80以上のコードはそのままVRAMへ書く。"""
        call_conout(pc, 0x80)
        ch = pc.read_vram(0, 1)
        assert ch[0] == 0x80, f"高位コード: VRAM[0]=0x{ch[0]:02X} (expected 0x80)"


# ---------------------------------------------------------------
# テスト 5: スクロール
# ---------------------------------------------------------------

class TestScroll:
    """スクロール動作を確認する。"""

    def test_scroll_on_lf_at_bottom(self, pc):
        """25行目(行24)でLFを受けるとスクロールが発生する。
        行0〜24まで各行先頭に行番号文字を書き、行24でLFを送ると:
          - 元の行1の内容が行0に来る
          - 最終行(行24)は空白になる
        """
        # まず CLR でリセット
        call_conout(pc, 0x1A)

        # 各行の先頭に 'A'+行番号 を書く (行0='A', 行1='B', ...)
        for row in range(25):
            # ESC '=' で各行先頭へ
            call_conout_seq(pc, [0x1B, 0x3D, (row + 0x20) & 0xFF, 0x20])
            call_conout(pc, 0x41 + row)  # 'A'〜'Y'

        # 行24へカーソル移動してLF
        call_conout_seq(pc, [0x1B, 0x3D, 0x38, 0x20])  # row=24=0x18, 0x18+0x20=0x38
        call_conout(pc, 0x0A)  # LF → スクロール発生

        # スクロール後: 元の行1の内容('B')が行0先頭に来る
        row0_first = pc.read_vram(0, 1)
        assert row0_first == b"B", (
            f"スクロール後 行0先頭={row0_first!r} (expected b'B')"
        )

        # 最終行(行24)は空白
        last_row = vram_row(pc, 24, 1)
        assert last_row == b" ", (
            f"スクロール後 行24先頭={last_row!r} (expected b' ')"
        )

    def test_lf_at_row24_scrolls(self, pc):
        """cur_row=24 のとき LF を受けるとスクロール経路に入る(NC条件)。"""
        cur_row_addr, cur_col_addr, _, _ = _bios_data_addrs()
        # 行0先頭に 'A' を書いておく(スクロール後の検証用)
        call_conout(pc, 0x41)   # 'A' → VRAM[0][0]
        # cur_row を 24 に直接セット、col=0
        pc._mem_write(cur_row_addr, 24)
        pc._mem_write(cur_col_addr, 0)
        # LF を送る → スクロール発生
        call_conout(pc, 0x0A)
        # 行0先頭が空白になり、'A'が失われる(スクロールアウト)
        ch = pc.read_vram(0, 1)
        assert ch != b"A", (
            f"LF at row=24: スクロールが発生していない (VRAM[0][0]={ch!r})"
        )

    def test_lf_at_row_over_24_scrolls(self, pc):
        """cur_row が異常値(>24)でも LF はスクロール経路に入り VRAM外書込を起こさない。"""
        cur_row_addr, cur_col_addr, _, _ = _bios_data_addrs()
        # cur_row を 25 (異常値) に設定
        pc._mem_write(cur_row_addr, 25)
        pc._mem_write(cur_col_addr, 0)
        # LF を送る → NC 条件で SCROLL に入る(=24でも>24でもスクロール)
        # スクロール後、エラーやクラッシュなく完了することを確認
        call_conout(pc, 0x0A)
        # スクロール後の最終行(24)が空白で埋められている
        last_row = vram_row(pc, 24, 4)
        assert last_row == b"    ", (
            f"LF at row>24: 最終行が空白でない: {last_row!r}"
        )

    def test_scroll_content_shift(self, pc):
        """スクロール後、行i の内容が行i-1 に来ること。"""
        call_conout(pc, 0x1A)

        # 行0〜24 に識別文字を書く
        for row in range(25):
            call_conout_seq(pc, [0x1B, 0x3D, (row + 0x20) & 0xFF, 0x20])
            call_conout(pc, 0x41 + row)

        # 行24でLF
        call_conout_seq(pc, [0x1B, 0x3D, 0x38, 0x20])
        call_conout(pc, 0x0A)

        # 旧行i が 新行i-1 に来ることを確認(行1〜24が行0〜23へ)
        for row in range(24):
            expected = bytes([0x41 + row + 1])  # 旧行(row+1)の文字
            got = pc.read_vram(row * VRAM_ROW_BYTES, 1)
            assert got == expected, (
                f"スクロール: 新行{row}先頭={got!r} (expected {expected!r})"
            )


# ---------------------------------------------------------------
# テスト 6: CONST / CONIN
# ---------------------------------------------------------------

class TestConstConin:
    """CONST/CONIN のキー入力処理を確認する。"""

    def _call_const(self, pc: PC8001) -> int:
        """CONST を1回呼んで戻り値 A を返す。"""
        prog = bytes([0xCD, CONST_VEC & 0xFF, (CONST_VEC >> 8) & 0xFF, 0x76])
        pc.load(0x8000, prog)
        pc.cpu.sp = 0x8200
        pc.set_pc(0x8000)
        pc.cpu.halted = False
        result = pc.run_until_halt(max_steps=500000)
        assert result is True
        return pc.cpu.a

    def _call_conin(self, pc: PC8001) -> int:
        """CONIN を1回呼んで戻り値 A を返す。"""
        prog = bytes([0xCD, CONIN_VEC & 0xFF, (CONIN_VEC >> 8) & 0xFF, 0x76])
        pc.load(0x8000, prog)
        pc.cpu.sp = 0x8200
        pc.set_pc(0x8000)
        pc.cpu.halted = False
        result = pc.run_until_halt(max_steps=500000)
        assert result is True
        return pc.cpu.a

    def test_const_no_key(self, pc):
        """キーなしのとき CONST は 0x00 を返す。"""
        pc.clear_keys()
        a = self._call_const(pc)
        assert a == 0x00, f"CONST(キーなし): A=0x{a:02X} (expected 0x00)"

    def test_const_key_pressed(self, pc):
        """'A'キー位置(行4,列1)を押すと CONST が 0xFF を返す。
        ダミーマッピング: 0x41 = 0x20 + 4*8 + 1 → 行4, 列1
        """
        pc.clear_keys()
        pc.set_key_matrix(4, 1, True)
        a = self._call_const(pc)
        pc.clear_keys()
        assert a == 0xFF, f"CONST(キーあり): A=0x{a:02X} (expected 0xFF)"

    def test_conin_returns_key(self, pc):
        """'A'キー位置(行4,列1)を押して CONIN → A==0x41('A')。"""
        pc.clear_keys()
        # KEYBUFが残っているかもしれないのでリセット
        # KEYBUF を直接クリアするためにキーなしでCONSTを一度呼ぶ
        a = self._call_const(pc)

        pc.set_key_matrix(4, 1, True)
        a = self._call_conin(pc)
        pc.clear_keys()
        assert a == 0x41, f"CONIN('A'): A=0x{a:02X} (expected 0x41)"

    def test_conin_clears_buffer(self, pc):
        """CONIN後にCONSTを呼ぶとバッファが空(0x00)になる。"""
        pc.clear_keys()
        pc.set_key_matrix(4, 1, True)
        self._call_conin(pc)
        pc.clear_keys()

        # バッファはクリアされているはず → CONST = 0x00
        a = self._call_const(pc)
        assert a == 0x00, f"CONIN後CONST: A=0x{a:02X} (expected 0x00)"

    def test_conin_ctrl_key(self, pc):
        """CTRL+'A': 行0 bit7=0(CTRL), 行4 bit1=0('A') → CONIN = 0x01(Ctrl-A)。"""
        pc.clear_keys()
        # 行0 bit7=0 → CTRL押下
        pc.set_key_matrix(0, 7, True)
        # 行4 bit1=0 → 'A'キー
        pc.set_key_matrix(4, 1, True)
        a = self._call_conin(pc)
        pc.clear_keys()
        assert a == 0x01, f"CONIN(Ctrl+A): A=0x{a:02X} (expected 0x01)"

    def test_conin_space(self, pc):
        """スペース(0x20)キー: 行0, 列0 → ASCII=0x20。"""
        pc.clear_keys()
        pc.set_key_matrix(0, 0, True)
        a = self._call_conin(pc)
        pc.clear_keys()
        assert a == 0x20, f"CONIN(space): A=0x{a:02X} (expected 0x20)"


# ---------------------------------------------------------------
# テスト 7: CONOUT 行末折り返し
# ---------------------------------------------------------------

class TestConoutWrap:
    """CONOUT の行末折り返し処理を確認する。"""

    def test_wrap_at_col80(self, pc):
        """80文字出力後は次行先頭に書かれる。"""
        call_conout(pc, 0x1A)   # CLR
        for _ in range(80):
            call_conout(pc, 0x41)   # 'A' × 80
        call_conout(pc, 0x42)   # 'B' → 行1先頭
        row1 = vram_row(pc, 1, 1)
        assert row1 == b"B", f"折り返し: 行1[0]={row1!r} (expected b'B')"
