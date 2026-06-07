"""SD作成ツール(scripts/sd_tool.py)のテスト(#38)。

テスト一覧:
  1. mkimage: サイズ確認、各ドライブのディレクトリ領域が 0xE5 で埋まっている
  2. sys: ドライブAのOFF領域にBIOS/CCP/BDOSが正しく配置されている
  3. put小ファイル(128B): dir で1件表示, get でラウンドトリップ一致
  4. put大ファイル(20KB → 2エクステント): get で完全一致, dir は1ファイルとして集約
  5. put 512KB超ファイル(>32エクステント, S2桁上げ): get でラウンドトリップ一致
  6. format: ディレクトリ16KBが0xE5に戻る, OFFは保持
  7. ファイル名正規化: normalize_name の各ケース確認
  8. エクステントヘルパ: extent_no / extent_to_fields の往復確認
"""

import io
import os
import struct
import tempfile

import pytest

# テスト対象モジュール(sys.path 調整なし: PYTHONPATH=external/z80 で実行)
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from sd_tool import (
    # 定数
    SD_BLOCK, CPM_BLOCK_SIZE, DRIVE_BLOCKS, NUM_DRIVES,
    OFF_SECTORS, DIR_CPM_BLOCKS, DIR_SIZE, DIR_ENTRY_SIZE, DIR_ENTRIES,
    EMPTY_ENTRY, DSM, EXTENT_BLOCKS, EXTENT_SIZE, EXTENT_RECORDS,
    SYS_LAYOUT, SYS_TOTAL_BLOCKS,
    # ヘルパ関数
    extent_no, extent_to_fields,
    normalize_name, parse_name_arg, parse_drive,
    drive_base_offset, dir_area_offset, cpm_block_offset,
    load_image, save_image,
    read_dir_entry, write_dir_entry, parse_dir_entry, build_dir_entry,
    collect_used_blocks, alloc_blocks, find_free_dir_entry,
    delete_file,
    # サブコマンド
    cmd_mkimage, cmd_sys, cmd_put, cmd_dir, cmd_get, cmd_format,
)


# -------------------------------------------------------------------------
# ヘルパ
# -------------------------------------------------------------------------

def _make_temp_image(tmp_path, size=None):
    """テスト用の空 SD イメージファイルを tmp_path に作成して返す。
    size 未指定時は cmd_mkimage で作成。
    """
    img_path = str(tmp_path / 'test.img')
    cmd_mkimage(img_path)
    return img_path


def _make_fake_bin(size: int, pattern: bytes = b'\xAB') -> bytes:
    """テスト用のダミーバイナリを生成する。"""
    full, rem = divmod(size, len(pattern))
    return pattern * full + pattern[:rem]


# -------------------------------------------------------------------------
# 1. mkimage テスト
# -------------------------------------------------------------------------

class TestMkimage:
    def test_image_size(self, tmp_path):
        """作成されたイメージのサイズが 33024 × 512B = 16,908,288B であること。"""
        img_path = str(tmp_path / 'sd.img')
        cmd_mkimage(img_path)
        expected = DRIVE_BLOCKS * NUM_DRIVES * SD_BLOCK  # 33024 × 512
        assert os.path.getsize(img_path) == expected, (
            f"期待={expected}, 実際={os.path.getsize(img_path)}"
        )

    def test_dir_area_initialized_all_drives(self, tmp_path):
        """全ドライブのディレクトリ領域(16KB)が 0xE5 で埋まっていること。"""
        img_path = str(tmp_path / 'sd.img')
        cmd_mkimage(img_path)
        image = load_image(img_path)
        for d in range(NUM_DRIVES):
            off = dir_area_offset(d)
            area = image[off:off + DIR_SIZE]
            assert all(b == EMPTY_ENTRY for b in area), (
                f"ドライブ{chr(ord('A')+d)} のディレクトリ領域に 0xE5 以外のバイトがある"
            )

    def test_off_area_is_zero(self, tmp_path):
        """OFF領域(ドライブA先頭32ブロック=16KB)が 0x00 であること(未書込状態)。"""
        img_path = str(tmp_path / 'sd.img')
        cmd_mkimage(img_path)
        image = load_image(img_path)
        base = drive_base_offset(0)
        off_area = image[base:base + OFF_SECTORS * SD_BLOCK]
        assert all(b == 0x00 for b in off_area), "OFF領域が 0x00 でない(mkimage後)"

    def test_total_block_count(self, tmp_path):
        """イメージの総SDブロック数が 33024 であること。"""
        img_path = str(tmp_path / 'sd.img')
        cmd_mkimage(img_path)
        total = os.path.getsize(img_path) // SD_BLOCK
        assert total == 33024


# -------------------------------------------------------------------------
# 2. sys テスト
# -------------------------------------------------------------------------

