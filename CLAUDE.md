# CLAUDE.md

このファイルは Claude Code (claude.ai/code) がこのリポジトリで作業する際のガイドです。

## ビルド・実行

```bash
# Docker（推奨）
docker compose up --build        # http://localhost:8000

# ローカル開発（ffmpeg が必要）
pip install -r requirements.txt
OPENAI_API_KEY=sk-... python main.py
```

環境変数: `.env` に `OPENAI_API_KEY` のみ（python-dotenv で読み込み）。

## アーキテクチャ

シングルプロセスの FastAPI アプリで、非同期の音声処理パイプラインを実行する:

```
アップロード → FFmpeg分割 → Whisper API → 結合 → GPT-4o生成 → ダウンロード
```

**2つのレイヤー:**
- `pipeline/` - 処理ステージ群。各モジュールは非同期。`orchestrator.py` がチェーンして進捗イベントを発行する。
- `storage/` - インメモリのジョブ状態（`dict`）と `/tmp/session-digest/{jobId}/` 配下の一時ファイル管理。

**パイプラインの流れ（`orchestrator.run_pipeline`）:**
1. `audio_splitter` - FFmpeg で10分チャンクに分割、30秒オーバーラップ（モノラル, 16kHz, 64kbps）
2. `transcriber` - Whisper API、`asyncio.Semaphore(5)` で並列度制限、3回リトライ。失敗チャンクはプレースホルダーに置換。
3. `transcript_merger` - タイムスタンプオフセットでオーバーラップ区間を重複除去し、タイムスタンプ付きテキストを生成。
4. `document_generator` - GPT-4o で `prompts/` のテンプレートから3種類のドキュメントを並列生成。

**リアルタイム進捗:** `StreamingResponse` による SSE。`JobStore` が pub/sub（`asyncio.Queue`）でイベントを配信。進捗配分: 分割 0-5%, 文字起こし 5-75%, 結合 75-80%, 生成 80-98%。

**フロントエンド:** Jinja2テンプレート + vanilla JS + htmx。`index.html` がドラッグ&ドロップアップロード、`job.html` が SSE 接続とタブ切替で結果表示。

## 主要な設計方針

- すべてのI/Oは非同期（aiofiles, AsyncOpenAI, asyncio.create_subprocess_exec）
- OpenAI API呼び出しには `tenacity` による指数バックオフリトライを適用
- 部分的な失敗でパイプラインを止めない: 文字起こし失敗チャンクはエラープレースホルダーを挿入、ドキュメント生成失敗は `/api/jobs/{id}/regenerate/{doc_type}` で個別再生成可能
- プロンプトテンプレートは `prompts/*.md` に配置、`{transcript}` プレースホルダーで書き起こしテキストを挿入
- 一時ファイル: チャンクは結合後に即削除、ジョブディレクトリ全体は24時間後に自動削除
