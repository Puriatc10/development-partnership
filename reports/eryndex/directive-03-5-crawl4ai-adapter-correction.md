# Directive 03.5 — Crawl4AI Adapter Contract Correction

**Date:** 2026-11-14
**Scope:** `integrations/crawl4ai/adapter.py`, `tests/test_crawl4ai_adapter_contract.py`
**Upstream pin:** `crawl4ai==0.9.5`
**Live verification: STILL PENDING.** `endpoint_verified` remains `False`; every result carries an "unverified" warning. No container was started, no Docker resource touched, no migration authored or applied.

---

## 1. How the upstream contract was verified

`raw.githubusercontent.com` is unreachable from this host, so the adapter was checked against the **actual 0.9.5 wheel** installed from PyPI into a throwaway target directory, not against documentation:

```
uv pip install "crawl4ai==0.9.5" --no-deps --target <tmp>/c4a_pkg
```

Facts taken from that source, with line references:

| Fact | Evidence in `crawl4ai==0.9.5` |
|---|---|
| `MarkdownGenerationResult` = `raw_markdown`, `markdown_with_citations`, `references_markdown`, `fit_markdown`, `fit_html` | `crawl4ai/models.py:120-128` |
| `CrawlResult.markdown` is a private attr re-emitted by a `model_dump` override → **nested object over HTTP** | `models.py:150`, `models.py:254-270` |
| `CacheMode` values are lowercase: `enabled`, `disabled`, `read_only`, `write_only`, `bypass` | `crawl4ai/cache_context.py:16-20` |
| The untrusted gate validates `cache_mode.lower()` against that set | `crawl4ai/async_configs.py:462-467` |
| Config travels as `{"type": <ClassName>, "params": {...}}`, parsed by `CrawlerRunConfig.load(..., provenance=UNTRUSTED)` | `async_configs.py:1280-1288` |
| Untrusted fields are filtered against an allowlist | `async_configs.py:283-330` |
| `CrawlResult.links` is `Dict[str, List[Dict]]` (internal/external) | `models.py:142` |
| No per-result id field exists on `CrawlResult` | `models.py:130-160` |

What could **not** be verified from the wheel: the HTTP route file (`deploy/docker/api.py`) ships only in the repo, not the wheel, and GitHub is unreachable. The envelope (`urls`, `browser_config`, `crawler_config`) is therefore unchanged from the previously documented surface — but every field now sent is one the allowlist above proves the server accepts.

## 2. Defects confirmed and fixed

| # | Defect | Was | Now |
|---|---|---|---|
| 1 | **Nested markdown read as text** | `item["markdown"] or …` passed the dict straight through: `str()` produced a dict repr, the excerpt was `{'raw_markdown': …}` and `content_hash` hashed that repr | `normalize_markdown()` walks `raw_markdown` → `markdown_with_citations` → `references_markdown` → `fit_markdown` (one level of nesting tolerated), returns `""` when no string body exists; the hash is taken over the normalized text |
| 2 | **Uppercase `cache_mode`** | `"BYPASS"` / `"ENABLED"` | canonical lowercase `bypass` / `enabled`, matching the enum, sent inside the typed envelope |
| 3 | **`extract` validated then dropped** | modes accepted, never expressed; the response always returned everything | modes are mapped onto allowlisted `CrawlerRunConfig` fields — `text` → `only_text: true` (only when text is the sole mode), `links` → `exclude_external_links: false` + `exclude_social_media_links: false`; unsupported modes (e.g. `html`, `llm`, `screenshot`) are rejected with `schema_violation` before any request |
| 4 | **`browser_config` never sent** | documented in the module docstring, absent from the body | forwarded as `{"type": "BrowserConfig", "params": {...}}` after validation against a 17-field allowlist with per-field JSON types (booleans rejected where numbers are expected); unknown keys such as `proxy` are refused, not silently forwarded; omitted entirely when not supplied so server defaults apply |
| 5 | **Provenance invented nothing but under-reported** | no provider id, no statement about it | `provider_result_id` is explicitly `None` with the limitation documented; Evidence carries url, title, provider, provider_version, `retrieved_at` and `content_hash` |

## 3. Before / after

**Before — request**

```json
{
  "urls": ["https://example.com/"],
  "crawler_config": {"cache_mode": "BYPASS"}
}
```

**After — request**

```json
{
  "urls": ["https://example.com/"],
  "crawler_config": {
    "type": "CrawlerRunConfig",
    "params": {"cache_mode": "bypass", "only_text": true}
  }
}
```
(with `browser_config: {"type": "BrowserConfig", "params": {...}}` added only when supplied)

**Before — response handling.** Input `markdown` = `{"raw_markdown": "# Example Domain…", …}` produced excerpt `{'raw_markdown': '# Example Domain…'}` and `content_hash = sha256(str(dict))`.

**After — response handling.** The same input yields excerpt `# Example Domain…`, `content_hash = sha256("# Example Domain…")`, `title: "Example Domain"`, `markdown_chars`, and links only when requested.

## 4. Tests

New file `tests/test_crawl4ai_adapter_contract.py` — **24 tests**, deterministic, fake transport, no server:

markdown as nested dict · markdown as plain string · unusable markdown fails closed · `normalize_markdown` never stringifies a mapping · `cache_mode` lowercase for both values · extraction maps to allowlisted fields and never sends an `extract` key · text-only sets `only_text` and returns `links: None` · four unsupported-mode rejections (and the transport is never called) · browser config forwarded · browser config omitted by default · three invalid browser-config rejections before the wire · content hash over normalized text (explicitly different from `sha256(str(dict))`) · every markdown field name known to the normalizer · no-results fails closed · partial page → `partial` with one evidence row · 401 not retried · 504/`unavailable` retried exactly 3 times then surfaced · adapter still declares itself unverified.

```
tests/test_crawl4ai_adapter_contract.py   24 passed in 0.05s
full suite                                360 collected → all pass, 6 skipped
```

The 6 skips remain the database tests (`ERYNDEX_DB_URL` unset) — reported as skipped, not passed.

## 5. Remaining limitations

1. **No live server.** The envelope shape and error taxonomy are unchanged and still unproven against a running container; `endpoint_verified` must stay `False` until a real crawl succeeds. Recovery procedure: `reports/eryndex/docker-restart-plan.md`.
2. **No provider-issued result id** exists in v0.9.5, so evidence is deduplicated by `content_hash` + url rather than by an upstream id.
3. **`deploy/docker/api.py` was not readable**, so the route-level parsing of `browser_config` (vs `crawler_config`) rests on the earlier documented surface. If the first live run rejects a field, the allowlist is the first thing to re-check.
4. **`fit_markdown` is a fallback, not a preference**: when only it is present the excerpt is the readability-filtered text, which is shorter than the raw page. That is recorded in `markdown_chars` so a thin excerpt is visible.
5. **Links are capped** at 50 per page (internal/external) to bound payload size.

## 6. Status

**Contract corrected and unit-tested; live verification pending.** The adapter no longer corrupts evidence, no longer sends an invalid cache mode, no longer silently drops extraction or browser configuration, and refuses configuration it cannot express. It has still never spoken to a real Crawl4AI server.