class TestSys:
    def _write_and_load(self, tmp_path):
        """ダミーBIOS/CCP/BDOSを作成し、sys コマンドを実行してイメージを返す。"""
        img_path = _make_temp_image(tmp_path)

        # ダミーバイナリ(各レイアウトに合わせたサイズ)
        bios_size = 5 * SD_BLOCK  # 2560B
        ccp_size  = 4 * SD_BLOCK  # 2048B
        bdos_size = 7 * SD_BLOCK  # 3584B

        bios_bin = str(tmp_path / 'bios.bin')
        ccp_bin  = str(tmp_path / 'ccp.bin')
        bdos_bin = str(tmp_path / 'bdos.bin')

        with open(bios_bin, 'wb') as f:
            f.write(_make_fake_bin(bios_size, b'\xBB'))
        with open(ccp_bin, 'wb') as f:
            f.write(_make_fake_bin(ccp_size, b'\xCC'))
        with open(bdos_bin, 'wb') as f:
            f.write(_make_fake_bin(bdos_size, b'\xDD'))

        cmd_sys(img_path, bios_bin, ccp_bin, bdos_bin)
        return load_image(img_path), bios_bin, ccp_bin, bdos_bin

    def test_bios_at_lba0(self, tmp_path):
        """BIOS(LBA 0-4)がドライブAのOFF先頭に書かれていること。"""
        image, bios_bin, _, _ = self._write_and_load(tmp_path)
        with open(bios_bin, 'rb') as f:
            expected = f.read()
        base = drive_base_offset(0)
        actual = bytes(image[base:base + len(expected)])
        assert actual == expected

    def test_ccp_at_lba5(self, tmp_path):
        """CCP(LBA 5-8)が正しい位置に書かれていること。"""
        image, _, ccp_bin, _ = self._write_and_load(tmp_path)
        with open(ccp_bin, 'rb') as f:
            expected = f.read()
        base = drive_base_offset(0) + 5 * SD_BLOCK
        actual = bytes(image[base:base + len(expected)])
        assert actual == expected

    def test_bdos_at_lba9(self, tmp_path):
        """BDOS(LBA 9-15)が正しい位置に書かれていること。"""
        image, _, _, bdos_bin = self._write_and_load(tmp_path)
        with open(bdos_bin, 'rb') as f:
            expected = f.read()
        base = drive_base_offset(0) + 9 * SD_BLOCK
        actual = bytes(image[base:base + len(expected)])
        assert actual == expected

    def test_sys_layout_total(self, tmp_path):
        """SYS_TOTAL_BLOCKS(=16)×512B の範囲が有効であること。"""
        image, bios_bin, ccp_bin, bdos_bin = self._write_and_load(tmp_path)
        total_written = SYS_TOTAL_BLOCKS * SD_BLOCK
        # ドライブAのOFF領域(16KB)内に収まっていること
        assert total_written <= OFF_SECTORS * SD_BLOCK

    def test_dir_area_preserved_after_sys(self, tmp_path):
        """sys 後もドライブAのディレクトリ領域が 0xE5 のまま保持されること。"""
        img_path = str(tmp_path / 'sd.img')
        bios_bin = str(tmp_path / 'bios.bin')
        ccp_bin  = str(tmp_path / 'ccp.bin')
        bdos_bin = str(tmp_path / 'bdos.bin')
        with open(bios_bin, 'wb') as f:
            f.write(b'\xBB' * (5 * SD_BLOCK))
        with open(ccp_bin, 'wb') as f:
            f.write(b'\xCC' * (4 * SD_BLOCK))
        with open(bdos_bin, 'wb') as f:
            f.write(b'\xDD' * (7 * SD_BLOCK))
        cmd_mkimage(img_path)
        cmd_sys(img_path, bios_bin, ccp_bin, bdos_bin)
        image = load_image(img_path)
        off = dir_area_offset(0)
        area = image[off:off + DIR_SIZE]
        assert all(b == EMPTY_ENTRY for b in area), (
            "sys 後にドライブAのディレクトリ領域が変化している"
        )


# -------------------------------------------------------------------------
# 3. put 小ファイル (128B)
# -------------------------------------------------------------------------

