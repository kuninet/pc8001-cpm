#!/usr/bin/env python3
"""CP/M システムイメージ(SDシステム領域)生成ツール。

ローダ(#35)が SD から読み込むレイアウトに合わせて、BIOS/CCP/BDOS を
16 ブロック × 512B = 8192B のイメージに結合する。

レイアウト:
  LBA 0-4  (2560B): BIOS  → 0xE900 にロード
  LBA 5-8  (2048B): CCP   → 0xD300 にロード
  LBA 9-15 (3584B): BDOS  → 0xDB00 にロード

各ブロックの不足分は 0x00 でパディング。入力サイズが規定上限を超える場合はエラー。

設計参照: doc/設計/02_ブートシーケンス.md, doc/設計/08_CPM取得ビルド.md
"""
import argparse
import sys

BLOCK_SIZE = 512
LAYOUT = [
    ("BIOS", 0, 5),   # LBA 0-4 (5 blocks = 2560B)
    ("CCP",  5, 4),   # LBA 5-8 (4 blocks = 2048B)
    ("BDOS", 9, 7),   # LBA 9-15 (7 blocks = 3584B)
]
TOTAL_BLOCKS = 16


def load(path: str, max_bytes: int, name: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    if len(data) > max_bytes:
        sys.stderr.write(
            f"ERROR: {name} ({path}) が規定サイズ {max_bytes}B を超過: {len(data)}B\n"
        )
        sys.exit(1)
    # ブロックサイズ単位にパディング
    return data + b"\x00" * (max_bytes - len(data))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--bios", required=True)
    p.add_argument("--ccp", required=True)
    p.add_argument("--bdos", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    inputs = {"BIOS": args.bios, "CCP": args.ccp, "BDOS": args.bdos}

    image = bytearray(TOTAL_BLOCKS * BLOCK_SIZE)
    for name, start_lba, blocks in LAYOUT:
        max_bytes = blocks * BLOCK_SIZE
        data = load(inputs[name], max_bytes, name)
        off = start_lba * BLOCK_SIZE
        image[off : off + max_bytes] = data
        print(
            f"  {name:4s}: LBA {start_lba:2d}-{start_lba + blocks - 1:2d} "
            f"({blocks} blocks, {max_bytes}B) <- {inputs[name]}"
        )

    with open(args.out, "wb") as f:
        f.write(image)
    print(f"  生成: {args.out} ({len(image)}B = {TOTAL_BLOCKS}ブロック)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
