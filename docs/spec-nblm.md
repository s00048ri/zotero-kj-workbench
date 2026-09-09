# Zotero → Gemini Notebook 送信ツール 実装仕様書

**版:** v1.0 (2026-09-09)
**宛先:** Claude Code
**想定環境:** macOS (Mac mini M4), Zotero 7/8, Google Chrome, Python 3.12+, Node 20+

---

## 0. この文書の使い方

本仕様は**3つのコンポーネント**からなる1つのモノレポを定義する。
Phase 0（検証）を必ず先に実施し、その結果を `docs/findings.md` に記録してから実装に入ること。
Phase 0 の結果次第で Track B の設計が変わるため、検証をスキップしてはならない。

未確定事項は本文中に `⚠️ 要検証` として明示している。推測で埋めずに、検証して確定させること。

---

## 1. ゴール

Zotero のライブラリで文献を複数選択 → 右クリック → 添付PDFを Gemini Notebook（旧 NotebookLM）の指定ノートブックにソースとして一括登録する。

### UX 要件

- **選択は複数対応**。1件だけの操作に最適化しない。
- 右クリックメニューは「Gemini Notebook に送る」＋サブメニューで送信先ノートブックを選択。
- 送信先には「新規ノートブックを作成（コレクション名を使用）」も含める。
- 送信はバックグラウンドで進み、UI をブロックしない。進捗と結果は通知で返す。
- 一度送った文献は既定でスキップする（後述の重複防止）。

### 非ゴール（v1 では作らない）

- 音声概要・スライド等の Studio 生成の自動起動（v2 で検討）
- Zotero への書き戻し（生成物のリンクをノートに保存する等）
- Windows / Linux 対応（動けばよいが検証しない）

---

## 2. 前提知識（実装者への背景説明）

### 2.1 Gemini Notebook 側

- 2026年7月に NotebookLM から Gemini Notebook へリブランドされた。同一サービスで旧リンクは自動転送される。
- **消費者版に公開APIは存在しない。** 公式に文書化されているのは Gemini Notebook Enterprise のプレビューAPIのみで、本プロジェクトの対象外。
- したがって非公式経路を使う。既存の実装として `notebooklm-py`（Python）と `notebooklm-go`（Go）があり、いずれも内部の `batchexecute` RPC を直接叩く。`notebooklm-py` は Playwright によるブラウザ自動化を自動フォールバックとして併用する二重構成。
- **本プロジェクトは `notebooklm-py` を依存ライブラリとして採用する。** 自前で RPC を再実装しない。ただし後述の Track B ステップ3のみ例外。
- 非公式APIは予告なく壊れる。**壊れたときに利用者のデータが失われない設計**にすること（失敗したジョブはローカルに残し、対象ファイルのパスを提示して手動アップロードに退避できるようにする）。

### 2.2 Zotero 側

- Zotero 7 以降は正式なプラグイン機構（.xpi / JavaScript）を持つ。アイテムツリーの右クリックメニュー拡張は標準的な拡張ポイント。
- Zotero デスクトップは `http://localhost:23119/api/` に **読み取り専用のローカルAPI** を公開している。Web API と同じエンドポイント体系でローカルDBを参照でき、オフラインで動きレート制限がない。
  - 設定 → 詳細 → 「他のアプリケーションがこのコンピュータ上の Zotero と通信することを許可」が **ON である必要がある**。
  - User-Agent が `Mozilla/` で始まるリクエスト（＝ブラウザからのリクエスト）は既定で 403 になるが、**`zotero-allowed-request: 1` ヘッダを付けると許可される**。これが Track B の成立条件。
- Zotero Web API は `https://api.zotero.org` で、`Zotero-API-Key` ヘッダによる認証。ローカルAPIが使えない場合のフォールバック。

---

## 3. アーキテクチャ

```
┌─────────────────────────┐
│ Track A                 │
│ Zotero 7 プラグイン      │──┐
│ (右クリックメニュー)      │  │
└─────────────────────────┘  │
                             │   共通 JSON 契約 (§4)
┌─────────────────────────┐  ├──▶ ┌──────────────────┐     ┌──────────────────┐
│ Track B                 │  │    │ nbbridge         │────▶│ Gemini Notebook  │
│ Chrome 拡張 (MV3)        │──┘    │ (Python/FastAPI) │     │ (notebooklm-py)  │
│ (zotero.org 上で右クリック)│       │ 127.0.0.1:8787   │     └──────────────────┘
└─────────────────────────┘       └──────────────────┘
```