class TestPutSmall:
    def test_roundtrip_128b(self, tmp_path):
        """128Bファイルのput→get ラウンドトリップ一致。"""
        img_path = _make_temp_image(tmp_path)
        src = _make_fake_bin(128, b'\x42')
        src_file = str(tmp_path / 'TEST.COM')
        with open(src_file, 'wb') as f:
            f.write(src)

        cmd_put(img_path, 'A', src_file, name_arg='TEST.COM')

        out_file = str(tmp_path / 'out.bin')
        cmd_get(img_path, 'A', 'TEST.COM', out_file)

        with open(out_file, 'rb') as f:
            got = f.read()

        # 128B境界にパディングされるため先頭128Bが一致すること
        assert got[:128] == src

    def test_dir_shows_one_entry(self, tmp_path, capsys):
        """128Bファイルをputした後、dirで1件表示されること。"""
        img_path = _make_temp_image(tmp_path)
        src = _make_fake_bin(128, b'\x42')
        src_file = str(tmp_path / 'HELLO.COM')
        with open(src_file, 'wb') as f:
            f.write(src)

        cmd_put(img_path, 'A', src_file, name_arg='HELLO.COM')
        cmd_dir(img_path, 'A')

        captured = capsys.readouterr()
        assert 'HELLO' in captured.out
        assert 'COM' in captured.out

    def test_dir_entry_fields(self, tmp_path):
        """put後のディレクトリエントリのフィールドが正しく設定されていること。"""
        img_path = _make_temp_image(tmp_path)
        src = _make_fake_bin(128, b'\x55')
        src_file = str(tmp_path / 'FOO.COM')
        with open(src_file, 'wb') as f:
            f.write(src)

        cmd_put(img_path, 'A', src_file, name_arg='FOO.COM')
        image = load_image(img_path)

        # 最初のディレクトリエントリを確認
        e = read_dir_entry(image, 0, 0)
        parsed = parse_dir_entry(e)
        assert parsed['user'] == 0
        assert parsed['name'].rstrip(b' ') == b'FOO'
        assert parsed['ext'].rstrip(b' ') == b'COM'
        assert parsed['ex'] == 0
        assert parsed['s2'] == 0
        assert parsed['rc'] == 1  # 128B = 1レコード
        assert parsed['alloc'][0] == DIR_CPM_BLOCKS  # ブロック8から割当

    def test_user_number(self, tmp_path):
        """ユーザ番号指定が正しくエントリに反映されること。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'USR.COM')
        with open(src_file, 'wb') as f:
            f.write(b'\x00' * 128)

        cmd_put(img_path, 'A', src_file, name_arg='USR.COM', user=3)
        image = load_image(img_path)
        e = read_dir_entry(image, 0, 0)
        assert e[0] == 3


# -------------------------------------------------------------------------
# 4. put 大ファイル (20KB → 2エクステント)
# -------------------------------------------------------------------------

class TestPutLarge:
    def test_roundtrip_20kb(self, tmp_path):
        """20KBファイルの put→get ラウンドトリップ完全一致。"""
        img_path = _make_temp_image(tmp_path)
        # 20KB = 16KB(エクステント1) + 4KB(エクステント2)
        src = bytes(range(256)) * 80  # 20480B = 20KB
        assert len(src) == 20480
        src_file = str(tmp_path / 'BIG.BIN')
        with open(src_file, 'wb') as f:
            f.write(src)

        cmd_put(img_path, 'A', src_file, name_arg='BIG.BIN')

        out_file = str(tmp_path / 'out.bin')
        cmd_get(img_path, 'A', 'BIG.BIN', out_file)

        with open(out_file, 'rb') as f:
            got = f.read()

        # 取り出したデータの先頭20480Bが元と一致すること
        assert got[:20480] == src

    def test_two_extents_created(self, tmp_path):
        """20KBファイルに対して2つのディレクトリエントリ(エクステント)が作成されること。"""
        img_path = _make_temp_image(tmp_path)
        src = b'\xAB' * 20480
        src_file = str(tmp_path / 'TWO.BIN')
        with open(src_file, 'wb') as f:
            f.write(src)

        cmd_put(img_path, 'A', src_file, name_arg='TWO.BIN')
        image = load_image(img_path)

        entries = []
        name_b = b'TWO     '
        ext_b  = b'BIN'
        for i in range(DIR_ENTRIES):
            e = read_dir_entry(image, 0, i)
            if e[0] != EMPTY_ENTRY:
                parsed = parse_dir_entry(e)
                if parsed['name'] == name_b and parsed['ext'] == ext_b:
                    entries.append(parsed)

        assert len(entries) == 2, f"エクステント数が期待と異なる: {len(entries)}"
        ext_nos = sorted(e['extent_no'] for e in entries)
        assert ext_nos == [0, 1]

    def test_dir_aggregates_one_file(self, tmp_path, capsys):
        """20KBファイルの dir 表示が 1ファイルとして集約されること。"""
        img_path = _make_temp_image(tmp_path)
        src = b'\xCD' * 20480
        src_file = str(tmp_path / 'AGG.BIN')
        with open(src_file, 'wb') as f:
            f.write(src)

        cmd_put(img_path, 'A', src_file, name_arg='AGG.BIN')
        cmd_dir(img_path, 'A')

        captured = capsys.readouterr()
        # dir出力のみを対象に 'AGG' を含む行を抽出する(put の書込メッセージは除外)
        # dir出力の書式: 先頭が空白+数字(ユーザ番号)の行がファイル一覧
        dir_lines = [
            l for l in captured.out.splitlines()
            if 'AGG' in l and l.strip().startswith('0')
        ]
        assert len(dir_lines) == 1, (
            f"dir出力に AGG のファイル行が1件でない:\n{captured.out}"
        )

    def test_first_extent_rc_is_128(self, tmp_path):
        """20KBファイルの最初のエクステントのRCが128(満杯=16KB)であること。"""
        img_path = _make_temp_image(tmp_path)
        src = b'\xEF' * 20480
        src_file = str(tmp_path / 'RC128.BIN')
        with open(src_file, 'wb') as f:
            f.write(src)

        cmd_put(img_path, 'A', src_file, name_arg='RC128.BIN')
        image = load_image(img_path)

        name_b = b'RC128   '
        ext_b  = b'BIN'
        extents = []
        for i in range(DIR_ENTRIES):
            e = read_dir_entry(image, 0, i)
            if e[0] != EMPTY_ENTRY:
                parsed = parse_dir_entry(e)
                if parsed['name'] == name_b and parsed['ext'] == ext_b:
                    extents.append(parsed)

        extents.sort(key=lambda x: x['extent_no'])
        # エクステント0のRCは128(満杯)
        assert extents[0]['rc'] == 128


# -------------------------------------------------------------------------
# 5. put 512KB超ファイル (>32エクステント, S2桁上げ)
# -------------------------------------------------------------------------

class TestPutVeryLarge:
    def test_roundtrip_s2_rollover(self, tmp_path):
        """33エクステント(528KB)ファイルの put→get ラウンドトリップ一致。

        EX=0-31 で32エクステント後、S2=1, EX=0 に桁上げされることを確認する。
        """
        img_path = _make_temp_image(tmp_path)
        # 33エクステント = 32×16KB + 1×16KB = 33×16384 = 540672B
        # ただしディレクトリエントリは512件しかないので33エクステントは問題ない
        num_extents = 33
        src_size = num_extents * EXTENT_SIZE  # 33 × 16384 = 540672B
        # パターンでデータを作成(検証を確実に)
        src = bytes(i & 0xFF for i in range(src_size))
        src_file = str(tmp_path / 'HUGE.BIN')
        with open(src_file, 'wb') as f:
            f.write(src)

        cmd_put(img_path, 'A', src_file, name_arg='HUGE.BIN')

        out_file = str(tmp_path / 'out.bin')
        cmd_get(img_path, 'A', 'HUGE.BIN', out_file)

        with open(out_file, 'rb') as f:
            got = f.read()

        assert got[:src_size] == src

    def test_s2_increments_after_32_extents(self, tmp_path):
        """33エクステント以上のファイルに対してS2=1のエントリが存在すること。"""
        img_path = _make_temp_image(tmp_path)
        num_extents = 33
        src_size = num_extents * EXTENT_SIZE
        src = b'\x77' * src_size
        src_file = str(tmp_path / 'S2CHK.BIN')
        with open(src_file, 'wb') as f:
            f.write(src)

        cmd_put(img_path, 'A', src_file, name_arg='S2CHK.BIN')
        image = load_image(img_path)

        name_b = b'S2CHK   '
        ext_b  = b'BIN'
        s2_values = []
        for i in range(DIR_ENTRIES):
            e = read_dir_entry(image, 0, i)
            if e[0] != EMPTY_ENTRY:
                parsed = parse_dir_entry(e)
                if parsed['name'] == name_b and parsed['ext'] == ext_b:
                    s2_values.append(parsed['s2'])

        # S2=1 を含むエントリが存在すること(エクステント32以降)
        assert 1 in s2_values, f"S2=1 のエントリが存在しない: s2_values={s2_values}"


# -------------------------------------------------------------------------
# 6. format テスト
# -------------------------------------------------------------------------

class TestFormat:
    def test_dir_area_cleared(self, tmp_path):
        """format後にディレクトリ領域が 0xE5 で埋まること。"""
        img_path = _make_temp_image(tmp_path)

        # ファイルを put してディレクトリに書き込む
        src_file = str(tmp_path / 'FILL.COM')
        with open(src_file, 'wb') as f:
            f.write(b'\xAA' * 512)
        cmd_put(img_path, 'A', src_file, name_arg='FILL.COM')

        # format でディレクトリ領域を初期化
        cmd_format(img_path, 'A')

        image = load_image(img_path)
        off = dir_area_offset(0)
        area = image[off:off + DIR_SIZE]
        assert all(b == EMPTY_ENTRY for b in area), (
            "format後にディレクトリ領域に 0xE5 以外のバイトが残っている"
        )

    def test_off_area_preserved_after_format(self, tmp_path):
        """format後もOFF領域(BIOS/CCP/BDOS)が保持されること。"""
        img_path = _make_temp_image(tmp_path)

        # sys でOFF領域を書き込む
        bios_bin = str(tmp_path / 'bios.bin')
        ccp_bin  = str(tmp_path / 'ccp.bin')
        bdos_bin = str(tmp_path / 'bdos.bin')
        sentinel = b'\xDE\xAD\xBE\xEF'
        with open(bios_bin, 'wb') as f:
            f.write(sentinel + b'\x00' * (5 * SD_BLOCK - len(sentinel)))
        with open(ccp_bin, 'wb') as f:
            f.write(b'\xCC' * (4 * SD_BLOCK))
        with open(bdos_bin, 'wb') as f:
            f.write(b'\xDD' * (7 * SD_BLOCK))
        cmd_sys(img_path, bios_bin, ccp_bin, bdos_bin)

        # format
        cmd_format(img_path, 'A')

        image = load_image(img_path)
        # OFF先頭のセンチネルが保持されていること
        base = drive_base_offset(0)
        assert bytes(image[base:base + 4]) == sentinel, (
            "format後にOFF領域のデータが消えている"
        )

    def test_other_drive_not_affected(self, tmp_path):
        """format(ドライブA)が他ドライブ(B)に影響しないこと。"""
        img_path = _make_temp_image(tmp_path)

        # ドライブBにファイルを put
        src_file = str(tmp_path / 'KEEP.COM')
        with open(src_file, 'wb') as f:
            f.write(b'\x11' * 256)
        cmd_put(img_path, 'B', src_file, name_arg='KEEP.COM')

        # ドライブAをformat
        cmd_format(img_path, 'A')

        # ドライブBのディレクトリエントリが残っていること
        image = load_image(img_path)
        e = read_dir_entry(image, 1, 0)  # ドライブB(drive=1)
        assert e[0] != EMPTY_ENTRY, (
            "ドライブAのformatがドライブBのエントリに影響している"
        )

    def test_data_beyond_dir_preserved(self, tmp_path):
        """format後もディレクトリ領域外のデータブロック(ファイル本体)は保持されること。"""
        img_path = _make_temp_image(tmp_path)

        # ブロック8(ディレクトリ直後の最初のデータブロック)に直接データを書き込む
        image = load_image(img_path)
        blk8_off = cpm_block_offset(0, DIR_CPM_BLOCKS)  # ブロック8のオフセット
        marker = b'\xFF\xFE\xFD\xFC'
        image[blk8_off:blk8_off + 4] = marker
        save_image(img_path, image)

        cmd_format(img_path, 'A')

        image2 = load_image(img_path)
        assert bytes(image2[blk8_off:blk8_off + 4]) == marker, (
            "format後にデータブロック8の内容が変化している"
        )


# -------------------------------------------------------------------------
# 7. ファイル名正規化
# -------------------------------------------------------------------------

class TestNormalizeName:
    def test_lowercase_to_upper(self):
        """小文字ファイル名が大文字に変換されること。"""
        name, ext = normalize_name('hello.com')
        assert name == b'HELLO   '
        assert ext == b'COM'

    def test_8char_name(self):
        """8文字のファイル名が切り詰めなしで通ること。"""
        name, ext = normalize_name('FILENAME.EXT')
        assert name == b'FILENAME'
        assert ext == b'EXT'

    def test_name_truncated_at_8(self):
        """9文字以上のファイル名が8文字に切り詰められること。"""
        name, ext = normalize_name('TOOLONGNAME.COM')
        assert len(name) == 8
        assert name == b'TOOLONGN'

    def test_no_extension(self):
        """拡張子なしファイルの ext がスペース埋めになること。"""
        name, ext = normalize_name('NOEXT')
        assert name == b'NOEXT   '
        assert ext == b'   '

    def test_b7_cleared(self):
        """返り値の全バイトで b7=0 が保証されること。"""
        name, ext = normalize_name('test.bin')
        for b in name + ext:
            assert (b & 0x80) == 0, f"b7=1 のバイトが存在: 0x{b:02X}"

    def test_parse_name_arg(self):
        """parse_name_arg が '8.3' 形式を正しくパースすること。"""
        name, ext = parse_name_arg('TEST.COM')
        assert name == b'TEST    '
        assert ext == b'COM'


# -------------------------------------------------------------------------
# 8. エクステントヘルパ
# -------------------------------------------------------------------------

class TestExtentHelpers:
    def test_extent_no_basic(self):
        """EX=0, S2=0 → extent_no=0。"""
        assert extent_no(0, 0) == 0

    def test_extent_no_ex31(self):
        """EX=31, S2=0 → extent_no=31。"""
        assert extent_no(31, 0) == 31

    def test_extent_no_s2_1(self):
        """EX=0, S2=1 → extent_no=32。"""
        assert extent_no(0, 1) == 32

    def test_extent_no_max(self):
        """EX=31, S2=3 → extent_no=127(1ドライブ最大=128エクステント-1)。"""
        assert extent_no(31, 3) == 127

    def test_roundtrip(self):
        """extent_to_fields(extent_no(ex, s2)) == (ex, s2) が成立すること。"""
        for s2 in range(4):
            for ex in range(32):
                n = extent_no(ex, s2)
                got_ex, got_s2 = extent_to_fields(n)
                assert (got_ex, got_s2) == (ex, s2), (
                    f"往復不一致: ex={ex}, s2={s2} → n={n} → ({got_ex}, {got_s2})"
                )

    def test_ex_mask(self):
        """EX フィールドは下位5bit のみ有効(0x1F マスク)。"""
        # extent_no は EX & 0x1F で計算する
        assert extent_no(0x3F, 0) == extent_no(0x1F, 0)

    def test_s2_mask(self):
        """S2 フィールドは下位6bit のみ有効(0x3F マスク)。"""
        assert extent_no(0, 0x7F) == extent_no(0, 0x3F)


# -------------------------------------------------------------------------
# 9. 複数ドライブへの put テスト
# -------------------------------------------------------------------------

class TestMultiDrive:
    def test_put_different_drives(self, tmp_path):
        """異なるドライブへのput後、各ドライブのディレクトリが独立していること。"""
        img_path = _make_temp_image(tmp_path)

        src_a = str(tmp_path / 'DRIVE_A.COM')
        src_b = str(tmp_path / 'DRIVE_B.COM')
        with open(src_a, 'wb') as f:
            f.write(b'\xAA' * 256)
        with open(src_b, 'wb') as f:
            f.write(b'\xBB' * 256)

        cmd_put(img_path, 'A', src_a, name_arg='DRIVE_A.COM')
        cmd_put(img_path, 'B', src_b, name_arg='DRIVE_B.COM')

        image = load_image(img_path)

        # ドライブAにDRIVE_A.COM が存在すること
        found_a = False
        for i in range(DIR_ENTRIES):
            e = read_dir_entry(image, 0, i)
            if e[0] != EMPTY_ENTRY:
                parsed = parse_dir_entry(e)
                if parsed['name'].rstrip(b' ') == b'DRIVE_A':
                    found_a = True
        assert found_a, "ドライブAにDRIVE_A.COMが見つからない"

        # ドライブBにDRIVE_B.COM が存在すること
        found_b = False
        for i in range(DIR_ENTRIES):
            e = read_dir_entry(image, 1, i)
            if e[0] != EMPTY_ENTRY:
                parsed = parse_dir_entry(e)
                if parsed['name'].rstrip(b' ') == b'DRIVE_B':
                    found_b = True
        assert found_b, "ドライブBにDRIVE_B.COMが見つからない"

    def test_put_drive_h(self, tmp_path):
        """最終ドライブH(drive=7)への put が正常に動作すること。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'LAST.COM')
        with open(src_file, 'wb') as f:
            f.write(b'\x88' * 128)

        cmd_put(img_path, 'H', src_file, name_arg='LAST.COM')

        image = load_image(img_path)
        found = False
        for i in range(DIR_ENTRIES):
            e = read_dir_entry(image, 7, i)
            if e[0] != EMPTY_ENTRY:
                parsed = parse_dir_entry(e)
                if parsed['name'].rstrip(b' ') == b'LAST':
                    found = True
        assert found, "ドライブHにLAST.COMが見つからない"


