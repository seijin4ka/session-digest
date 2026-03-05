# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ビルド・実行

```bash
# Docker（推奨）
docker compose up --build        # http://localhost:8000

# ローカル開発（ffmpeg が必要）
pip install -r requirements.txt
OPENAI_API_KEY=sk-... python main.py
```

環境変数: `.env` に `OPENAI_API_KEY` のみ（python-dotenv で読み込み）。テストやリンターは未導入。

## アーキテクチャ

シングルプロセスの FastAPI アプリで、非同期の音声処理パイプラインを実行する:

```
アップロード → FFmpeg分割 → 音声レベル解析 → Whisper API(無音スキップ)
→ ハルシネーション検出 → 結合 → GPT-4o生成 → ダウンロード
```

**2つのレイヤー:**
- `pipeline/` - 処理ステージ群。各モジュールは非同期。`orchestrator.py` がチェーンして進捗イベントを発行する。
- `storage/` - インメモリのジョブ状態（`dict`）と `/tmp/session-digest/{jobId}/` 配下の一時ファイル管理。

**パイプラインの流れ（`orchestrator.run_pipeline`）:**
1. `audio_splitter` - FFmpeg で10分チャンクに分割、30秒オーバーラップ（モノラル, 16kHz, 64kbps）
2. `silence_detector.analyze_chunks` - FFmpeg `volumedetect` で各チャンクの平均/最大音量を解析。平均 < -55dB かつ 最大 < -35dB の場合のみ無音判定（両方満たさないと無音にならない）
3. `transcriber` - Whisper API（言語自動検出、英語+日本語混在対応）、`asyncio.Semaphore(5)` で並列度制限、3回リトライ。無音チャンクはAPI呼び出しスキップ。
4. `silence_detector.check_hallucination` - 既知フレーズ検出、繰り返しパターン検出（60%以上）、テキスト密度チェック。ハルシネーション検出チャンクは結合から除外。
5. `transcript_merger` - タイムスタンプオフセットでオーバーラップ区間を重複除去し、タイムスタンプ付きテキストを生成。`skipped`/`hallucinated`チャンクは自動除外。
6. `document_generator` - GPT-4o で `prompts/` のテンプレートから3種類のドキュメントを並列生成（構造化ノート、全文書き起こし+要約、ハンズオン手順書）。

**無音/ハルシネーション対策:**
- 全チャンク無効 → `SilentAudioError` でエラー終了
- 一部無効 → 有効チャンクのみで処理続行、SSE `warning` イベントでユーザーに通知
- 無音チャンクの Whisper API 呼び出しをスキップしてコスト節約

**リアルタイム進捗:** `StreamingResponse` による SSE。`JobStore` が pub/sub（`asyncio.Queue`）でイベントを配信。イベントタイプ: `progress`（進捗）、`warning`（警告バナー）、`regenerated`（再生成完了）。進捗配分: 分割 0-5%, 文字起こし 5-75%, 結合 75-80%, 生成 80-98%。

**フロントエンド:** Jinja2テンプレート + vanilla JS + htmx。`index.html` がドラッグ&ドロップアップロード、`job.html` が SSE 接続とタブ切替で結果表示。

## 主要な設計方針

- すべてのI/Oは非同期（aiofiles, AsyncOpenAI, asyncio.create_subprocess_exec）
- OpenAI API呼び出しには `tenacity` による指数バックオフリトライを適用
- 部分的な失敗でパイプラインを止めない: 文字起こし失敗チャンクはエラープレースホルダーを挿入、ドキュメント生成失敗は `/api/jobs/{id}/regenerate/{doc_type}` で個別再生成可能
- Whisper APIの言語指定なし（自動検出）: 英語スピーカー+日本語通訳などの多言語音声に対応
- プロンプトテンプレートは `prompts/*.md` に配置、`{transcript}` プレースホルダーで書き起こしテキストを挿入
- 一時ファイル: チャンクは結合後に即削除、ジョブディレクトリ全体は24時間後に自動削除

## APIエンドポイント

- `GET /` - アップロードフォーム
- `POST /api/upload` - ファイルアップロード → パイプライン開始
- `GET /job/{job_id}` - ジョブ進捗ページ
- `GET /api/jobs/{job_id}/events` - SSEストリーム
- `GET /api/jobs/{job_id}` - ジョブ状態JSON
- `GET /api/jobs/{job_id}/download/{doc_type}` - ドキュメントダウンロード
- `POST /api/jobs/{job_id}/regenerate/{doc_type}` - ドキュメント個別再生成
