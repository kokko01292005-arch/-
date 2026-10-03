from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Literal

import httpx
import uvicorn
from mcp.server import MCPServer
from starlette.responses import JSONResponse
from starlette.routing import Route

NDL_API_BASE = os.getenv("NDL_API_BASE", "https://lab.ndl.go.jp/dl/api").rstrip("/")
HTTP_TIMEOUT = float(os.getenv("NDL_HTTP_TIMEOUT", "90"))
PORT = int(os.getenv("PORT", "8000"))
USER_AGENT = os.getenv(
    "NDL_USER_AGENT",
    "ndl-digital-collection-mcp/1.0 (personal research tool)",
)

mcp = MCPServer(
    "NDL Digital Collection Research",
    instructions=(
        "国立国会図書館ラボ『次世代デジタルライブラリー』の公開APIを使う、読み取り専用の調査ツールです。"
        "人物・作品・事件などの古い文献上の言及を探すときは、まず search_ndl、表記揺れがある場合は "
        "search_ndl_variants を使ってください。資料PIDが得られたら get_ndl_book_metadata と "
        "search_inside_ndl_book で絞り込み、必要な場合だけ extract_ndl_ocr_context を使って本文前後を取得してください。"
        "OCRには誤認識があるため、重要な事実は複数資料または紙面画像でも確認してください。"
    ),
)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _pid(value: str | int) -> str:
    text = str(value).strip()
    if not re.fullmatch(r"\d+", text):
        raise ValueError("PIDは数字のみで指定してください。例: 897115")
    return text


async def _get_json(path: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{NDL_API_BASE}{path}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers, follow_redirects=True) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"NDL APIがタイムアウトしました: {url}") from exc
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500]
        raise RuntimeError(
            f"NDL APIがHTTP {exc.response.status_code}を返しました: {url}\n{body}"
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"NDL APIの取得またはJSON解析に失敗しました: {url}: {exc}") from exc


def _enrich_links(value: Any) -> Any:
    """返却JSON中のPIDらしい id に、人間が開けるURLを補助的に付ける。"""
    if isinstance(value, list):
        return [_enrich_links(v) for v in value]
    if not isinstance(value, dict):
        return value

    out = {k: _enrich_links(v) for k, v in value.items()}
    raw_id = str(out.get("id", ""))

    if raw_id.isdigit():
        out.setdefault("_ndl_digital_collection_url", f"https://dl.ndl.go.jp/pid/{raw_id}")
        out.setdefault("_ndl_lab_metadata_api", f"{NDL_API_BASE}/book/{raw_id}")
    else:
        match = re.fullmatch(r"(\d+)_(\d+)(?:_\d+)?", raw_id)
        if match:
            pid, page = match.group(1), match.group(2)
            out.setdefault("_ndl_digital_collection_url", f"https://dl.ndl.go.jp/pid/{pid}")
            out.setdefault(
                "_ndl_iiif_page_image",
                f"https://dl.ndl.go.jp/api/iiif/{pid}/R{int(page):07d}/full/full/0/default.jpg",
            )
    return out