# -------------------------------------------------------------------------
# 10. ブロック割当ロジック
# -------------------------------------------------------------------------

class TestAllocBlocks:
    def test_alloc_starts_at_block8(self):
        """空の使用済みセットから割当ると、ブロック8から始まること。"""
        used: set[int] = set()
        result = alloc_blocks(used, 1)
        assert result == [DIR_CPM_BLOCKS]  # = [8]

    def test_alloc_sequential(self):
        """連続割当でブロック8, 9, 10...と昇順になること。"""
        used: set[int] = set()
        result = alloc_blocks(used, 3)
        assert result == [8, 9, 10]

    def test_alloc_skips_used(self):
        """使用済みブロックをスキップして次の空きを割当ること。"""
        used = {8, 9, 11}
        result = alloc_blocks(used, 2)
        assert result == [10, 12]

    def test_alloc_updates_used_set(self):
        """割当後に使用済みセットが更新されること。"""
        used: set[int] = set()
        alloc_blocks(used, 2)
        assert 8 in used
        assert 9 in used

    def test_alloc_overflow_raises(self):
        """空きブロックが不足したとき RuntimeError が発生すること。"""
        # DSM=1023 のため、ブロック8〜1023 = 1016ブロック
        used = set(range(DIR_CPM_BLOCKS, DSM + 1))  # 8〜1023 全部使用済み
        with pytest.raises(RuntimeError):
            alloc_blocks(used, 1)