**設計原則:** 2つのフロントエンドは同じ JSON 契約で `nbbridge` を叩く。Gemini Notebook との通信ロジックは `nbbridge` にのみ存在し、フロントエンドには一切書かない。これにより非公式APIの破壊的変更が1箇所で吸収される。

Track B には将来的にブリッジを介さない直接モード（§8.4）があるが、**v1 のスコープ外**。まずブリッジ経由で完成させる。

---

## 4. 共通契約: nbbridge HTTP API

すべて `127.0.0.1` のみに bind。外部インターフェースに bind してはならない。

### 認証

- 起動時にトークンを生成し `~/.config/nbbridge/token` に 0600 で保存。
- 全リクエストに `Authorization: Bearer <token>` を要求。
- CORS: `chrome-extension://<拡張ID>` を許可オリジンとして設定ファイルから読む。ワイルドカード禁止。

### エンドポイント

#### `GET /health`
```json
{ "status": "ok", "version": "0.1.0", "auth_state": "valid" | "expired" | "unknown" }
```
`auth_state` は Gemini Notebook のセッション有効性。フロントエンドは起動時にこれを見て、期限切れなら再ログインを促す。

#### `GET /notebooks`
```json
{ "notebooks": [ { "id": "abc-123", "title": "AI Governance", "source_count": 42 } ] }
```
結果は 60 秒キャッシュする。

#### `POST /jobs`
リクエスト:
```json
{
  "target": { "notebook_id": "abc-123" },
  "options": { "skip_duplicates": true, "attach_metadata_note": true },
  "items": [ { /* ItemPayload */ } ]
}
```
`target` は `notebook_id` または `create_title`（新規作成）のいずれか一方。

レスポンス: `202 Accepted`
```json
{ "job_id": "j_01H...", "accepted": 12, "skipped": 3 }
```

#### `GET /jobs/{job_id}`
```json
{
  "job_id": "j_01H...",
  "state": "running" | "done" | "failed",
  "progress": { "total": 12, "done": 9, "failed": 1 },
  "results": [
    { "zotero_key": "ABCD1234", "state": "done", "source_id": "src_xyz" },
    { "zotero_key": "EFGH5678", "state": "failed", "error": "upload_rejected",
      "detail": "...", "fallback_path": "/Users/ren/Zotero/storage/EFGH/paper.pdf" }
  ]
}
```

#### `POST /jobs/{job_id}/blob`
Track B 用。ファイル実体をブラウザから送るための multipart エンドポイント。
フォームフィールド: `zotero_key`, `file`（バイナリ）。

### ItemPayload

```jsonc
{
  "zotero_key": "ABCD1234",        // 必須。重複判定キー
  "library_id": 123456,            // 必須
  "title": "Governing Agentic AI", // 必須
  "creators": "Iida, R.; Tanaka, K.",
  "year": 2026,
  "doi": "10.1234/xyz",
  "url": "https://...",
  "collection_path": "AI Governance/Agentic AI",

  "source": {
    "kind": "local_path" | "blob_pending" | "url",
    "path": "/Users/ren/Zotero/storage/ABCD/paper.pdf",  // kind=local_path のとき
    "file_name": "paper.pdf",
    "mime": "application/pdf",
    "size": 1234567
  }
}
```

- **Track A** は必ず `kind: "local_path"` を使う。ブリッジが同一マシンのファイルを直接読むため、バイト列を HTTP に載せない。
- **Track B** は `kind: "blob_pending"` で `POST /jobs` した後、`/jobs/{id}/blob` に実体を送る。ただし §8.2 のローカルAPI経路が成立する場合は Track B も `local_path` を使えるので、そちらを優先する。

---

## 5. Phase 0: 検証タスク（実装前に必須）

各項目の結果を `docs/findings.md` に、実行したコマンドと生の出力ごと記録すること。

### V1. Zotero ローカルAPI のファイル取得可否 【最重要】

Track B の設計を左右する。

```bash
# 1) ローカルAPI が有効か
curl -H 'zotero-allowed-request: 1' http://localhost:23119/api/users/0/items/top?limit=1

# 2) 添付アイテムのキーを1つ取得したうえで、ファイル実体が取れるか
curl -i -H 'zotero-allowed-request: 1' \
  http://localhost:23119/api/users/0/items/<ATTACHMENT_KEY>/file
```

