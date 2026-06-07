#!/usr/bin/env python3
"""CP/M システムイメージ(SDシステム領域)生成ツール。

ローダ(#35)が SD から読み込むレイアウトに合わせて、BIOS/CCP/BDOS を
1枚のイメージに結合する。レイアウトは単一パラメータ --bios-blocks(N)から導出:

  LBA 0..(N-1)      : BIOS  (N ブロック)
  LBA N..(N+3)      : CCP   (4 ブロック = 2048B)
  LBA (N+4)..(N+10) : BDOS  (7 ブロック = 3584B)
  合計 N+11 ブロック

CCP/BDOS のサイズは CP/M 本体で固定(各 4/7 ブロック)。BIOS だけが可変。
各ブロックの不足分は 0x00 でパディング。入力サイズが規定上限を超える場合はエラー。

設計参照: doc/設計/02_ブートシーケンス.md, doc/設計/08_CPM取得ビルド.md
"""
import argparse
import sys

BLOCK_SIZE = 512
CCP_BLOCKS = 4    # CP/M 本体で固定(2048B)
BDOS_BLOCKS = 7   # CP/M 本体で固定(3584B)


def make_layout(bios_blocks: int):
    """BIOS_BLOCKS から (name, start_lba, blocks) のレイアウトを導出。"""
    return [
        ("BIOS", 0, bios_blocks),
        ("CCP", bios_blocks, CCP_BLOCKS),
        ("BDOS", bios_blocks + CCP_BLOCKS, BDOS_BLOCKS),
    ]


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
    p.add_argument("--bios-blocks", type=int, default=9,
                   help="BIOS が占める SD ブロック数(Makefile の BIOS_BLOCKS と一致させる)")
    p.add_argument("--bios", required=True)
    p.add_argument("--ccp", required=True)
    p.add_argument("--bdos", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    layout = make_layout(args.bios_blocks)
    total_blocks = args.bios_blocks + CCP_BLOCKS + BDOS_BLOCKS

    inputs = {"BIOS": args.bios, "CCP": args.ccp, "BDOS": args.bdos}

    image = bytearray(total_blocks * BLOCK_SIZE)
    for name, start_lba, blocks in layout:
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
    print(f"  生成: {args.out} ({len(image)}B = {total_blocks}ブロック)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
