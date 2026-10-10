# fast-ccsds-parser

[![CI](https://github.com/daikiito-dk/fast-ccsds-parser/actions/workflows/ci.yml/badge.svg)](https://github.com/daikiito-dk/fast-ccsds-parser/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/fast-ccsds-parser)](https://pypi.org/project/fast-ccsds-parser/)

[紹介ページ](https://daikiito-dk.github.io/fast-ccsds-parser/?lang=ja) · [English documentation is here](README.md)

宇宙業界の通信標準 **CCSDSスペースパケット**（[CCSDS 133.0-B-2]）を、
モダンなPythonデータスタック向けに高速・依存ゼロで解析するライブラリです。
テレメトリダンプを1パスでdictや構造化オブジェクトに変換します。
プライマリヘッダ、パケット境界、ペイロードを取り出すコアはPython標準
ライブラリのみで構成され、どこでも `pip install` ですぐ動きます。

小型衛星開発チーム、大学の宇宙工学研究室、衛星データ解析エンジニア向け。

[CCSDS 133.0-B-2]: https://public.ccsds.org/Pubs/133x0b2e1.pdf

## インストール

```console
pip install fast-ccsds-parser
```

またはソースから:

```console
pip install git+https://github.com/daikiito-dk/fast-ccsds-parser.git
```

## クイックスタート

```python
from fast_ccsds_parser import parse_primary_header, iter_file

# ヘッダだけ欲しい場合はdictで:
header = parse_primary_header(raw_bytes)
# {'version': 0, 'type': 0, 'secondary_header_flag': 1, 'apid': 42,
#  'sequence_flags': 3, 'sequence_count': 1337, 'packet_length': 255}

# 地上局ダンプを1パケットずつストリーム処理（メモリ使用量は一定）:
for pkt in iter_file("dump_20261010.pkts"):
    print(pkt.header.apid, pkt.header.sequence_count, len(pkt.payload))

# メモリ上のバッファも同様に:
packets = [p.to_dict() for p in iter_packets(buffer)]
```

## パース対象

6バイトのCCSDSプライマリヘッダ（全フィールドビッグエンディアン）:

```text
bit:   0   1   2   3   4   5   6   7   8   9  10  11  12  13  14  15
       +---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+
0-1    | version  | T | S |                APID                       |
       +---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+
2-3    | seq flags |        sequence count / packet name               |
       +---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+
4-5    |                     packet length                            |
       +---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+
```

| フィールド             | ビット | 意味                                                |
| ---------------------- | ----- | --------------------------------------------------- |
| `version`             | 3     | スペースパケットでは `0`                             |
| `type`                | 1     | 0 = テレメトリ、1 = テレコマンド                     |
| `secondary_header_flag` | 1   | 1 = プライマリヘッダの後にセカンダリヘッダが存在     |
| `apid`                | 11    | アプリケーションプロセス識別子（全ビット1 = アイドル） |
| `sequence_flags`      | 2     | 分割状態（非分割 = `0b11`）                          |
| `sequence_count`      | 14    | パケット系列カウンタ / パケット名                    |
| `packet_length`       | 16    | データフィールド長 **マイナス1**（下記参照）         |

## API

| エントリポイント | 用途 |
| ---------------- | ---- |
| `parse_primary_header(data, *, offset=0, strict=True)` | ヘッダフィールドを `dict` で返す |
| `PrimaryHeader.from_bytes(data, *, offset=0, strict=True)` | ヘッダを `NamedTuple` で返す（`.apid`、`.total_length`、`.is_idle`、`.to_dict()` など） |
| `parse_packet(data, *, strict=True)` | 1パケットだけを `SpacePacket` として返す |
| `iter_packets(data, *, strict=True, skip_idle=False)` | 連結されたパケット列のバッファをイテレート |
| `parse_packets(data, ...)` | 同上（`list` で返す） |
| `iter_file(path, ...)` / `parse_file(path, ...)` | ダンプファイルからメモリ一定でストリーム処理 |

`SpacePacket` は `header`（`PrimaryHeader`）、`payload`（データフィールドの
バイト列）、`raw`（パケット全体のバイト列）を持ち、`to_dict()` で
すべてを1つのdictに展開します。

## 知っておくべきセマンティクス

- **`packet_length` はゼロ起算。** データフィールド長は
  `packet_length + 1` バイト、パケット全体は `6 + packet_length + 1`
  バイトです。CCSDSデータ処理で一番バグりやすいポイントで、
  本ライブラリはここを正しく処理します。
- **`payload` = データフィールド全体。** CCSDS 133.0-B-2ではデータ
  フィールドは「オプションのセカンダリヘッダ + ユーザデータ」であり、
  ペイロードはそのまま返されます。ミッション固有のセカンダリヘッダが
  先頭にあるかは `header.has_secondary_header` で判定できます。
  その内容のデコードは構造パーサの守備範囲外です（ロードマップ参照）。
- **アイドルパケット**（APID = `0x7FF`）はデフォルトで通常パケットと
  同様に返されます。`skip_idle=True` で除外できます。
- **strictモード（デフォルト）** はパケットバージョンが0でない場合に
  `InvalidPacketError` を投げます。多くの場合、バイト列のアラインメント
  ずれを意味します。`strict=False` で強制的にパースできます。
- すべての例外は `FastCCSDSError`（`ValueError` のサブクラス）から派生します。

## 関連プロジェクト

CCSDSスペースパケットを扱う成熟したPythonライブラリはすでに複数あり、
どれもパケット定義なしでパケットの分割とプライマリヘッダの読み取りが
できます。用途に合わせて選んでください。

| （2026年10月時点） | fast-ccsds-parser 0.1 | [ccsdspy] 2.0 | [space-packet-parser] 6.2 | [spacepackets] 0.32 |
| --- | --- | --- | --- | --- |
| 主な目的 | 構造の解析（ヘッダ、パケットの区切り、ペイロード） | パケット定義に基づくフィールドのデコード | XTCE定義に基づくフィールドのデコード | パケットの作成と解析（ECSS PUS、CFDPを含む） |
| 実行時の依存 | なし | numpy、bitstruct、pyyaml、appdirs | click、lxml、rich | fastcrc、deprecation |
| 定義なしでのヘッダ解析 | あり | あり（`ccsdspy.utils.read_primary_headers`、`split_by_apid`） | あり（`ccsds_generator`） | あり（`parse_space_packets`） |
| 表形式での出力 | 予定: Arrow / Polars / pandas | NumPy配列（pandasは [SPaC-Kit] 経由） | xarray（オプション） | - |
| リアルタイム入力 | 予定: asyncioでTCP / WebSocket | - | ソケット、UDP（同期処理） | dequeからの逐次解析 |
| 分割パケットの結合 | - | - | あり | - |

パケットの**中身**をミッションのパケット定義に従ってデコードしたい場合は、
[ccsdspy] か [space-packet-parser] を使ってください。どちらも成熟しており、
実際のミッションで使われています。ECSS PUSやCFDPの通信を作成・解析する
場合は [spacepackets] が適しています。fast-ccsds-parserは、大きなパケット
ダンプやリアルタイムのストリームを、少ないメモリですばやくデータ分析
ツールに渡すことに力を入れています。

[ccsdspy]: https://github.com/CCSDSPy/ccsdspy
[space-packet-parser]: https://github.com/lasp/space_packet_parser
[spacepackets]: https://github.com/ustgt-irs/spacepackets-py
[SPaC-Kit]: https://github.com/CCSDSPy/SPaC-Kit

## 性能

[`benchmarks/bench.py`](benchmarks/bench.py) で、処理速度と最大メモリ
使用量を計測できます（各ケースを別プロセスで実行）。32バイトの合成
パケット100万個（32 MB）、Python 3.12、Apple M4、ccsdspy 2.0.1、
space_packet_parser 6.2.0、pandas 3.0.6での結果:

| ケース | 時間 | 最大メモリ |
| --- | ---: | ---: |
| fast-ccsds-parser: ヘッダだけを読む | 0.47秒 | 53 MB |
| fast-ccsds-parser: `iter_file`（ストリーム処理） | 0.83秒 | 22 MB |
| fast-ccsds-parser: パケットごとのdictからpandasへ変換 | 2.37秒 | 670 MB |
| ccsdspy: `read_primary_headers` | 21.28秒 | 123 MB |
| ccsdspy: `iter_packet_bytes`（生バイトのみ） | 0.41秒 | 63 MB |
| space_packet_parser: `ccsds_generator`（生バイトのみ） | 0.36秒 | 72 MB |

この結果から分かること:

- `iter_file` によるストリーム処理は、すべてのケースの中で最もメモリ使用量が少ない。
- パケットごとのdictからDataFrameを作ると、メモリが入力サイズの約20倍に
  膨らむ。下記Phase 2aの列形式の出力は、これをなくすための設計。
- 生バイトを取り出すだけなら、ccsdspyとspace_packet_parserのほうが約2倍
  速い。`iter_file` はヘッダのデコードとペイロードのコピーも行っている
  ためで、今後の改善対象。

自分で実行する場合:

```console
pip install -e . ccsdspy space_packet_parser pandas
python benchmarks/bench.py                  # 合成データ
python benchmarks/bench.py path/to/dump.bin # 手元のダンプファイル
```

## ロードマップ

- **Phase 2a:** 列形式の出力（`to_arrow()`、`to_polars()`、`to_pandas()`）。
  メモリ使用量を入力サイズに近く保つ。Arrow / Polars / pandasは
  オプションの依存にする。
- **Phase 2b:** セカンダリヘッダのCCSDS時刻コード（CUC / CDS）を
  タイムスタンプに変換。
- **Phase 2c:** TCPソケット / WebSocketのリアルタイムストリームを
  asyncioで解析（WebSocketはオプションの依存）。
- **Phase 3:** ベンチマークで遅いと分かった処理をRust（PyO3）に置き換え。
  Python APIは変更なし。

## 開発

```console
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest            # オフラインテスト
ruff check .      # lint
```

`network` マーク付きのテストは、公開されている実衛星データのダンプを
その場でダウンロードして解析します。オフライン時は自動スキップ
（CIでは実行されます）。

## PyPIへのリリース

公開には[トラステッドパブリッシング]（APIトークン不要）を使います。
初回設定は [`.github/workflows/publish-testpypi.yml`](.github/workflows/publish-testpypi.yml)
に記載しています（ファイル名は経緯によるもので、公開先はPyPIです）。
`src/fast_ccsds_parser/__init__.py` の `__version__` を上げ、CHANGELOGを
追記してから:

```console
git tag vX.Y.Z
git push origin vX.Y.Z
```

ワークフローがsdist + wheelをビルドし、`twine check` を通して
[PyPI] へアップロードします。

[トラステッドパブリッシング]: https://docs.pypi.org/trusted-publishers/
[PyPI]: https://pypi.org/project/fast-ccsds-parser/

## ライセンス

[MIT](LICENSE)
