# PC-8001 CP/M 開発用 Makefile
#
#   make setup   開発・テスト環境を構築 (.venv 作成 + Z80 エミュコア取得/ビルド)
#   make smoke   ツールチェーン疎通テスト (asl -> p2bin -> エミュレータ実行)
#   make clean   ビルド成果物を削除
#
# 新規コード (BIOS/ローダ等) は The Macroassembler AS (asl) でアセンブルする。
#   ソース(.asm) --asl--> 中間(.p) --p2bin--> バイナリ(.bin)

AS      := asl
P2BIN   := p2bin
P2HEX   := p2hex
PY      := .venv/bin/python
EMU_PYTHONPATH := external/z80

BUILD := build

.PHONY: all setup smoke clean fetch-cpm cpm check-setup check-cpm bios loader cpm-image

all: smoke

# --- 環境構築 ---
setup:
	./scripts/setup_env.sh

# --- ビルドディレクトリ ---
$(BUILD):
	mkdir -p $(BUILD)

# --- アセンブル: .asm -> .p -> .bin ---
$(BUILD)/smoke.p: tests/smoke.asm | $(BUILD)
	$(AS) -o $@ $<

$(BUILD)/smoke.bin: $(BUILD)/smoke.p
	$(P2BIN) $< $@

# --- setup 未実施チェック ---
.PHONY: check-setup
check-setup:
	@test -x $(PY) || { echo "ERROR: $(PY) がありません。先に 'make setup' を実行してください。"; exit 1; }
	@test -d $(EMU_PYTHONPATH)/z80 || { echo "ERROR: $(EMU_PYTHONPATH) がありません。先に 'make setup' を実行してください。"; exit 1; }

# --- 疎通テスト ---
smoke: check-setup $(BUILD)/smoke.bin
	PYTHONPATH=$(EMU_PYTHONPATH) $(PY) tests/smoke_emu.py $(BUILD)/smoke.bin

# --- CP/M 本体 (CCP/BDOS) 取得・ビルド ---
# 取得: external/cpm22 (gitignore 対象) へ
fetch-cpm:
	./scripts/fetch_cpm.sh

# ============================================================
# メモリ配置の単一パラメータ化(#4 メモリマップ詳細設計)
# ------------------------------------------------------------
# 唯一の調整点は BIOS_BLOCKS(BIOSが占めるSDブロック数=512B単位)。
# 固定値: VRAM=0xF300, CCP=4ブロック(2048B), BDOS=7ブロック(3584B)。
# 以下を BIOS_BLOCKS から全導出する(数値直書きを排除):
#   BIOS_ORG = 0xF300 - BIOS_BLOCKS*512
#   BDOS_ORG = BIOS_ORG - 3584
#   CCP_ORG  = BDOS_ORG - 2048
#   LBA: BIOS 0..(N-1), CCP N..(N+3), BDOS (N+4)..(N+10)   (N=BIOS_BLOCKS)
#
# 例) BIOS_BLOCKS=9 → BIOS=0xE100, BDOS=0xD300, CCP=0xCB00
#     LBA: BIOS 0-8 / CCP 9-12 / BDOS 13-19 (計20ブロック)
#     ゼロページ: WBOOT=0xE103, BDOSエントリ=0xD306
#
# ※ tests/memmap.py のデフォルトと必ず一致させること(現在 9)。
# ============================================================
BIOS_BLOCKS ?= 9

