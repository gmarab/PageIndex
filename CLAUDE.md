# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PageIndex is a vectorless, reasoning-based RAG system. Instead of chunking documents into vector embeddings, it parses PDFs (or Markdown) into a hierarchical tree index (like a structured TOC) and uses LLM reasoning to traverse the tree for retrieval. Output is a JSON tree with `title`, `node_id`, `start_index`/`end_index`, and optional `summary` per node.

## Commands

```bash
# Install dependencies
pip3 install --upgrade -r requirements.txt

# Run PDF indexing
python3 run_pageindex.py --pdf_path /path/to/document.pdf

# Run Markdown indexing
python3 run_pageindex.py --md_path /path/to/document.md
```

There are no automated tests, linting, or CI pipelines. The `tests/` directory contains sample PDFs and pre-generated reference JSON outputs only.

## Environment Setup

Create `.env` in the project root with `CHATGPT_API_KEY=your_key`. Configuration defaults live in `pageindex/config.yaml`.

**Note:** `pageindex/utils.py` currently has OpenAI API calls commented out and replaced with `ollama.AsyncClient`. The active LLM backend is Ollama, not OpenAI, despite config referencing `gpt-4o`.

## Architecture

### Module Relationships

- **`run_pageindex.py`** — CLI entry point (argparse), routes to PDF or Markdown pipeline
- **`pageindex/page_index.py`** — PDF pipeline (~1144 lines). Orchestrates TOC detection, tree parsing, verification, and node expansion
- **`pageindex/page_index_md.py`** — Markdown pipeline (~338 lines). Uses regex header detection and stack-based tree construction
- **`pageindex/utils.py`** — Shared utilities (~753 lines): LLM API wrappers, token counting, JSON extraction, tree construction (`post_processing`, `list_to_tree`), config loading, logging

### PDF Pipeline Flow

`page_index_main` → `tree_parser` → `check_toc` (detect TOC via LLM) → `meta_processor` (one of three modes depending on TOC quality):
1. `process_toc_with_page_numbers` — TOC found with page numbers
2. `process_toc_no_page_numbers` — TOC found without page numbers
3. `process_no_toc` — no TOC detected

After initial tree generation: `verify_toc` spot-checks accuracy → if >60% correct, `fix_incorrect_toc_with_retries` (up to 3 passes); if ≤60%, falls back to a more expensive mode. Large nodes exceeding `max_page_num_each_node` (10) AND `max_token_num_each_node` (20k) are recursively split via `process_large_node_recursively`.

### Key Patterns

- **Async throughout:** All LLM calls use `asyncio`. Sync entry points bridge via `asyncio.run()`.
- **Physical index tagging:** Pages are wrapped with `<physical_index_X>` XML tags before LLM calls to give positional grounding.
- **LLM-driven decisions:** TOC detection, section boundary mapping, verification, and correction are all delegated to LLM calls returning JSON. Retry loops (up to 10 retries with 1s sleep) handle failures.
- **ConfigLoader:** Merges `config.yaml` defaults with user-supplied overrides into a `SimpleNamespace`.

### Output Format

JSON saved to `./results/<pdf_name>_structure.json` with nested `structure` array containing nodes with `title`, `node_id`, `start_index`, `end_index`, `summary`, and recursive `nodes`.
