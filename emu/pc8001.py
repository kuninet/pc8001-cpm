"""
PC-8001 ハードウェア層エミュレータ
設計参照: doc/設計/01_メモリマップ.md
"""

import z80

# キー押下がアクティブローかどうか(未押下=1, 押下=0)
KEY_PRESSED_IS_LOW: bool = True

# VRAMベースアドレス(テキストVRAM: 0xF300-0xFEB7)
VRAM_BASE: int = 0xF300
# VRAMの行あたりバイト数(表示80 + 属性40 = 120バイト)
VRAM_ROW_BYTES: int = 120


class PC8001:
    """
    PC-8001無印 + PC8001-MEM ハードウェア層。
    - 下位32KB(0x0000-0x7FFF): PC8001-MEM供給(4バンク×32KB RAM or N-BASIC ROM)
    - 上位32KB(0x8000-0xFFFF): 本体メインRAM
    """

    def __init__(self, rom: bytes | None = None) -> None:
        # 本体メインRAM(0x8000-0xFFFF)
        self.main_ram: bytearray = bytearray(0x8000)
        # PC8001-MEM 拡張RAM(4バンク × 32KB)
        self.bank_ram: list[bytearray] = [bytearray(0x8000) for _ in range(4)]
        # ROM(N-BASIC ROM相当 0x0000-0x7FFF)
        raw = bytes(rom) if rom else b'\xff' * 0x8000
        # 長さを0x8000に正規化
        if len(raw) >= 0x8000:
            self.rom: bytearray = bytearray(raw[:0x8000])
        else:
            self.rom = bytearray(raw) + bytearray(0x8000 - len(raw))

        # PC8001-MEM 制御レジスタ
        # e2 bit0: 読出選択(0=ROM, 1=拡張RAM)
        # e2 bit4: 拡張RAM書込許可
        self.e2: int = 0x00
        # e3 bit1-0: バンク選択(リセット時バンク0)
        self.e3: int = 0x00

        # I/Oポート記録
        self.port40: int = 0x00          # 表示制御ポート
        self.port30: int = 0x00          # 汎用ポート
        self.crtc_param: list[int] = []  # CRTCパラメータ(ポート0x50)
        self.crtc_cmd: list[int] = []    # CRTCコマンド(ポート0x51)
        self.io_out_log: dict[int, int] = {}  # その他OUTログ(最終値のみ)
        # μPD8257 DMA(Ch2)送出列を順序付きで記録(ポート0x64/0x65/0x68)。
        # 同一ポートへの複数バイト送出(アドレス下位→上位等)を回帰検証するため。
        self.dma_log: list[tuple[int, int]] = []  # (ポート, 値)

        # キーボードマトリクス(0x00-0x09行、未押下=0xFF)
        self.kbd_rows: list[int] = [0xFF] * 10

        # 8255 SD ブリッジ (SD-DOS 互換, ベースアドレス 0xFC)
        # ポートB=0xFD: bit0=CLK, bit1=MOSI, bit2=CS(負論理), bit3=LED
        # ポートC=0xFE: bit4=MISO(入力)
        self.sd = None                   # 接続中の SDCard (None=未接続)
        self.ppi_b: int = 0xFF           # ポートB 出力ラッチ
        self.ppi_ctl: int = 0x00         # 8255 制御ワード
        self._spi_bitcnt: int = 0        # 現在のビット転送カウント(0-7)
        self._spi_in: int = 0            # 受信シフトレジスタ
        self._spi_out: int = 0xFF        # 送信シフトレジスタ(現バイト)
        self._spi_next_out: int = 0xFF   # 次に送出するバイト(SD から取得済み)
        self._miso: int = 1              # 現在の MISO 値 (bit)
        self._prev_cs: int = 1           # 前回の CS 状態

        # Z80 CPU生成・コールバック登録
        self.cpu: z80.Z80Machine = z80.Z80Machine()
        self.cpu.set_read_callback(self._mem_read)
        self.cpu.set_write_callback(self._mem_write)
        self.cpu.set_input_callback(self._io_in)
        self.cpu.set_output_callback(self._io_out)

    # ------------------------------------------------------------------
    # メモリコールバック
    # ------------------------------------------------------------------

    def _mem_read(self, addr: int) -> int:
        """メモリ読み出し。doc/設計/01_メモリマップ.md §2 準拠。"""
        if addr < 0x8000:
            if self.e2 & 0x01:
                # bit0=1: 拡張RAMを読む
                return self.bank_ram[self.e3][addr]
            else:
                # bit0=0: ROMを読む
                return self.rom[addr]
        else:
            return self.main_ram[addr - 0x8000]

    def _mem_write(self, addr: int, value: int) -> None:
        """メモリ書き込み。bit0(読出選択)とbit4(書込許可)は独立。"""
        v = value & 0xFF
        if addr < 0x8000:
            if self.e2 & 0x10:
                # bit4=1: 拡張RAMへ書込許可
                self.bank_ram[self.e3][addr] = v
            # bit4=0 の場合は書き込み無視(ROM/書込禁止)
        else:
            self.main_ram[addr - 0x8000] = v

    # ------------------------------------------------------------------
    # I/Oコールバック
    # ------------------------------------------------------------------

    def _io_out(self, port: int, value: int) -> None:
        """I/O OUT。上位8bitにはAレジスタが乗るため下位8bitで判定。"""
        p = port & 0xFF
        v = value & 0xFF
        if p == 0xE2:
            self.e2 = v
        elif p == 0xE3:
            self.e3 = v & 0x03
        elif p == 0x40:
            self.port40 = v
        elif p == 0x30:
            self.port30 = v
        elif p == 0x50:
            self.crtc_param.append(v)
        elif p == 0x51:
            self.crtc_cmd.append(v)
        elif p in (0x64, 0x65, 0x68):
            # μPD8257 DMA(Ch2): 送出順を保持して記録(最終値も io_out_log に)
            self.dma_log.append((p, v))
            self.io_out_log[p] = v
        elif p == 0xFF:
            # 8255 制御ワード
            self.ppi_ctl = v
        elif p == 0xFD:
            # 8255 ポートB (SD SPI 制御線)
            self._ppi_b_write(v)
        elif p == 0xFC:
            # 8255 ポートA (ログのみ)
            self.io_out_log[p] = v
        elif p == 0xFE:
            # 8255 ポートC (ログのみ)
            self.io_out_log[p] = v
        else:
            self.io_out_log[p] = v

    def _io_in(self, port: int) -> int:
        """I/O IN。下位8bitで判定。"""
        p = port & 0xFF
        if 0x00 <= p <= 0x09:
            # キーボードマトリクス行
            return self.kbd_rows[p]
        elif p == 0x50:
            # CRTCステータス(スタブ)
            return 0x00
        elif p == 0x51:
            return 0x00
        elif p == 0xFD:
            # 8255 ポートB: 出力ラッチ値を返す (read-modify-write 対応)
            return self.ppi_b
        elif p == 0xFE:
            # 8255 ポートC: bit4 = MISO
            return (self._miso & 1) << 4
        elif p == 0xFF:
            # 8255 制御レジスタ読み出し (スタブ)
            return 0xFF
        else:
            return 0xFF

    # ------------------------------------------------------------------
    # 8255 SD ブリッジ
    # ------------------------------------------------------------------

    def attach_sd(self, sdcard) -> None:
        """SDCard を 8255 SPI ブリッジに接続する。"""
        self.sd = sdcard

    def _ppi_b_write(self, v: int) -> None:
        """
        ポートB(0xFD)書き込み処理。SPI ビットバンギングを模擬する。
        bit0=CLK, bit1=MOSI, bit2=CS(負論理: 0=選択), bit3=LED(無視)
        """
        v &= 0xFF

        # 更新前の状態を取得
        prev_clk = self.ppi_b & 0x01
        prev_cs  = self._prev_cs

        cs   = (v >> 2) & 1   # 0=選択, 1=非選択
        clk  = v & 1
        mosi = (v >> 1) & 1

        # CS 立下り (1→0): SPI 状態リセット・選択開始
        if prev_cs == 1 and cs == 0:
            self._spi_bitcnt  = 0
            self._spi_in      = 0
            self._spi_out     = 0xFF
            self._spi_next_out = 0xFF
            self._miso        = 1

        # CS 立上り (0→1): リセット
        elif prev_cs == 0 and cs == 1:
            self._spi_bitcnt = 0
            self._spi_in     = 0
            if self.sd is not None:
                self.sd.cs_deassert()

        # CLK 立上り (0→1) かつ CS 選択中 かつ SD 接続済み
        if prev_clk == 0 and clk == 1 and cs == 0 and self.sd is not None:
            # 新バイト先頭(bitcnt==0)なら次バイトを送信シフトレジスタへ
            if self._spi_bitcnt == 0:
                self._spi_out = self._spi_next_out

            # MISO: 送信シフトレジスタの MSB を出力
            self._miso    = (self._spi_out >> 7) & 1
            self._spi_out = (self._spi_out << 1) & 0xFF

            # MOSI: 受信シフトレジスタに取り込む
            self._spi_in = ((self._spi_in << 1) | mosi) & 0xFF
            self._spi_bitcnt += 1

            # 8 ビット揃ったら SD に渡し、次の送出バイトを取得
            if self._spi_bitcnt == 8:
                self._spi_next_out = self.sd.exchange_byte(self._spi_in)
                self._spi_bitcnt   = 0
                self._spi_in       = 0

        # ラッチ更新
        self.ppi_b  = v
        self._prev_cs = cs

    # ------------------------------------------------------------------
    # キーボード
    # ------------------------------------------------------------------

    def set_key_matrix(self, row: int, col: int, pressed: bool) -> None:
        """指定行・列のキービットを設定する。アクティブロー。"""
        assert 0 <= row <= 9
        assert 0 <= col <= 7
        if KEY_PRESSED_IS_LOW:
            if pressed:
                self.kbd_rows[row] &= ~(1 << col) & 0xFF
            else:
                self.kbd_rows[row] |= (1 << col) & 0xFF
        else:
            if pressed:
                self.kbd_rows[row] |= (1 << col) & 0xFF
            else:
                self.kbd_rows[row] &= ~(1 << col) & 0xFF

    def clear_keys(self) -> None:
        """全キーを未押下状態(0xFF)にリセット。"""
        self.kbd_rows = [0xFF] * 10

    # ------------------------------------------------------------------
    # VRAM / テキスト表示ヘルパ
    # ------------------------------------------------------------------

    def read_vram(self, offset: int, length: int) -> bytes:
        """
        テキストVRAM(0xF300基準)からデータを読む。
        main_ram上のオフセット = 0xF300 - 0x8000 = 0x7300
        """
        base = 0x7300  # 0xF300 - 0x8000
        return bytes(self.main_ram[base + offset: base + offset + length])

    def screen_text(self, rows: int = 25, cols: int = 80) -> str:
        """
        テキストVRAMの内容を文字列として返す(デバッグ/テスト用)。
        各行 120バイト構成(表示80 + 属性40)。先頭80バイトを表示文字として使用。
        0x20-0x7E はASCII文字そのまま、それ以外は '.' に変換。
        """
        lines: list[str] = []
        for r in range(rows):
            row_data = self.read_vram(r * VRAM_ROW_BYTES, cols)
            chars = []
            for b in row_data:
                if 0x20 <= b <= 0x7E:
                    chars.append(chr(b))
                else:
                    chars.append('.')
            lines.append(''.join(chars))
        return '\n'.join(lines)

    # ------------------------------------------------------------------
    # CPU 操作
    # ------------------------------------------------------------------

    def load(self, addr: int, data: bytes, bank: int | None = None) -> None:
        """
        メモリへデータを直接書き込むテスト用ローダ。
        addr < 0x8000: bank_ram[bank or e3] へ書き込む
        addr >= 0x8000: main_ram へ書き込む
        バンク跨ぎは考慮しない(テスト用途)。
        """
        b = bank if bank is not None else self.e3
        for i, byte in enumerate(data):
            a = addr + i
            if a < 0x8000:
                self.bank_ram[b][a] = byte & 0xFF
            else:
                self.main_ram[a - 0x8000] = byte & 0xFF

    def set_pc(self, addr: int) -> None:
        """CPUのプログラムカウンタを設定する。"""
        self.cpu.pc = addr

    def step(self, ticks: int = 1) -> None:
        """指定ティック数だけCPUを実行する。"""
        self.cpu.ticks_to_stop = ticks
        self.cpu.run()

    def run(self, max_ticks: int) -> None:
        """指定ティック数だけCPUを実行する。"""
        self.cpu.ticks_to_stop = max_ticks
        self.cpu.run()

    def run_until_halt(self, max_steps: int = 100000) -> bool:
        """
        HALT命令(0x76)に達するまでCPUを実行する。
        到達した場合True、max_steps超過でFalseを返す。
        小刻みに実行しHALT検出を行う。
        """
        # 1ステップあたり約100ティックで回す
        ticks_per_step = 100
        for _ in range(max_steps):
            if self.cpu.halted:
                return True
            # PCのバイトがHALT(0x76)かどうか確認
            if self._mem_read(self.cpu.pc) == 0x76:
                return True
            self.cpu.ticks_to_stop = ticks_per_step
            self.cpu.run()
        # 最終チェック
        return self.cpu.halted