- **200 でバイナリが返る場合** → Track B はローカルAPIから直接ファイルを取れる。DOM スクレイピングも Web API キーも不要になり、大幅に簡単になる。
- **404 / 未実装の場合** → Track B は Zotero Web API（要APIキー・要クラウド同期）にフォールバックする。§8.3 を採用。

さらに、Chrome 拡張の service worker から同じリクエストが通るかも確認すること（`host_permissions` に `http://localhost:23119/*` を追加した状態で）。curl で通ってもブラウザからは弾かれる可能性がある。

### V2. notebooklm-py の疎通

捨てても構わない検証用ノートブックを作り、以下を確認：

- ログインとセッション永続化の手順。トークンはどこに保存されるか。
- ローカルPDFファイルをソースとして追加できるか（**既存の YouTube URL 送信とはコードパスが別**。URL は文字列を渡すだけだが、ファイルはアップロード経路を通る）。
- 大きめのPDF（30MB 程度）が通るか。サイズ上限は何か。
- 連続投入したときのレート制限の挙動。何秒間隔なら安定するか。
- 認証が切れたときにどんな例外／エラーが返るか（`auth_state` の判定に使う）。

### V3. Zotero プラグインの選択取得

`zotero-plugin-template` で最小プラグインを立ち上げ、以下を確認：

- アイテムツリーの右クリックメニューにサブメニュー付きの項目を追加できるか。
- `ZoteroPane.getSelectedItems()` で複数選択が取れるか。
- 各アイテムから添付PDFのローカルパスが取れるか（`item.getAttachments()` → `getFilePath()`）。**リンク添付（linked file）と保存添付（imported file）で挙動が違う点に注意。**
- 添付が複数ある場合、EPUB がある場合の扱い。

### V4. Zotero ウェブ版の DOM 構造 ⚠️ 要検証

V1 が 200 を返した場合でも、「どのアイテムを右クリックしたか」を知るには DOM が必要。

- `https://www.zotero.org/<username>/library` のアイテム行から item key を取り出す方法（data 属性、リンクの href、URL ハッシュのいずれか）。
- 複数選択の状態を DOM から判別できるか。**できない場合は、拡張側で選択状態を自前管理する UI に切り替える**（行クリックでチェックを付ける等）。この分岐は設計に大きく影響するので早期に確定させること。

---

## 6. リポジトリ構成

```
zotero-nblm/
├── README.md
├── docs/
│   ├── findings.md          # Phase 0 の検証結果
│   └── spec.md              # 本文書
├── bridge/                  # Python / FastAPI
│   ├── pyproject.toml
│   ├── nbbridge/
│   │   ├── __main__.py
│   │   ├── api.py           # FastAPI ルーティング
│   │   ├── queue.py         # ジョブキュー（単一ワーカー、逐次実行）
│   │   ├── nblm.py          # notebooklm-py ラッパー。ここだけが外部APIを知る
│   │   ├── ledger.py        # 送信済み台帳 (SQLite)
│   │   └── config.py
│   └── tests/
├── zotero-plugin/           # Track A: TypeScript
│   ├── package.json
│   ├── src/
│   │   ├── index.ts
│   │   ├── menu.ts          # 右クリックメニュー登録
│   │   ├── collect.ts       # 選択アイテム → ItemPayload
│   │   └── bridge.ts        # nbbridge クライアント
│   └── addon/manifest.json
└── chrome-extension/        # Track B: TypeScript + Vite + CRXJS
    ├── package.json
    ├── manifest.json
    └── src/
        ├── background.ts    # service worker
        ├── content.ts       # zotero.org 上で動作
        ├── zotero.ts        # ローカルAPI / Web API クライアント
        ├── bridge.ts        # nbbridge クライアント（zotero-plugin と同ロジック）
        └── options/
```

`bridge.ts` は両フロントエンドで実質同じになる。無理に共有パッケージ化せず、**まず複製してよい**。2つが安定してから抽出する。

---

## 7. Track A: Zotero 7 プラグイン

### 技術スタック

- `windingwind/zotero-plugin-template` を土台にする（TypeScript, zotero-plugin-scaffold）。
- `npm run start` で Zotero がプラグイン込みで起動する開発ループを最初に確立すること。

