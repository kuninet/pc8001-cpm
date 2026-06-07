;==============================================================
; PC-8001 CP/M 2.2 BIOS
; doc/設計/03_BIOS構成.md, doc/設計/04_コンソール.md 準拠
;   - BIOS_ORG = 0E900h
;   - ジャンプテーブル: 17エントリ × 3バイト = 51バイト
;   - コンソール: テキストVRAM 0F300h, ADM-3A相当
;
; ビルド:
;   asl -D origin=0E900h -o build/bios.p src/bios/bios.asm
;   p2bin build/bios.p build/bios.bin
;==============================================================
	cpu	z80

	ifndef	origin
origin	equ	0E900h
	endif

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
; BOOT: コールドブート (0E933h 固定)
;   スタックポインタ設定 → CRTC初期化 → VRAMクリア → サインオン → HALT
;   TODO: 実機ではCCPへ #35/#37
;   ※ BOOT本体は WBOOT の後(BOOT_BODY)に配置
;      ジャンプテーブル直後のアドレス制約(0xE933/0xE945)を守るため
;--------------------------------------------------------------
BOOT:					; == 0E933h ==
	LD	SP, 0DF00h		; スタックポインタ設定 (3B: 0xE933-0xE935)
	JP	BOOT_BODY		; BOOT本体へ (3B: 0xE936-0xE938)
	NOP				; パディング (1B: 0xE939)
	NOP				; パディング (1B: 0xE93A)
	NOP				; パディング (1B: 0xE93B)
	NOP				; パディング (1B: 0xE93C)
	NOP				; パディング (1B: 0xE93D)
	NOP				; パディング (1B: 0xE93E)
	NOP				; パディング (1B: 0xE93F)
	NOP				; パディング (1B: 0xE940)
	NOP				; パディング (1B: 0xE941)
	NOP				; パディング (1B: 0xE942)
	NOP				; パディング (1B: 0xE943)
	NOP				; パディング (1B: 0xE944)

;--------------------------------------------------------------
; WBOOT: ウォームブート (0E945h 固定)
;   TODO: CCP/BDOS再ロード #10/#35
;--------------------------------------------------------------
WBOOT:					; == 0E945h ==
	HALT				; TODO: CCP/BDOS再ロード #10/#35

;--------------------------------------------------------------
; BOOT_BODY: BOOT本体 (WBOOTの後に配置)
;   CRTC初期化 → VRAMクリア → サインオン → HALT
;--------------------------------------------------------------
BOOT_BODY:
	; CRTC 初期化スタブ
	; TODO: ICW SCREEN FORMAT 詳細は実機確認 (#39 でCP-3確認)
	; 現状の OUT (0x51), 0x00 のみでは実機μPD3301の正しい初期化に
	; ならない可能性が高い(SCREEN FORMAT パラメータ未送出)。
	; 80桁モード切替/カーソル設定/DMA設定/OCWは未対応。
	; 実機/MAME での検証は #39 (CP-3) で確定する。
	LD	A, 00h
	OUT	(51h), A		; ICW: RESET (スタブ値)

	; VRAM クリア
	CALL	CLS

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
	HALT				; TODO: 実機ではCCPへ #35/#37

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

;--------------------------------------------------------------
; HOME: トラック0シーク (ダミー)
;--------------------------------------------------------------
HOME:
	RET

;--------------------------------------------------------------
; SELDSK: ディスク選択
;   戻り: HL=0(無効)
;--------------------------------------------------------------
SELDSK:
	LD	HL, 0
	RET

;--------------------------------------------------------------
; SETTRK: トラック設定 (ダミー)
;--------------------------------------------------------------
SETTRK:
	RET

;--------------------------------------------------------------
; SETSEC: セクタ設定 (ダミー)
;--------------------------------------------------------------
SETSEC:
	RET

;--------------------------------------------------------------
; SETDMA: DMAアドレス設定 (ダミー)
;--------------------------------------------------------------
SETDMA:
	RET

;--------------------------------------------------------------
; READ: セクタ読込 (常にエラー)
;   戻り: A=1
;--------------------------------------------------------------
READ:
	LD	A, 1
	RET

;--------------------------------------------------------------
; WRITE: セクタ書込 (常にエラー)
;   戻り: A=1
;--------------------------------------------------------------
WRITE:
	LD	A, 1
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
; データ領域
;--------------------------------------------------------------
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

	end
