;==============================================================
; PC-8001 CP/M 2.2 BIOS
; doc/設計/03_BIOS構成.md, doc/設計/04_コンソール.md 準拠
;   - 配置/CCP/BDOSアドレス・LBAはすべて Makefile から -D で受領する
;     (origin=BIOS_ORG, CCP_ORG, BDOS_ORG, CCP_LBA, BDOS_LBA)。
;   - 数値直書きを排除し、メモリ配置は BIOS_BLOCKS の単一パラメータから導出。
;   - ジャンプテーブル: 17エントリ × 3バイト = 51バイト
;   - コンソール: テキストVRAM 0F300h(固定), ADM-3A相当
;
; ビルド(例 BIOS_BLOCKS=9):
;   asl -D origin=0E100h -D CCP_ORG=0CB00h -D BDOS_ORG=0D300h \
;       -D CCP_LBA=9 -D BDOS_LBA=13 -o build/bios.p src/bios/bios.asm
;   p2bin build/bios.p build/bios.bin -r '$E100-$F2FF'
;==============================================================
	cpu	z80

; --- 配置パラメータ(未指定時のデフォルト = BIOS_BLOCKS=9 相当)---
	ifndef	origin
origin	equ	0E100h
	endif
	ifndef	CCP_ORG
CCP_ORG	equ	0CB00h
	endif
	ifndef	BDOS_ORG
BDOS_ORG	equ	0D300h
	endif
	ifndef	CCP_LBA
CCP_LBA	equ	9
	endif
	ifndef	BDOS_LBA
BDOS_LBA	equ	13
	endif

; BIOS専用スタック。テキストVRAM(0xF300-0xFEB7)の上の空き領域に置く。
;   CLS が VRAM全域(0xF300-0xFEB7)をクリアするため、スタックはその上(0xFF00)に
;   退避する。CCP/BDOS/BIOS本体のいずれも破壊しない安全領域。
BIOS_STACK	equ	0FF00h

	org	origin

;--------------------------------------------------------------
; ジャンプテーブル (各エントリ JP nn = 3バイト)
; vec(n) = origin + 3*n
;--------------------------------------------------------------
	JP	BOOT		; 0: BOOT   コールドブート
	JP	WBOOT		; 1: WBOOT  ウォームブート
	JP	CONST		; 2: CONST  コンソール入力状態
	JP	CONIN		; 3: CONIN  コンソール1文字入力
	JP	CONOUT		; 4: CONOUT コンソール1文字出力
	JP	LIST		; 5: LIST   プリンタ出力(ダミー)
	JP	PUNCH		; 6: PUNCH  パンチ出力(ダミー)
	JP	READER		; 7: READER リーダ入力(ダミー)
	JP	HOME		; 8: HOME   トラック0シーク
	JP	SELDSK		; 9: SELDSK ディスク選択
	JP	SETTRK		; 10: SETTRK トラック設定
	JP	SETSEC		; 11: SETSEC セクタ設定
	JP	SETDMA		; 12: SETDMA DMAアドレス設定
	JP	READ		; 13: READ   セクタ読込
	JP	WRITE		; 14: WRITE  セクタ書込
	JP	LISTST		; 15: LISTST プリンタ状態
	JP	SECTRAN		; 16: SECTRAN 論理→物理セクタ変換

;--------------------------------------------------------------
; BOOT: コールドブート (origin+0x33 固定)
;   スタックポインタ設定 → CRTC初期化 → VRAMクリア → サインオン → CCPへ
;   ※ BOOT本体は WBOOT の後(BOOT_BODY)に配置
;      ジャンプテーブル直後のアドレス制約(origin+0x33 / origin+0x45)を守るため
;   スタックは VRAM領域(BIOS_STACK)を使う。実機ではBOOT時点でCCP/BDOSが
;   ロード済みのため、それらを破壊しない VRAM領域に退避する。
;--------------------------------------------------------------
BOOT:					; == origin+0x33 ==
	LD	SP, BIOS_STACK		; スタックポインタ設定 (3B: origin+0x33..0x35)
	JP	BOOT_BODY		; BOOT本体へ (3B: origin+0x36..0x38)
	NOP				; パディング (1B: origin+0x39)
	NOP				; パディング (1B: origin+0x3A)
	NOP				; パディング (1B: origin+0x3B)
	NOP				; パディング (1B: origin+0x3C)
	NOP				; パディング (1B: origin+0x3D)
	NOP				; パディング (1B: origin+0x3E)
	NOP				; パディング (1B: origin+0x3F)
	NOP				; パディング (1B: origin+0x40)
	NOP				; パディング (1B: origin+0x41)
	NOP				; パディング (1B: origin+0x42)
	NOP				; パディング (1B: origin+0x43)
	NOP				; パディング (1B: origin+0x44)

;--------------------------------------------------------------
; WBOOT: ウォームブート (origin+0x45 固定)
;   CCP/BDOS を SD から再ロードしてゼロページを再設定、CCP へジャンプ
;--------------------------------------------------------------
WBOOT:					; == origin+0x45 ==
	JP	WBOOT_BODY		; WBOOT本体へ (3B: origin+0x45..0x47)