### 実装項目

1. **メニュー登録** — アイテムツリーの右クリックに「Gemini Notebook に送る」を追加。サブメニューは `GET /notebooks` の結果を動的に構築する。ブリッジが応答しない場合はメニューを無効化し、理由をツールチップに出す。

2. **選択の収集** — `ZoteroPane.getSelectedItems()` から ItemPayload の配列を作る。
   - 通常アイテムなら子添付を辿って PDF / EPUB を探す。
   - 添付アイテムが直接選択されていればそれを使う。
   - **コレクションが右クリックされた場合は、その配下（サブコレクション含む）の全アイテムを対象にする**。ユーザーは章立てとしてサブコレクションを使っているため、この経路の需要が高い。
   - PDF が見つからないアイテムは、DOI または URL があれば `kind: "url"` にフォールバックする。それもなければスキップし、件数を結果に含める。

3. **送信** — `POST /jobs` して job_id を受け取り、ポーリング（2秒間隔）で進捗を Zotero の ProgressWindow に表示。

4. **設定画面** — ブリッジのURL、トークン、既定の送信先ノートブック、重複スキップの ON/OFF。

---

## 8. Track B: Chrome 拡張 (MV3)

### 8.1 技術スタック

- TypeScript + Vite + **CRXJS** プラグイン（ホットリロードのため）。
- Manifest V3。background は service worker（常駐しない前提で書くこと）。

### manifest.json の骨子

```jsonc
{
  "manifest_version": 3,
  "name": "Zotero → Gemini Notebook",
  "version": "0.1.0",
  "permissions": ["storage", "contextMenus", "notifications"],
  "host_permissions": [
    "https://www.zotero.org/*",
    "http://localhost:23119/*",
    "http://127.0.0.1:8787/*",
    "https://api.zotero.org/*"
  ],
  "background": { "service_worker": "src/background.ts", "type": "module" },
  "content_scripts": [
    { "matches": ["https://www.zotero.org/*/library*"], "js": ["src/content.ts"] }
  ],
  "options_page": "src/options/index.html"
}
```

### 8.2 ファイル取得の優先順位

V1 の結果に応じて、以下のチェーンを実装する。実行時に順に試し、最初に成功した経路を使う。

1. **ローカルAPI経路（第一候補）** — `http://localhost:23119/api/users/0/items/{key}/file` に `zotero-allowed-request: 1` を付けて取得。Zotero デスクトップが起動している必要がある。この経路が使える場合、ItemPayload は `kind: "local_path"` にできる可能性がある（ブリッジが同じマシンにいるため）。パスが取れるなら実体転送を省略する。
2. **Web API経路（フォールバック）** — `https://api.zotero.org/users/{userID}/items/{key}/file` に `Zotero-API-Key` ヘッダ。APIキーは options ページで設定。クラウド同期済みであることが前提。
3. どちらも失敗したら、そのアイテムを `skipped` として理由付きで報告する。

### 8.3 右クリックの実装

`chrome.contextMenus` はクリック対象の DOM 要素を教えてくれない。以下の標準パターンを使う：

1. content script が `document.addEventListener('contextmenu', ...)` で最後に右クリックされた要素を捕捉。
2. そこから item key を解決（V4 の結果に依存）。
3. `chrome.runtime.sendMessage` で service worker に保存。
4. メニュークリック時に service worker がその key を使う。

**V4 で複数選択が DOM から判別できないと分かった場合**は、この方式を捨てて、content script が独自のチェックボックス列をアイテム行に注入する方式に切り替える。選択状態は content script のメモリに保持し、ツールバーの「送信」ボタンで確定する。仕様変更としてこちらを正式に採用してよい。

### 8.4 直接モード（v2 スコープ・実装しない）

拡張から `fetch(..., { credentials: 'include' })` で Gemini Notebook の内部 RPC を直接叩けば、Cookie が自動で乗るためブリッジも再認証機構も不要になる。ただし `batchexecute` のネストした配列ペイロードを JS で再実装する必要があり、`notebooklm-py` の資産が使えない。

**v1 では実装しない。** ただし Phase 0 で以下だけ調べて `findings.md` に残しておくこと：認証トークン（`at`）がページのどこから取得できるか、単純な list 系 RPC が拡張の fetch から 200 で返るか。v2 の着手判断に使う。

---

