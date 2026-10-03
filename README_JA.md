# NDL Digital Collection Research MCP

国立国会図書館ラボ「次世代デジタルライブラリー」の公開APIを、ChatGPTなどのMCPクライアントから検索するための**読み取り専用MCPサーバー**です。

PERSON OF THE YEAR型の作問リサーチを想定し、単なる書誌検索だけでなく、古い図書・古典籍のOCR本文から人物名や固有名詞の言及を探し、前後文脈を取り出せるようにしてあります。

## 入っているツール

- `search_ndl` — OCR本文／書誌を横断検索
- `search_ndl_variants` — 旧字体・別表記・別名をまとめて検索
- `get_ndl_book_metadata` — PIDから書誌、刊年、目次などを取得
- `search_inside_ndl_book` — 1冊の中だけをOCR全文検索
- `extract_ndl_ocr_context` — 全文OCRから検索語の前後を広めに切り出す
- `get_ndl_ocr_segments` — OCR全文の連続断片を読む

## 1. ローカルで試す

Python 3.10以上を用意します。

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
mcp dev server.py
```

MCP Inspectorが起動したら `search_ndl` に、たとえば `keyword="福家梅太郎"` を入れて試してください。

通常のHTTPサーバーとして起動する場合：

```bash
python server.py
```

- MCP: `http://localhost:8000/mcp`
- ヘルスチェック: `http://localhost:8000/health`

## 2. インターネット上へ置く

ChatGPTはローカルPC上のMCPへ直接は接続しないため、ChatGPTから使うにはこのサーバーをHTTPSで公開します。

### RenderなどDocker対応サービスでの最短手順

1. このフォルダ一式をGitHubの新しいリポジトリへ入れる。
2. Render等で「Web Service」を作り、そのGitHubリポジトリを指定する。
3. Dockerfileを使ってデプロイする。
4. デプロイ後、ブラウザで `https://あなたのホスト名/health` を開き、`"status":"ok"` が返ることを確認する。
5. ChatGPTに登録するMCP URLは `https://あなたのホスト名/mcp`。

このサーバー自体にはログイン機能を付けていません。NDLの公開・読み取り専用データだけを扱いますが、URLを公開すると第三者にも呼ばれる可能性があります。個人利用でアクセス制限を付けたい場合は、デプロイ先のアクセス制御、またはMCP側の認証を追加してください。

## 3. ChatGPTから使う

カスタムMCPを接続できるプランでは、ChatGPT WebのDeveloper modeを有効にしてカスタムアプリを作成し、MCP endpointに以下を入れます。

```text
https://あなたのホスト名/mcp
```

ツールのスキャン後、NDL関連のツールが表示されれば接続成功です。

### 使い方の例

```text
NDL Digital Collection Researchを使って「福家梅太郎」をOCR本文検索して。
出てきた資料のうち、本人の経歴紹介ではなく、他の著名人・事件・制度との意外な接点があるものを優先して。
有望なPIDは資料内検索もし、パーオブの一振り目候補を5件、書名・刊年・該当文脈付きで出して。
```

```text
「芳賀徹」「芳賀 徹」の両表記でデジコレを検索し、Wikipediaや一般的な人物紹介に載りにくそうな逸話の種を探して。
OCR誤認識の可能性がある箇所はその旨も書いて。
```

```text
PID 1234567 の資料内で「三島由紀夫」を検索して。ヒットしたページの前後を extract_ndl_ocr_context で広めに読んで、
何が書かれているか要約して。
```

## 4. 作問リサーチでのおすすめ手順

1. `search_ndl` で人物名を `contentonly` 検索。
2. 表記揺れがありそうなら `search_ndl_variants`。
3. 面白そうな古い回想録、社史、随筆、雑誌系資料のPIDを拾う。
4. `get_ndl_book_metadata` で刊年・書誌を確認。
5. `search_inside_ndl_book` で、人物名＋相手の人物名や出来事を絞る。
6. `extract_ndl_ocr_context` で前後300〜800字程度を取得。
7. 最後にデジタルコレクション本体の紙面画像を開いてOCR誤認識を確認する。

## 5. 注意

- 対象は「次世代デジタルライブラリー」収録資料です。国会図書館デジタルコレクション全資料のOCR全文をこのAPIで取得できるわけではありません。
- OCRには旧字体、ルビ、縦書き、汚れなどに由来する誤認識があります。
- API仕様はNDL側で将来変更される可能性があります。
- 大量アクセスは避けてください。`search_ndl_variants` は一度に最大8検索に制限しています。

## 参照API

- NDLラボ 次世代デジタルライブラリーAPI: `https://lab.ndl.go.jp/service/tsugidigi/apiinfo/`
- 通常の国立国会図書館デジタルコレクション: `https://dl.ndl.go.jp/`
