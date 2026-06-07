"""
PC-8001 ハードウェア層エミュレータ テスト
実行: PYTHONPATH=external/z80 .venv/bin/python -m pytest tests/test_pc8001.py
"""

import sys
import os
import pytest

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu.pc8001 import PC8001


class TestBankSwitch:
    """バンク切替動作のテスト"""

    def test_write_and_read_bank0(self):
        """e2 bit0=1, bit4=1, e3=0 でバンク0に書き込んで読める"""
        pc = PC8001()
        pc.e2 = 0x11  # bit0=1(RAM可視), bit4=1(書込許可)
        pc.e3 = 0
        pc._mem_write(0x1000, 0xAB)
        assert pc._mem_read(0x1000) == 0xAB

    def test_bank1_is_independent_from_bank0(self):
        """e3=0とe3=1は別データを持つ"""
        pc = PC8001()
        pc.e2 = 0x11
        # バンク0に書く
        pc.e3 = 0
        pc._mem_write(0x1000, 0xAA)
        # バンク1に別データを書く
        pc.e3 = 1
        pc._mem_write(0x1000, 0xBB)
        # バンク0に戻すとAAが見える
        pc.e3 = 0
        assert pc._mem_read(0x1000) == 0xAA
        # バンク1に切り替えるとBBが見える
        pc.e3 = 1
        assert pc._mem_read(0x1000) == 0xBB

    def test_bit0_0_shows_rom(self):
        """e2 bit0=0 にすると ROM 値が読める"""
        # ROMに特定パターンを持つインスタンスを作成
        rom = bytearray(0x8000)
        rom[0x1000] = 0x55
        pc = PC8001(rom=bytes(rom))
        # bit0=0: ROM可視
        pc.e2 = 0x00
        assert pc._mem_read(0x1000) == 0x55

    def test_bit0_1_shows_ram_not_rom(self):
        """e2 bit0=1 にすると RAM が見えROMは見えない"""
        rom = bytearray(0x8000)
        rom[0x1000] = 0x55
        pc = PC8001(rom=bytes(rom))
        pc.e2 = 0x11
        pc.e3 = 0
        pc._mem_write(0x1000, 0x99)
        # RAM可視時はRAMの値が返る
        assert pc._mem_read(0x1000) == 0x99


class TestWriteEnableIndependence:
    """書込許可(bit4)と読出選択(bit0)の独立性テスト"""

    def test_write_to_ram_while_rom_visible(self):
        """
        bit0=0(ROM可視)・bit4=1 で書き込み後、
        bit0=1 にすると書いた値が読める(ROM可視中にRAM書込)
        """
        pc = PC8001()
        # bit0=0(ROM可視), bit4=1(RAM書込許可)
        pc.e2 = 0x10
        pc.e3 = 0
        pc._mem_write(0x0000, 0x42)
        # まだbit0=0なのでROM(0xFF)が見える
        assert pc._mem_read(0x0000) == 0xFF
        # bit0=1にするとRAMが見え、書いた値が読める
        pc.e2 = 0x11
        assert pc._mem_read(0x0000) == 0x42

    def test_no_write_when_bit4_clear(self):
        """bit4=0(書込禁止)の場合、書き込みは無視される"""
        pc = PC8001()
        # まずRAMに値を書いておく
        pc.e2 = 0x10  # bit4=1
        pc._mem_write(0x2000, 0xCC)
        # bit4=0(書込禁止)にして別の値を書こうとする
        pc.e2 = 0x01  # bit0=1, bit4=0
        pc._mem_write(0x2000, 0xDD)
        # 元の値が保持されているはず
        assert pc._mem_read(0x2000) == 0xCC


class TestMainRam:
    """上位RAM(0x8000-0xFFFF)のテスト"""

    def test_write_and_read_main_ram(self):
        """0x9000 に書いて読める"""
        pc = PC8001()
        pc._mem_write(0x9000, 0x7E)
        assert pc._mem_read(0x9000) == 0x7E

    def test_upper_boundary(self):
        """0xFFFF に書いて読める"""
        pc = PC8001()
        pc._mem_write(0xFFFF, 0x12)
        assert pc._mem_read(0xFFFF) == 0x12

    def test_lower_boundary_of_main_ram(self):
        """0x8000 に書いて読める"""
        pc = PC8001()
        pc._mem_write(0x8000, 0x34)
        assert pc._mem_read(0x8000) == 0x34