# -------------------------------------------------------------------------
# 11. 同名再 put(上書き)— R3 回帰テスト
# -------------------------------------------------------------------------

class TestOverwrite:
    def test_same_name_put_twice_dir_shows_one(self, tmp_path, capsys):
        """同じファイル名を2回 put した後、dir に1件だけ表示されること。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'FOO.COM')

        # 1回目: 256B
        with open(src_file, 'wb') as f:
            f.write(b'\x11' * 256)
        cmd_put(img_path, 'A', src_file, name_arg='FOO.COM')

        # 2回目: 内容変更して同名で put
        with open(src_file, 'wb') as f:
            f.write(b'\x22' * 256)
        cmd_put(img_path, 'A', src_file, name_arg='FOO.COM')

        # dir 出力に FOO のファイル行が1件だけ
        cmd_dir(img_path, 'A')
        captured = capsys.readouterr()
        dir_lines = [
            l for l in captured.out.splitlines()
            if 'FOO' in l and l.strip().startswith('0')
        ]
        assert len(dir_lines) == 1, (
            f"上書き後に FOO の dir 行が複数:\n{captured.out}"
        )

    def test_same_name_put_twice_get_returns_latest(self, tmp_path):
        """同名再 put 後、get は最新内容を返すこと。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'BAR.COM')

        # 1回目: 旧データ
        old_data = b'\xAA' * 512
        with open(src_file, 'wb') as f:
            f.write(old_data)
        cmd_put(img_path, 'A', src_file, name_arg='BAR.COM')

        # 2回目: 新データ(明確に異なる)
        new_data = b'\xBB' * 256
        with open(src_file, 'wb') as f:
            f.write(new_data)
        cmd_put(img_path, 'A', src_file, name_arg='BAR.COM')

        out_file = str(tmp_path / 'out.bin')
        cmd_get(img_path, 'A', 'BAR.COM', out_file)
        with open(out_file, 'rb') as f:
            got = f.read()

        assert got[:256] == new_data, "最新内容が取得できていない"

    def test_overwrite_releases_blocks(self, tmp_path):
        """上書き後、旧エクステントのブロックが解放され再利用されること。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'REUSE.COM')

        # 1回目: 256B → 1ブロック(ブロック8)に書込
        with open(src_file, 'wb') as f:
            f.write(b'\x33' * 256)
        cmd_put(img_path, 'A', src_file, name_arg='REUSE.COM')

        # 上書き
        with open(src_file, 'wb') as f:
            f.write(b'\x44' * 256)
        cmd_put(img_path, 'A', src_file, name_arg='REUSE.COM')

        image = load_image(img_path)
        # 同名ファイルのエクステントを取得
        for i in range(DIR_ENTRIES):
            e = read_dir_entry(image, 0, i)
            if e[0] == EMPTY_ENTRY:
                continue
            parsed = parse_dir_entry(e)
            if parsed['name'].rstrip(b' ') == b'REUSE':
                # ブロック8が再利用されていること(リークしていないことを示す)
                assert parsed['alloc'][0] == DIR_CPM_BLOCKS
                return
        pytest.fail("上書き後に REUSE.COM のエントリが見つからない")

    def test_overwrite_large_to_small(self, tmp_path):
        """大ファイル(2エクステント)を小ファイル(1エクステント)で上書きしてエントリが1つに減ること。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'SHRINK.BIN')

        # 1回目: 20KB(2エクステント)
        with open(src_file, 'wb') as f:
            f.write(b'\x55' * 20480)
        cmd_put(img_path, 'A', src_file, name_arg='SHRINK.BIN')

        # 2回目: 256B(1エクステント)
        with open(src_file, 'wb') as f:
            f.write(b'\x66' * 256)
        cmd_put(img_path, 'A', src_file, name_arg='SHRINK.BIN')

        image = load_image(img_path)
        count = 0
        for i in range(DIR_ENTRIES):
            e = read_dir_entry(image, 0, i)
            if e[0] == EMPTY_ENTRY:
                continue
            parsed = parse_dir_entry(e)
            if parsed['name'].rstrip(b' ') == b'SHRINK':
                count += 1
        assert count == 1, f"上書き後にエクステント数が削減されていない: {count}"


