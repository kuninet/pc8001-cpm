"""
SDカード SPI スレーブモデル (SDHC, ブロックアドレッシング)
SD-DOS / PC-8001_SD 向けビットバンギングドライバとの疎通を想定。
"""

from __future__ import annotations


# SDCard 内部状態
_ST_IDLE       = 0   # コマンド待ち
_ST_CMD        = 1   # コマンドバイト受信中
_ST_WRITE_WAIT = 2   # WRITE: データトークン 0xFE 待ち
_ST_WRITE_DATA = 3   # WRITE: 512+2 バイト受信中


class SDCard:
    """
    SDHC SPI スレーブモデル。
    exchange_byte(in_byte) で 1 バイトずつ全二重転送を模擬する。
    """

    def __init__(
        self,
        image: bytearray | None = None,
        size_blocks: int = 33024,
    ) -> None:
        # ブロックイメージ (bytearray, バイト単位アクセス)
        if image is not None:
            self._image: bytearray = image
        else:
            self._image = bytearray(size_blocks * 512)

        # 内部状態
        self._state: int = _ST_IDLE
        self._cmd_buf: list[int] = []
        # 送信キュー (int のリスト)
        self._tx: list[int] = []
        # ACMD フラグ: CMD55 直後のみ True
        self._app_cmd: bool = False

        # WRITE コマンド用の書き込み先ブロック番号
        self._write_arg: int = 0
        # WRITE データ受信バッファ
        self._write_buf: list[int] = []

    # ------------------------------------------------------------------
    # 内部ヘルパ
    # ------------------------------------------------------------------

    def read_block(self, n: int) -> bytes:
        """
        ブロック n を読み出す。常に正確に 512 バイトを返す。
        範囲外(イメージ未割当領域)は 0 埋めした 512 バイトを返す。
        """
        offset = n * 512
        if n < 0 or offset + 512 > len(self._image):
            # 範囲外: 0 埋めブロックを返す
            return bytes(512)
        return bytes(self._image[offset: offset + 512])

    def write_block(self, n: int, data: bytes | bytearray) -> None:
        """
        ブロック n に 512 バイトを書き込む。
        - イメージ長は絶対に変えない(スライス代入の伸縮を防止)。
        - n が範囲外なら書き込みをスキップする。
        - data は 512 バイトに正規化(不足は 0 埋め、超過は切り詰め)。
        """
        offset = n * 512
        if n < 0 or offset + 512 > len(self._image):
            # 範囲外: 書き込みスキップ(イメージ非破壊)
            return
        # 512 バイトに正規化
        data512 = bytearray(512)
        src = data[:512]
        data512[: len(src)] = src
        # 長さ不変を保証するスライス代入(両辺とも 512)
        self._image[offset: offset + 512] = data512

    def _enqueue(self, data: list[int] | bytes) -> None:
        """送信キューにバイト列を追加する。"""
        for b in data:
            self._tx.append(b & 0xFF)

    def _dequeue(self) -> int:
        """送信キューから 1 バイト取り出す。キューが空なら 0xFF。"""
        if self._tx:
            return self._tx.pop(0)
        return 0xFF

    # ------------------------------------------------------------------
    # コマンド処理
    # ------------------------------------------------------------------

    def _process_command(self, cmd_buf: list[int]) -> None:
        """
        6 バイトのコマンドフレームを解釈し、応答を _tx に積む。
        """
        raw_cmd = cmd_buf[0] & 0x3F
        arg = (
            (cmd_buf[1] << 24)
            | (cmd_buf[2] << 16)
            | (cmd_buf[3] << 8)
            | cmd_buf[4]
        )

        # ACMD 判定: CMD55 直後で cmd==41 のとき ACMD41 とする
        is_acmd = self._app_cmd
        # CMD55 以外のコマンドは _app_cmd をリセット
        if raw_cmd != 55:
            self._app_cmd = False

        if raw_cmd == 0:
            # CMD0: GO_IDLE_STATE → R1=0x01
            self._enqueue([0x01])

        elif raw_cmd == 8:
            # CMD8: SEND_IF_COND → R1=0x01 + R7 4 バイト (echo 0x1AA)
            self._enqueue([0x01, 0x00, 0x00, 0x01, 0xAA])

        elif raw_cmd == 55:
            # CMD55: APP_CMD → R1=0x01, 次コマンドを ACMD として扱う
            self._app_cmd = True
            self._enqueue([0x01])

        elif raw_cmd == 41 and is_acmd:
            # ACMD41: SD_SEND_OP_COND → R1=0x00 (初期化完了)
            self._enqueue([0x00])

        elif raw_cmd == 58:
            # CMD58: READ_OCR → R1=0x00 + OCR 4 バイト (CCS=1)
            self._enqueue([0x00, 0xC0, 0xFF, 0x80, 0x00])

        elif raw_cmd == 16:
            # CMD16: SET_BLOCKLEN → 無視、R1=0x00
            self._enqueue([0x00])

        elif raw_cmd == 17:
            # CMD17: READ_SINGLE_BLOCK
            # R1=0x00, Ncr ギャップ (数バイト 0xFF), データトークン 0xFE, 512 バイト, CRC 2 バイト
            block_data = self.read_block(arg)
            # Ncr ギャップ: 1 バイト 0xFF
            response = [0x00, 0xFF, 0xFE]
            response.extend(block_data)
            response.extend([0x00, 0x00])  # CRC (ダミー)
            self._enqueue(response)

        elif raw_cmd == 24:
            # CMD24: WRITE_SINGLE_BLOCK
            # R1=0x00 をキューに積み、WRITE_WAIT 状態へ遷移
            self._enqueue([0x00])
            self._write_arg = arg
            self._write_buf = []
            self._state = _ST_WRITE_WAIT
            return  # 状態遷移後に IDLE には戻さない

        else:
            # 未対応コマンド: illegal command
            self._enqueue([0x04])

        # 通常は IDLE へ
        self._state = _ST_IDLE

    def cs_deassert(self) -> None:
        """
        CS が非選択 (CS=1) になったときに呼ぶ。
        送信キューと途中の状態をリセットする。
        WRITE_WAIT/WRITE_DATA 中は中断扱い。
        _app_cmd フラグは保持する (CMD55→CS高→CS低→ACMD41 に対応)。
        """
        self._tx.clear()
        self._cmd_buf = []
        self._write_buf = []
        self._state = _ST_IDLE

    # ------------------------------------------------------------------
    # 全二重バイト交換 (SPI スレーブ)
    # ------------------------------------------------------------------

    def exchange_byte(self, in_byte: int) -> int:
        """
        全二重 SPI 1 バイト交換。
        - 送信バイトは _tx から独立に取り出す(あれば先頭、無ければ 0xFF)。
        - 受信バイト(in_byte)は毎回必ず受信 state machine に通す。
        - 新規コマンド先頭((in_byte & 0xC0)==0x40)を検出したら、
          残っている stale な _tx を破棄して新コマンドを開始する。
        これにより CS を Low に保ったままの連続コマンドが正しく動く。
        """
        in_byte &= 0xFF

        # 送信は受信処理に先立ち、現時点のキュー先頭を取り出す(1 バイト遅延応答)
        out_byte = self._dequeue()

        # 受信 state machine
        self._feed_rx(in_byte)

        return out_byte

    def _feed_rx(self, in_byte: int) -> None:
        """受信バイトを state machine に通す(送信は行わない)。"""
        if self._state == _ST_IDLE:
            # コマンド先頭検出: 上位 2 ビット = 0b01
            if (in_byte & 0xC0) == 0x40:
                # stale 応答を破棄して新コマンド開始
                self._tx.clear()
                self._cmd_buf = [in_byte]
                self._state = _ST_CMD
            # IDLE で非コマンドバイトは無視
            return

        if self._state == _ST_CMD:
            # コマンドフレーム継続(2-6 バイト目)
            self._cmd_buf.append(in_byte)
            if len(self._cmd_buf) == 6:
                self._process_command(self._cmd_buf)
            return

        if self._state == _ST_WRITE_WAIT:
            # データトークン 0xFE を待つ(先頭コマンド検出はしない)
            if in_byte == 0xFE:
                self._write_buf = []
                self._state = _ST_WRITE_DATA
            return

        if self._state == _ST_WRITE_DATA:
            # 512 + CRC 2 = 514 バイト受信
            self._write_buf.append(in_byte)
            if len(self._write_buf) == 514:
                # 512 バイトを書き込む
                self.write_block(self._write_arg, bytes(self._write_buf[:512]))
                # データレスポンス: 0x05 (accepted), ビジー 0x00 x2, 0xFF
                self._enqueue([0x05, 0x00, 0x00, 0xFF])
                self._state = _ST_IDLE
            return
