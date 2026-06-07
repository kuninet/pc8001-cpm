;==============================================================
; PC-8001 CP/M ブートローダ
; doc/設計/02_ブートシーケンス.md 準拠
;
;   配置: 拡張ROM 0x6000-0x7FFF (8KB)
;   org 0x6000 で ROM 全体を配置する。
;
;   配置アドレス・LBAは Makefile から -D で受領する(数値直書きを排除):
;     BIOS_ORG, CCP_ORG, BDOS_ORG, BIOS_BLOCKS, CCP_LBA, BDOS_LBA
;   メモリ配置は単一パラメータ BIOS_BLOCKS から導出される。
;
;   ブートフロー(例 BIOS_BLOCKS=9):
;     1. 0x6000(ROM): E2 bit4=1 → 拡張RAM書込許可
;     2. SD初期化(自前SDドライバ内蔵)
;     3. SDからCP/M本体読込:
;          BIOS  LBA 0-8  (9ブロック=4608B) → 0xE100
;          CCP   LBA 9-12 (4ブロック=2048B) → 0xCB00
;          BDOS  LBA 13-19(7ブロック=3584B) → 0xD300
;     4. ゼロページ初期化
;        0x0000: JP (BIOS_ORG+3)  (WBOOT)
;        0x0005: JP (BDOS_ORG+6)  (BDOSエントリ)
;     5. ROM切替コードを 0x8200 へコピー
;     6. JP 0x8200 → OUT(E2),0x11; JP BIOS_ORG
;
;   SDドライバはBIOS実装(#33)の独立コピー。
;   8255 ポート割当(SD-DOS互換):
;     PA=0xFC, PB=0xFD, PC=0xFE, CTL=0xFF
;     PB bit0=CLK, bit1=MOSI, bit2=CS(負論理), bit3=LED
;     PC bit4=MISO
;
; ビルド:
;   asl -o build/loader.p src/loader/loader.asm
;   p2bin build/loader.p build/loader.bin -r '$6000-$7fff'
;   p2hex build/loader.p build/loader.hex
;==============================================================
	cpu	z80

;--------------------------------------------------------------
; 定数定義
;--------------------------------------------------------------
; PC8001-MEM 制御
E2_PORT		equ	0E2h
E2_WE		equ	10h		; bit4=1: 拡張RAM書込許可
E2_HIDE_ROM	equ	11h		; bit0=1 + bit4=1: ROM隠蔽

; CP/M 配置アドレス(-D 未指定時のデフォルト = BIOS_BLOCKS=9 相当)
	ifndef	BIOS_ORG
BIOS_ORG	equ	0E100h
	endif
	ifndef	CCP_ORG
CCP_ORG	equ	0CB00h
	endif
	ifndef	BDOS_ORG
BDOS_ORG	equ	0D300h
	endif
	ifndef	BIOS_BLOCKS
BIOS_BLOCKS	equ	9
	endif
	ifndef	CCP_LBA
CCP_LBA	equ	9
	endif
	ifndef	BDOS_LBA
BDOS_LBA	equ	13
	endif

BIOS_ADDR	equ	BIOS_ORG
CCP_ADDR	equ	CCP_ORG
BDOS_ADDR	equ	BDOS_ORG

; LBAレイアウト(BIOS_BLOCKS から導出)
BIOS_LBA_START	equ	0
CCP_LBA_START	equ	CCP_LBA
CCP_BLOCKS	equ	4		; 4 × 512 = 2048B
BDOS_LBA_START	equ	BDOS_LBA
BDOS_BLOCKS	equ	7		; 7 × 512 = 3584B

; RAM上の一時バッファ(本体RAM 0x8000以上)
LD_BUF		equ	09100h		; SDブロック読込バッファ(512B, 0x9100-0x92FF)
LD_BLK_CNT	equ	09300h		; ブロックカウンタ(1バイト, LD_BUF直後)
LD_CMD_RAM	equ	09301h		; RAMコマンドフレーム(6バイト, R/W可能)
LD_CCS_RAM	equ	09307h		; CCS保存(1バイト, LD_CMD_RAM直後)
LD_SWITCH	equ	08200h		; ROM切替コード配置アドレス

; 8255ポート
PPI_CTL		equ	0FFh
PPI_PB		equ	0FDh
PPI_PC		equ	0FEh

;==============================================================
; MAIN: 0x6000 エントリポイント
;==============================================================
	org	06000h

LOADER_START:
	; スタックは本体RAM上に設定(ROM切替に影響されない)
	LD	SP, 09000h

	; E3=バンク0 を明示初期化(リセット時0だが防御的に)
	XOR	A
	OUT	(0E3h), A

	; E2 bit4=1: 拡張RAM書込許可(bit0=0でROM可視のまま)
	LD	A, E2_WE
	OUT	(E2_PORT), A

	; SD初期化
	CALL	LD_SD_INIT
	JR	C, LOADER_HANG		; 失敗: 停止

	; BIOSをSDから読込 → BIOS_ORG
	LD	HL, BIOS_ADDR		; 書込先アドレス
	LD	DE, BIOS_LBA_START	; 開始LBA(上位=D=0, 下位=E=0)
	LD	B, BIOS_BLOCKS		; ブロック数
	CALL	LD_READ_BLOCKS
	JR	C, LOADER_HANG

	; CCPをSDから読込 → CCP_ORG
	LD	HL, CCP_ADDR
	LD	DE, CCP_LBA_START
	LD	B, CCP_BLOCKS
	CALL	LD_READ_BLOCKS
	JR	C, LOADER_HANG

	; BDOSをSDから読込 → BDOS_ORG
	LD	HL, BDOS_ADDR
	LD	DE, BDOS_LBA_START
	LD	B, BDOS_BLOCKS
	CALL	LD_READ_BLOCKS
	JR	C, LOADER_HANG

	; ゼロページ初期化(E2 bit4=1 で書込可能)
	; 0x0000-0x0002: JP (BIOS_ORG+3) (WBOOT)
	LD	HL, 0000h
	LD	(HL), 0C3h			; JP
	INC	HL
	LD	(HL), (BIOS_ORG+3)&0FFh		; WBOOT 低バイト
	INC	HL
	LD	(HL), ((BIOS_ORG+3)>>8)&0FFh	; WBOOT 高バイト
	; 0x0005-0x0007: JP (BDOS_ORG+6) (BDOSエントリ)
	LD	HL, 0005h
	LD	(HL), 0C3h			; JP
	INC	HL
	LD	(HL), (BDOS_ORG+6)&0FFh		; BDOSエントリ 低バイト
	INC	HL
	LD	(HL), ((BDOS_ORG+6)>>8)&0FFh	; BDOSエントリ 高バイト

	; ROM切替コードを本体RAM(0x8200)へコピー
	; コード: OUT (E2),0x11; JP BIOS_ORG
	LD	HL, LD_SWITCH_CODE	; コピー元(ROM上)
	LD	DE, LD_SWITCH		; コピー先(本体RAM 0x8200)
	LD	BC, LD_SWITCH_SIZE
	LDIR

	; 本体RAMのROM切替コードへジャンプ
	JP	LD_SWITCH

LOADER_HANG:
	; SD初期化失敗または読込失敗: 無限ループで停止
	JR	LOADER_HANG

;==============================================================
; ROM切替コードテンプレート(ROM上に置いておき0x8200へコピーして使う)
; 0x8200 で実行されるため、ROM(0x6000)が隠れても安全
;==============================================================
LD_SWITCH_CODE:
	LD	A, E2_HIDE_ROM		; 0x11: ROM隠蔽 + 書込許可
	OUT	(E2_PORT), A		; OUT (0xE2), 0x11
	JP	BIOS_ADDR		; JP BIOS_ORG
LD_SWITCH_END:
LD_SWITCH_SIZE	equ	LD_SWITCH_END - LD_SWITCH_CODE

;==============================================================
; LD_READ_BLOCKS: 複数SDブロックを連続読込してRAMへ展開
;   入力:
;     HL = 書込先アドレス
;     DE = 開始LBA16 (D=上位8bit, E=下位8bit; LBA < 0x10000)
;     B  = 読込ブロック数
;   出力: CY=0 成功 / CY=1 失敗
;   破壊: AF, BC, DE, HL, IX, IY
;
;   注意: LD_SD_READ_BLOCK 内の LD_SEND_CMD が B を破壊するため、
;         ループカウンタをRAM(LD_BLK_CNT)に保存する。
;==============================================================
LD_READ_BLOCKS:
	PUSH	IY			; IY を保存

	; 書込先を IX に保存、LBA を IY に保存
	PUSH	HL
	POP	IX			; IX = 書込先アドレス

	PUSH	DE
	POP	IY			; IY = LBA16 (IYH=D, IYL=E)

	; ブロックカウンタをRAMへ保存
	LD	A, B
	LD	(LD_BLK_CNT), A

LD_RB_LOOP:
	; LBA を DE:HL に設定
	; DE = 0(LBA上位16bit), HL = LBA下位16bit(IYをスタック経由でHL取得)
	PUSH	IY
	POP	HL			; HL = IY(LBA16)
	LD	D, 0
	LD	E, 0
	CALL	LD_SD_READ_BLOCK	; LD_BUF にブロック読込
	JR	C, LD_RB_FAIL

	; LD_BUF を書込先(IX)へ512バイトコピー
	LD	HL, LD_BUF
	PUSH	IX
	POP	DE			; DE = 書込先
	LD	BC, 512
	LDIR

	; IX を512進める(LDIRでDEが進んでいる)
	PUSH	DE
	POP	IX

	; LBA++ (IY インクリメント)
	INC	IY

	; ブロックカウンタをデクリメント、残りがあればループ
	LD	A, (LD_BLK_CNT)
	DEC	A
	LD	(LD_BLK_CNT), A
	JR	NZ, LD_RB_LOOP

	POP	IY
	OR	A			; CY=0
	RET

LD_RB_FAIL:
	POP	IY
	SCF				; CY=1
	RET

;==============================================================
; 以下、SDドライバ(BIOS #33の独立コピー)
; ポート割当・プロトコルはbios.asm SDドライバと同一。
;==============================================================

;--------------------------------------------------------------
; LD_SD_INIT_PORTS: 8255初期化
;--------------------------------------------------------------
LD_SD_INIT_PORTS:
	LD	A, 88h
	OUT	(PPI_CTL), A		; 8255制御ワード
	LD	A, 0FFh
	OUT	(PPI_PB), A		; PB初期値(CS=High, CLK=High)
	RET

;--------------------------------------------------------------
; LD_SD_CS_LOW: CS アサート(bit2=0)
;--------------------------------------------------------------
LD_SD_CS_LOW:
	IN	A, (PPI_PB)
	AND	11111011B
	OUT	(PPI_PB), A
	RET

;--------------------------------------------------------------
; LD_SD_CS_HIGH: CS ネゲート(bit2=1)
;--------------------------------------------------------------
LD_SD_CS_HIGH:
	IN	A, (PPI_PB)
	OR	00000100B
	OUT	(PPI_PB), A
	RET

;--------------------------------------------------------------
; LD_SPI_OUT: 1バイト送受信(MSBファースト, SPI mode0)
;   入力: C = 送信バイト
;   出力: A = 受信バイト, D = 受信バイト
;   破壊: AF, D
;--------------------------------------------------------------
LD_SPI_OUT:
	PUSH	BC
	PUSH	HL
	IN	A, (PPI_PB)
	AND	11111100B		; CLK=0, MOSI=0
	LD	L, A
	LD	D, 0
	LD	H, 8
LD_SPI_LOOP:
	LD	A, L
	AND	11111101B		; MOSI=0クリア
	SLA	C
	JR	NC, LD_SPI_CLK0
	OR	00000010B		; MOSI=1
LD_SPI_CLK0:
	OUT	(PPI_PB), A
	LD	L, A
	OR	00000001B		; CLK=1
	OUT	(PPI_PB), A
	SLA	D
	IN	A, (PPI_PC)
	AND	00010000B		; bit4=MISO
	JR	Z, LD_SPI_MISO0
	INC	D
LD_SPI_MISO0:
	LD	A, L
	AND	11111110B		; CLK=0
	OUT	(PPI_PB), A
	LD	L, A
	DEC	H
	JR	NZ, LD_SPI_LOOP
	LD	A, D
	POP	HL
	POP	BC
	RET

;--------------------------------------------------------------
; LD_SPI_IN: 0xFF送信して1バイト受信
;--------------------------------------------------------------
LD_SPI_IN:
	PUSH	BC
	LD	C, 0FFh
	CALL	LD_SPI_OUT
	POP	BC
	RET

;--------------------------------------------------------------
; LD_SEND_CMD: 6バイトコマンドフレーム送信
;   入力: HL = コマンド構造体ポインタ
;   破壊: AF, B, C, D, HL
;--------------------------------------------------------------
LD_SEND_CMD:
	LD	B, 6
LD_SCMD_LOOP:
	LD	C, (HL)
	CALL	LD_SPI_OUT
	INC	HL
	DJNZ	LD_SCMD_LOOP
	RET

;--------------------------------------------------------------
; LD_POLL_R1: R1レスポンスポーリング(最大16回)
;   出力: A = R1, CY=0 成功 / CY=1 タイムアウト
;--------------------------------------------------------------
LD_POLL_R1:
	PUSH	BC
	LD	B, 16
LD_PR1_LOOP:
	CALL	LD_SPI_IN
	BIT	7, A
	JR	Z, LD_PR1_OK
	DJNZ	LD_PR1_LOOP
	POP	BC
	LD	A, 0FFh
	SCF
	RET
LD_PR1_OK:
	POP	BC
	OR	A
	RET

;--------------------------------------------------------------
; LD_SD_INIT: SD初期化
;   出力: CY=0 成功 / CY=1 失敗
;--------------------------------------------------------------
LD_SD_INIT:
	CALL	LD_SD_INIT_PORTS

	; CCSをRAMへ0初期化(SDSCデフォルト)
	XOR	A
	LD	(LD_CCS_RAM), A

	; CS=High で 74クロック以上ダミー送信
	LD	B, 10
LD_INIT_DUMMY:
	CALL	LD_SPI_IN
	DJNZ	LD_INIT_DUMMY

	CALL	LD_SD_CS_LOW

	; CMD0: GO_IDLE_STATE
	LD	HL, LD_CMD0
	CALL	LD_SEND_CMD
	CALL	LD_POLL_R1
	JR	C, LD_INIT_FAIL
	CP	01h
	JR	NZ, LD_INIT_FAIL

	; CMD8: SEND_IF_COND
	LD	HL, LD_CMD8
	CALL	LD_SEND_CMD
	CALL	LD_POLL_R1
	JR	C, LD_INIT_FAIL
	CP	01h
	JR	NZ, LD_INIT_FAIL
	; R7 4バイト受信: 最後が0xAAか確認
	CALL	LD_SPI_IN
	CALL	LD_SPI_IN
	CALL	LD_SPI_IN
	CALL	LD_SPI_IN
	CP	0AAh
	JR	NZ, LD_INIT_FAIL

	; ACMD41ループ(最大256回)
	LD	B, 0
LD_ACMD41_LOOP:
	; CMD55
	LD	HL, LD_CMD55
	CALL	LD_SEND_CMD
	CALL	LD_POLL_R1
	JR	C, LD_INIT_FAIL
	; ACMD41
	LD	HL, LD_CMD41
	CALL	LD_SEND_CMD
	CALL	LD_POLL_R1
	JR	C, LD_INIT_FAIL
	CP	00h
	JR	Z, LD_ACMD41_DONE
	DJNZ	LD_ACMD41_LOOP
	JR	LD_INIT_FAIL

LD_ACMD41_DONE:
	; CMD58: READ_OCR(CCS確認)
	LD	HL, LD_CMD58
	CALL	LD_SEND_CMD
	CALL	LD_POLL_R1
	JR	C, LD_INIT_FAIL
	CP	00h
	JR	NZ, LD_INIT_FAIL
	CALL	LD_SPI_IN		; OCR[0]
	PUSH	AF
	CALL	LD_SPI_IN
	CALL	LD_SPI_IN
	CALL	LD_SPI_IN
	POP	AF			; OCR[0]
	AND	40h			; CCS bit
	LD	(LD_CCS_RAM), A		; RAMに保存(ROM書き込みは反映されないため)

	; CCS=0 なら CMD16 でブロック長512設定
	JR	NZ, LD_INIT_OK
	LD	HL, LD_CMD16
	CALL	LD_SEND_CMD
	CALL	LD_POLL_R1

LD_INIT_OK:
	CALL	LD_SD_CS_HIGH
	CALL	LD_SPI_IN		; CS=High 後の8クロックダミー(R2)
	XOR	A
	RET

LD_INIT_FAIL:
	CALL	LD_SD_CS_HIGH
	CALL	LD_SPI_IN		; CS=High 後の8クロックダミー(R2)
	SCF
	RET

;--------------------------------------------------------------
; LD_SD_READ_BLOCK: ブロック読込
;   入力: DE:HL = LBA32(DE=上位, HL=下位)
;   出力: CY=0 成功(データはLD_BUFへ) / CY=1 失敗
;   破壊: AF, BC, DE, HL
;
;   注意: ROM上にコマンドarg領域を置けない(bit0=0でROMが読まれる)ため
;         コマンドフレームをRAM(LD_CMD_RAM)に組み立てて送信する。
;         SDSC(CCS=0)カードはバイトアドレッシングのため LBA×512 が必要。
;--------------------------------------------------------------
LD_SD_READ_BLOCK:
	; SDSC(CCS=0)時は LBA×512(=左9bitシフト)してバイトアドレスに変換
	; ※ LBA<128 のとき DE:HL の左9bitシフトは 24bit に収まる
	LD	A, (LD_CCS_RAM)
	OR	A
	JR	NZ, LD_RB_ADDR_OK	; CCS≠0(SDHC): LBAそのまま

	; SDSC: DE:HL を左9bit(×512)シフト
	; 9bit シフト = 1bitシフト×9。
	; 32bit左1シフト: SLA L; RL H; RL E; RL D を 9回。
	LD	B, 9
LD_RB_SHIFT:
	SLA	L
	RL	H
	RL	E
	RL	D
	DJNZ	LD_RB_SHIFT

LD_RB_ADDR_OK:
	; CMD17フレームをRAM(LD_CMD_RAM)に組み立てる
	; [0]=0x51(CMD17), [1-4]=arg(32bit big-endian, DE=上位, HL=下位), [5]=0xFF
	PUSH	IX
	LD	IX, LD_CMD_RAM
	LD	(IX+0), 051h		; CMD17
	LD	(IX+1), D		; arg[0] = 上位上位
	LD	(IX+2), E		; arg[1] = 上位下位
	LD	(IX+3), H		; arg[2] = 下位上位
	LD	(IX+4), L		; arg[3] = 下位下位
	LD	(IX+5), 0FFh		; CRC(ダミー)
	POP	IX

	CALL	LD_SD_CS_LOW

	; CMD17送信(RAM上のフレームを使用)
	LD	HL, LD_CMD_RAM
	CALL	LD_SEND_CMD
	CALL	LD_POLL_R1
	JR	C, LD_RB_FAIL2
	CP	00h
	JR	NZ, LD_RB_FAIL2

	; データトークン 0xFE 待ち(最大64回)
	LD	B, 64
LD_RB_TOKEN:
	CALL	LD_SPI_IN
	CP	0FEh
	JR	Z, LD_RB_DATA
	DJNZ	LD_RB_TOKEN
	JR	LD_RB_FAIL2

LD_RB_DATA:
	; 512バイト受信 → LD_BUF
	LD	HL, LD_BUF
LD_RB_RECV:
	CALL	LD_SPI_IN
	LD	(HL), A
	INC	HL
	; HL == LD_BUF + 512 になったら終了
	LD	A, H
	CP	((LD_BUF + 512) >> 8) & 0FFh
	JR	NZ, LD_RB_RECV
	LD	A, L
	CP	(LD_BUF + 512) & 0FFh
	JR	NZ, LD_RB_RECV

	; CRC 2バイト読み捨て
	CALL	LD_SPI_IN
	CALL	LD_SPI_IN

	CALL	LD_SD_CS_HIGH
	CALL	LD_SPI_IN		; CS=High 後の8クロックダミー(R2)
	XOR	A
	RET

LD_RB_FAIL2:
	CALL	LD_SD_CS_HIGH
	CALL	LD_SPI_IN		; CS=High 後の8クロックダミー(R2)
	SCF
	RET

;--------------------------------------------------------------
; SDコマンドフレーム
;--------------------------------------------------------------
LD_CMD0:
	DB	40h, 00h, 00h, 00h, 00h, 95h

LD_CMD8:
	DB	48h, 00h, 00h, 01h, 0AAh, 87h

LD_CMD55:
	DB	77h, 00h, 00h, 00h, 00h, 0FFh

LD_CMD41:
	DB	69h, 40h, 00h, 00h, 00h, 0FFh

LD_CMD58:
	DB	7Ah, 00h, 00h, 00h, 00h, 0FFh

LD_CMD16:
	DB	50h, 00h, 00h, 02h, 00h, 0FFh

; CMD17はLD_SD_READ_BLOCKでRAM(LD_CMD_RAM)に動的組み立てするためROM不要

	end