# BIOS_BLOCKS 変更検出: 前回ビルド時と値が異なれば関連成果物を破棄して確実に
# 再ビルドさせる(mtime解像度に依存しない、make parse 時に評価)。
# BIOS_BLOCKS はコマンドライン override されうるため Makefile 依存では検出できない。
_BB_STAMP := $(BUILD)/.bios_blocks.stamp
_BB_PREV  := $(shell cat $(_BB_STAMP) 2>/dev/null)
ifneq ($(_BB_PREV),)
ifneq ($(_BB_PREV),$(BIOS_BLOCKS))
$(info * BIOS_BLOCKS 変更を検出 ($(_BB_PREV) -> $(BIOS_BLOCKS)): 関連成果物を再ビルドします)
$(shell rm -f $(BUILD)/bios.p $(BUILD)/bios.bin $(BUILD)/bios.lst $(BUILD)/ccp.p $(BUILD)/ccp.bin $(BUILD)/bdos.p $(BUILD)/bdos.bin $(BUILD)/loader.p $(BUILD)/loader.bin $(BUILD)/loader.hex $(BUILD)/cpm-image.bin)
endif
endif
$(shell mkdir -p $(BUILD) >/dev/null 2>&1 && printf '%s' '$(BIOS_BLOCKS)' > $(_BB_STAMP))

# --- 派生アドレス(10進で計算 → asl用に "0XXXXh" へ整形)---
VRAM_BASE_DEC  := 62208		# 0xF300
BIOS_ORG_DEC   := $(shell echo $$(( $(VRAM_BASE_DEC) - $(BIOS_BLOCKS) * 512 )))
BDOS_ORG_DEC   := $(shell echo $$(( $(BIOS_ORG_DEC) - 3584 )))
CCP_ORG_DEC    := $(shell echo $$(( $(BDOS_ORG_DEC) - 2048 )))
BIOS_END_DEC   := $(shell echo $$(( $(VRAM_BASE_DEC) - 1 )))	# 0xF2FF
BDOS_END_DEC   := $(shell echo $$(( $(BIOS_ORG_DEC) - 1 )))
CCP_END_DEC    := $(shell echo $$(( $(BDOS_ORG_DEC) - 1 )))

# asl 用 "0XXXXh" 形式
BIOS_ORG  := $(shell printf '0%Xh' $(BIOS_ORG_DEC))
BDOS_ORG  := $(shell printf '0%Xh' $(BDOS_ORG_DEC))
CCP_ORG   := $(shell printf '0%Xh' $(CCP_ORG_DEC))

# p2bin 用 "$XXXX-$YYYY" レンジ
CCP_RANGE  := $$$(shell printf '%X' $(CCP_ORG_DEC))-$$$(shell printf '%X' $(CCP_END_DEC))
BDOS_RANGE := $$$(shell printf '%X' $(BDOS_ORG_DEC))-$$$(shell printf '%X' $(BDOS_END_DEC))
BIOS_RANGE := $$$(shell printf '%X' $(BIOS_ORG_DEC))-$$$(shell printf '%X' $(BIOS_END_DEC))

# LBA 開始ブロック
BIOS_LBA   := 0
CCP_LBA    := $(BIOS_BLOCKS)
BDOS_LBA   := $(shell echo $$(( $(BIOS_BLOCKS) + 4 )))

CPM_SRC    := external/cpm22

check-cpm:
	@test -f $(CPM_SRC)/ccp.asm || { echo "ERROR: $(CPM_SRC) がありません。先に 'make fetch-cpm' を実行してください。"; exit 1; }

cpm: check-cpm $(BUILD)/ccp.bin $(BUILD)/bdos.bin
	@echo "CCP/BDOS ビルド完了 (CCP_ORG=$(CCP_ORG) BDOS_ORG=$(BDOS_ORG))"

$(BUILD)/ccp.bin: $(CPM_SRC)/ccp.asm | $(BUILD)
	$(AS) -D origin=$(CCP_ORG) -o $(BUILD)/ccp.p $<
	$(P2BIN) -l '$$00' -r '$(CCP_RANGE)' $(BUILD)/ccp.p $@

$(BUILD)/bdos.bin: $(CPM_SRC)/bdos.asm | $(BUILD)
	$(AS) -D origin=$(BDOS_ORG) -o $(BUILD)/bdos.p $<
	$(P2BIN) -l '$$00' -r '$(BDOS_RANGE)' $(BUILD)/bdos.p $@