;--------------------------------------------------------------
; BOOT_BODY: BOOT本体 (WBOOTの後に配置)
;   CRTC初期化 → VRAMクリア → サインオン → RST7スタブ設置 → HALT
;--------------------------------------------------------------
BOOT_BODY:
	; CRTC 初期化スタブ
	; TODO: ICW SCREEN FORMAT 詳細は実機確認 (#39 でCP-3確認)
	; 現状の OUT (0x51), 0x00 のみでは実機μPD3301の正しい初期化に
	; ならない可能性が高い(SCREEN FORMAT パラメータ未送出)。
	; 80桁モード切替/カーソル設定/DMA設定/OCWは未対応。
	; 実機/MAME での検証は #39 (CP-3) で確定する。
	DI				; 割り込み禁止
	LD	A, 00h
	OUT	(51h), A		; ICW: RESET (スタブ値)

	; VRAM クリア
	CALL	CLS

	; RST7スタブ設置
	CALL	INSTALL_RST7_STUB

	; サインオン文字列出力
	LD	HL, SIGNON
BOOT_LOOP:
	LD	A, (HL)
	OR	A
	JR	Z, BOOT_DONE
	LD	C, A
	CALL	CONOUT
	INC	HL
	JR	BOOT_LOOP
BOOT_DONE:
	; ローダがゼロページ/CCP/BDOSをロード済み、WBOOTがワーク初期化を行う前提で
	; BOOT は CCP(CCP_ORG)へ直接ジャンプする。
	JP	CCP_ORG

;--------------------------------------------------------------
; CONST: コンソール入力状態確認
;   戻り: A=0FFh(有り) / A=00h(無し)
;--------------------------------------------------------------
CONST:
	LD	A, (KEYBUF)
	OR	A
	JR	NZ, CONST_HAVE
	CALL	SCAN_KBD
	LD	(KEYBUF), A
	OR	A
	JR	Z, CONST_NONE
CONST_HAVE:
	LD	A, 0FFh
	RET
CONST_NONE:
	XOR	A
	RET

;--------------------------------------------------------------
; CONIN: コンソール1文字入力
;   戻り: A = 入力文字(ブロッキング)
;--------------------------------------------------------------
CONIN:
	LD	A, (KEYBUF)
	OR	A
	JR	NZ, CONIN_GOT
CONIN_WAIT:
	CALL	SCAN_KBD
	OR	A
	JR	Z, CONIN_WAIT
	LD	(KEYBUF), A
CONIN_GOT:
	LD	A, (KEYBUF)
	PUSH	AF
	XOR	A
	LD	(KEYBUF), A
	POP	AF
	RET

;--------------------------------------------------------------
; CONOUT: コンソール1文字出力 (ADM-3A 状態機械)
;   入力: C = 出力文字
;--------------------------------------------------------------
CONOUT:
	PUSH	AF
	PUSH	BC
	PUSH	DE
	PUSH	HL

	LD	A, C

	; 現在の ESC 状態を確認
	LD	B, A			; B = 文字を保存
	LD	A, (ESC_STATE)
	OR	A
	JP	Z, CONOUT_NORMAL	; 状態0: 通常
	CP	1
	JP	Z, CONOUT_ESC1		; 状態1: ESC受信済
	CP	2
	JP	Z, CONOUT_ESC_ROW	; 状態2: row待ち
	; 状態3: col待ち
	LD	A, B
	SUB	20h
	CP	50h			; 80(=0x50)以上は不正値
	JR	NC, CONOUT_ESC_BAD	; 範囲外: 座標更新せず状態0復帰
	LD	(CUR_COL), A
	XOR	A
	LD	(ESC_STATE), A
	JP	CONOUT_EXIT
CONOUT_ESC_BAD:
	; ESC '=' の row/col 範囲外: 座標は変更せず状態0復帰
	XOR	A
	LD	(ESC_STATE), A
	JP	CONOUT_EXIT

CONOUT_ESC_ROW:
	; 状態2: row受信
	LD	A, B
	SUB	20h
	CP	19h			; 25(=0x19)以上は不正値
	JR	NC, CONOUT_ESC_BAD	; 範囲外: 座標更新せず状態0復帰
	LD	(CUR_ROW), A
	LD	A, 3
	LD	(ESC_STATE), A
	JP	CONOUT_EXIT

CONOUT_ESC1:
	; 状態1: ESC受信済 → '='(0x3D)なら状態2
	LD	A, B
	CP	3Dh
	JR	NZ, CONOUT_ESC_DROP
	LD	A, 2
	LD	(ESC_STATE), A
	JP	CONOUT_EXIT
CONOUT_ESC_DROP:
	XOR	A
	LD	(ESC_STATE), A
	JP	CONOUT_EXIT

CONOUT_NORMAL:
	LD	A, B

	; 制御コード分岐
	CP	07h			; BEL
	JP	Z, CONOUT_BEL
	CP	08h			; BS
	JP	Z, CONOUT_BS
	CP	0Ah			; LF
	JP	Z, CONOUT_LF
	CP	0Dh			; CR
	JP	Z, CONOUT_CR
	CP	1Ah			; CLR
	JP	Z, CONOUT_CLR
	CP	1Bh			; ESC
	JP	Z, CONOUT_ESC_SET
	CP	1Eh			; HOME
	JP	Z, CONOUT_HOME

	; 0x20未満の未対応制御文字は無視
	CP	20h
	JP	C, CONOUT_EXIT
	; 0x7F は無視
	CP	7Fh
	JP	Z, CONOUT_EXIT

	; 表示文字 (0x20-0x7E および 0x80以上)
	CALL	PUT_CHAR
	JP	CONOUT_EXIT

CONOUT_BEL:
	; BEL: ビープ音スタブ
	; TODO: ポート0x40 操作でビープ (#39 で実機確認)
	JP	CONOUT_EXIT

CONOUT_BS:
	; BS: カーソル左
	LD	A, (CUR_COL)
	OR	A
	JP	Z, CONOUT_EXIT
	DEC	A
	LD	(CUR_COL), A
	JP	CONOUT_EXIT

CONOUT_LF:
	; LF: カーソル下(最下行以降はスクロール)
	; cur_row >= 24 でスクロール: 異常値(>24)でもVRAM外書込を防ぐ
	LD	A, (CUR_ROW)
	CP	18h			; 24
	JP	NC, CONOUT_DO_SCROLL
	INC	A
	LD	(CUR_ROW), A
	JP	CONOUT_EXIT
CONOUT_DO_SCROLL:
	CALL	SCROLL
	JP	CONOUT_EXIT

CONOUT_CR:
	; CR: 行頭へ
	XOR	A
	LD	(CUR_COL), A
	JP	CONOUT_EXIT

CONOUT_CLR:
	; CLR: 画面消去 + ホーム
	CALL	CLS
	XOR	A
	LD	(CUR_ROW), A
	LD	(CUR_COL), A
	JP	CONOUT_EXIT

CONOUT_ESC_SET:
	; ESC: 状態1へ
	LD	A, 1
	LD	(ESC_STATE), A
	JP	CONOUT_EXIT

CONOUT_HOME:
	; HOME: カーソルを(0,0)へ
	XOR	A
	LD	(CUR_ROW), A
	LD	(CUR_COL), A
	JP	CONOUT_EXIT		; 明示的JP(フォールスルー脆弱性回避)

CONOUT_EXIT:
	POP	HL
	POP	DE
	POP	BC
	POP	AF
	RET

;--------------------------------------------------------------
; PUT_CHAR: 現在カーソル位置に文字(B)を書き込みカーソル前進
;   入力: B = 文字(CONOUT から呼ぶ時点)
;   実際の文字は CONOUT_NORMAL から来るので A に入っている
;   PUT_CHAR 呼び出し前に B = 文字が保存されている
;--------------------------------------------------------------
PUT_CHAR:
	PUSH	AF
	PUSH	BC
	PUSH	DE
	PUSH	HL

	; VRAM アドレス計算
	CALL	CALC_VRAM_ADDR		; HL = 0xF300 + CUR_ROW*120 + CUR_COL
	LD	(HL), B			; 文字(B)をVRAMへ

	; カーソル前進
	LD	A, (CUR_COL)
	INC	A
	CP	50h			; 80(=0x50)
	JR	C, PUT_COL_OK		; col < 80 なら更新のみ
	; col >= 80: 行末 → 次行へ
	XOR	A
	LD	(CUR_COL), A
	LD	A, (CUR_ROW)
	CP	18h			; 24
	JR	Z, PUT_DO_SCROLL
	INC	A
	LD	(CUR_ROW), A
	JR	PUT_DONE
PUT_DO_SCROLL:
	CALL	SCROLL
	JR	PUT_DONE
PUT_COL_OK:
	LD	(CUR_COL), A
PUT_DONE:
	POP	HL
	POP	DE
	POP	BC
	POP	AF
	RET

;--------------------------------------------------------------
; CALC_VRAM_ADDR: (CUR_ROW, CUR_COL) から VRAM アドレスを計算
;   戻り: HL = 0xF300 + CUR_ROW * 120 + CUR_COL
;   破壊: AF, DE, HL
;   計算: row*120 = row*128 - row*8 (SBC HL,DE を使用)
;--------------------------------------------------------------
CALC_VRAM_ADDR:
	; HL = CUR_ROW * 128
	LD	A, (CUR_ROW)
	LD	L, A
	LD	H, 0			; HL = row
	ADD	HL, HL			; *2
	ADD	HL, HL			; *4
	ADD	HL, HL			; *8
	LD	D, H
	LD	E, L			; DE = row*8
	ADD	HL, HL			; *16
	ADD	HL, HL			; *32
	ADD	HL, HL			; *64
	ADD	HL, HL			; *128
	; HL = row*128, DE = row*8
	; HL = row*120 = row*128 - row*8
	OR	A			; キャリークリア (SCF;CCF より短く高速)
	SBC	HL, DE			; HL = row*120

	; HL += CUR_COL
	LD	A, (CUR_COL)
	LD	E, A
	LD	D, 0
	ADD	HL, DE

	; HL += 0xF300
	LD	DE, 0F300h
	ADD	HL, DE
	RET

;--------------------------------------------------------------
; CLS: 画面クリア
;   表示部(各行80B) = 0x20、アトリビュート部(各行40B) = 0x00
;   0xF300〜0xFEB7 (25行 × 120B = 3000B)
;   実装: 全3000バイトをまず 0x20 で埋め、
;          各行のアトリビュート40バイト(+80〜+119)を0x00で上書き
;--------------------------------------------------------------
CLS:
	PUSH	AF
	PUSH	BC
	PUSH	DE
	PUSH	HL

	; 全3000バイトを 0x20 で埋める (LDIR + 初期化バイトコピー方式)
	; DE を dst、HL を src(1バイトの0x20を先頭に置いて連鎖コピー)は複雑なので
	; 単純ループ: Bレジスタを使わずBC全体をカウンタに使う
	LD	HL, 0F300h
	LD	(HL), 20h		; 最初の1バイトを 0x20 で書く
	LD	D, H
	LD	E, L
	INC	DE			; DE = 0xF301
	LD	BC, 0BB7h		; 残り 3000-1 = 2999 = 0xBB7 バイト
	LDIR				; src=HL(0xF300), dst=DE(0xF301): 連鎖コピー

	; 各行のアトリビュート部(+80〜+119)を 0x00 で上書き
	; LDIR で 0xF350→0xF351, 0x40-1バイト を25行分
	LD	B, 25			; 25行
	LD	HL, 0F350h		; 行0のアトリビュート先頭 = 0xF300+80
CLS_ATTR_ROW:
	PUSH	BC
	PUSH	HL
	LD	(HL), 00h		; 先頭1バイトを0x00
	LD	D, H
	LD	E, L
	INC	DE
	LD	BC, 39			; 残り 40-1 = 39 バイト
	LDIR				; アトリビュート40バイトを 0x00 でクリア
	POP	HL
	; 次行のアトリビュート先頭 = 現在 HL + 120
	LD	DE, 120
	ADD	HL, DE
	POP	BC
	DJNZ	CLS_ATTR_ROW

	POP	HL
	POP	DE
	POP	BC
	POP	AF
	RET

;--------------------------------------------------------------
; SCROLL: ソフトウェアスクロール
;   行1〜24 を行0〜23 へコピー
;   最終行(行24)を空白+属性0でクリア
;--------------------------------------------------------------
SCROLL:
	PUSH	AF
	PUSH	BC
	PUSH	DE
	PUSH	HL

	; 行1〜24を行0〜23へコピー (24行×120B = 2880B)
	LD	HL, 0F378h		; 0xF300 + 120 = 行1先頭
	LD	DE, 0F300h		; 行0先頭
	LD	BC, 0B40h		; 24*120 = 2880 = 0xB40
	LDIR

	; 最終行(行24)をクリア: 0xF300 + 24*120 = 0xF300 + 0xB40 = 0xFE40
	LD	HL, 0FE40h
	; 表示80バイトを 0x20 で
	LD	B, 80
	LD	A, 20h
SCROLL_CHAR:
	LD	(HL), A
	INC	HL
	DJNZ	SCROLL_CHAR
	; アトリビュート40バイトを 0x00 で
	LD	B, 40
	XOR	A
SCROLL_ATTR:
	LD	(HL), A
	INC	HL
	DJNZ	SCROLL_ATTR

	POP	HL
	POP	DE
	POP	BC
	POP	AF
	RET

;--------------------------------------------------------------
; SCAN_KBD: キーボードマトリクス走査
;   行0〜9 を順にスキャン、押下キーをASCII変換して返す
;   戻り: A = ASCII文字 (0=押下なし)
;
;   ダミーマッピング(実機確定は要更新):
;     ASCII 0x20〜0x7E を 10行×8列=80キーへ線形マップ
;     行(0〜9)×8 + 列(0〜7) → ASCII = 0x20 + row*8 + col
;
;   SHIFT検出: 行0 bit6=0 で SHIFT押下
;   CTRL検出:  行0 bit7=0 で CTRL押下
;   ※ マトリクスダミー、実機確定は要更新
;--------------------------------------------------------------
SCAN_KBD:
	PUSH	BC
	PUSH	DE
	PUSH	HL

	; 行0を読んでSHIFT/CTRL状態を保存
	IN	A, (0)
	LD	D, A			; D = 行0の値(SHIFT/CTRL用)

	; 行0〜9 を全行スキャン (B=行番号, C=列番号)
	LD	B, 0			; B = 行番号

SCAN_NEXT_ROW:
	; 行Bの値を取得してSCAN_COL_LOOPへ
	CALL	SCAN_READ_ROW		; A = 行Bのキー状態

	; A = 行Bのキー状態(アクティブロー: 0=押下)
	LD	E, A			; E = 行の値
	LD	C, 0			; C = 列番号

SCAN_COL_LOOP:
	; 列Cのビットをチェック: bit C of E が0なら押下
	LD	A, E
	LD	H, C			; H = 列番号を保存
	; Cビット目を取り出す: A を C 回右回転
	OR	A
	JR	Z, SCAN_CHK_BIT
SCAN_ROT:
	RRCA
	DEC	H
	JR	NZ, SCAN_ROT
SCAN_CHK_BIT:
	AND	01h			; 1=未押下
	LD	H, C			; H = 列番号に戻す(Cは変えない)
	JR	NZ, SCAN_NEXT_COL

	; 押下検出: 行B, 列C → ASCII
	CALL	SCAN_TO_ASCII		; A = ASCII文字
	OR	A
	JR	Z, SCAN_NEXT_COL	; 変換結果0は修飾キー等→次の列へ

	; CTRL/SHIFT変換
	CALL	SCAN_MOD		; 修飾キー適用
	JR	SCAN_KBD_RET

SCAN_NEXT_COL:
	INC	C
	LD	A, C
	CP	8
	JR	C, SCAN_COL_LOOP

	INC	B
	LD	A, B
	CP	10
	JR	C, SCAN_NEXT_ROW

SCAN_KBD_NONE:
	XOR	A

SCAN_KBD_RET:
	POP	HL
	POP	DE
	POP	BC
	RET

;--------------------------------------------------------------
; SCAN_READ_ROW: 行B(0〜9)のポートを読む
;   戻り: A = 行Bのキー状態
;--------------------------------------------------------------
SCAN_READ_ROW:
	LD	A, B
	OR	A
	JR	NZ, SRR_1
	IN	A, (0)
	RET
SRR_1:	CP	1
	JR	NZ, SRR_2
	IN	A, (1)
	RET
SRR_2:	CP	2
	JR	NZ, SRR_3
	IN	A, (2)
	RET
SRR_3:	CP	3
	JR	NZ, SRR_4
	IN	A, (3)
	RET
SRR_4:	CP	4
	JR	NZ, SRR_5
	IN	A, (4)
	RET
SRR_5:	CP	5
	JR	NZ, SRR_6
	IN	A, (5)
	RET
SRR_6:	CP	6
	JR	NZ, SRR_7
	IN	A, (6)
	RET
SRR_7:	CP	7
	JR	NZ, SRR_8
	IN	A, (7)
	RET
SRR_8:	CP	8
	JR	NZ, SRR_9
	IN	A, (8)
	RET
SRR_9:	IN	A, (9)
	RET

;--------------------------------------------------------------
; SCAN_TO_ASCII: 行B/列Cからダミーマッピングで ASCII を計算
;   戻り: A = ASCII (0=範囲外)
;   行0の列6(SHIFT)/列7(CTRL)は修飾キー専用のため0を返す
;--------------------------------------------------------------
SCAN_TO_ASCII:
	; 行0の列6(SHIFT)/列7(CTRL)は修飾キー専用: 除外
	LD	A, B
	OR	A
	JR	NZ, STA_CALC	; 行0以外はそのまま計算
	LD	A, C
	CP	6
	JR	NC, STA_EXCL	; 列6以上(=6,7)は修飾キー→除外
STA_CALC:
	LD	A, B
	ADD	A, A
	ADD	A, A
	ADD	A, A		; A = B*8
	ADD	A, C		; A = B*8 + C
	ADD	A, 20h		; A = 0x20 + B*8 + C
	CP	7Fh
	JR	C, STA_OK	; < 0x7F → 有効
STA_EXCL:
	XOR	A		; 範囲外/修飾キー: 無効
STA_OK:
	RET

;--------------------------------------------------------------
; SCAN_MOD: CTRL/SHIFT 修飾を A に適用
;   入力: A = ASCII (0x20〜0x7E), D = 行0の値
;   戻り: A = 変換後 ASCII
;--------------------------------------------------------------
SCAN_MOD:
	; CTRL 確認: bit7=0 → CTRL押下
	BIT	7, D
	JR	NZ, SMOD_NO_CTRL
	; CTRL変換: アルファベットを 0x01〜0x1A に
	CP	61h
	JR	C, SMOD_CTRL_CHK
	CP	7Bh
	JR	NC, SMOD_CTRL_CHK
	SUB	20h		; 小文字→大文字
SMOD_CTRL_CHK:
	CP	41h
	RET	C		; < 'A': そのまま
	CP	5Bh
	RET	NC		; > 'Z': そのまま
	SUB	40h		; 0x41〜0x5A → 0x01〜0x1A
	RET

SMOD_NO_CTRL:
	; SHIFT 確認: bit6=0 → SHIFT押下
	BIT	6, D
	RET	NZ		; SHIFT未押下: そのまま
	; SHIFT: 大小文字トグル
	CP	61h
	JR	C, SMOD_UC_TO_LC
	CP	7Bh
	RET	NC		; 範囲外: そのまま
	SUB	20h		; 小文字→大文字
	RET
SMOD_UC_TO_LC:
	CP	41h
	RET	C		; 範囲外: そのまま
	CP	5Bh
	RET	NC
	ADD	A, 20h		; 大文字→小文字
	RET

;--------------------------------------------------------------
; LIST: プリンタ出力 (未使用ダミー)
;--------------------------------------------------------------
LIST:
	RET

;--------------------------------------------------------------
; PUNCH: パンチ出力 (未使用ダミー)
;--------------------------------------------------------------
PUNCH:
	RET

;--------------------------------------------------------------
; READER: リーダ入力 (未使用ダミー)
;   戻り: A=0x1A(EOF)
;--------------------------------------------------------------
READER:
	LD	A, 1Ah
	RET

SIGNON:
	DB	'PC-8001 CP/M 2.2 BIOS', 0

; カーソル位置 (行0-24, 列0-79)
CUR_ROW:
	DB	0
CUR_COL:
	DB	0

; ESC シーケンス状態 (0=通常, 1=ESC受信, 2=row待ち, 3=col待ち)
ESC_STATE:
	DB	0

; キー入力1文字バッファ (0=空, それ以外=有効文字)
KEYBUF:
	DB	0

;==============================================================
; ウォームブート補助ルーチン群
;   KEYBUF の後、SDブロックドライバ固定ジャンプテーブルの前に連続配置。
;   (配置アドレスは origin から連続レイアウトで決まる)
;==============================================================

;--------------------------------------------------------------
; INSTALL_RST7_STUB: RST7ベクタ(0x0038)に安全スタブを設置
;   割り込み発生時に即RETするスタブ(0xC9)を書き込む。
;   ゼロページ書込は拡張RAM有効(e2 bit0=1, bit4=1)前提。
;   破壊: AF
;--------------------------------------------------------------
INSTALL_RST7_STUB:
	PUSH	AF
	LD	A, 0C9h			; RET命令
	LD	(0038h), A		; RST7ベクタに書き込む
	POP	AF
	RET

;--------------------------------------------------------------
; FLUSH_DIRTY_BUF: ダーティバッファをSDに書き戻す
;   BUF_DIRTY != 0 なら DISK_FLUSH を呼ぶ。
;   破壊: AF
;--------------------------------------------------------------
FLUSH_DIRTY_BUF:
	PUSH	AF
	LD	A, (BUF_DIRTY)
	OR	A
	JR	Z, FDB_DONE
	CALL	DISK_FLUSH		; ディスク層の共通フラッシュ
FDB_DONE:
	POP	AF
	RET

;==============================================================
; SDブロックドライバ(#33)ジャンプテーブル
;   SD_INIT_VEC  : SD初期化
;   SD_READ_VEC  : ブロック読込
;   SD_WRITE_VEC : ブロック書込
;   (シンボル参照で呼ぶ。配置は origin から連続レイアウトで決まる)
;==============================================================

SD_INIT_VEC:
	JP	SD_INIT		; 0: SD初期化
SD_READ_VEC:
	JP	SD_READ_BLOCK	; 3: ブロック読込
SD_WRITE_VEC:
	JP	SD_WRITE_BLOCK	; 6: ブロック書込

;==============================================================
; SDブロックドライバ実装
;   8255 ポート割当 (SD-DOS互換):
;     PA=0xFC, PB=0xFD, PC=0xFE, CTL=0xFF
;     制御ワード = 0x88 (MODE0/A=IN/B=OUT/CH=IN/CL=OUT)
;     PB bit0=CLK, bit1=MOSI, bit2=CS(負論理), bit3=LED
;     PC bit4=MISO
;==============================================================

;--------------------------------------------------------------
; SD_INIT_PORTS: 8255初期化
;   CTLに0x88設定、PBを0xFF(CS=High, CLK=High, MOSI=High, LED=High)
;--------------------------------------------------------------
SD_INIT_PORTS:
	LD	A, 88h
	OUT	(0FFh), A		; 8255制御ワード設定
	LD	A, 0FFh
	OUT	(0FDh), A		; PB初期値(CS=High)
	RET

;--------------------------------------------------------------
; SD_CS_LOW: CS をアサート(bit2=0)
;--------------------------------------------------------------
SD_CS_LOW:
	IN	A, (0FDh)
	AND	11111011B		; bit2=0(CSアサート)
	OUT	(0FDh), A
	RET

;--------------------------------------------------------------
; SD_CS_HIGH: CS をネゲート(bit2=1)
;--------------------------------------------------------------
SD_CS_HIGH:
	IN	A, (0FDh)
	OR	00000100B		; bit2=1(CSネゲート)
	OUT	(0FDh), A
	RET

;--------------------------------------------------------------
; SD_SPI_OUT: 1バイト送受信(MSBファースト, SPI mode0)
;   入力: C = 送信バイト
;   出力: A = 受信バイト, D = 受信バイト(コピー)
;   破壊: AF, D
;   保存: BC, HL (PUSH/POP)
;   注意: CS=0 を維持したまま呼ぶこと
;--------------------------------------------------------------
SD_SPI_OUT:
	PUSH	BC
	PUSH	HL
	; ベース値をLに格納: CS/LED現在値を保持してCLK=0, MOSI=0
	IN	A, (0FDh)
	AND	11111100B		; CLK=0, MOSI=0 クリア(CS/LEDは保持)
	LD	L, A			; L = ベース値(CS=0, LED=1保持)
	LD	D, 0			; D = 受信シフトレジスタ
	LD	H, 8			; H = ビットカウンタ
SPI_OUT_LOOP:
	; MOSI bit設定: CのMSBをbit1へ(SLAでCYに取り出す)
	LD	A, L
	AND	11111101B		; MOSI=0クリア
	SLA	C			; MSBをCYへ, C左シフト(LSB=0)
	JR	NC, SPI_OUT_CLK0	; CY=0: MOSI=0
	OR	00000010B		; CY=1: MOSI=1
SPI_OUT_CLK0:
	; CLK=0 で書き出す(CLKはベース値で既に0)
	OUT	(0FDh), A
	LD	L, A			; L更新(MOSI含む)
	; CLK立上り
	OR	00000001B		; CLK=1
	OUT	(0FDh), A
	; MISO読み取り(CLK=1の後)
	SLA	D			; D左シフト(LSB=0)
	IN	A, (0FEh)
	AND	00010000B		; bit4=MISO, ZF: MISO=0ならZ
	JR	Z, SPI_OUT_MISO0	; MISO=0: Dのbit0=0のまま
	INC	D			; MISO=1: D.bit0=1
SPI_OUT_MISO0:
	; CLK立下り
	LD	A, L
	AND	11111110B		; CLK=0
	OUT	(0FDh), A
	LD	L, A			; L更新
	DEC	H
	JR	NZ, SPI_OUT_LOOP
	LD	A, D			; 受信バイトをAに
	POP	HL
	POP	BC
	RET

;--------------------------------------------------------------
; SD_SPI_IN: 0xFFを送って1バイト受信
;   出力: A = 受信バイト
;   保存: BC (LD C,0FFh で C を書き換えるので PUSH/POP で保護)
;--------------------------------------------------------------
SD_SPI_IN:
	PUSH	BC
	LD	C, 0FFh
	CALL	SD_SPI_OUT
	POP	BC
	RET

;--------------------------------------------------------------
; SD_SEND_CMD: コマンドフレーム送信
;   入力: HL = 6バイトコマンド構造体ポインタ
;         (cmd|0x40, arg3, arg2, arg1, arg0, crc)
;   破壊: AF, B, C, D, H, HL
;--------------------------------------------------------------
SD_SEND_CMD:
	LD	B, 6			; 6バイト送信
SEND_CMD_LOOP:
	LD	C, (HL)
	CALL	SD_SPI_OUT
	INC	HL
	DJNZ	SEND_CMD_LOOP
	RET


;--------------------------------------------------------------
; SD_INIT: SD カード初期化
;   出力: CY=0 成功 / CY=1 失敗
;   破壊: AF, BC, DE, HL
;--------------------------------------------------------------
SD_INIT:
	CALL	SD_INIT_PORTS

	; CS=High で 0xFF を 10回送信(74クロック以上のダミー)
	LD	B, 10
SD_INIT_DUMMY:
	CALL	SD_SPI_IN
	DJNZ	SD_INIT_DUMMY

	; CS=Low
	CALL	SD_CS_LOW

	; CMD0送信(GO_IDLE_STATE, CRC=0x95)
	LD	HL, SD_CMD0
	CALL	SD_SEND_CMD
	; R1ポーリング: 0x01を期待
	CALL	SD_POLL_R1
	JR	C, SD_INIT_FAIL		; タイムアウト
	CP	01h
	JR	NZ, SD_INIT_FAIL	; R1≠0x01

	; CMD8送信(SEND_IF_COND, arg=0x000001AA, CRC=0x87)
	LD	HL, SD_CMD8
	CALL	SD_SEND_CMD
	; R1ポーリング: 0x01を期待
	CALL	SD_POLL_R1
	JR	C, SD_INIT_FAIL
	CP	01h
	JR	NZ, SD_INIT_FAIL
	; R7 4バイト受信: 最後のバイトが0xAAか確認
	CALL	SD_SPI_IN		; R7[0]
	CALL	SD_SPI_IN		; R7[1]
	CALL	SD_SPI_IN		; R7[2]
	CALL	SD_SPI_IN		; R7[3] → A
	CP	0AAh
	JR	NZ, SD_INIT_FAIL

	; ACMD41ループ(最大256回)
	LD	B, 0			; Bカウンタ(0=256回)
SD_ACMD41_LOOP:
	; CMD55
	LD	HL, SD_CMD55
	CALL	SD_SEND_CMD
	CALL	SD_POLL_R1
	JR	C, SD_INIT_FAIL
	; ACMD41(arg=0x40000000)
	LD	HL, SD_CMD41
	CALL	SD_SEND_CMD
	CALL	SD_POLL_R1
	JR	C, SD_INIT_FAIL
	CP	00h
	JR	Z, SD_ACMD41_DONE	; R1==0x00: 初期化完了
	DJNZ	SD_ACMD41_LOOP
	JR	SD_INIT_FAIL		; タイムアウト

SD_ACMD41_DONE:
	; CMD58(READ_OCR)
	LD	HL, SD_CMD58
	CALL	SD_SEND_CMD
	CALL	SD_POLL_R1
	JR	C, SD_INIT_FAIL
	CP	00h
	JR	NZ, SD_INIT_FAIL
	; OCR 4バイト受信: 先頭バイトのbit6=CCS
	CALL	SD_SPI_IN		; OCR[0]
	PUSH	AF			; OCR[0]保存
	CALL	SD_SPI_IN		; OCR[1]
	CALL	SD_SPI_IN		; OCR[2]
	CALL	SD_SPI_IN		; OCR[3]
	POP	AF			; OCR[0]
	AND	40h			; bit6=CCS
	LD	(SD_CCS), A		; CCS保存(非0=SDHC)

	; CCS=0ならCMD16でブロック長512設定
	JR	NZ, SD_INIT_OK		; CCS≠0(SDHC): スキップ
	LD	HL, SD_CMD16
	CALL	SD_SEND_CMD
	CALL	SD_POLL_R1
	; CMD16失敗は無視(v1カードがない場合)

SD_INIT_OK:
	CALL	SD_CS_HIGH
	OR	A			; CY=0
	CCF				; CY=0確定(OR Aでクリアされているため不要だが明示)
	XOR	A			; A=0, CY=0
	RET

SD_INIT_FAIL:
	CALL	SD_CS_HIGH
	SCF				; CY=1
	RET

;--------------------------------------------------------------
; SD_POLL_R1: R1レスポンスをポーリング取得
;   最大16回試行、MSB=0のバイトを返す
;   出力: A = R1, CY=0 成功 / A=0xFF, CY=1 タイムアウト
;   破壊: AF
;--------------------------------------------------------------
SD_POLL_R1:
	PUSH	BC
	LD	B, 16
SD_PR1_LOOP:
	CALL	SD_SPI_IN
	BIT	7, A
	JR	Z, SD_PR1_OK		; bit7=0: 有効なR1
	DJNZ	SD_PR1_LOOP
	; タイムアウト
	POP	BC
	LD	A, 0FFh
	SCF				; CY=1
	RET
SD_PR1_OK:
	POP	BC
	OR	A			; CY=0
	RET

;--------------------------------------------------------------
; SD_READ_BLOCK: ブロック読込
;   入力: DE:HL = LBA(DE=上位16bit, HL=下位16bit)
;   出力: CY=0 成功(データはSD_BUFへ) / CY=1 失敗
;   破壊: AF, BC, HL
;--------------------------------------------------------------
SD_READ_BLOCK:
	; LBAをコマンドバッファに書き込む (IX使用)
	PUSH	IX
	LD	IX, SD_RD_ARG
	LD	(IX+0), D		; arg[0]=LBA上位上位
	LD	(IX+1), E		; arg[1]=LBA上位下位
	LD	(IX+2), H		; arg[2]=LBA下位上位
	LD	(IX+3), L		; arg[3]=LBA下位下位
	POP	IX

	CALL	SD_CS_LOW

	; CMD17(READ_SINGLE_BLOCK)送信
	LD	HL, SD_CMD17
	CALL	SD_SEND_CMD
	; R1チェック
	CALL	SD_POLL_R1
	JR	C, SD_RB_FAIL
	CP	00h
	JR	NZ, SD_RB_FAIL

	; データトークン 0xFE を待つ(最大64回)
	LD	B, 64
SD_RB_TOKEN_WAIT:
	CALL	SD_SPI_IN
	CP	0FEh
	JR	Z, SD_RB_DATA		; トークン受信
	DJNZ	SD_RB_TOKEN_WAIT
	JR	SD_RB_FAIL		; タイムアウト

SD_RB_DATA:
	; 512バイト受信 → SD_BUFへ(SDブロックサイズ=512固定 = 256×2)
	; SD_SPI_IN/OUT は BC・HL を保存するので B(DJNZ)/E をカウンタに使える。
	; アドレスの絶対値に依存しない(SD_BUF の配置が変わっても正しく512バイト)。
	LD	HL, SD_BUF
	LD	E, 2			; 外側: 256バイト × 2 = 512
SD_RB_RECV_OUTER:
	LD	B, 0			; 内側: 256回 (B=0 → 256)
SD_RB_RECV_LOOP:
	CALL	SD_SPI_IN
	LD	(HL), A
	INC	HL
	DJNZ	SD_RB_RECV_LOOP
	DEC	E
	JR	NZ, SD_RB_RECV_OUTER

	; CRC 2バイト読み捨て
	CALL	SD_SPI_IN
	CALL	SD_SPI_IN

	CALL	SD_CS_HIGH
	XOR	A			; CY=0
	RET

SD_RB_FAIL:
	CALL	SD_CS_HIGH
	SCF				; CY=1
	RET

;--------------------------------------------------------------
; SD_WRITE_BLOCK: ブロック書込
;   入力: DE:HL = LBA, データソースはSD_BUF
;   出力: CY=0 成功 / CY=1 失敗
;   破壊: AF, BC, HL
;--------------------------------------------------------------
SD_WRITE_BLOCK:
	; LBAをコマンドバッファに書き込む (IX使用)
	PUSH	IX
	LD	IX, SD_WR_ARG
	LD	(IX+0), D
	LD	(IX+1), E
	LD	(IX+2), H
	LD	(IX+3), L
	POP	IX

	CALL	SD_CS_LOW

	; CMD24(WRITE_SINGLE_BLOCK)送信
	LD	HL, SD_CMD24
	CALL	SD_SEND_CMD
	; R1チェック
	CALL	SD_POLL_R1
	JR	C, SD_WB_FAIL
	CP	00h
	JR	NZ, SD_WB_FAIL

	; データトークン 0xFE 送信
	LD	C, 0FEh
	CALL	SD_SPI_OUT

	; SD_BUFから512バイト送信(SDブロックサイズ=512固定 = 256×2)
	; SD_SPI_OUT は BC・HL を保存するので B(DJNZ)/E をカウンタに使える。
	; アドレスの絶対値に依存しない(SD_BUF の配置が変わっても正しく512バイト)。
	LD	HL, SD_BUF
	LD	E, 2			; 外側: 256バイト × 2 = 512
SD_WB_SEND_OUTER:
	LD	B, 0			; 内側: 256回 (B=0 → 256)
SD_WB_SEND_LOOP:
	LD	C, (HL)
	CALL	SD_SPI_OUT
	INC	HL
	DJNZ	SD_WB_SEND_LOOP
	DEC	E
	JR	NZ, SD_WB_SEND_OUTER

	; CRC 2バイト送信(ダミー)
	LD	C, 00h
	CALL	SD_SPI_OUT
	LD	C, 00h
	CALL	SD_SPI_OUT

	; データレスポンス受信(下位5bit=0b00101確認)
	CALL	SD_POLL_R1		; データレスポンスをポーリング
	JR	C, SD_WB_FAIL
	AND	1Fh			; 下位5bit
	CP	05h			; 0b00101 = accepted
	JR	NZ, SD_WB_FAIL

	; busy待ち(0xFFが来るまで最大256回)
	LD	B, 0
SD_WB_BUSY_WAIT:
	CALL	SD_SPI_IN
	CP	0FFh
	JR	Z, SD_WB_OK
	DJNZ	SD_WB_BUSY_WAIT
	JR	SD_WB_FAIL

SD_WB_OK:
	CALL	SD_CS_HIGH
	XOR	A			; CY=0
	RET

SD_WB_FAIL:
	CALL	SD_CS_HIGH
	SCF				; CY=1
	RET

;--------------------------------------------------------------
; SDコマンドフレームテーブル
;--------------------------------------------------------------
SD_CMD0:
	DB	40h, 00h, 00h, 00h, 00h, 95h	; CMD0: GO_IDLE_STATE

SD_CMD8:
	DB	48h, 00h, 00h, 01h, 0AAh, 87h	; CMD8: SEND_IF_COND (VHS=1, 0xAA)

SD_CMD55:
	DB	77h, 00h, 00h, 00h, 00h, 0FFh	; CMD55: APP_CMD

SD_CMD41:
	DB	69h, 40h, 00h, 00h, 00h, 0FFh	; ACMD41: SD_SEND_OP_COND(HCS=1)

SD_CMD58:
	DB	7Ah, 00h, 00h, 00h, 00h, 0FFh	; CMD58: READ_OCR

SD_CMD16:
	DB	50h, 00h, 00h, 02h, 00h, 0FFh	; CMD16: SET_BLOCKLEN 512

SD_CMD17:
	DB	51h			; CMD17: READ_SINGLE_BLOCK
SD_RD_ARG:
	DB	00h, 00h, 00h, 00h	; arg (動的書き換え)
	DB	0FFh			; CRC

SD_CMD24:
	DB	58h			; CMD24: WRITE_SINGLE_BLOCK
SD_WR_ARG:
	DB	00h, 00h, 00h, 00h	; arg (動的書き換え)
	DB	0FFh			; CRC

;--------------------------------------------------------------
; ゼロページ設定データ
;   SDコマンドテーブル末尾の直後、SD_BUF の前に配置(連続レイアウト)。
;   WBOOTのゼロページ初期化LDIR処理から参照する。
;   WBOOTベクタ = origin+3(ジャンプテーブルの JP WBOOT エントリ)。
;   BDOSエントリ = BDOS_ORG+6。
;--------------------------------------------------------------

WB_ZP_DATA:
	DB	0C3h, (origin+3)&0FFh, ((origin+3)>>8)&0FFh	; 0x0000: JP origin+3 (WBOOT)
	DB	00h, 00h		; 0x0003-0x0004: IOBYTE=0, ドライブ=A
	DB	0C3h, (BDOS_ORG+6)&0FFh, ((BDOS_ORG+6)>>8)&0FFh	; 0x0005: JP BDOS_ORG+6 (BDOS)

;--------------------------------------------------------------
; SD用データ領域 (SDコマンドテーブル末尾の直後に連続配置)
;--------------------------------------------------------------

SD_BUF:
	DS	512			; 512バイトブロックバッファ

SD_CCS:
	DB	0			; カード種別 (0=SDSC, 非0=SDHC)

;==============================================================
; ディスク層ワーク領域 (SD_CCS の直後に連続配置)
;==============================================================

CUR_DISK:
	DB	0FFh			; 選択中ドライブ(0=A〜7=H, 0xFF=未選択)

CUR_TRACK:
	DW	0			; 現在のトラック番号(16bit)

CUR_SECTOR:
	DW	0			; 現在のセクタ番号(16bit)

CUR_DMA:
	DW	0080h			; DMAバッファアドレス(CP/M標準0x0080)

BUF_LBA:
	DB	0FFh, 0FFh, 0FFh, 0FFh	; バッファ内LBA(0xFFFFFFFF=無効)

BUF_DIRTY:
	DB	0			; 0=clean, 非0=dirty

WRITE_TYPE:
	DB	0			; 書込タイプ退避

REC_DIV4:
	DW	0			; CALC_LBA内部ワーク(rec>>2)

DISK_LBA_TMP:
	DS	4			; ENSURE_BUF内部ワーク(目標LBA)

;==============================================================
; DPB共通 (15バイト, ワーク領域の直後に連続配置)
; SPT=64, BSH=4, BLM=15, EXM=0, DSM=1023, DRM=511,
; AL0=0xFF, AL1=0x00, CKS=0, OFF=2
;==============================================================

DPB_COMMON:
	DW	64			; SPT: 1トラックのレコード数
	DB	4			; BSH: ブロックシフト(2KB)
	DB	15			; BLM: ブロックマスク
	DB	0			; EXM: エクステントマスク
	DW	1023			; DSM: 最大ブロック番号(1024ブロック)
	DW	511			; DRM: ディレクトリエントリ最大数
	DB	0FFh			; AL0: ディレクトリ用ブロック割当(上位)
	DB	00h			; AL1: ディレクトリ用ブロック割当(下位)
	DW	0			; CKS: チェックサム(固定ディスク=0)
	DW	2			; OFF: システム予約トラック数

;==============================================================
; DIRBUF共通 (128バイト, DPB の直後に連続配置)
;==============================================================

DIRBUF:
	DS	128			; ディレクトリバッファ

;==============================================================
; ALVバッファ(アロケーションベクタ, 8ドライブ×128B = 1024B)
;   DSM=1023 → ALVサイズ = (DSM/8)+1 = 128バイト/ドライブ。
;   各ドライブのALVは独立領域が必須(共有不可)。BDOSが起動時に
;   ディレクトリを走査してブロック使用状況をこのビットマップに構築する。
;   ※ これが未確保(ALV=0)だと BDOS が NULL ポインタ(0x0000=ゼロページ)を
;     破壊する致命バグになる。CKS=0 でも ALV は常に必須(省略可なのは CSV のみ)。
;==============================================================

ALV_BUF:
	DS	128*8			; 8ドライブ分のALV(各128B)

;==============================================================
; DPHテーブル (8ドライブ×16バイト = 128バイト)
; 各DPH構成:
;   XLT   (2B) = 0          (恒等変換のためNULL)
;   000000(6B)               (予約0)
;   DIRBUF(2B) = DIRBUF      (全ドライブ共有)
;   DPB   (2B) = DPB_COMMON  (全ドライブ共有)
;   CSV   (2B) = 0           (CKS=0のためNULL; 省略可)
;   ALV   (2B) = ALV_BUF+d*128 (ドライブ毎に独立; 必須)
;==============================================================

DPH_TABLE:
DPH0:	DW	0		; XLT
	DW	0,0,0		; 予約6バイト
	DW	DIRBUF		; DIRBUF
	DW	DPB_COMMON	; DPB
	DW	0		; CSV
	DW	ALV_BUF+0*128	; ALV (ドライブ0)

DPH1:	DW	0
	DW	0,0,0
	DW	DIRBUF
	DW	DPB_COMMON
	DW	0
	DW	ALV_BUF+1*128	; ALV (ドライブ1)

DPH2:	DW	0
	DW	0,0,0
	DW	DIRBUF
	DW	DPB_COMMON
	DW	0
	DW	ALV_BUF+2*128	; ALV (ドライブ2)

DPH3:	DW	0
	DW	0,0,0
	DW	DIRBUF
	DW	DPB_COMMON
	DW	0
	DW	ALV_BUF+3*128	; ALV (ドライブ3)

DPH4:	DW	0
	DW	0,0,0
	DW	DIRBUF
	DW	DPB_COMMON
	DW	0
	DW	ALV_BUF+4*128	; ALV (ドライブ4)

DPH5:	DW	0
	DW	0,0,0
	DW	DIRBUF
	DW	DPB_COMMON
	DW	0
	DW	ALV_BUF+5*128	; ALV (ドライブ5)

DPH6:	DW	0
	DW	0,0,0
	DW	DIRBUF
	DW	DPB_COMMON
	DW	0
	DW	ALV_BUF+6*128	; ALV (ドライブ6)

DPH7:	DW	0
	DW	0,0,0
	DW	DIRBUF
	DW	DPB_COMMON
	DW	0
	DW	ALV_BUF+7*128	; ALV (ドライブ7)


;==============================================================
; ディスクBIOS実装 (DPHテーブルの直後に連続配置)
;   ジャンプテーブルから直接参照される。
;   VRAMは 0xF300(固定)から。BIOS全体が origin..0xF2FF に収まること。
;==============================================================

;--------------------------------------------------------------
; HOME: トラック0シーク
;   dirtyバッファがあればフラッシュ後、CUR_TRACK=0
;--------------------------------------------------------------
HOME:
	PUSH	AF
	PUSH	BC
	PUSH	DE
	PUSH	HL
	LD	A, (BUF_DIRTY)
	OR	A
	CALL	NZ, DISK_FLUSH
	XOR	A
	LD	(CUR_TRACK), A
	LD	(CUR_TRACK+1), A
	POP	HL
	POP	DE
	POP	BC
	POP	AF
	RET

;--------------------------------------------------------------
; SELDSK: ディスク選択
;   入力: C = ドライブ番号 (0=A〜7=H)
;   戻り: HL = DPHアドレス(有効) / HL=0(無効)
;--------------------------------------------------------------
SELDSK:
	PUSH	AF
	PUSH	DE
	LD	A, C
	CP	8
	JR	NC, SELDSK_INV
	LD	(CUR_DISK), A
	LD	L, A
	LD	H, 0
	ADD	HL, HL		; ×2
	ADD	HL, HL		; ×4
	ADD	HL, HL		; ×8
	ADD	HL, HL		; ×16 = A*16
	LD	DE, DPH_TABLE
	ADD	HL, DE		; HL = DPH_TABLE + A*16
	JR	SELDSK_RET
SELDSK_INV:
	LD	HL, 0
SELDSK_RET:
	POP	DE
	POP	AF
	RET

;--------------------------------------------------------------
; SETTRK: トラック番号保存
;   入力: BC = トラック番号
;--------------------------------------------------------------
SETTRK:
	LD	(CUR_TRACK), BC
	RET

;--------------------------------------------------------------
; SETSEC: セクタ番号保存
;   入力: BC = セクタ番号
;--------------------------------------------------------------
SETSEC:
	LD	(CUR_SECTOR), BC
	RET

;--------------------------------------------------------------
; SETDMA: DMAアドレス保存
;   入力: BC = DMAバッファアドレス
;--------------------------------------------------------------
SETDMA:
	LD	(CUR_DMA), BC
	RET

;--------------------------------------------------------------
; LISTST: プリンタ状態
;   戻り: A=0xFF
;--------------------------------------------------------------
LISTST:
	LD	A, 0FFh
	RET

;--------------------------------------------------------------
; SECTRAN: 論理→物理セクタ変換 (恒等変換)
;   入力: BC=論理セクタ, DE=変換表
;   戻り: HL=BC
;--------------------------------------------------------------
SECTRAN:
	LD	H, B
	LD	L, C
	RET

;--------------------------------------------------------------
; READ: セクタ読込
;   戻り: A=0(成功) / A=1(エラー)
;--------------------------------------------------------------
READ:
	PUSH	BC
	PUSH	DE
	PUSH	HL
	CALL	CALC_LBA		; DE:HL = LBA32, B = offset番号(0-3)
	CALL	ENSURE_BUF		; バッファ確保。CY=1 で失敗
	JR	C, DISK_ERR
	CALL	READ_TO_DMA		; SD_BUF+offset*128 → CUR_DMA(128B)
	XOR	A
	POP	HL
	POP	DE
	POP	BC
	RET
DISK_ERR:
	LD	A, 1
	POP	HL
	POP	DE
	POP	BC
	RET

;--------------------------------------------------------------
; WRITE: セクタ書込
;   入力: C = 書込タイプ (0=通常, 1=ディレクトリ, 2=未使用先頭)
;   戻り: A=0(成功) / A=1(エラー)
;--------------------------------------------------------------
WRITE:
	PUSH	BC
	PUSH	DE
	PUSH	HL
	LD	A, C
	LD	(WRITE_TYPE), A
	CALL	CALC_LBA		; DE:HL = LBA32, B = offset番号(0-3)
	CALL	ENSURE_BUF		; read-modify-write。CY=1 で失敗
	JR	C, WRITE_ERR
	CALL	WRITE_FROM_DMA		; CUR_DMA → SD_BUF+offset*128(128B)
	LD	A, 1
	LD	(BUF_DIRTY), A
	LD	A, (WRITE_TYPE)
	CP	1			; ディレクトリ書込なら即フラッシュ
	JR	NZ, WRITE_OK
	CALL	DISK_FLUSH
	JR	C, WRITE_ERR
WRITE_OK:
	XOR	A
	POP	HL
	POP	DE
	POP	BC
	RET
WRITE_ERR:
	LD	A, 1
	POP	HL
	POP	DE
	POP	BC
	RET

;--------------------------------------------------------------
; CALC_LBA: 論理(track,sector)→物理(LBA32)計算
;   使用ワーク: CUR_DISK, CUR_TRACK, CUR_SECTOR, REC_DIV4
;   戻り:
;     DE:HL = LBA32 (DE=上位16bit, HL=下位16bit)
;     B     = rec & 3  (0..3, 128Bオフセット番号)
;   破壊: AF, BC, DE, HL
;
;   計算:
;     track_adj = CUR_TRACK + 2 (OFF=2)
;     rec       = track_adj * 64 + CUR_SECTOR  (SPT=64)
;     B         = rec & 3
;     rec_div4  = rec >> 2
;     lba       = d * 4128 + rec_div4
;                 4128 = 0x1000 + 0x20
;                 d(0-7): d*4128 <= 28896 = 0x70E0 (16bitに収まる)
;--------------------------------------------------------------
CALC_LBA:
	; --- rec 計算 ---
	LD	HL, (CUR_TRACK)
	INC	HL
	INC	HL			; HL = track + OFF(2)
	ADD	HL, HL			; x2
	ADD	HL, HL			; x4
	ADD	HL, HL			; x8
	ADD	HL, HL			; x16
	ADD	HL, HL			; x32
	ADD	HL, HL			; x64 → HL = track_adj * SPT
	LD	A, (CUR_SECTOR)
	ADD	A, L
	LD	L, A
	JR	NC, CLBA_NC
	INC	H
CLBA_NC:
	; HL = rec
	LD	A, L
	AND	03h
	LD	B, A			; B = rec & 3
	SRL	H
	RR	L
	SRL	H
	RR	L			; HL = rec_div4
	LD	(REC_DIV4), HL		; 退避

	; --- d * 4128 = d * 0x1000 + d * 0x20 ---
	LD	A, (CUR_DISK)
	LD	H, A
	LD	L, 0
	ADD	HL, HL			; d*512
	ADD	HL, HL			; d*1024
	ADD	HL, HL			; d*2048
	ADD	HL, HL			; d*4096 = d*0x1000
	LD	A, (CUR_DISK)
	LD	E, A
	LD	D, 0
	SLA	E
	SLA	E
	SLA	E
	SLA	E
	SLA	E			; E = d*32 = d*0x20 (d<=7, キャリーなし)
	ADD	HL, DE			; HL = d*4128

	; --- lba = d*4128 + rec_div4 ---
	LD	DE, (REC_DIV4)
	ADD	HL, DE			; HL = lba (下位16bit)
	LD	DE, 0
	JR	NC, CLBA_NC2
	INC	DE			; キャリーを上位へ
CLBA_NC2:
	; DE:HL = LBA32
	RET

;--------------------------------------------------------------
; ENSURE_BUF: バッファ確保 (ミス時フラッシュ+SD読込)
;   入力: DE:HL = 目標LBA, B = offset番号(0-3)
;   出力: CY=0 成功 / CY=1 失敗, B変更なし
;--------------------------------------------------------------
ENSURE_BUF:
	PUSH	BC
	PUSH	DE
	PUSH	HL
	LD	(DISK_LBA_TMP), HL
	LD	(DISK_LBA_TMP+2), DE
	; BUF_LBA と比較 (4バイト一致でヒット)
	LD	A, (BUF_LBA)
	CP	L
	JR	NZ, ENS_MISS
	LD	A, (BUF_LBA+1)
	CP	H
	JR	NZ, ENS_MISS
	LD	A, (BUF_LBA+2)
	CP	E
	JR	NZ, ENS_MISS
	LD	A, (BUF_LBA+3)
	CP	D
	JR	Z, ENS_HIT
ENS_MISS:
	; ミス: dirtyならフラッシュ
	LD	A, (BUF_DIRTY)
	OR	A
	JR	Z, ENS_LOAD
	CALL	DISK_FLUSH
	JR	C, ENS_FAIL
ENS_LOAD:
	LD	HL, (DISK_LBA_TMP)
	LD	DE, (DISK_LBA_TMP+2)
	CALL	SD_READ_BLOCK
	JR	C, ENS_FAIL
	; BUF_LBA 更新
	LD	HL, (DISK_LBA_TMP)
	LD	DE, (DISK_LBA_TMP+2)
	LD	(BUF_LBA), HL
	LD	(BUF_LBA+2), DE
	XOR	A
	LD	(BUF_DIRTY), A
ENS_HIT:
	POP	HL
	POP	DE
	POP	BC
	OR	A			; CY=0
	RET
ENS_FAIL:
	POP	HL
	POP	DE
	POP	BC
	SCF				; CY=1
	RET

;--------------------------------------------------------------
; DISK_FLUSH: 現在のバッファ(BUF_LBA)をSDに書き戻す
;   出力: CY=0 成功 / CY=1 失敗, BUF_DIRTY=0(成功時)
;--------------------------------------------------------------
DISK_FLUSH:
	PUSH	DE
	PUSH	HL
	LD	HL, (BUF_LBA)
	LD	DE, (BUF_LBA+2)
	CALL	SD_WRITE_BLOCK
	JR	C, FLUSH_FAIL
	XOR	A
	LD	(BUF_DIRTY), A
	POP	HL
	POP	DE
	OR	A			; CY=0
	RET
FLUSH_FAIL:
	POP	HL
	POP	DE
	SCF				; CY=1
	RET

;--------------------------------------------------------------
; READ_TO_DMA: SD_BUF+B*128 → CUR_DMA (128バイトコピー)
;   入力: B = offset番号(0-3)
;   破壊: AF, BC, DE, HL
;--------------------------------------------------------------
READ_TO_DMA:
	PUSH	BC
	PUSH	DE
	PUSH	HL
	; src = SD_BUF + B * 128
	LD	H, 0
	LD	L, B
	ADD	HL, HL
	ADD	HL, HL
	ADD	HL, HL
	ADD	HL, HL
	ADD	HL, HL
	ADD	HL, HL
	ADD	HL, HL			; HL = B * 128
	LD	DE, SD_BUF
	ADD	HL, DE			; HL = SD_BUF + B*128 (src)
	LD	DE, (CUR_DMA)		; DE = dst
	LD	BC, 128
	LDIR
	POP	HL
	POP	DE
	POP	BC
	RET

;--------------------------------------------------------------
; WRITE_FROM_DMA: CUR_DMA → SD_BUF+B*128 (128バイトコピー)
;   入力: B = offset番号(0-3)
;   破壊: AF, BC, DE, HL
;--------------------------------------------------------------
WRITE_FROM_DMA:
	PUSH	BC
	PUSH	DE
	PUSH	HL
	; dst = SD_BUF + B * 128
	LD	H, 0
	LD	L, B
	ADD	HL, HL
	ADD	HL, HL
	ADD	HL, HL
	ADD	HL, HL
	ADD	HL, HL
	ADD	HL, HL
	ADD	HL, HL			; HL = B * 128
	LD	DE, SD_BUF
	ADD	HL, DE			; HL = SD_BUF + B*128 (dst)
	EX	DE, HL			; DE = dst (SD_BUF+B*128)
	LD	HL, (CUR_DMA)		; HL = src (CUR_DMA)
	LD	BC, 128
	LDIR
	POP	HL
	POP	DE
	POP	BC
	RET

;==============================================================
; ウォームブート本体・サブルーチン (#36)
;   ディスクBIOSの直後、VRAM(0xF300)の前に連続配置
;==============================================================

;--------------------------------------------------------------
; WBOOT_BODY: ウォームブート本体
;   1. SPをBIOS専用スタック(BIOS_STACK=VRAM領域)に設定
;   2. DI
;   3. ダーティバッファフラッシュ
;   4. SD から CCP/BDOS 再ロード(CCP_LBA..(CCP_LBA+10) → CCP_ORG..BDOS末尾)
;   5. ゼロページ再設定(0x0000-0x0007 LDIRで書込)
;   6. RST7スタブ設置(INSTALL_RST7_STUB呼出)
;   7. ワーク初期化(CUR_DISK=0, CUR_DMA=0x0080)
;      (CUR_TRACK/CUR_SECTORはBDOSが常にSETTRK/SETSECで設定するため省略)
;   8. CCP(CCP_ORG)へジャンプ
;--------------------------------------------------------------
WBOOT_BODY:
	LD	SP, BIOS_STACK		; BIOS専用スタック(VRAM領域、BIOSコード末尾+余裕)
	DI				; 割り込み禁止
	CALL	FLUSH_DIRTY_BUF		; ダーティバッファフラッシュ
	CALL	LOAD_CPM_FROM_SD	; CCP/BDOS 再ロード
	; ゼロページ設定: LDIRで一括書込 (8バイト 0x0000-0x0007)
	; 0x0003=IOBYTE=0, 0x0004=デフォルトドライブ=Aは意図的に0クリア
	LD	HL, WB_ZP_DATA		; src = ZPデータテーブル
	LD	DE, 0000h		; dst = 0x0000
	LD	BC, 8
	LDIR
	; RST7ベクタ(0x0038)にRET命令を書込(INSTALL_RST7_STUBを再利用)
	CALL	INSTALL_RST7_STUB
	; ワーク初期化(必須分のみ)
	XOR	A
	LD	(CUR_DISK), A		; カレントドライブ = A(0)
	LD	HL, 0080h
	LD	(CUR_DMA), HL		; DMAアドレス = 0x0080(デフォルト)
	; CCP(CCP_ORG)へジャンプ
	JP	CCP_ORG

;--------------------------------------------------------------
; LOAD_CPM_FROM_SD: CCP/BDOSをSDから一括再ロード
;   CCP_LBA..(CCP_LBA+10)(11ブロック)を CCP_ORG から連続ロード。
;   CCP:  CCP_LBA  .. CCP_LBA+3  → CCP_ORG  (4ブロック)
;   BDOS: BDOS_LBA .. BDOS_LBA+6 → BDOS_ORG (7ブロック)
;   ※ CCP/BDOSは連続配置(BDOS_ORG = CCP_ORG + 2048)のため一括ロード可能。
;   IX=LBAカウンタ(SD_READ_BLOCKで保存)、DE=dstアドレス
;   SD_READ_BLOCK破壊: AF,BC,HL (DE・IXは保存される)
;   LDIR後のDE(=旧dst+512)をそのまま次ループのdstとして使用。
;--------------------------------------------------------------
LOAD_CPM_FROM_SD:
	LD	IX, CCP_LBA		; IX = 開始LBA(CCP_LBA)
	LD	DE, CCP_ORG		; DE = ロード先先頭(CCP_ORG)
	LD	B, 11			; B = ブロック数(11=CCP4+BDOS7)
LCPM_LOOP:
	PUSH	BC			; ブロック数を保存
	PUSH	DE			; dstアドレスを保存
	PUSH	IX
	POP	HL			; HL = LBA(IXから転送)
	LD	DE, 0			; LBA上位 = 0
	CALL	SD_READ_BLOCK		; DE:HL=LBA → SD_BUF(512B)
	POP	DE			; dstアドレス復元
	POP	BC			; ブロック数復元
	JR	C, LCPM_DONE		; 読込失敗時は中断
	PUSH	BC			; ブロック数再保存(LDIRがBCを破壊)
	LD	HL, SD_BUF		; src = SD_BUF
	LD	BC, 512
	LDIR				; 512バイト転送(DE→次ブロック先頭に自動更新)
	POP	BC			; ブロック数復元
	INC	IX			; LBA++
	DJNZ	LCPM_LOOP
LCPM_DONE:
	RET

	end