## 9. 共通の設計事項

### 9.1 重複防止（送信済み台帳）

`~/.local/share/nbbridge/ledger.db` (SQLite):

```sql
CREATE TABLE sent (
  zotero_key   TEXT NOT NULL,
  library_id   INTEGER NOT NULL,
  notebook_id  TEXT NOT NULL,
  source_id    TEXT,
  file_hash    TEXT,              -- SHA-256。PDF差し替えを検知する
  sent_at      TEXT NOT NULL,
  PRIMARY KEY (zotero_key, library_id, notebook_id)
);
```

`skip_duplicates: true` のとき、同じ `(key, library, notebook)` の組が存在し `file_hash` も一致すればスキップ。ハッシュが違えば「更新版」として送信し、台帳を上書きする。

### 9.2 メタデータの同時投入

PDF だけを送ると書誌情報が失われ、Gemini Notebook 側の引用が「paper.pdf」のような無意味な表示になる。以下を必ず実装する：

- ソースのタイトルを `著者 (年) タイトル` の形式にリネームする（notebooklm-py がリネームに対応していれば）。
- `attach_metadata_note: true` のとき、ジョブ末尾に**テキストソースを1件追加**し、そのジョブで送った全文献の書誌一覧（著者・年・タイトル・DOI・Zoteroキー）を投入する。これによりノートブック内で文献同定ができるようになる。

### 9.3 レート制御とリトライ

- ワーカーは**単一・逐次**。並列アップロードはしない。
- アイテム間に設定可能なスリープ（既定 3 秒、V2 の結果で調整）。
- 失敗時は指数バックオフで最大2回リトライ。3回目の失敗で `failed` 確定。
- 認証エラー（`auth_state: expired`）を検知したら**ジョブ全体を即座に停止**し、残りを `pending` のまま保持する。再認証後に `POST /jobs/{id}/resume` で再開できるようにする。

### 9.4 エラー時のフォールバック

失敗したアイテムの結果には必ず `fallback_path`（ローカルの実ファイルパス）を含める。UI 側は「失敗した N 件のファイルを Finder で表示」を提供し、手動アップロードに退避できるようにする。**非公式APIが壊れた日に作業が完全に止まらないこと**が要件。

---

## 10. マイルストーン

| # | 内容 | 完了条件 |
|---|---|---|
| M0 | Phase 0 検証 | `docs/findings.md` に V1–V4 の結果が記録されている |
| M1 | nbbridge 単体 | CLI から `nbbridge send --notebook X file.pdf` でPDFが登録できる |
| M2 | bridge HTTP API | `/health` `/notebooks` `/jobs` が動き、curl で1件送れる |
| M3 | Zotero プラグイン | 右クリック → 単一アイテム送信が通る |
| M4 | Track A 完成 | 複数選択・コレクション選択・重複スキップ・進捗表示 |
| M5 | Chrome 拡張 | zotero.org 上から複数件送信が通る |
| M6 | 仕上げ | メタデータ投入、リトライ、失敗時フォールバックUI |

M3 まで来た時点で一度実利用してから M5 に進むこと。実際に使うと要件が変わる可能性が高い。

---

## 11. 実装者への指示

- **Phase 0 を飛ばさない。** V1 と V4 の結果次第で Track B の設計が根本的に変わる。
- 検証で判明した事実と、推測・仮定を `findings.md` 上で明確に区別すること。
- `notebooklm-py` のバージョンを `pyproject.toml` でピン留めする。非公式ライブラリなので勝手に上がると壊れる。
- ブリッジは絶対に `0.0.0.0` に bind しない。認証トークンを省略しない。
- 秘密情報（Google セッション、Zotero APIキー）をログに出力しない。
- 各マイルストーンで、動くものを見せてから次に進む。一気に全部書かない。

---

## 12. 未解決事項

以下は Phase 0 完了時点で本仕様を改訂して確定させること。

1. Zotero ローカルAPI で添付ファイル実体が取得できるか（V1）
2. Chrome 拡張の service worker からローカルAPIへのリクエストが通るか（V1）
3. Zotero ウェブ版で複数選択を DOM から判別できるか、独自UI注入が必要か（V4）
4. Gemini Notebook のファイルサイズ上限と安定する投入間隔（V2）
5. ソース名のリネームが `notebooklm-py` で可能か（§9.2 の設計に影響）
