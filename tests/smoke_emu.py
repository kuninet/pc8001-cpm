#!/usr/bin/env python3
"""ツールチェーン疎通用スモークテスト(エミュレータ側)。

build/smoke.bin を 0x0100 に読み込んで kosarev/z80 コアで実行し、
ポート 0x10 への OUT 値が 0x03 であることを検証する。

これは「asl でアセンブル → p2bin でバイナリ化 → Z80 コアで実行 →
I/O コールバックで結果確認」という開発・テストの一連の流れが
機能することを保証するための最小テスト。

実行: PYTHONPATH=external/z80 python tests/smoke_emu.py build/smoke.bin
"""
import sys

import z80


def main() -> int:
    bin_path = sys.argv[1] if len(sys.argv) > 1 else "build/smoke.bin"
    with open(bin_path, "rb") as f:
        code = f.read()

    mem = bytearray(0x10000)
    org = 0x0100
    mem[org:org + len(code)] = code

    outs: list[tuple[int, int]] = []
    m = z80.Z80Machine()
    m.set_read_callback(lambda a: mem[a & 0xFFFF])
    m.set_write_callback(lambda a, v: mem.__setitem__(a & 0xFFFF, v))
    m.set_input_callback(lambda p: 0xFF)
    m.set_output_callback(lambda p, v: outs.append((p, v)))
    m.pc = org

    # HALT (0x76) に到達するまで実行(暴走防止に上限あり)
    for _ in range(1000):
        m.ticks_to_stop = 4
        m.run()
        if mem[m.pc & 0xFFFF] == 0x76:
            break
    else:
        print("NG: HALT に到達しませんでした", file=sys.stderr)
        return 1

    # OUT (n),A はアドレスバス上位に A が乗るため、ポート下位 8bit で判定
    low_port_outs = [(p & 0xFF, v) for p, v in outs]
    if (0x10, 0x03) in low_port_outs:
        print(f"OK: ポート0x10へ 0x03 を出力 (events={[(hex(p), hex(v)) for p, v in outs]})")
        return 0

    print(f"NG: 期待した出力なし (events={[(hex(p), hex(v)) for p, v in outs]})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