# --- BIOS ---
# 配置(BIOS_ORG)・CCP/BDOSアドレス・LBAはすべて -D で BIOS へ配布する。
# -L -OLIST でシンボル表付きリスティングを出力(テストがシンボルでアドレス取得)
BIOS_DEFS := -D origin=$(BIOS_ORG) -D CCP_ORG=$(CCP_ORG) -D BDOS_ORG=$(BDOS_ORG) \
             -D CCP_LBA=$(CCP_LBA) -D BDOS_LBA=$(BDOS_LBA)

$(BUILD)/bios.p: src/bios/bios.asm | $(BUILD)
	$(AS) $(BIOS_DEFS) -L -OLIST $(BUILD)/bios.lst -o $(BUILD)/bios.p src/bios/bios.asm

$(BUILD)/bios.bin: $(BUILD)/bios.p
	$(P2BIN) $(BUILD)/bios.p $(BUILD)/bios.bin -r '$(BIOS_RANGE)'

bios: $(BUILD)/bios.bin
	@echo "BIOS ビルド完了 (BIOS_BLOCKS=$(BIOS_BLOCKS) BIOS_ORG=$(BIOS_ORG) BDOS_ORG=$(BDOS_ORG) CCP_ORG=$(CCP_ORG))"

# --- ブートローダ ---
# ローダにも配置アドレス・LBAを -D で配布する(数値直書きを排除)。
LOADER_DEFS := -D BIOS_ORG=$(BIOS_ORG) -D CCP_ORG=$(CCP_ORG) -D BDOS_ORG=$(BDOS_ORG) \
               -D BIOS_BLOCKS=$(BIOS_BLOCKS) -D CCP_LBA=$(CCP_LBA) -D BDOS_LBA=$(BDOS_LBA)

$(BUILD)/loader.p: src/loader/loader.asm | $(BUILD)
	$(AS) $(LOADER_DEFS) -o $(BUILD)/loader.p src/loader/loader.asm

$(BUILD)/loader.bin: $(BUILD)/loader.p
	$(P2BIN) $(BUILD)/loader.p $(BUILD)/loader.bin -r '$$6000-$$7fff'

$(BUILD)/loader.hex: $(BUILD)/loader.p
	$(P2HEX) $(BUILD)/loader.p $(BUILD)/loader.hex

loader: $(BUILD)/loader.bin $(BUILD)/loader.hex
	@echo "ローダ ビルド完了 (loader.bin=$(shell wc -c < $(BUILD)/loader.bin)B, loader.hex=$(shell wc -c < $(BUILD)/loader.hex)B)"

# --- CP/M システムイメージ(SD先頭 (BIOS_BLOCKS+11) ブロック) ---
# ローダ(#35)が SD から読み込むレイアウトに合わせて結合:
#   LBA 0..(N-1)    : BIOS  → BIOS_ORG にロード
#   LBA N..(N+3)    : CCP   → CCP_ORG  にロード
#   LBA (N+4)..(N+10): BDOS → BDOS_ORG にロード   (N=BIOS_BLOCKS)
# 計 (N+11) ブロック × 512B。
$(BUILD)/cpm-image.bin: $(BUILD)/bios.bin $(BUILD)/ccp.bin $(BUILD)/bdos.bin
	$(PY) scripts/build_cpm_image.py \
	  --bios-blocks $(BIOS_BLOCKS) \
	  --bios $(BUILD)/bios.bin \
	  --ccp  $(BUILD)/ccp.bin \
	  --bdos $(BUILD)/bdos.bin \
	  --out  $@

cpm-image: $(BUILD)/cpm-image.bin
	@echo "CP/M システムイメージ ビルド完了 (cpm-image.bin=$(shell wc -c < $(BUILD)/cpm-image.bin)B)"

clean:
	rm -rf $(BUILD)
