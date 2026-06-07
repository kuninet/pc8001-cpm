#!/usr/bin/env python3
"""PC-8001 CP/M 用 SD イメージ作成・管理ツール。

サブコマンド一覧:
  mkimage  空のSDイメージを作成(各ドライブのディレクトリ領域を 0xE5 で初期化)
  sys      ドライブAのOFF領域にBIOS/CCP/BDOSを書き込み
  put      ホストファイルを指定ドライブのCP/Mファイルとして書き込み
  dir      指定ドライブのCP/Mディレクトリ一覧を表示
  get      CP/MファイルをホストPC側に取り出す
  format   指定ドライブのディレクトリ領域を初期化(OFF/データ本体は保持)

レイアウト(#8確定):
  - 8ドライブ(A〜H)、各 4128ブロック × 512B ≈ 2MB、合計 33024ブロック ≈ 16MB
  - ドライブ d(0-7)の先頭LBA = d × 4128
  - 各ドライブ内: OFF(32ブロック=16KB)=システム領域、残り4096ブロック=データ領域
  - データ領域内: 先頭8ブロック(BLS=2KB×8=16KB)=ディレクトリ、続いてファイル領域
  - システム領域(ドライブA先頭): BIOS(LBA 0..N-1) / CCP(LBA N..N+3) / BDOS(LBA N+4..N+10)。N=BIOS_BLOCKS(既定9)。配置アドレスは BIOS_BLOCKS から導出(doc/設計/01_メモリマップ.md, tests/memmap.py)

DPB(Disk Parameter Block):
  SPT=64, BSH=4(BLS=2KB), BLM=15, EXM=0
  DSM=1023, DRM=511, AL0=0xFF, AL1=0x00, CKS=0, OFF=2

設計参照: doc/設計/10_SD作成ツール.md, doc/設計/05_ディスクサブシステム.md
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from typing import Optional

# -------------------------------------------------------------------------
# レイアウト定数
# -------------------------------------------------------------------------

# 1ブロック = 512B(SDセクタ)
SD_BLOCK = 512

# 1CP/Mブロック = 2KB = 4セクタ (BLS=2KB, BSH=4)
CPM_BLOCK_SIZE = 2048
CPM_BLOCK_SECTORS = 4  # 2048 / 512

# ドライブあたりの総ブロック数(SDセクタ単位)
DRIVE_BLOCKS = 4128   # 4128 × 512B = 2,113,536B ≈ 2MB

# OFF領域: 先頭32ブロック = 16KB (= 2トラック×64セクタ/トラック / 4)
# 設計では OFF=2 トラック → 2×64セクタ=128レコード=32SDブロック
OFF_SECTORS = 32       # SDブロック単位でのOFF領域サイズ

# データ領域のSDブロック数
DATA_SECTORS = DRIVE_BLOCKS - OFF_SECTORS   # 4096

# CP/Mブロック番号0〜のオフセット(SDブロック相対): OFF直後から
# OFF = 32 SDブロック → データ先頭 = SDブロック 32
DATA_OFFSET_SECTORS = OFF_SECTORS  # = 32

# ディレクトリはCP/Mブロック0〜7(8ブロック×2KB=16KB=512エントリ)
DIR_CPM_BLOCKS = 8
DIR_SIZE = DIR_CPM_BLOCKS * CPM_BLOCK_SIZE  # 16384B

# ディレクトリエントリ数と1エントリサイズ
DIR_ENTRY_SIZE = 32
DIR_ENTRIES = DIR_SIZE // DIR_ENTRY_SIZE   # 512

# 空きエントリのユーザ番号マーカー
EMPTY_ENTRY = 0xE5

# DSM(最大ブロック番号 = 1023)
DSM = 1023

# 1エクステント = 8 CP/Mブロック × 2KB = 16KB
EXTENT_BLOCKS = 8
EXTENT_SIZE = EXTENT_BLOCKS * CPM_BLOCK_SIZE   # 16384B = 16KB

# 1エクステント内の128Bレコード数
EXTENT_RECORDS = EXTENT_SIZE // 128   # 128

# システム領域(build_cpm_image.py 準拠、単一パラメータ BIOS_BLOCKS から導出)
SYS_CCP_BLOCKS = 4       # CCP 2048B 固定
SYS_BDOS_BLOCKS = 7      # BDOS 3584B 固定
DEFAULT_BIOS_BLOCKS = 9  # Makefile の BIOS_BLOCKS デフォルトと一致させること


def make_sys_layout(bios_blocks: int):
    """BIOS_BLOCKS から (name, start_lba, blocks) のシステム領域レイアウトを導出。"""
    return [
        ("BIOS", 0, bios_blocks),
        ("CCP", bios_blocks, SYS_CCP_BLOCKS),
        ("BDOS", bios_blocks + SYS_CCP_BLOCKS, SYS_BDOS_BLOCKS),
    ]

# ドライブ数
NUM_DRIVES = 8


# -------------------------------------------------------------------------
# エクステント番号ヘルパ
# -------------------------------------------------------------------------

def extent_no(ex: int, s2: int) -> int:
    """EX, S2 フィールドから論理エクステント番号を計算する。"""
    return ((s2 & 0x3F) << 5) | (ex & 0x1F)


def extent_to_fields(n: int) -> tuple[int, int]:
    """論理エクステント番号を (EX, S2) フィールドに変換する。"""
    return (n & 0x1F, (n >> 5) & 0x3F)


# -------------------------------------------------------------------------
# ファイル名正規化
# -------------------------------------------------------------------------

# CP/Mで禁止の文字('.' は名前/拡張子の区切りとして別途処理するため除外)
_FORBIDDEN_CHARS = set('<>,;:=?*[]')


def normalize_name(filename: str, strict: bool = True) -> tuple[bytes, bytes]:
    """ホストファイル名から CP/M 8.3 形式の(name_bytes, ext_bytes)を返す。

    - 小文字→大文字変換
    - 7bit ASCII に制限、b7=0 を保証
    - CP/M禁止文字(`< > , ; : = ? * [ ]`)が含まれた場合は ValueError を送出(strict=True)
    - 名前8文字、拡張子3文字にトリム・スペース埋め

    strict=False では禁止文字を黙って除去する(後方互換用)。
    """
    base = os.path.basename(filename)
    # 最後の '.' で分割
    if '.' in base:
        dot = base.rfind('.')
        name_raw = base[:dot]
        ext_raw = base[dot + 1:]
    else:
        name_raw = base
        ext_raw = ''

    # 禁止文字の検出(strict時はエラー)
    if strict:
        for ch in name_raw + ext_raw:
            if ch in _FORBIDDEN_CHARS:
                raise ValueError(
                    f"ファイル名に CP/M 禁止文字 '{ch}' が含まれています: {base}"
                )

    def clean(s: str, maxlen: int) -> bytes:
        s = s.upper()
        result = []
        for ch in s:
            if ch in _FORBIDDEN_CHARS:
                continue
            code = ord(ch)
            if code < 0x20 or code > 0x7E:
                continue
            result.append(code & 0x7F)  # b7=0 を保証
            if len(result) >= maxlen:
                break
        # スペース(0x20)でパディング
        while len(result) < maxlen:
            result.append(0x20)
        return bytes(result)

    return clean(name_raw, 8), clean(ext_raw, 3)


def parse_name_arg(name_arg: str) -> tuple[bytes, bytes]:
    """--name 引数(例: 'FOO.COM')から (name_bytes, ext_bytes) を返す。"""
    return normalize_name(name_arg)


def parse_drive(drive_letter: str) -> int:
    """ドライブ文字(A-H)を 0-7 のインデックスに変換する。範囲外はエラー。"""
    if not isinstance(drive_letter, str) or len(drive_letter) != 1:
        raise ValueError(f"ドライブ指定は1文字でなければなりません: {drive_letter!r}")
    ch = drive_letter.upper()
    if not ('A' <= ch <= 'H'):
        raise ValueError(f"ドライブは A〜H の範囲で指定してください: {drive_letter!r}")
    return ord(ch) - ord('A')


# -------------------------------------------------------------------------
# イメージオフセット計算
# -------------------------------------------------------------------------

def drive_base_offset(drive: int) -> int:
    """ドライブ d (0-7) のSDイメージ内バイトオフセットを返す。"""
    return drive * DRIVE_BLOCKS * SD_BLOCK


def dir_area_offset(drive: int) -> int:
    """ドライブ d のディレクトリ領域先頭バイトオフセットを返す。
    ディレクトリはCP/Mブロック0〜7 = データ領域先頭16KB。
    データ領域 = OFF直後 = drive_base + OFF_SECTORS×512。
    """
    return drive_base_offset(drive) + OFF_SECTORS * SD_BLOCK


def cpm_block_offset(drive: int, block_no: int) -> int:
    """ドライブ d の CP/M ブロック番号 block_no のSDイメージ内バイトオフセットを返す。
    ブロック0 = データ領域先頭(= OFF直後)。

    block_no が DPB の DSM(=1023)を超える場合はドライブ境界を越えて隣ドライブを
    破壊する恐れがあるため RuntimeError を送出する。
    """
    if not (0 <= block_no <= DSM):
        raise RuntimeError(
            f"ブロック番号がDSM(={DSM})を超えています: block_no={block_no}"
        )
    return drive_base_offset(drive) + OFF_SECTORS * SD_BLOCK + block_no * CPM_BLOCK_SIZE


# -------------------------------------------------------------------------
# イメージ I/O
# -------------------------------------------------------------------------

def load_image(path: str) -> bytearray:
    """SDイメージファイルを bytearray として読み込む。"""
    with open(path, 'rb') as f:
        return bytearray(f.read())


def save_image(path: str, image: bytearray) -> None:
    """SDイメージを書き出す。"""
    with open(path, 'wb') as f:
        f.write(image)


# -------------------------------------------------------------------------
# ディレクトリ操作
# -------------------------------------------------------------------------

def read_dir_entry(image: bytearray, drive: int, idx: int) -> bytes:
    """ドライブ drive のディレクトリエントリ idx を返す(32B)。"""
    off = dir_area_offset(drive) + idx * DIR_ENTRY_SIZE
    return bytes(image[off:off + DIR_ENTRY_SIZE])


def write_dir_entry(image: bytearray, drive: int, idx: int, entry: bytes) -> None:
    """ドライブ drive のディレクトリエントリ idx を書き込む(32B)。"""
    off = dir_area_offset(drive) + idx * DIR_ENTRY_SIZE
    image[off:off + DIR_ENTRY_SIZE] = entry[:DIR_ENTRY_SIZE]


def parse_dir_entry(entry: bytes) -> dict:
    """ディレクトリエントリ(32B)を辞書にパースする。

    返り値:
      user, name(bytes,8), ext(bytes,3), ex, s1, s2, rc, alloc(list of 8 int)
    """
    user = entry[0]
    name = bytes(b & 0x7F for b in entry[1:9])
    ext  = bytes(b & 0x7F for b in entry[9:12])
    ex   = entry[12] & 0x1F
    s1   = entry[13]
    s2   = entry[14] & 0x3F
    rc   = entry[15]
    # DSM=1023 > 255 のため 16bit アロケーションマップ(8個)
    alloc = list(struct.unpack_from('<8H', entry, 16))
    return {
        'user': user, 'name': name, 'ext': ext,
        'ex': ex, 's1': s1, 's2': s2, 'rc': rc,
        'alloc': alloc,
        'extent_no': extent_no(ex, s2),
    }


def build_dir_entry(user: int, name: bytes, ext: bytes,
                    ex: int, s2: int, rc: int,
                    alloc: list[int]) -> bytes:
    """ディレクトリエントリ(32B)を構築する。"""
    entry = bytearray(DIR_ENTRY_SIZE)
    entry[0] = user & 0xFF
    entry[1:9]  = name[:8]
    entry[9:12] = ext[:3]
    entry[12] = ex & 0x1F
    entry[13] = 0
    entry[14] = s2 & 0x3F
    entry[15] = rc & 0xFF
    # 16bit アロケーションマップ(8個、リトルエンディアン)
    struct.pack_into('<8H', entry, 16, *alloc)
    return bytes(entry)


def collect_used_blocks(image: bytearray, drive: int) -> set[int]:
    """ドライブ drive のディレクトリを走査し、使用済みCP/Mブロック番号の集合を返す。"""
    used: set[int] = set()
    for i in range(DIR_ENTRIES):
        e = read_dir_entry(image, drive, i)
        if e[0] == EMPTY_ENTRY:
            continue
        parsed = parse_dir_entry(e)
        for blk in parsed['alloc']:
            if blk != 0:
                used.add(blk)
    return used


def alloc_blocks(used: set[int], count: int) -> list[int]:
    """ブロック8(ディレクトリ予約8ブロックを除く先頭)から昇順で未使用ブロックを count 個確保する。

    確保したブロック番号のリストを返す。空きが不足する場合は RuntimeError を送出する。
    """
    result = []
    n = DIR_CPM_BLOCKS  # = 8
    while len(result) < count:
        if n > DSM:
            raise RuntimeError(
                f"ドライブに空きブロックが不足しています(要求={count}, 確保={len(result)})"
            )
        if n not in used:
            result.append(n)
            used.add(n)
        n += 1
    return result


def find_free_dir_entry(image: bytearray, drive: int) -> int:
    """ドライブ drive の空きディレクトリエントリのインデックスを返す。

    空きが無い場合は RuntimeError を送出する。
    """
    for i in range(DIR_ENTRIES):
        e = read_dir_entry(image, drive, i)
        if e[0] == EMPTY_ENTRY:
            return i
    raise RuntimeError("ディレクトリに空きエントリがありません")


def delete_file(image: bytearray, drive: int,
                user: int, name: bytes, ext: bytes) -> int:
    """ドライブ drive 上の同名ファイル(user, name, ext)を全エクステント削除する。

    各エクステントのアロケーションマップに含まれるブロックは「未使用」となるよう
    エントリ自体を 0xE5 で消すだけで良い(空きブロック判定は collect_used_blocks が
    生存中のエントリのみを集計するため)。

    返り値: 削除したエントリ数。
    """
    deleted = 0
    for i in range(DIR_ENTRIES):
        e = read_dir_entry(image, drive, i)
        if e[0] == EMPTY_ENTRY:
            continue
        parsed = parse_dir_entry(e)
        if (parsed['user'] == user
                and parsed['name'] == name
                and parsed['ext'] == ext):
            # エントリ全体を 0xE5 で塗りつぶす(先頭1Bが 0xE5 なら空きと判定される)
            write_dir_entry(image, drive, i, bytes([EMPTY_ENTRY]) * DIR_ENTRY_SIZE)
            deleted += 1
    return deleted


# -------------------------------------------------------------------------
# サブコマンド実装
# -------------------------------------------------------------------------

def cmd_mkimage(out_path: str) -> None:
    """16MBのSDイメージを作成する。各ドライブのディレクトリ領域を 0xE5 で初期化する。"""
    total_bytes = DRIVE_BLOCKS * NUM_DRIVES * SD_BLOCK  # 33024 × 512 = 16,908,288B
    image = bytearray(total_bytes)

    for d in range(NUM_DRIVES):
        # ディレクトリ領域(16KB)を 0xE5 で初期化
        off = dir_area_offset(d)
        image[off:off + DIR_SIZE] = bytes([EMPTY_ENTRY]) * DIR_SIZE

    save_image(out_path, image)
    print(f"  作成: {out_path} ({len(image)}B = {total_bytes // SD_BLOCK}ブロック)")


def cmd_sys(image_path: str, bios_path: str, ccp_path: str, bdos_path: str,
            bios_blocks: int = DEFAULT_BIOS_BLOCKS) -> None:
    """ドライブAのOFF領域にBIOS/CCP/BDOSを書き込む。

    レイアウトは BIOS_BLOCKS(N)から導出(build_cpm_image.py と同一):
      LBA 0..(N-1)      : BIOS (N ブロック)
      LBA N..(N+3)      : CCP  (4ブロック = 2048B)
      LBA (N+4)..(N+10) : BDOS (7ブロック = 3584B)
    """
    sys_layout = make_sys_layout(bios_blocks)
    sys_total = bios_blocks + SYS_CCP_BLOCKS + SYS_BDOS_BLOCKS
    if sys_total > OFF_SECTORS:
        print(f"ERROR: システム領域 {sys_total}ブロックが OFF領域 {OFF_SECTORS}ブロックを超過"
              f"(BIOS_BLOCKS={bios_blocks} が大きすぎる)", file=sys.stderr)
        sys.exit(1)

    def _load(path: str, max_bytes: int, name: str) -> bytes:
        with open(path, 'rb') as f:
            data = f.read()
        if len(data) > max_bytes:
            print(f"ERROR: {name} ({path}) が規定サイズ {max_bytes}B を超過: {len(data)}B",
                  file=sys.stderr)
            sys.exit(1)
        return data + b'\x00' * (max_bytes - len(data))

    inputs = {'BIOS': bios_path, 'CCP': ccp_path, 'BDOS': bdos_path}
    image = load_image(image_path)

    # ドライブAの先頭 = バイトオフセット 0
    base = drive_base_offset(0)
    for name, start_lba, blocks in sys_layout:
        max_bytes = blocks * SD_BLOCK
        data = _load(inputs[name], max_bytes, name)
        off = base + start_lba * SD_BLOCK
        image[off:off + max_bytes] = data
        print(
            f"  {name:4s}: LBA {start_lba:2d}-{start_lba + blocks - 1:2d} "
            f"({blocks}ブロック, {max_bytes}B) <- {inputs[name]}"
        )

    save_image(image_path, image)
    print(f"  書込完了: {image_path}")


def cmd_put(image_path: str, drive_letter: str, file_path: str,
            name_arg: Optional[str] = None, user: int = 0) -> None:
    """ホストファイルをCP/Mドライブに書き込む。

    1エクステント = 16KB(8ブロック × 2KB)。
    16KB超のファイルはEX/S2を増やした複数ディレクトリエントリで表現する。
    DSM=1023 > 255 のため、アロケーションマップは16bitブロック番号(リトルエンディアン)。

    同名(user, name, ext)の既存ファイルが存在する場合は事前に削除してから書き込む。
    0Bファイルは RC=0, alloc=全0 のエントリ1個だけを記録する(ブロック割当なし)。
    """
    # ドライブ範囲チェック
    try:
        drive = parse_drive(drive_letter)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # ユーザ番号バリデーション(CP/M仕様: 0-15)
    if not isinstance(user, int) or not (0 <= user <= 15):
        print(
            f"ERROR: ユーザ番号は 0〜15 の範囲で指定してください: user={user}",
            file=sys.stderr
        )
        sys.exit(1)

    # ファイル名決定(禁止文字検出含む)
    try:
        if name_arg:
            cpm_name, cpm_ext = parse_name_arg(name_arg)
        else:
            cpm_name, cpm_ext = normalize_name(file_path)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    with open(file_path, 'rb') as f:
        file_data = f.read()

    image = load_image(image_path)

    # 既存の同名ファイルを削除(エントリを 0xE5 で消す)
    deleted = delete_file(image, drive, user, cpm_name, cpm_ext)
    if deleted > 0:
        print(f"  上書: 既存エントリ {deleted} 件を削除")

    # 空きブロック集計(削除後の状態で再計算)
    used = collect_used_blocks(image, drive)

    # 0Bファイル: 空エントリ(RC=0, alloc=全0)を1件だけ書く
    if len(file_data) == 0:
        ex_val, s2_val = extent_to_fields(0)
        dir_idx = find_free_dir_entry(image, drive)
        entry = build_dir_entry(user, cpm_name, cpm_ext,
                                ex_val, s2_val, 0, [0] * EXTENT_BLOCKS)
        write_dir_entry(image, drive, dir_idx, entry)
        save_image(image_path, image)
        print(
            f"  書込: {cpm_name.rstrip(b' ').decode()}.{cpm_ext.rstrip(b' ').decode()} "
            f"(0B, 空ファイル) -> ドライブ{drive_letter.upper()}"
        )
        return

    # 128Bレコードに分割(最終レコードは0パディング)
    record_size = 128
    records = []
    for i in range(0, len(file_data), record_size):
        chunk = file_data[i:i + record_size]
        chunk = chunk + b'\x00' * (record_size - len(chunk))
        records.append(chunk)

    total_records = len(records)
    rec_idx = 0        # 処理済みレコード数
    extent_idx = 0     # 論理エクステント番号

    while rec_idx < total_records:
        # このエクステントのレコード数
        remaining = total_records - rec_idx
        this_extent_records = min(remaining, EXTENT_RECORDS)  # 最大128レコード

        # 必要ブロック数(1ブロック = 16レコード = 2KB)
        blocks_needed = (this_extent_records + 15) // 16  # 切り上げ

        # 空きブロックを確保
        alloc_list = alloc_blocks(used, blocks_needed)

        # アロケーションマップ(8スロット、未使用は0)
        alloc_map = [0] * EXTENT_BLOCKS
        for i, blk in enumerate(alloc_list):
            alloc_map[i] = blk

        # RC: このエクステントの128Bレコード数(最終エクステントは端数、満杯は128)
        # CP/M仕様: 満杯エクステントのRC = 128(=0x80)
        if remaining > EXTENT_RECORDS:
            rc = EXTENT_RECORDS  # = 128 = 0x80
        else:
            rc = this_extent_records

        # EX, S2フィールドに変換
        ex_val, s2_val = extent_to_fields(extent_idx)

        # 空きディレクトリエントリを見つけて書き込む
        dir_idx = find_free_dir_entry(image, drive)
        entry = build_dir_entry(user, cpm_name, cpm_ext, ex_val, s2_val, rc, alloc_map)
        write_dir_entry(image, drive, dir_idx, entry)

        # データブロックへレコードを書き込む
        for blk_i, blk_no in enumerate(alloc_list):
            blk_off = cpm_block_offset(drive, blk_no)
            for rec_in_blk in range(16):  # 1ブロック = 16レコード
                rec_global = rec_idx + blk_i * 16 + rec_in_blk
                if rec_global < total_records:
                    src = records[rec_global]
                else:
                    src = b'\x00' * record_size
                data_off = blk_off + rec_in_blk * record_size
                image[data_off:data_off + record_size] = src

        rec_idx += this_extent_records
        extent_idx += 1

    save_image(image_path, image)
    size = len(file_data)
    print(
        f"  書込: {cpm_name.rstrip(b' ').decode()}.{cpm_ext.rstrip(b' ').decode()} "
        f"({size}B, {extent_idx}エクステント) -> ドライブ{drive_letter.upper()}"
    )


def cmd_dir(image_path: str, drive_letter: str) -> None:
    """指定ドライブのCP/Mディレクトリを一覧表示する。

    同一(name, ext, user)ファイルの複数エクステントを集約してファイルサイズを表示する。
    """
    try:
        drive = parse_drive(drive_letter)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    image = load_image(image_path)

    # (user, name, ext) -> list of (extent_no, rc) 収集
    files: dict[tuple, list] = {}
    for i in range(DIR_ENTRIES):
        e = read_dir_entry(image, drive, i)
        if e[0] == EMPTY_ENTRY:
            continue
        parsed = parse_dir_entry(e)
        key = (parsed['user'], parsed['name'], parsed['ext'])
        ext_n = extent_no(parsed['ex'], parsed['s2'])
        files.setdefault(key, []).append((ext_n, parsed['rc']))

    if not files:
        print(f"  ドライブ {drive_letter.upper()}: ファイルなし")
        return

    print(f"  ドライブ {drive_letter.upper()}:")
    print(f"  {'ユーザ':>4}  {'ファイル名':<12}  {'サイズ':>8}")
    print(f"  {'-'*4}  {'-'*12}  {'-'*8}")

    for (user, name, ext), extents in sorted(files.items()):
        extents.sort(key=lambda x: x[0])
        # 合計サイズ = エクステントの RC × 128B の和
        # 最終エクステントのみ端数、それ以外は 128×128=16384B
        total_size = 0
        for ei, (ext_no_val, rc) in enumerate(extents):
            if ei < len(extents) - 1:
                # 中間エクステントは満杯(16KB)
                total_size += EXTENT_RECORDS * 128
            else:
                # 最終エクステントのみ RC 通り
                total_size += rc * 128

        name_str = name.rstrip(b' ').decode('ascii', errors='replace')
        ext_str  = ext.rstrip(b' ').decode('ascii', errors='replace')
        fname = f"{name_str}.{ext_str}" if ext_str else name_str
        print(f"  {user:4d}  {fname:<12}  {total_size:8d}")


def cmd_get(image_path: str, drive_letter: str, name_arg: str, out_path: str) -> None:
    """CP/Mファイルをホストファイルとして取り出す。

    name_arg は '8.3' 形式(例: 'FOO.COM')。
    エクステントを昇順に結合して出力する。最終エクステントの末尾は RC に基づき切り出す。

    注意: CP/M 2.2 の仕様上、ファイルサイズは 128B レコード単位でしか保持されない。
    したがって、出力サイズは常に 128B 境界に揃う(元ファイルがそれ未満や中途半端な
    長さでも 128B 単位に切り上がり、末尾はゼロパディングされた状態で取り出される)。
    バイト単位の元サイズは保存されない。
    """
    try:
        drive = parse_drive(drive_letter)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    cpm_name, cpm_ext = parse_name_arg(name_arg)
    image = load_image(image_path)

    # 対象ファイルのエクステントを収集
    found: list[dict] = []
    for i in range(DIR_ENTRIES):
        e = read_dir_entry(image, drive, i)
        if e[0] == EMPTY_ENTRY:
            continue
        parsed = parse_dir_entry(e)
        if parsed['name'] == cpm_name and parsed['ext'] == cpm_ext:
            found.append(parsed)

    if not found:
        name_str = cpm_name.rstrip(b' ').decode()
        ext_str  = cpm_ext.rstrip(b' ').decode()
        print(
            f"ERROR: ファイルが見つかりません: {name_str}.{ext_str} "
            f"(ドライブ{drive_letter.upper()})",
            file=sys.stderr
        )
        sys.exit(1)

    # extent_no 昇順にソート
    found.sort(key=lambda x: x['extent_no'])

    result = bytearray()
    for fi, parsed in enumerate(found):
        rc = parsed['rc']
        alloc = parsed['alloc']
        is_last = (fi == len(found) - 1)

        if not is_last:
            # 中間エクステントは満杯
            read_records = EXTENT_RECORDS
        else:
            read_records = rc

        for blk_slot, blk_no in enumerate(alloc):
            if blk_no == 0:
                break
            blk_off = cpm_block_offset(drive, blk_no)
            for rec_in_blk in range(16):  # 1ブロック = 16レコード
                rec_global = blk_slot * 16 + rec_in_blk
                if rec_global >= read_records:
                    break
                data_off = blk_off + rec_in_blk * 128
                result.extend(image[data_off:data_off + 128])

    with open(out_path, 'wb') as f:
        f.write(result)

    print(f"  取出: {name_arg} -> {out_path} ({len(result)}B)")


def cmd_format(image_path: str, drive_letter: str) -> None:
    """指定ドライブのディレクトリ領域(16KB)を 0xE5 に初期化する。

    OFF領域(システム)およびデータ本体ブロックは保持する。
    """
    try:
        drive = parse_drive(drive_letter)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    image = load_image(image_path)
    off = dir_area_offset(drive)
    image[off:off + DIR_SIZE] = bytes([EMPTY_ENTRY]) * DIR_SIZE
    save_image(image_path, image)
    print(f"  フォーマット完了: ドライブ{drive_letter.upper()} ディレクトリ領域初期化")


# -------------------------------------------------------------------------
# CLI エントリポイント
# -------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog='sd_tool.py',
        description='PC-8001 CP/M 用 SD イメージ作成・管理ツール',
    )
    sub = parser.add_subparsers(dest='command', metavar='COMMAND')
    sub.required = True

    # mkimage
    p_mk = sub.add_parser('mkimage', help='空の SD イメージを作成')
    p_mk.add_argument('--out', required=True, metavar='IMG', help='出力イメージファイル')

    # sys
    p_sys = sub.add_parser('sys', help='ドライブAのOFF領域にシステムを書き込み')
    p_sys.add_argument('--image', required=True, metavar='IMG', help='SDイメージファイル')
    p_sys.add_argument('--bios', required=True, metavar='FILE', help='BIOS バイナリ')
    p_sys.add_argument('--ccp', required=True, metavar='FILE', help='CCP バイナリ')
    p_sys.add_argument('--bdos', required=True, metavar='FILE', help='BDOS バイナリ')
    p_sys.add_argument('--bios-blocks', type=int, default=DEFAULT_BIOS_BLOCKS,
                       metavar='N', help='BIOSが占めるSDブロック数(Makefile の BIOS_BLOCKS と一致)')

    # put
    p_put = sub.add_parser('put', help='ファイルをCP/Mドライブに書き込み')
    p_put.add_argument('--image', required=True, metavar='IMG', help='SDイメージファイル')
    p_put.add_argument('--drive', required=True, metavar='DRV', help='ドライブ文字(A-H)')
    p_put.add_argument('--file', required=True, metavar='FILE', help='ホストファイルパス')
    p_put.add_argument('--name', default=None, metavar='NAME', help='CP/Mファイル名(8.3形式)')
    p_put.add_argument('--user', type=int, default=0, metavar='N', help='ユーザ番号(0-15)')

    # dir
    p_dir = sub.add_parser('dir', help='CP/Mディレクトリを一覧表示')
    p_dir.add_argument('--image', required=True, metavar='IMG', help='SDイメージファイル')
    p_dir.add_argument('--drive', required=True, metavar='DRV', help='ドライブ文字(A-H)')

    # get
    p_get = sub.add_parser('get', help='CP/Mファイルを取り出し')
    p_get.add_argument('--image', required=True, metavar='IMG', help='SDイメージファイル')
    p_get.add_argument('--drive', required=True, metavar='DRV', help='ドライブ文字(A-H)')
    p_get.add_argument('--name', required=True, metavar='NAME', help='CP/Mファイル名(8.3形式)')
    p_get.add_argument('--out', required=True, metavar='OUT', help='出力ファイルパス')

    # format
    p_fmt = sub.add_parser('format', help='指定ドライブのディレクトリ領域を初期化')
    p_fmt.add_argument('--image', required=True, metavar='IMG', help='SDイメージファイル')
    p_fmt.add_argument('--drive', required=True, metavar='DRV', help='ドライブ文字(A-H)')

    args = parser.parse_args()

    if args.command == 'mkimage':
        cmd_mkimage(args.out)
    elif args.command == 'sys':
        cmd_sys(args.image, args.bios, args.ccp, args.bdos, args.bios_blocks)
    elif args.command == 'put':
        cmd_put(args.image, args.drive, args.file, args.name, args.user)
    elif args.command == 'dir':
        cmd_dir(args.image, args.drive)
    elif args.command == 'get':
        cmd_get(args.image, args.drive, args.name, args.out)
    elif args.command == 'format':
        cmd_format(args.image, args.drive)

    return 0


if __name__ == '__main__':
    sys.exit(main())
