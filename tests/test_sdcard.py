"""
SDCard SPI スレーブモデル + 8255 SD ブリッジ テスト
実行: PYTHONPATH=external/z80 .venv/bin/python -m pytest tests/test_sdcard.py -q
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu.sdcard import SDCard
from emu.pc8001 import PC8001


# ------------------------------------------------------------------
# ヘルパ関数
# ------------------------------------------------------------------

def send_cmd(sd: SDCard, cmd: int, arg: int = 0, crc: int = 0x95) -> int:
    """
    6 バイトのコマンドフレームを SDCard に送信し、R1 を返す。
    R1 は bit7=0 の最初の応答バイト。
    6 バイト目の返値も候補に含める(SD 仕様では応答は Ncr 後だが
    本モデルは 6 バイト目で即返す実装のため)。
    """
    frame = [
        0x40 | cmd,
        (arg >> 24) & 0xFF,
        (arg >> 16) & 0xFF,
        (arg >> 8)  & 0xFF,
        arg & 0xFF,
        crc,
    ]
    last_resp = 0xFF
    for b in frame:
        last_resp = sd.exchange_byte(b)

    # 6 バイト目の返値が有効な R1 ならそれを使う
    if (last_resp & 0x80) == 0:
        return last_resp

    # 最大 16 バイト 0xFF を送って R1 を待つ
    for _ in range(16):
        r = sd.exchange_byte(0xFF)
        if (r & 0x80) == 0:
            return r
    return 0xFF  # タイムアウト


def cmd_frame(cmd: int, arg: int = 0, crc: int = 0x95) -> list[int]:
    """6 バイトのコマンドフレームを生成する。"""
    return [
        0x40 | cmd,
        (arg >> 24) & 0xFF,
        (arg >> 16) & 0xFF,
        (arg >> 8)  & 0xFF,
        arg & 0xFF,
        crc,
    ]


def send_cmd_held(sd: SDCard, cmd: int, arg: int = 0, crc: int = 0x95) -> int:
    """
    CS を Low に保ったまま(cs_deassert を呼ばずに)コマンドを送り R1 を返す。
    実機ドライバが CS 保持で連続コマンドを送るケースを模擬する。
    """
    for b in cmd_frame(cmd, arg, crc):
        sd.exchange_byte(b)
    for _ in range(16):
        r = sd.exchange_byte(0xFF)
        if (r & 0x80) == 0:
            return r
    return 0xFF


def read_bytes(sd: SDCard, n: int) -> list[int]:
    """n バイト分 0xFF を送り、受信バイトリストを返す。"""
    return [sd.exchange_byte(0xFF) for _ in range(n)]


# ------------------------------------------------------------------
# A. バイトレベルテスト (SDCard.exchange_byte 直接)
# ------------------------------------------------------------------

class TestSDCardCommands:
    """SDCard コマンド応答テスト"""

    def test_cmd0_returns_idle(self):
        """CMD0 → R1==0x01 (アイドル状態)"""
        sd = SDCard()
        r1 = send_cmd(sd, 0, crc=0x95)
        assert r1 == 0x01

    def test_cmd8_voltage_check(self):
        """CMD8(arg=0x1AA) → R1==0x01、R7 末尾バイト==0xAA"""
        sd = SDCard()
        r1 = send_cmd(sd, 8, arg=0x000001AA, crc=0x87)
        assert r1 == 0x01
        r7 = read_bytes(sd, 4)
        assert r7[3] == 0xAA

    def test_acmd41_init_complete(self):
        """CMD55→R1==0x01、ACMD41→R1==0x00 (初期化完了)"""
        sd = SDCard()
        r1_55 = send_cmd(sd, 55)
        assert r1_55 == 0x01
        r1_41 = send_cmd(sd, 41, arg=0x40000000)
        assert r1_41 == 0x00

    def test_cmd58_ocr_ccs_bit(self):
        """CMD58 → R1==0x00、OCR[0] の bit6 (CCS=1) が立っている"""
        sd = SDCard()
        # まず初期化シーケンスを通す
        send_cmd(sd, 0)
        send_cmd(sd, 8, arg=0x000001AA, crc=0x87)
        send_cmd(sd, 55)
        send_cmd(sd, 41, arg=0x40000000)
        # CMD58
        r1 = send_cmd(sd, 58)
        assert r1 == 0x00
        ocr = read_bytes(sd, 4)
        assert (ocr[0] & 0x40) != 0  # CCS=1

    def test_write_then_read_block(self):
        """CMD24 でブロック 5 に書き込み、CMD17 で読み返して一致する"""
        sd = SDCard()

        # 書き込みデータ: i & 0xFF のパターン (512 バイト)
        write_data = bytes([i & 0xFF for i in range(512)])

        # CMD24 (WRITE_SINGLE_BLOCK, ブロック 5)
        r1 = send_cmd(sd, 24, arg=5)
        assert r1 == 0x00

        # データトークン 0xFE + 512 バイト + CRC 2 バイト を送信
        sd.exchange_byte(0xFE)
        for b in write_data:
            sd.exchange_byte(b)
        sd.exchange_byte(0x00)  # CRC1
        # CRC2 の返値がデータレスポンス (0x05) になる
        data_resp = sd.exchange_byte(0x00)  # CRC2
        if (data_resp & 0x1F) != 0x05:
            # まだ返っていない場合はポーリング
            for _ in range(16):
                data_resp = sd.exchange_byte(0xFF)
                if data_resp != 0xFF:
                    break
        assert (data_resp & 0x1F) == 0x05  # accepted

        # ビジー解除待ち
        for _ in range(8):
            sd.exchange_byte(0xFF)

        # CMD17 (READ_SINGLE_BLOCK, ブロック 5)
        r1 = send_cmd(sd, 17, arg=5)
        assert r1 == 0x00

        # データトークン 0xFE を待つ
        token = 0xFF
        for _ in range(20):
            token = sd.exchange_byte(0xFF)
            if token == 0xFE:
                break
        assert token == 0xFE

        # 512 バイト読み出し
        read_data = read_bytes(sd, 512)
        assert bytes(read_data) == write_data

    def test_unknown_cmd_returns_illegal(self):
        """未対応コマンドは illegal (R1 の bit2 が立つ)"""
        sd = SDCard()
        r1 = send_cmd(sd, 63)  # 未使用コマンド
        assert (r1 & 0x04) != 0  # illegal command bit

    def test_acmd41_not_triggered_without_cmd55(self):
        """CMD55 なしで CMD41 を送ると ACMD41 にはならない"""
        sd = SDCard()
        # CMD55 なしで CMD41: 通常コマンドとして処理 → illegal
        r1 = send_cmd(sd, 41, arg=0x40000000)
        assert r1 != 0x00  # 初期化完了にならない

    def test_cmd16_accepted(self):
        """CMD16 (SET_BLOCKLEN) → R1==0x00"""
        sd = SDCard()
        send_cmd(sd, 0)
        r1 = send_cmd(sd, 16, arg=512)
        assert r1 == 0x00

    def test_image_persistence(self):
        """write_block / read_block がイメージに反映される"""
        sd = SDCard()
        data = bytes(range(256)) + bytes(range(256))  # 512 バイト
        sd.write_block(10, data)
        assert sd.read_block(10) == data

    def test_custom_image(self):
        """外部イメージを渡した場合にそのデータが読める"""
        image = bytearray(33024 * 512)
        image[512] = 0xAB   # ブロック 1 の先頭バイト
        sd = SDCard(image=image)
        assert sd.read_block(1)[0] == 0xAB


# ------------------------------------------------------------------
# S1. CS 保持での連続コマンド (M1 回帰テスト)
# ------------------------------------------------------------------

class TestCsHeldContinuous:
    """CS を Low に保ったままの連続コマンド処理テスト"""

    def test_init_sequence_cs_held(self):
        """
        CS 保持のまま CMD0→CMD8→CMD55→ACMD41→CMD58 を連送し
        01,01,01,00,00 を確認する。
        """
        sd = SDCard()
        assert send_cmd_held(sd, 0) == 0x01
        assert send_cmd_held(sd, 8, arg=0x000001AA, crc=0x87) == 0x01
        assert send_cmd_held(sd, 55) == 0x01
        assert send_cmd_held(sd, 41, arg=0x40000000) == 0x00
        assert send_cmd_held(sd, 58) == 0x00

    def test_acmd41_when_prev_response_not_drained(self):
        """
        CMD55 の応答を読み切らずに ACMD41 フレームを送っても
        新コマンド検出で stale 応答が破棄され ACMD41 が成立する。
        """
        sd = SDCard()
        # CMD55 を送るが R1 を 1 バイトだけ読む(読み切らない)
        for b in cmd_frame(55):
            sd.exchange_byte(b)
        sd.exchange_byte(0xFF)  # 部分的に R1 を読む
        # すぐ ACMD41 を送出(残応答が混入しないこと)
        for b in cmd_frame(41, arg=0x40000000):
            sd.exchange_byte(b)
        r1 = 0xFF
        for _ in range(16):
            r = sd.exchange_byte(0xFF)
            if (r & 0x80) == 0:
                r1 = r
                break
        assert r1 == 0x00

    def test_cmd_header_discards_stale_tx(self):
        """
        前コマンドの残応答キューがあっても、新コマンド先頭で破棄される。
        CMD8 (R7 4 バイト残あり) の直後に CMD0 を送り 0x01 を得る。
        """
        sd = SDCard()
        # CMD8: R1 + R7 を全部読まずに残す
        for b in cmd_frame(8, arg=0x000001AA, crc=0x87):
            sd.exchange_byte(b)
        sd.exchange_byte(0xFF)  # R1 だけ読む(R7 4 バイトが _tx に残る)
        # CMD0 を送出: stale R7 が破棄され 0x01 が返るはず
        assert send_cmd_held(sd, 0) == 0x01


# ------------------------------------------------------------------
# S2. 往復強化テスト
# ------------------------------------------------------------------

class TestRoundTrip:
    """読み書き往復の強化テスト"""

    def _write_block_via_spi(self, sd, block, data):
        """SPI バイト交換でブロックを書き込む。"""
        r1 = send_cmd(sd, 24, arg=block)
        assert r1 == 0x00
        sd.exchange_byte(0xFE)  # データトークン
        for b in data:
            sd.exchange_byte(b)
        sd.exchange_byte(0x00)  # CRC1
        sd.exchange_byte(0x00)  # CRC2
        # データレスポンスを待つ
        data_resp = 0xFF
        for _ in range(16):
            data_resp = sd.exchange_byte(0xFF)
            if data_resp != 0xFF:
                break
        assert (data_resp & 0x1F) == 0x05  # accepted
        # ビジー解除待ち
        for _ in range(8):
            sd.exchange_byte(0xFF)

    def _read_block_via_spi(self, sd, block):
        """SPI バイト交換でブロックを読み出す。"""
        r1 = send_cmd(sd, 17, arg=block)
        assert r1 == 0x00
        token = 0xFF
        for _ in range(20):
            token = sd.exchange_byte(0xFF)
            if token == 0xFE:
                break
        assert token == 0xFE
        return bytes(read_bytes(sd, 512))

    def test_read_initial_zero_then_write_then_overwrite(self):
        """初期値0確認 → 書込 → 別パターンで上書き → read 一致"""
        sd = SDCard()
        block = 7

        # 書込前 read: 初期値は全 0
        initial = self._read_block_via_spi(sd, block)
        assert initial == bytes(512)

        # パターン1を書き込む
        pat1 = bytes([i & 0xFF for i in range(512)])
        self._write_block_via_spi(sd, block, pat1)
        assert self._read_block_via_spi(sd, block) == pat1

        # パターン2で上書き
        pat2 = bytes([(255 - (i & 0xFF)) for i in range(512)])
        self._write_block_via_spi(sd, block, pat2)
        assert self._read_block_via_spi(sd, block) == pat2


# ------------------------------------------------------------------
# S3. 境界・異常系テスト (M2/M3 回帰)
# ------------------------------------------------------------------

class TestBoundaryCases:
    """範囲外アクセス・イメージ長保証のテスト"""

    def test_cmd17_out_of_range_returns_zero_512(self):
        """範囲外 CMD17 はデータトークン後に 0 埋め 512 バイトを返す"""
        sd = SDCard(size_blocks=16)  # 16 ブロックのみ
        # ブロック 100 (範囲外) を読む
        r1 = send_cmd(sd, 17, arg=100)
        assert r1 == 0x00
        token = 0xFF
        for _ in range(20):
            token = sd.exchange_byte(0xFF)
            if token == 0xFE:
                break
        assert token == 0xFE
        data = bytes(read_bytes(sd, 512))
        assert len(data) == 512
        assert data == bytes(512)  # 全 0

    def test_cmd24_out_of_range_keeps_image_intact(self):
        """範囲外 CMD24 はイメージ長を変えず、他ブロックも破壊しない"""
        sd = SDCard(size_blocks=16)
        orig_len = len(sd._image)

        # ブロック 3 にマーカーを書いておく
        marker = bytes([0xAA] * 512)
        sd.write_block(3, marker)

        # 範囲外ブロック 100 へ CMD24 書込
        r1 = send_cmd(sd, 24, arg=100)
        assert r1 == 0x00
        sd.exchange_byte(0xFE)
        for i in range(512):
            sd.exchange_byte(i & 0xFF)
        sd.exchange_byte(0x00)
        sd.exchange_byte(0x00)
        # 応答処理を進める
        for _ in range(8):
            sd.exchange_byte(0xFF)

        # イメージ長は不変
        assert len(sd._image) == orig_len
        # ブロック 3 のマーカーは破壊されていない
        assert sd.read_block(3) == marker

    def test_write_block_short_data_normalized(self):
        """512 未満のデータでも write_block はイメージ長を変えない"""
        sd = SDCard(size_blocks=16)
        orig_len = len(sd._image)
        sd.write_block(2, bytes([0x11, 0x22, 0x33]))  # 3 バイトのみ
        assert len(sd._image) == orig_len
        blk = sd.read_block(2)
        assert len(blk) == 512
        assert blk[:3] == bytes([0x11, 0x22, 0x33])
        assert blk[3:] == bytes(509)  # 残りは 0 埋め

    def test_write_block_oversize_data_truncated(self):
        """512 超のデータは切り詰められ、隣接ブロックを侵さない"""
        sd = SDCard(size_blocks=16)
        orig_len = len(sd._image)
        sd.write_block(2, bytes([0x55] * 600))  # 600 バイト
        assert len(sd._image) == orig_len
        # ブロック 2 は 0x55 で埋まる
        assert sd.read_block(2) == bytes([0x55] * 512)
        # ブロック 3 は破壊されない(全 0)
        assert sd.read_block(3) == bytes(512)

    def test_read_block_out_of_range_returns_zero(self):
        """範囲外 read_block は 0 埋め 512 バイトを返す"""
        sd = SDCard(size_blocks=4)
        assert sd.read_block(1000) == bytes(512)
        assert sd.read_block(-1) == bytes(512)


# ------------------------------------------------------------------
# B. ブリッジ統合テスト (Python 側ビットバンギング)
# ------------------------------------------------------------------

# ポートB ビット定義 (SD-DOS 準拠)
_CLK  = 0x01   # bit0
_MOSI = 0x02   # bit1
_CS   = 0x04   # bit2 (負論理)
_LED  = 0x08   # bit3

# CS 非選択時のポートB デフォルト値 (CS=1, LED=1, CLK=0, MOSI=0)
_PB_DESEL = _CS | _LED


def _spi_select(pc: PC8001) -> None:
    """CS を 1→0 に落として SPI 選択する。"""
    # まず CS=1 の状態を確定
    pc._io_out(0xFD, _PB_DESEL)
    # CS=0 (選択)
    pc._io_out(0xFD, _LED)  # CS=0, LED=1, CLK=0, MOSI=0


def _spi_deselect(pc: PC8001) -> None:
    """CS を 0→1 に上げて SPI 非選択にする。"""
    pc._io_out(0xFD, _PB_DESEL)


def spi_byte(pc: PC8001, out_byte: int) -> int:
    """
    SD-DOS MMC_1WR/1RD 相当のビットバンギング。
    CS 選択中に 8 ビットを MSB ファーストで送受信して受信バイトを返す。
    """
    recv = 0
    # CS=0 を維持したベース値 (LED=1, CS=0)
    base = _LED  # bit3=1, bit2=0(CS選択)

    for i in range(8):
        mosi_bit = (out_byte >> (7 - i)) & 1
        # CLK=0, MOSI セット
        pb_lo = base | (mosi_bit << 1)
        pc._io_out(0xFD, pb_lo)
        # CLK=1
        pb_hi = pb_lo | _CLK
        pc._io_out(0xFD, pb_hi)
        # MISO サンプル (CLK=1 の後で読む)
        miso = (pc._io_in(0xFE) >> 4) & 1
        recv = (recv << 1) | miso

    return recv & 0xFF


class TestBridgeIntegration:
    """8255 SD ブリッジ + SDCard 統合テスト"""

    def _setup(self):
        """PC8001 + SDCard をセットアップして返す。"""
        pc = PC8001()
        sd = SDCard()
        pc.attach_sd(sd)
        # 8255 制御ワード設定 (SD-DOS 準拠: 0x88)
        pc._io_out(0xFF, 0x88)
        return pc, sd

    def test_cmd0_via_bridge(self):
        """ブリッジ経由で CMD0 を送り R1==0x01 を得る"""
        pc, sd = self._setup()

        # CS 選択
        _spi_select(pc)

        # CMD0 フレーム送信 (6 バイト)
        frame = [0x40, 0x00, 0x00, 0x00, 0x00, 0x95]
        for b in frame:
            spi_byte(pc, b)

        # R1 を最大 16 バイト 0xFF 送って待つ
        r1 = 0xFF
        for _ in range(16):
            r = spi_byte(pc, 0xFF)
            if (r & 0x80) == 0:
                r1 = r
                break

        _spi_deselect(pc)
        assert r1 == 0x01

    def test_ppi_b_latch(self):
        """IN 0xFD はポートB の出力ラッチ値を返す"""
        pc = PC8001()
        pc._io_out(0xFD, 0xAB)
        assert pc._io_in(0xFD) == 0xAB

    def test_ppi_c_miso_bit(self):
        """IN 0xFE の bit4 に MISO が反映される"""
        pc, sd = self._setup()
        # MISO=1 の初期状態確認
        assert pc._miso == 1
        pc._miso = 0
        assert (pc._io_in(0xFE) & 0x10) == 0
        pc._miso = 1
        assert (pc._io_in(0xFE) & 0x10) != 0

    def test_spi_cs_deselect_resets_state(self):
        """CS 非選択時に SPI 状態がリセットされる"""
        pc, sd = self._setup()
        _spi_select(pc)
        # ビット転送を途中まで行う
        pc._io_out(0xFD, _LED | _MOSI)        # CLK=0
        pc._io_out(0xFD, _LED | _MOSI | _CLK) # CLK=1 (1 ビット転送)
        assert pc._spi_bitcnt == 1
        # CS 非選択でリセット
        _spi_deselect(pc)
        assert pc._spi_bitcnt == 0

    def test_full_init_sequence_via_bridge(self):
        """
        ブリッジ経由で CMD0→CMD8→CMD55→ACMD41→CMD58 の
        初期化シーケンスを実行し、各 R1 を確認する。
        """
        pc, sd = self._setup()

        def bridge_send_cmd(cmd, arg=0, crc=0x95):
            _spi_select(pc)
            frame = [
                0x40 | cmd,
                (arg >> 24) & 0xFF,
                (arg >> 16) & 0xFF,
                (arg >> 8)  & 0xFF,
                arg & 0xFF,
                crc,
            ]
            for b in frame:
                spi_byte(pc, b)
            r1 = 0xFF
            for _ in range(16):
                r = spi_byte(pc, 0xFF)
                if (r & 0x80) == 0:
                    r1 = r
                    break
            _spi_deselect(pc)
            return r1

        assert bridge_send_cmd(0, crc=0x95) == 0x01
        assert bridge_send_cmd(8, arg=0x000001AA, crc=0x87) == 0x01
        assert bridge_send_cmd(55) == 0x01
        assert bridge_send_cmd(41, arg=0x40000000) == 0x00
        assert bridge_send_cmd(58) == 0x00

    def test_clk_ignored_when_cs_high(self):
        """CS=1 (非選択) のまま CLK を叩いても SPI 交換されない"""
        pc, sd = self._setup()
        # CS=1 を維持(非選択)
        pc._io_out(0xFD, _PB_DESEL)
        # CLK を複数回トグル(CS=1 のまま)
        for _ in range(8):
            pc._io_out(0xFD, _PB_DESEL)          # CLK=0
            pc._io_out(0xFD, _PB_DESEL | _CLK)   # CLK=1
        # ビットカウントが進んでいない = 交換されていない
        assert pc._spi_bitcnt == 0
        # SD 側も IDLE のまま(コマンド受信していない)
        from emu.sdcard import _ST_IDLE
        assert sd._state == _ST_IDLE

    def test_cmd24_round_trip_via_bridge(self):
        """ブリッジ経由で CMD24 書込 → CMD17 読出の往復が一致する"""
        pc, sd = self._setup()
        block = 9
        write_data = [(i * 3) & 0xFF for i in range(512)]

        # --- CMD24 書込 ---
        _spi_select(pc)
        for b in cmd_frame(24, arg=block):
            spi_byte(pc, b)
        # R1 待ち
        r1 = 0xFF
        for _ in range(16):
            r = spi_byte(pc, 0xFF)
            if (r & 0x80) == 0:
                r1 = r
                break
        assert r1 == 0x00
        # データトークン + 512 + CRC2
        spi_byte(pc, 0xFE)
        for b in write_data:
            spi_byte(pc, b)
        spi_byte(pc, 0x00)
        spi_byte(pc, 0x00)
        # データレスポンス待ち
        data_resp = 0xFF
        for _ in range(16):
            r = spi_byte(pc, 0xFF)
            if r != 0xFF:
                data_resp = r
                break
        assert (data_resp & 0x1F) == 0x05
        # ビジー解除待ち
        for _ in range(8):
            spi_byte(pc, 0xFF)
        _spi_deselect(pc)

        # --- CMD17 読出 ---
        _spi_select(pc)
        for b in cmd_frame(17, arg=block):
            spi_byte(pc, b)
        r1 = 0xFF
        for _ in range(16):
            r = spi_byte(pc, 0xFF)
            if (r & 0x80) == 0:
                r1 = r
                break
        assert r1 == 0x00
        # データトークン待ち
        token = 0xFF
        for _ in range(20):
            token = spi_byte(pc, 0xFF)
            if token == 0xFE:
                break
        assert token == 0xFE
        read_data = [spi_byte(pc, 0xFF) for _ in range(512)]
        _spi_deselect(pc)

        assert read_data == write_data
