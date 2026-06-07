# pc8001-cpm

PC-8001（無印）で CP/M 2.2 を動かすためのプロジェクトです。

## 目的

- CP/M 2.2 を PC-8001 無印上で起動・動作させる
- メモリーボード [PC8001-MEM](https://github.com/kuninet/PC8001-MEM) を用いて
  CP/M に必須となる低位アドレスの RAM（ゼロページ）を確保する
- SD カードからのブートに対応する
- 起動用 SD を作成するためのホスト側ツールを整備する

## 方針

- CP/M 2.2 本体（CCP / BDOS）はネット上で公開されているソースを利用する。
  **本リポジトリには CP/M 本体のソースは含めない**（取得方法・ビルド方法までを扱う）。
- ハードウェア依存部である **BIOS は本プロジェクトで新規作成**する。
- ストレージは SD のみを対象とする。
- コンソールは PC-8001 の 80 桁テキスト画面を用いる（英数モードのみ／漢字非対応）。

## 想定ハードウェア構成

- 本体: PC-8001（無印, μPD780 / Z80 互換 4MHz）
- 増設メモリ: PC8001-MEM（128KB / 4バンク×32KB、0x0000〜0x7FFF を ROM/RAM 切替）
- SD ブート手段（いずれか）:
  - [SD-DOS](https://github.com/chiqlappe/SD-DOS)（8255 ビットバンギング）
  - [PC-8001_SD](https://github.com/yanataka60/PC-8001_SD)（マイコンで SD 制御）
  - Raspberry Pi Pico による自作

## ドキュメント

- [概要検討](doc/概要検討/概要検討.md)

## 参考リンク

- PC8001-MEM メモリーボード: https://github.com/kuninet/PC8001-MEM
- SD-DOS: https://github.com/chiqlappe/SD-DOS
- PC-8001_SD: https://github.com/yanataka60/PC-8001_SD

## ライセンス

未定。
