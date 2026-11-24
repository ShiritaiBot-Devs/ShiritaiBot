# Knowledge base

**English** · [简体中文](KNOWLEDGE.md)

The knowledge base holds material that doesn't change: manuals, regulations, spreadsheets, lecture notes. The code is in `src/shiritaibot/knowledge/`, the configuration is `[knowledge]` in `config.toml`, and the WebUI page is **Knowledge Base**.

Add knowledge bases under **Search and import settings**, choose which one the WebUI chat, private chats, or group chats use, then save the configuration. One knowledge base can be bound to several sessions. Search and import in each session only touch the bound knowledge base, or you can choose **Disable knowledge base**. **Managed knowledge bases** picks where WebUI uploads, searches, downloads, and deletes go. Removing a knowledge base from the settings keeps its data on disk; add it again with the same ID to restore it.

## 1. How it differs from memory

| | Long-term memory (`docs/MEMORY.md`, in Chinese) | Knowledge base |
| --- | --- | --- |
| Contents | People, things, events; they change | Fixed documents that don't change |
| Structure | Entities, facts, events, evidence, conflicts | Documents → chunks, only two levels |
| Writing | Extracted by the model, evidence checked by code | Files are imported; no text model is called |
| Changes | Superseded, merged, conflicts flagged | Only by deleting a whole document and importing it again |
| Use | Injected each turn as needed | Searched only when the model calls `kb_search`; never injected |

## 2. Storage

- Existing files and indexes stay in the default knowledge base, with original files still at `<home>/knowledge/<sha256>`. Custom knowledge bases have their own SQLite database, full-text index, vector index, and file folder. Duplicate content is only detected within the same knowledge base.
- The default knowledge base keeps document IDs like `d12`. Custom knowledge bases add their ID, for example `project:d12`, and chunks are written `project:d12#3` (counting from 0).
- `kb_fts` is an FTS5 full-text index whose `rowid` equals the chunk ID; `kb_vector` holds vectors, separate from memory and stickers.
- All rows of a document are written in one transaction, so a failure partway never leaves half a document.

## 3. Import

Supported formats and how they're split:

| Format | Parsing | Sections |
| --- | --- | --- |
| txt, rst, log, json, yaml, yml, xml | UTF-8 / UTF-16 / GB18030 detected automatically | None |
| md, markdown | Same as above | Heading path (`A > B`) |
| csv, tsv | Delimiter detected automatically | Table |
| pdf | pypdf | One section per page; scanned PDFs have no text layer and give an error asking for OCR first |
| docx | python-docx | Heading path; body text and tables keep their original order |
| doc | olefile reads the piece table of Word's binary format | None |
| xlsx / xls | openpyxl / xlrd | One section per sheet, handled as a table |
| pptx | python-pptx | One section per slide, including notes |
| ppt | olefile reads PowerPoint records | One section per slide, including notes |

- The parser is chosen from the file header, so a file named `.doc` that's actually OOXML can still be read. RTF and encrypted files give a clear error.
- For `.ppt`, the latest version is found by following Current User → UserEdit → PersistDirectory. Fast save leaves old versions in the file, and a sequential scan would read old text and wrong slide numbers. Only when this chain is broken does it fall back to scanning all text, and then there are no slide sections.
- For `.doc`, only the body is read, without headers, footers, or footnotes.

Chunking:

- Body text is accumulated line by line up to `chunk_chars`. An overly long line is split by sentences first, then cut hard. Neighboring chunks in the same section overlap by `chunk_overlap` characters.
- Tables are split by rows, each chunk repeats the header, and chunks don't overlap, so a cell's meaning stays clear outside its context.
- Changing the chunking settings only affects documents imported afterwards.

## 4. Search

`kb_search` merges two result lists with reciprocal rank fusion (RRF, k=60), taking up to 50 results from each:

- **Keywords**: FTS5 BM25. Indexing and queries both use jieba segmentation plus Chinese character bigrams. jieba splits words that aren't in its dictionary, and the same word may be segmented differently in the question and the document; bigrams make sure the two sides still match. English words are also indexed and searched by their stem, so "meetings" finds "meeting".
- **Semantic**: vectors from the `embedding` route; results with cosine similarity below 0.3 are dropped.

A chunk's title and section name are indexed along with it. Results carry a `match` field: `hybrid`, `keyword`, or `semantic`. No text model is called at any point.

Vectors are filled in by a background loop: it wakes right after an import and only computes vectors missing for the current model. After the Embedding model changes, everything is recomputed. Without an Embedding model, or if the call fails, only keywords are used, and imports aren't affected.

Vector search is brute force with numpy, and all vectors stay in memory: 100,000 chunks with 1024 dimensions take about 400 MB. Larger collections need an approximate index.

## 5. Tools

| Tool | Risk | Chat mode | Purpose |
| --- | --- | --- | --- |
| `kb_search` | safe | Can be enabled per session | Hybrid search, optionally within one document |
| `kb_read` | safe | Can be enabled per session | Read chunks in order, to see the context around a hit |
| `kb_list` | safe | Can be enabled per session | List documents |
| `kb_import` | normal | Not available | Import a QQ file reference, a file in the session folder, or plain text |
| `kb_delete` | sensitive | Not available | Delete a document |

`kb_import` and `kb_delete` check again in code that they're only called from the owner's private chat. `kb_import`'s `path` can only point to the current session's working or download folder, so it can't be used to read arbitrary files.

In a QQ private chat: the owner sends a file and says "save it to the knowledge base". The message contains `[QQ file ref ref: file name]`, and the model passes the ref to `kb_import`. Nothing is imported automatically; if the owner only sends a file without saying what it's for, the model asks or decides.

A Chat session that's explicitly bound can search the whole chosen knowledge base, still subject to the session's tool permissions. `knowledge.chat` controls whether unbound Chat sessions can search the default knowledge base. Changing a binding only affects later tool calls; content that already appeared in an old conversation stays there.

## 6. Configuration

```toml
[knowledge]
enabled = true       # turning it off only hides the tools; stored documents are kept
chat = false         # whether unbound Chat sessions can search the default knowledge base
chunk_chars = 600    # 200–2000
chunk_overlap = 80   # 0–500, at most half of chunk_chars
max_file_mb = 50     # 1–50

[knowledge.libraries.project]
name = "Project files"

[knowledge.sessions]
"group:qq:123456" = "project"
"web:archive" = "disabled"
```

## 7. WebUI API

`GET /api/knowledge`, `POST /api/knowledge?filename=&title=` (the request body is the file), `GET /api/knowledge/search?q=`, `GET /api/knowledge/{id}?start=&count=`, `GET /api/knowledge/{id}/file`, `DELETE /api/knowledge/{id}`. These take a `library` query parameter; without it the default knowledge base is used. `GET /api/knowledge/libraries` returns the configured knowledge bases.