# -------------------------------------------------------------------------
# 12. 空ファイル(R9)
# -------------------------------------------------------------------------

class TestEmptyFile:
    def test_put_empty_file_no_blocks_allocated(self, tmp_path):
        """0B ファイル put 後のエントリは RC=0, alloc=全0 でブロック未割当。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'EMPTY.COM')
        with open(src_file, 'wb') as f:
            pass  # 0B
        assert os.path.getsize(src_file) == 0

        cmd_put(img_path, 'A', src_file, name_arg='EMPTY.COM')

        image = load_image(img_path)
        # 該当エントリを探す
        found = None
        for i in range(DIR_ENTRIES):
            e = read_dir_entry(image, 0, i)
            if e[0] == EMPTY_ENTRY:
                continue
            parsed = parse_dir_entry(e)
            if parsed['name'].rstrip(b' ') == b'EMPTY':
                found = parsed
                break
        assert found is not None, "0Bファイルのエントリが見つからない"
        assert found['rc'] == 0, f"RC=0 でない: rc={found['rc']}"
        assert all(b == 0 for b in found['alloc']), (
            f"alloc が全0 でない: {found['alloc']}"
        )

    def test_get_empty_file_returns_zero_bytes(self, tmp_path):
        """0B ファイル get は 0B を返すこと。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'ZERO.COM')
        with open(src_file, 'wb') as f:
            pass
        cmd_put(img_path, 'A', src_file, name_arg='ZERO.COM')

        out_file = str(tmp_path / 'out.bin')
        cmd_get(img_path, 'A', 'ZERO.COM', out_file)
        assert os.path.getsize(out_file) == 0

    def test_put_empty_file_dir_shows(self, tmp_path, capsys):
        """0B ファイル put 後、dir に表示されること。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'NOTHING.COM')
        with open(src_file, 'wb') as f:
            pass
        cmd_put(img_path, 'A', src_file, name_arg='NOTHING.COM')

        cmd_dir(img_path, 'A')
        captured = capsys.readouterr()
        assert 'NOTHING' in captured.out

    def test_put_empty_file_no_block_used(self, tmp_path):
        """0B ファイル put 後、collect_used_blocks が空集合を返すこと。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'EMPTY.COM')
        with open(src_file, 'wb') as f:
            pass
        cmd_put(img_path, 'A', src_file, name_arg='EMPTY.COM')

        image = load_image(img_path)
        used = collect_used_blocks(image, 0)
        assert used == set(), f"0Bファイルで未割当のはずがブロック使用: {used}"


