# Session Digest

[![CI](https://github.com/seijin4ka/session-digest/actions/workflows/ci.yml/badge.svg)](https://github.com/seijin4ka/session-digest/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg)](https://www.docker.com/)
[![Docker Hub](https://img.shields.io/docker/v/seijin4ka/session-digest?label=Docker%20Hub&sort=semver)](https://hub.docker.com/r/seijin4ka/session-digest)
[![Issues](https://img.shields.io/github/issues/seijin4ka/session-digest)](https://github.com/seijin4ka/session-digest/issues)

長時間セミナーの録音ファイルをアップロードするだけで、AIが自動的に以下の3種類のドキュメントを生成するWebアプリです。

- **構造化ノート** - トピックごとに整理されたタイムスタンプ付きノート
- **全文書き起こし + 要約** - フィラー除去・整形済みの全文テキストと冒頭要約
- **ハンズオン手順書** - セミナーの実習内容を再現可能なステップバイステップ手順書に変換

4時間超のハンズオンセミナーにも対応。英語スピーカー+日本語通訳などの多言語音声も自動検出して処理します。

![Session Digest スクリーンショット](https://raw.githubusercontent.com/seijin4ka/session-digest/main/screen.png)

## 仕組み

```
音声アップロード
  → FFmpegで10分チャンクに分割（30秒オーバーラップ）
  → 音声レベル解析（無音チャンクを検出・スキップ）
  → OpenAI Whisper APIで並列文字起こし（言語自動検出）
  → ハルシネーション検出（架空テキストを除外）
  → オーバーラップ区間の重複除去・結合
  → GPT-4oで3種類のドキュメントを並列生成
  → ブラウザでプレビュー・ダウンロード
```

### 無音・ハルシネーション対策

Whisper APIは無音区間に対して架空のテキストを生成することがあります（ハルシネーション）。Session Digestでは2段階の防御策を実装しています。

1. **音声レベル解析（Pre-transcription）** - FFmpegの`volumedetect`で各チャンクの音量を解析し、無音チャンクのAPI呼び出しをスキップ
2. **ハルシネーション検出（Post-transcription）** - 既知の定型フレーズ検出、繰り返しパターン検出、テキスト密度チェックで架空テキストを除外

全チャンクが無効な場合はエラー表示、一部が無効な場合は有効部分のみで処理を続行し警告を表示します。

## セットアップ

### 必要なもの

- Docker & Docker Compose
- OpenAI APIキー

### 起動

```bash
# ビルド & 起動（.env ファイルは不要）
docker compose up --build
```

ブラウザで http://localhost:8000 にアクセス。Web UIからAPIキーを設定して利用開始できます。

#### APIキーの設定方法

**方法1: Web UIから設定（推奨）**
起動後にブラウザでアクセスし、トップページのフォームからAPIキーを入力するだけで利用開始できます。

**方法2: 環境変数で設定**
```bash
cp .env.example .env
# .env を編集して OPENAI_API_KEY を設定
docker compose up --build
```

環境変数で設定済みの場合はWeb UIでの設定は不要です。両方設定した場合はWeb UIの設定が優先されます。

### ローカル開発（Docker不使用）

```bash
# ffmpeg が必要
brew install ffmpeg  # macOS

pip install -r requirements.txt
python main.py
# ブラウザからAPIキーを設定、または OPENAI_API_KEY 環境変数を設定
```

## 対応フォーマット

MP3, M4A, WAV, WebM, MP4, OGG, FLAC, AAC

## 技術スタック

| レイヤー | 技術 |
|---------|------|
| バックエンド | Python, FastAPI, uvicorn |
| フロントエンド | Jinja2, vanilla JS |
| 文字起こし | OpenAI Whisper API（言語自動検出） |
| ドキュメント生成 | OpenAI GPT-4o |
| 音声処理 | FFmpeg |
| 進捗通知 | Server-Sent Events (SSE) |
| デプロイ | Docker Compose |