class TestVram:
    """VRAM・screen_text のテスト"""

    def test_write_and_read_vram(self):
        """VRAM(0xF300)に書いた値が read_vram で読める"""
        pc = PC8001()
        pc._mem_write(0xF300, ord('A'))
        pc._mem_write(0xF301, ord('B'))
        data = pc.read_vram(0, 2)
        assert data[0] == ord('A')
        assert data[1] == ord('B')

    def test_screen_text_first_line(self):
        """VRAM に 'A','B' を書くと screen_text の1行目先頭が 'AB' で始まる"""
        pc = PC8001()
        pc._mem_write(0xF300, ord('A'))
        pc._mem_write(0xF301, ord('B'))
        text = pc.screen_text()
        first_line = text.split('\n')[0]
        assert first_line.startswith('AB')

    def test_screen_text_non_printable_becomes_dot(self):
        """表示範囲外バイトは '.' になる"""
        pc = PC8001()
        pc._mem_write(0xF300, 0x01)  # 非表示文字
        text = pc.screen_text()
        first_line = text.split('\n')[0]
        assert first_line[0] == '.'

    def test_screen_text_row_count(self):
        """screen_text のデフォルト行数は25行"""
        pc = PC8001()
        text = pc.screen_text()
        assert len(text.split('\n')) == 25


class TestIoPorts:
    """I/Oポート(E2/E3等)のテスト"""

    def test_e2_updated_by_out(self):
        """OUT 0xE2 で e2 が更新される"""
        pc = PC8001()
        pc._io_out(0xE2, 0x11)
        assert pc.e2 == 0x11

    def test_e3_updated_by_out(self):
        """OUT 0xE3 で e3 が更新される(下位2bitのみ)"""
        pc = PC8001()
        pc._io_out(0xE3, 0x03)
        assert pc.e3 == 0x03
        # 上位ビットはマスクされる
        pc._io_out(0xE3, 0xFF)
        assert pc.e3 == 0x03

    def test_e3_mask_upper_bits(self):
        """e3 は bit1-0 のみ有効(0-3)"""
        pc = PC8001()
        pc._io_out(0xE3, 0x05)  # 0b00000101 → 下位2bit = 0b01 = 1
        assert pc.e3 == 1

    def test_port40_recorded(self):
        """OUT 0x40 が記録される"""
        pc = PC8001()
        pc._io_out(0x40, 0xAA)
        assert pc.port40 == 0xAA

    def test_crtc_param_appended(self):
        """OUT 0x50 が crtc_param に追記される"""
        pc = PC8001()
        pc._io_out(0x50, 0x01)
        pc._io_out(0x50, 0x02)
        assert pc.crtc_param == [0x01, 0x02]

    def test_crtc_status_returns_0(self):
        """IN 0x50 は 0x00(スタブ)を返す"""
        pc = PC8001()
        assert pc._io_in(0x50) == 0x00

    def test_unknown_port_in_returns_ff(self):
        """未定義ポートの IN は 0xFF を返す"""
        pc = PC8001()
        assert pc._io_in(0x20) == 0xFF

    def test_e2_e3_via_cpu_out(self):
        """
        Z80プログラムで OUT命令を実行してe2/e3が更新されることを確認。
        OUT (n),A: LD A,0x11; OUT (0xE2),A; HALT
        """
        pc = PC8001()
        # 上位RAM(0x8000)にプログラムを配置
        # LD A, 0x11 = 3E 11
        # OUT (0xE2), A = D3 E2
        # HALT = 76
        prog = bytes([0x3E, 0x11, 0xD3, 0xE2, 0x76])
        pc.load(0x8000, prog)
        pc.set_pc(0x8000)
        result = pc.run_until_halt()
        assert result is True
        assert pc.e2 == 0x11