# -------------------------------------------------------------------------
# 13. マルチエクステント詳細検証(R17)
# -------------------------------------------------------------------------

class TestMultiExtentDetails:
    def test_rc_values_20kb(self, tmp_path):
        """20KB(160レコード=128+32)ファイル: extent0 RC=128, extent1 RC=32。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'RCCHK.BIN')
        with open(src_file, 'wb') as f:
            f.write(b'\xAB' * 20480)

        cmd_put(img_path, 'A', src_file, name_arg='RCCHK.BIN')

        image = load_image(img_path)
        extents = []
        for i in range(DIR_ENTRIES):
            e = read_dir_entry(image, 0, i)
            if e[0] == EMPTY_ENTRY:
                continue
            parsed = parse_dir_entry(e)
            if parsed['name'].rstrip(b' ') == b'RCCHK':
                extents.append(parsed)
        extents.sort(key=lambda x: x['extent_no'])

        assert len(extents) == 2
        assert extents[0]['rc'] == 128, f"extent0 RC期待=128, 実際={extents[0]['rc']}"
        assert extents[1]['rc'] == 32, f"extent1 RC期待=32, 実際={extents[1]['rc']}"

    def test_alloc_continuity_20kb(self, tmp_path):
        """20KBファイル: extent0 alloc=[8..15], extent1 alloc[0]=16(連続)。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'ALLOC.BIN')
        with open(src_file, 'wb') as f:
            f.write(b'\xCD' * 20480)

        cmd_put(img_path, 'A', src_file, name_arg='ALLOC.BIN')

        image = load_image(img_path)
        extents = []
        for i in range(DIR_ENTRIES):
            e = read_dir_entry(image, 0, i)
            if e[0] == EMPTY_ENTRY:
                continue
            parsed = parse_dir_entry(e)
            if parsed['name'].rstrip(b' ') == b'ALLOC':
                extents.append(parsed)
        extents.sort(key=lambda x: x['extent_no'])

        # extent0: ブロック8〜15
        assert extents[0]['alloc'] == [8, 9, 10, 11, 12, 13, 14, 15]
        # extent1: ブロック16 から(残り 32レコード = 2ブロック)
        assert extents[1]['alloc'][0] == 16
        assert extents[1]['alloc'][1] == 17
        # 残りスロットは0
        assert extents[1]['alloc'][2:] == [0] * 6


# -------------------------------------------------------------------------
# 14. ディレクトリ予約領域不可侵(R17)
# -------------------------------------------------------------------------

