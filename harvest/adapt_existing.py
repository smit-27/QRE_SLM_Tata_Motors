"""Convert existing qa-graphrag harvester output (NTRS, EverySpec, NIST, arXiv,
DTIC) or manually downloaded files (e.g. UNECE regulation PDFs) into the shared
schema, so every source looks the same to the processing stage.

Supported inputs (searched recursively): .pdf .txt .md .html/.htm .json .jsonl

Usage examples:
    python -m harvest.adapt_existing --input ../qa-graphrag/data/ntrs \
        --source ntrs --licence "US government" --vertical engineering

    python -m harvest.adapt_existing --input data/manual/everyspec \
        --source everyspec --licence "US government" --vertical shared

    # JSON/JSONL from your harvester: tell it which fields hold text/title/url/id
    python -m harvest.adapt_existing --input ../qa-graphrag/data/arxiv.jsonl \
        --source arxiv --licence "check per paper" --text-field abstract \
        --title-field title --url-field url --id-field id
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import ROOT, ShardWriter, clean_ws, load_config, setup_logging, sha256


def read_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "\n\n".join((p.extract_text() or "") for p in reader.pages)


def read_html(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        import trafilatura
        out = trafilatura.extract(raw, include_tables=True)
        if out:
            return out
    except ImportError:
        pass
    from lxml import html
    return html.fromstring(raw).text_content()


def iter_json_records(path: Path):
    if path.suffix == ".jsonl":
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
    else:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        yield from (data if isinstance(data, list) else [data])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True, help="file or folder")
    ap.add_argument("--source", required=True, help="short name, e.g. ntrs, everyspec, nist, arxiv, dtic, unece")
    ap.add_argument("--licence", required=True)
    ap.add_argument("--vertical", default=None, help="engineering | supplier | manufacturing | shared")
    ap.add_argument("--text-field", default="text")
    ap.add_argument("--title-field", default="title")
    ap.add_argument("--url-field", default="url")
    ap.add_argument("--id-field", default="id")
    ap.add_argument("--min-chars", type=int, default=300)
    ap.add_argument("--force", action="store_true", help="delete existing shards for this source first")
    args = ap.parse_args()

    log = setup_logging(f"adapt_{args.source}")
    cfg = load_config()
    out_dir = ROOT / cfg["raw_dir"] / args.source
    old = sorted(out_dir.glob("part-*.jsonl.gz"))
    if old and not args.force:
        raise SystemExit(f"{out_dir} already has {len(old)} shards. Use --force to rebuild.")
    for f in old:
        f.unlink()
    files = [args.input] if args.input.is_file() else sorted(p for p in args.input.rglob("*") if p.is_file())
    n_ok = n_skip = n_err = 0

    with ShardWriter(args.source, cfg["shard_docs"], cfg["raw_dir"]) as w:
        def emit(text, title, url, rid, meta):
            nonlocal n_ok, n_skip
            text = clean_ws(text or "")
            if len(text) < args.min_chars:
                n_skip += 1
                return
            w.write({
                "id": f"{args.source}-{rid or sha256(text)[:16]}",
                "source": args.source,
                "url": url or "unknown",
                "licence": args.licence,
                "vertical": args.vertical,
                "title": title,
                "text": text,
                "meta": meta,
            })
            n_ok += 1

        for p in files:
            ext = p.suffix.lower()
            try:
                if ext == ".pdf":
                    emit(read_pdf(p), p.stem, f"file://{p.name}", p.stem, {"file": p.name})
                elif ext in (".txt", ".md"):
                    emit(p.read_text(encoding="utf-8", errors="replace"), p.stem, f"file://{p.name}", p.stem, {"file": p.name})
                elif ext in (".html", ".htm"):
                    emit(read_html(p), p.stem, f"file://{p.name}", p.stem, {"file": p.name})
                elif ext in (".json", ".jsonl"):
                    for r in iter_json_records(p):
                        if not isinstance(r, dict):
                            continue
                        emit(r.get(args.text_field), r.get(args.title_field), r.get(args.url_field),
                             r.get(args.id_field), {"file": p.name})
                else:
                    continue
            except Exception as e:  # noqa: BLE001
                n_err += 1
                log.warning("failed %s: %s", p, e)
    log.info("DONE %s: %d records, %d too short, %d errors, %.1f M chars",
             args.source, n_ok, n_skip, n_err, w.chars / 1e6)


if __name__ == "__main__":
    main()
