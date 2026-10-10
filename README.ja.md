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

## 位置づけ

| | fast-ccsds-parser | ccsdspy | space-packet-parser |
| --- | --- | --- | --- |
| 目的 | 構造解析: パケットの分割・ラベル付けを高速に | パケット定義に基づくフィールド単位のデコード | XTCE定義に基づくデコード |
| 実行時依存 | なし | あり | あり |
| DataFrame / ストリーム連携 | ロードマップ（Phase 2） | - | - |

パケット**内容**をミッションのパケット定義に対してデコードしたい
場合は [ccsdspy] か [space-packet-parser] が優秀で実績もあります。
fast-ccsds-parserが担うのは一点: pandasやasyncioパイプラインに
そのまま差し込める、高速で依存ゼロの構造解析です。

[ccsdspy]: https://github.com/CCSDSPy/ccsdspy
[space-packet-parser]: https://pypi.org/project/space-packet-parser/

## 性能とロードマップ

Phase 1のコアは、プリコンパイル済み `struct` レイアウトと整数ビット演算に
よるシングルパスのパーサで、ファイルはメモリ一定でストリーム処理する
ため、メガ〜ギガバイト級のダンプも問題なく扱えます。

- **Phase 2（v0.2）:** `to_dataframe()`（オプション依存 `pandas`）と、
  TCPソケット / WebSocketのライブストリームに対するasyncioジェネレータ
  （オプション依存 `websocket`）。
- **Phase 3:** コアのホットループをRust（PyO3）に置き換え。Python APIは
  変更なし。

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
