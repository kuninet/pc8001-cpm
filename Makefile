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
PY      := .venv/bin/python
EMU_PYTHONPATH := external/z80

BUILD := build

.PHONY: all setup smoke clean

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

clean:
	rm -rf $(BUILD)
