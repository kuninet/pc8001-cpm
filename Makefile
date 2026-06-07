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

# 配置アドレスは #4(メモリマップ詳細設計)で確定済み。
#   CCP   = 0xD300〜0xDAFF (0x800バイト)
#   BDOS  = 0xDB00〜0xE8FF (0xE00バイト, エントリは0xDB06)
#   BIOS  = 0xE900〜0xF2FF (#6, 別ターゲット)
# ORG を変えるときは対応する RANGE もセットで更新すること。
CPM_SRC    := external/cpm22
CCP_ORG    ?= 0D300h
CCP_RANGE  ?= $$D300-$$DAFF
BDOS_ORG   ?= 0DB00h
BDOS_RANGE ?= $$DB00-$$E8FF

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
BIOS_ORG ?= 0E900h

# -L -OLIST でシンボル表付きリスティングを出力(テストがシンボルでアドレス取得)
$(BUILD)/bios.p: src/bios/bios.asm | $(BUILD)
	$(AS) -D origin=$(BIOS_ORG) -L -OLIST $(BUILD)/bios.lst -o $(BUILD)/bios.p src/bios/bios.asm

$(BUILD)/bios.bin: $(BUILD)/bios.p
	$(P2BIN) $(BUILD)/bios.p $(BUILD)/bios.bin -r '$$e900-$$f2ff'

bios: $(BUILD)/bios.bin
	@echo "BIOS ビルド完了 (BIOS_ORG=$(BIOS_ORG))"

# --- ブートローダ ---
$(BUILD)/loader.p: src/loader/loader.asm | $(BUILD)
	$(AS) -o $(BUILD)/loader.p src/loader/loader.asm

$(BUILD)/loader.bin: $(BUILD)/loader.p
	$(P2BIN) $(BUILD)/loader.p $(BUILD)/loader.bin -r '$$6000-$$7fff'

$(BUILD)/loader.hex: $(BUILD)/loader.p
	$(P2HEX) $(BUILD)/loader.p $(BUILD)/loader.hex

loader: $(BUILD)/loader.bin $(BUILD)/loader.hex
	@echo "ローダ ビルド完了 (loader.bin=$(shell wc -c < $(BUILD)/loader.bin)B, loader.hex=$(shell wc -c < $(BUILD)/loader.hex)B)"

# --- CP/M システムイメージ(SD先頭16ブロック=8192B) ---
# ローダ(#35)が SD から読み込むレイアウトに合わせて結合:
#   LBA 0-4  (2560B): BIOS  → 0xE900 にロード
#   LBA 5-8  (2048B): CCP   → 0xD300 にロード
#   LBA 9-15 (3584B): BDOS  → 0xDB00 にロード
# 計 16 ブロック × 512B = 8192B。
$(BUILD)/cpm-image.bin: $(BUILD)/bios.bin $(BUILD)/ccp.bin $(BUILD)/bdos.bin
	$(PY) scripts/build_cpm_image.py \
	  --bios $(BUILD)/bios.bin \
	  --ccp  $(BUILD)/ccp.bin \
	  --bdos $(BUILD)/bdos.bin \
	  --out  $@

cpm-image: $(BUILD)/cpm-image.bin
	@echo "CP/M システムイメージ ビルド完了 (cpm-image.bin=$(shell wc -c < $(BUILD)/cpm-image.bin)B)"

clean:
	rm -rf $(BUILD)