class TestKeyboard:
    """キーボードマトリクスのテスト"""

    def test_initial_state_all_unpressed(self):
        """初期状態では全行0xFF(未押下)"""
        pc = PC8001()
        for row in range(10):
            assert pc._io_in(row) == 0xFF

    def test_set_key_clears_bit(self):
        """set_key_matrix でキー押下するとビットがクリアされる(アクティブロー)"""
        pc = PC8001()
        pc.set_key_matrix(row=0, col=0, pressed=True)
        assert pc._io_in(0x00) == 0xFE  # bit0がクリア

    def test_set_key_different_cols(self):
        """複数列を押下するとそれぞれのビットがクリアされる"""
        pc = PC8001()
        pc.set_key_matrix(row=1, col=3, pressed=True)
        pc.set_key_matrix(row=1, col=5, pressed=True)
        val = pc._io_in(0x01)
        # bit3とbit5がクリア
        assert (val & (1 << 3)) == 0
        assert (val & (1 << 5)) == 0
        # 他のビットは1のまま
        assert (val & (1 << 0)) != 0

    def test_release_key_sets_bit(self):
        """キー解放するとビットが1に戻る"""
        pc = PC8001()
        pc.set_key_matrix(row=2, col=4, pressed=True)
        pc.set_key_matrix(row=2, col=4, pressed=False)
        assert pc._io_in(0x02) == 0xFF

    def test_clear_keys_resets_all(self):
        """clear_keys() で全行が0xFFに戻る"""
        pc = PC8001()
        pc.set_key_matrix(row=0, col=0, pressed=True)
        pc.set_key_matrix(row=5, col=7, pressed=True)
        pc.clear_keys()
        for row in range(10):
            assert pc._io_in(row) == 0xFF

    def test_keyboard_row9_accessible(self):
        """行9(最終行)にもアクセスできる"""
        pc = PC8001()
        pc.set_key_matrix(row=9, col=7, pressed=True)
        assert pc._io_in(0x09) == 0x7F  # bit7がクリア


class TestCpuExecution:
    """実CPU実行のテスト"""

    def test_ld_a_store_to_vram_halt(self):
        """
        Z80プログラムを実行してVRAMに値が書き込まれることを確認。
        LD A, 0x41   = 3E 41
        LD (0xF300), A = 32 00 F3
        HALT         = 76
        """
        pc = PC8001()
        prog = bytes([
            0x3E, 0x41,        # LD A, 0x41 ('A')
            0x32, 0x00, 0xF3,  # LD (0xF300), A
            0x76               # HALT
        ])
        pc.load(0x8000, prog)
        pc.set_pc(0x8000)
        result = pc.run_until_halt()
        assert result is True
        assert pc.read_vram(0, 1)[0] == 0x41

    def test_cpu_bank_switch_via_out(self):
        """
        Z80プログラムでバンク切替を行い、バンクRAMへ書込/読出を確認。
        OUT (0xE2), A でe2=0x11(RAM可視+書込許可)に設定してから
        バンクRAMに書き込む。
        """
        pc = PC8001()
        # e2=0x11, e3=0 に設定してから 0x0100 に 0x99 を書くプログラム
        # LD A, 0x11; OUT (0xE2), A   -- e2=0x11
        # LD A, 0x00; OUT (0xE3), A   -- e3=0
        # LD A, 0x99; LD (0x0100), A  -- バンクRAMに書込
        # HALT
        prog = bytes([
            0x3E, 0x11,        # LD A, 0x11
            0xD3, 0xE2,        # OUT (0xE2), A
            0x3E, 0x00,        # LD A, 0x00
            0xD3, 0xE3,        # OUT (0xE3), A
            0x3E, 0x99,        # LD A, 0x99
            0x32, 0x00, 0x01,  # LD (0x0100), A
            0x76               # HALT
        ])
        pc.load(0x8000, prog)
        pc.set_pc(0x8000)
        result = pc.run_until_halt()
        assert result is True
        # e2=0x11なのでRAMが見える
        assert pc._mem_read(0x0100) == 0x99

    def test_run_until_halt_returns_false_on_timeout(self):
        """無限ループはmax_steps超過でFalseを返す"""
        pc = PC8001()
        # JR -2(無限ループ): 18 FE
        prog = bytes([0x18, 0xFE])
        pc.load(0x8000, prog)
        pc.set_pc(0x8000)
        result = pc.run_until_halt(max_steps=10)
        assert result is False

    def test_load_with_bank_parameter(self):
        """load(addr, data, bank=N) で指定バンクに書き込まれる"""
        pc = PC8001()
        pc.load(0x2000, bytes([0xDE, 0xAD]), bank=2)
        assert pc.bank_ram[2][0x2000] == 0xDE
        assert pc.bank_ram[2][0x2001] == 0xAD
        # 他のバンクには影響しない
        assert pc.bank_ram[0][0x2000] == 0x00