def _iter_content_nodes(node: Any, path: str = "$"):
    """fulltext-json の構造が多少変わっても contents 文字列を拾えるよう再帰走査する。"""
    if isinstance(node, dict):
        contents = node.get("contents")
        if isinstance(contents, str) and contents.strip():
            meta: dict[str, Any] = {}
            for key in (
                "id",
                "page",
                "pageNo",
                "pageNum",
                "pageNumber",
                "sequence",
                "seq",
            ):
                if key in node and isinstance(node[key], (str, int, float)):
                    meta[key] = node[key]
            yield path, meta, contents
        for key, child in node.items():
            if key != "contents":
                yield from _iter_content_nodes(child, f"{path}.{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from _iter_content_nodes(child, f"{path}[{index}]")


@mcp.tool()
async def search_ndl(
    keyword: str,
    size: int = 20,
    offset: int = 0,
    searchfield: Literal["all", "contentonly", "metaonly"] = "contentonly",
    ndc_prefix: str = "",
    classics_only: bool | None = None,
    include_snippets: bool = True,
) -> dict[str, Any]:
    """次世代デジタルライブラリー収録資料を横断検索する。

    作問用の人物名・団体名・作品名などは searchfield='contentonly' が基本。
    書名・著者などだけを探す場合は 'metaonly'。本文と書誌の両方なら 'all'。
    keyword にヒットした前後はAPI側が最大3箇所までスニペットとして返す。
    """
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("keyword を空にすることはできません。")

    params: dict[str, Any] = {
        "keyword": keyword,
        "size": _clamp(size, 1, 50),
        "from": max(0, int(offset)),
    }
    if searchfield != "all":
        params["searchfield"] = searchfield
    if ndc_prefix.strip():
        params["f-ndc"] = ndc_prefix.strip()
    if classics_only is not None:
        params["fc-isClassic"] = "true" if classics_only else "false"
    if not include_snippets:
        params["withouthighlight"] = "true"

    result = await _get_json("/book/search", params)
    return {
        "query": params,
        "api": f"{NDL_API_BASE}/book/search",
        "result": _enrich_links(result),
        "note": "OCRには誤認識があります。重要な逸話は資料画像または別資料でも確認してください。",
    }


@mcp.tool()
async def search_ndl_variants(
    queries: list[str],
    size_each: int = 8,
    searchfield: Literal["all", "contentonly", "metaonly"] = "contentonly",
) -> dict[str, Any]:
    """表記揺れ・旧字体・別名をまとめて横断検索する。

    例: ['福家梅太郎', '福家 梅太郎', 'ふけ梅太郎'] のように渡す。
    一度に最大8クエリ。NDL側への負荷を避けるため順次問い合わせる。
    """
    cleaned: list[str] = []
    for q in queries:
        q = str(q).strip()
        if q and q not in cleaned:
            cleaned.append(q)
    cleaned = cleaned[:8]
    if not cleaned:
        raise ValueError("queries に1つ以上の検索語を指定してください。")

    results: list[dict[str, Any]] = []
    for index, q in enumerate(cleaned):
        params: dict[str, Any] = {
            "keyword": q,
            "size": _clamp(size_each, 1, 15),
            "from": 0,
        }
        if searchfield != "all":
            params["searchfield"] = searchfield
        raw = await _get_json("/book/search", params)
        results.append({"query": q, "result": _enrich_links(raw)})
        if index < len(cleaned) - 1:
            await asyncio.sleep(0.15)

    return {
        "queries": cleaned,
        "searchfield": searchfield,
        "results": results,
        "note": "同一資料が複数の表記で重複する場合があります。PIDでまとめてください。",
    }


@mcp.tool()
async def get_ndl_book_metadata(pid: str) -> dict[str, Any]:
    """PIDを指定して、書名・著者責任表示・出版社・刊年・NDC・目次などの書誌情報を取得する。"""
    pid = _pid(pid)
    raw = await _get_json(f"/book/{pid}")
    return {
        "pid": pid,
        "digital_collection_url": f"https://dl.ndl.go.jp/pid/{pid}",
        "metadata_api": f"{NDL_API_BASE}/book/{pid}",
        "result": _enrich_links(raw),
    }


@mcp.tool()
async def search_inside_ndl_book(
    pid: str,
    query: str,
    size: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """1冊の資料内だけをOCR全文検索し、ヒットしたコマとスニペットを取得する。

    横断検索で有望なPIDを見つけた後、人物名・別の固有名詞・出来事などで絞り込む用途に向く。
    """
    pid = _pid(pid)
    query = query.strip()
    if not query:
        raise ValueError("query を空にすることはできません。")

    params = {
        "f-book": pid,
        "q-contents": query,
        "size": _clamp(size, 1, 50),
        "from": max(0, int(offset)),
        "searchfield": "contentonly",
    }
    raw = await _get_json("/page/search", params)
    return {
        "pid": pid,
        "query": query,
        "digital_collection_url": f"https://dl.ndl.go.jp/pid/{pid}",
        "result": _enrich_links(raw),
    }


@mcp.tool()
async def extract_ndl_ocr_context(
    pid: str,
    query: str,
    context_chars: int = 300,
    max_hits: int = 12,
) -> dict[str, Any]:
    """資料全体のOCR JSONを取得し、検索語の前後だけを切り出す。

    APIのスニペットより広い前後関係が必要なときに使う。全文をそのまま返さないため、
    ChatGPTのコンテキストを圧迫しにくい。単純部分一致なので、見つからない場合は
    search_inside_ndl_book の検索結果を優先すること。
    """
    pid = _pid(pid)
    query = query.strip()
    if not query:
        raise ValueError("query を空にすることはできません。")

    context_chars = _clamp(context_chars, 80, 1200)
    max_hits = _clamp(max_hits, 1, 30)
    raw = await _get_json(f"/book/fulltext-json/{pid}")

    needle = query.casefold()
    hits: list[dict[str, Any]] = []

    for path, meta, text in _iter_content_nodes(raw):
        haystack = text.casefold()
        cursor = 0
        while len(hits) < max_hits:
            index = haystack.find(needle, cursor)
            if index < 0:
                break
            left = max(0, index - context_chars)
            right = min(len(text), index + len(query) + context_chars)
            hits.append(
                {
                    "path": path,
                    "page_hint": meta,
                    "context": text[left:right],
                    "match_offset_in_segment": index,
                }
            )
            cursor = index + max(1, len(query))
        if len(hits) >= max_hits:
            break

    return {
        "pid": pid,
        "query": query,
        "digital_collection_url": f"https://dl.ndl.go.jp/pid/{pid}",
        "fulltext_api": f"{NDL_API_BASE}/book/fulltext-json/{pid}",
        "hits": hits,
        "hit_count_returned": len(hits),
        "note": (
            "ここでの抽出はOCR JSONへの単純部分一致です。0件でもAPI側の全文検索でヒットする場合があります。"
            if not hits
            else "OCR誤認識や改ページを考慮し、重要箇所はデジタルコレクションの紙面画像でも確認してください。"
        ),
    }


@mcp.tool()
async def get_ndl_ocr_segments(
    pid: str,
    start_segment: int = 0,
    segment_count: int = 5,
    max_total_chars: int = 18000,
) -> dict[str, Any]:
    """資料のOCR全文JSONから、contents単位の連続したテキスト断片を取り出す。

    search_inside_ndl_book や extract_ndl_ocr_context で場所を把握した後、もう少し広く読みたい場合に使う。
    segment番号はこのツール内で再帰走査した順番であり、必ずしも印刷ページ番号と一致しない。
    """
    pid = _pid(pid)
    start_segment = max(0, int(start_segment))
    segment_count = _clamp(segment_count, 1, 12)
    max_total_chars = _clamp(max_total_chars, 1000, 30000)

    raw = await _get_json(f"/book/fulltext-json/{pid}")
    nodes = list(_iter_content_nodes(raw))
    selected = nodes[start_segment : start_segment + segment_count]

    remaining = max_total_chars
    segments: list[dict[str, Any]] = []
    for local_index, (path, meta, text) in enumerate(selected, start=start_segment):
        if remaining <= 0:
            break
        clipped = text[:remaining]
        segments.append(
            {
                "segment_index": local_index,
                "path": path,
                "page_hint": meta,
                "text": clipped,
                "truncated": len(clipped) < len(text),
            }
        )
        remaining -= len(clipped)

    return {
        "pid": pid,
        "digital_collection_url": f"https://dl.ndl.go.jp/pid/{pid}",
        "total_content_segments": len(nodes),
        "segments": segments,
    }


async def health(_request):
    return JSONResponse(
        {
            "status": "ok",
            "service": "NDL Digital Collection Research MCP",
            "mcp_endpoint": "/mcp",
            "ndl_api_base": NDL_API_BASE,
        }
    )


async def index(_request):
    return JSONResponse(
        {
            "name": "NDL Digital Collection Research MCP",
            "status": "running",
            "mcp_endpoint": "/mcp",
            "health": "/health",
            "source": "National Diet Library Lab Next Digital Library API",
        }
    )


app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
    custom_starlette_routes=[
        Route("/", endpoint=index, methods=["GET"]),
        Route("/health", endpoint=health, methods=["GET"]),
    ],
)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