class TestDirAreaIntegrity:
    def test_dir_area_not_corrupted_by_large_put(self, tmp_path):
        """大ファイル put 後もデータ領域先頭16KB(ブロック0-7=ディレクトリ)が
        ファイルデータで上書きされていないこと。
        """
        img_path = _make_temp_image(tmp_path)

        # ディレクトリ領域の初期スナップショット(全 0xE5)
        image_before = load_image(img_path)
        dir_off = dir_area_offset(0)
        snapshot_dir = bytes(image_before[dir_off:dir_off + DIR_SIZE])

        # 大ファイル(20KB = 2エクステント)を put
        # ファイルデータは 0xE5 以外のバイトを使い、誤書込検出を容易にする
        src_file = str(tmp_path / 'BIG.BIN')
        with open(src_file, 'wb') as f:
            f.write(b'\x77' * 20480)
        cmd_put(img_path, 'A', src_file, name_arg='BIG.BIN')

        image_after = load_image(img_path)
        dir_after = image_after[dir_off:dir_off + DIR_SIZE]

        # ディレクトリ「エントリ書込領域」以外の部分(つまり 32B エントリのうち
        # 使われていないエントリ)は 0xE5 のままであること。
        # 具体的には、最初の2エントリ以降(エクステント0,1の2件)が 0xE5 維持。
        # ここでは「ブロック0-7 のうち、エントリとして使われていない領域」が
        # ファイルデータ(0x77)で上書きされていないことを確認。
        for i in range(2, DIR_ENTRIES):
            entry_off = i * DIR_ENTRY_SIZE
            entry = dir_after[entry_off:entry_off + DIR_ENTRY_SIZE]
            assert all(b == EMPTY_ENTRY for b in entry), (
                f"エントリ{i} がファイルデータで破壊されている: {entry.hex()}"
            )

    def test_dir_blocks_not_used_for_file_data(self, tmp_path):
        """put 後、CP/M ブロック 0-7(ディレクトリ予約)がファイルデータ書込先になっていない。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'CHECK.BIN')
        # 40KB = 3エクステント程度
        with open(src_file, 'wb') as f:
            f.write(b'\x99' * 40960)
        cmd_put(img_path, 'A', src_file, name_arg='CHECK.BIN')

        image = load_image(img_path)
        # 全エクステントの alloc に 0-7 が含まれないこと
        for i in range(DIR_ENTRIES):
            e = read_dir_entry(image, 0, i)
            if e[0] == EMPTY_ENTRY:
                continue
            parsed = parse_dir_entry(e)
            for blk in parsed['alloc']:
                if blk == 0:
                    continue  # 未使用スロット
                assert blk >= DIR_CPM_BLOCKS, (
                    f"ディレクトリ予約ブロック {blk} がファイルに割当てられている"
                )


# -------------------------------------------------------------------------
# 15. 入力バリデーション(R6/R13/R14)
# -------------------------------------------------------------------------

class TestInputValidation:
    def test_invalid_drive_letter_z(self, tmp_path):
        """ドライブ Z は範囲外で SystemExit する。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'X.COM')
        with open(src_file, 'wb') as f:
            f.write(b'\x00' * 128)
        with pytest.raises(SystemExit):
            cmd_put(img_path, 'Z', src_file, name_arg='X.COM')

    def test_invalid_drive_letter_lowercase_i(self, tmp_path):
        """ドライブ I(範囲外、A-H のみ許可)は SystemExit する。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'X.COM')
        with open(src_file, 'wb') as f:
            f.write(b'\x00' * 128)
        with pytest.raises(SystemExit):
            cmd_put(img_path, 'I', src_file, name_arg='X.COM')

    def test_invalid_drive_letter_digit(self, tmp_path):
        """数字のドライブ指定は SystemExit する。"""
        img_path = _make_temp_image(tmp_path)
        with pytest.raises(SystemExit):
            cmd_dir(img_path, '1')

    def test_drive_lowercase_a_accepted(self, tmp_path):
        """小文字 'a' は A と等価に受け付けられること。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'LC.COM')
        with open(src_file, 'wb') as f:
            f.write(b'\x00' * 128)
        # SystemExit が発生しないこと
        cmd_put(img_path, 'a', src_file, name_arg='LC.COM')

    def test_invalid_user_negative(self, tmp_path):
        """user < 0 は SystemExit する。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'U.COM')
        with open(src_file, 'wb') as f:
            f.write(b'\x00' * 128)
        with pytest.raises(SystemExit):
            cmd_put(img_path, 'A', src_file, name_arg='U.COM', user=-1)

    def test_invalid_user_too_large(self, tmp_path):
        """user > 15 は SystemExit する。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'U.COM')
        with open(src_file, 'wb') as f:
            f.write(b'\x00' * 128)
        with pytest.raises(SystemExit):
            cmd_put(img_path, 'A', src_file, name_arg='U.COM', user=20)

    def test_user_15_accepted(self, tmp_path):
        """user=15(最大)が受け付けられること。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'U.COM')
        with open(src_file, 'wb') as f:
            f.write(b'\x00' * 128)
        cmd_put(img_path, 'A', src_file, name_arg='U.COM', user=15)
        image = load_image(img_path)
        e = read_dir_entry(image, 0, 0)
        assert e[0] == 15

    def test_forbidden_char_in_name(self, tmp_path):
        """禁止文字('<', '>' など)を含む --name は SystemExit する。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'X.COM')
        with open(src_file, 'wb') as f:
            f.write(b'\x00' * 128)
        with pytest.raises(SystemExit):
            cmd_put(img_path, 'A', src_file, name_arg='FOO<.COM')

    def test_normalize_name_forbidden_raises(self):
        """normalize_name(strict=True) が禁止文字で ValueError を送出する。"""
        for bad in ['A<.COM', 'A>.COM', 'A:B.COM', 'A;B.COM',
                    'A=B.COM', 'A?B.COM', 'A*B.COM', 'A[B].COM']:
            with pytest.raises(ValueError):
                normalize_name(bad, strict=True)

    def test_normalize_name_nonstrict_removes_forbidden(self):
        """normalize_name(strict=False) は禁止文字を黙って除去する。"""
        name, ext = normalize_name('A<B.COM', strict=False)
        # '<' が除去され、'AB' が残る
        assert name == b'AB      '
        assert ext == b'COM'


# -------------------------------------------------------------------------
# 16. ブロック範囲チェック(R4)
# -------------------------------------------------------------------------

class TestBlockRangeCheck:
    def test_cpm_block_offset_dsm_ok(self):
        """block_no = DSM(=1023)は受け付けられる。"""
        # 例外が出ないこと
        off = cpm_block_offset(0, DSM)
        assert off > 0

    def test_cpm_block_offset_above_dsm_raises(self):
        """block_no > DSM は RuntimeError を送出する。"""
        with pytest.raises(RuntimeError):
            cpm_block_offset(0, DSM + 1)

    def test_cpm_block_offset_negative_raises(self):
        """block_no < 0 は RuntimeError を送出する。"""
        with pytest.raises(RuntimeError):
            cpm_block_offset(0, -1)


# -------------------------------------------------------------------------
# 17. delete_file ヘルパ単体テスト
# -------------------------------------------------------------------------

class TestDeleteFile:
    def test_delete_existing_file(self, tmp_path):
        """delete_file で同名ファイルの全エクステントが消えること。"""
        img_path = _make_temp_image(tmp_path)
        src_file = str(tmp_path / 'DEL.BIN')
        # 20KB(2エクステント)
        with open(src_file, 'wb') as f:
            f.write(b'\xEE' * 20480)
        cmd_put(img_path, 'A', src_file, name_arg='DEL.BIN')

        image = load_image(img_path)
        name_b = b'DEL     '
        ext_b  = b'BIN'
        deleted = delete_file(image, 0, 0, name_b, ext_b)
        assert deleted == 2

        # 削除後にエントリが残っていないこと
        for i in range(DIR_ENTRIES):
            e = read_dir_entry(image, 0, i)
            if e[0] == EMPTY_ENTRY:
                continue
            parsed = parse_dir_entry(e)
            assert parsed['name'] != name_b or parsed['ext'] != ext_b

    def test_delete_nonexistent_returns_zero(self, tmp_path):
        """存在しないファイルの delete_file は0を返す。"""
        img_path = _make_temp_image(tmp_path)
        image = load_image(img_path)
        deleted = delete_file(image, 0, 0, b'NONE    ', b'BIN')
        assert deleted == 0
