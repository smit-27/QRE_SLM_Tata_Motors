"""USPTO patent grant full text (PTGRXML) -> CPC-filtered schema records.

Streams one weekly file at a time: download -> parse -> keep only patents whose
CPC classes match the configured prefixes -> write records -> delete the zip.
So disk use stays at one weekly file (~100-200 MB) plus the filtered output.

The USPTO Open Data Portal needs a free API key:
    export USPTO_API_KEY=...        (Linux/macOS)
    set USPTO_API_KEY=...           (Windows cmd)

Usage:
    python -m harvest.uspto --list-only            # just show which weekly files exist
    python -m harvest.uspto --max-files 2          # quick test on two weeks
    python -m harvest.uspto                        # full run (resumable)
    python -m harvest.uspto --urls-file urls.txt   # fallback: URLs copied from data.uspto.gov
    python -m harvest.uspto --local-dir D:/uspto   # fallback: zips you already downloaded
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path

import requests
from lxml import etree

from .common import (ROOT, ShardWriter, clean_ws, done_set, download, load_config,
                     mark_done, setup_logging, user_agent)

PARSER = etree.XMLParser(recover=True, resolve_entities=False, load_dtd=False,
                         no_network=True, huge_tree=True)


# ---------------------------------------------------------------- listing
def _find_files(obj, out):
    """Walk the API JSON and collect dicts that describe a downloadable file."""
    if isinstance(obj, dict):
        name = obj.get("fileName")
        if name:
            uri = next((v for k, v in obj.items()
                        if isinstance(v, str) and "download" in k.lower()
                        and v.startswith("http")), None)
            out.append({"fileName": name, "uri": uri,
                        "date": obj.get("fileDataToDate") or obj.get("fileReleaseDate")})
        for v in obj.values():
            _find_files(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _find_files(v, out)


def list_files(ucfg: dict, product: str, api_key: str, headers: dict, log, debug=False):
    url = f"{ucfg['api_base']}/api/v1/datasets/products/{product}"
    params = {"fileDataFromDate": ucfg["from_date"], "fileDataToDate": ucfg["to_date"],
              "includeFiles": "true"}
    r = requests.get(url, params=params, headers={**headers, "X-API-KEY": api_key}, timeout=120)
    if r.status_code in (401, 403):
        raise RuntimeError("USPTO API rejected the key (401/403). Check USPTO_API_KEY.")
    r.raise_for_status()
    data = r.json()
    if debug:
        (ROOT / "docs" / f"uspto_{product}_listing.json").write_text(json.dumps(data, indent=1))
    files = []
    _find_files(data, files)
    files = [f for f in files if f["fileName"].lower().endswith(".zip")]
    for f in files:
        if not f["uri"]:
            f["uri"] = f"{ucfg['api_base']}/api/v1/datasets/products/files/{product}/{f['fileName']}"
    files.sort(key=lambda f: f["fileName"])
    log.info("[%s] %d weekly files listed between %s and %s", product, len(files),
             ucfg["from_date"], ucfg["to_date"])
    if not files:
        log.warning("No files found. Run with --debug and inspect docs/uspto_%s_listing.json", product)
    return files


# ---------------------------------------------------------------- parsing
def iter_documents(zip_path: Path):
    """Weekly files are many XML documents concatenated; split on the XML declaration."""
    with zipfile.ZipFile(zip_path) as zf:
        for m in zf.namelist():
            if not m.lower().endswith(".xml"):
                continue
            buf = []
            with zf.open(m) as raw:
                for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace"):
                    if line.startswith("<?xml") and buf:
                        yield "".join(buf)
                        buf = []
                    buf.append(line)
            if buf:
                yield "".join(buf)


def text_of(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def cpc_codes(root) -> list[str]:
    codes = []
    for c in root.iter("classification-cpc"):
        sec = (c.findtext("section") or "").strip()
        cls = (c.findtext("class") or "").strip()
        sub = (c.findtext("subclass") or "").strip()
        grp = (c.findtext("main-group") or "").strip()
        sg = (c.findtext("subgroup") or "").strip()
        if sec and cls:
            code = f"{sec}{cls}{sub}"
            codes.append(f"{code} {grp}/{sg}".strip() if grp else code)
    return codes


def section_text(el) -> str:
    """Paragraphs and headings of a description/claims block, one per line."""
    if el is None:
        return ""
    parts = []
    for node in el.iter("heading", "p", "claim-text"):
        t = text_of(node)
        if t:
            parts.append(t)
    return "\n\n".join(parts)


def parse_patent(xml: str, prefixes, include_claims: bool):
    xml = re.sub(r"<!DOCTYPE[^>]*>", "", xml, count=1)
    root = etree.fromstring(xml.encode("utf-8"), PARSER)
    if root is None or root.tag != "us-patent-grant":
        return None
    codes = cpc_codes(root)
    matched = [c for c in codes if any(c.replace(" ", "").startswith(p) for p in prefixes)]
    if not matched:
        return None
    pub = root.find(".//publication-reference/document-id")
    number = pub.findtext("doc-number") if pub is not None else None
    date = pub.findtext("date") if pub is not None else None
    title = text_of(root.find(".//invention-title"))
    abstract = section_text(root.find(".//abstract")) or text_of(root.find(".//abstract"))
    desc = section_text(root.find(".//description"))
    body = [f"Title: {title}", f"Abstract:\n{abstract}", f"Description:\n{desc}"]
    if include_claims:
        body.append(f"Claims:\n{section_text(root.find('.//claims'))}")
    return {
        "number": number, "date": date, "title": title,
        "text": clean_ws("\n\n".join(b for b in body if b.split(':', 1)[-1].strip())),
        "cpc": sorted(set(codes)), "cpc_matched": sorted(set(matched)),
    }


def process_zip(zip_path: Path, ucfg: dict, writer: ShardWriter, src_url: str, log):
    seen = kept = 0
    for xml in iter_documents(zip_path):
        seen += 1
        try:
            p = parse_patent(xml, ucfg["cpc_prefixes"], ucfg.get("include_claims", False))
        except Exception as e:  # noqa: BLE001
            log.debug("parse error: %s", e)
            continue
        if not p or not p["number"] or len(p["text"]) < ucfg.get("min_chars", 1500):
            continue
        writer.write({
            "id": f"uspto-{p['number']}",
            "source": "uspto",
            "subsource": "grant",
            "url": src_url,
            "licence": ucfg["licence"],
            "vertical": ucfg.get("default_vertical"),
            "title": p["title"],
            "text": p["text"],
            "meta": {"pub_date": p["date"], "cpc": p["cpc"][:30], "cpc_matched": p["cpc_matched"][:30]},
        })
        kept += 1
    log.info("  %s: %d patents parsed, %d kept (%.1f%%)", zip_path.name, seen, kept,
             100 * kept / max(1, seen))
    return seen, kept


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--max-files", type=int, default=None)
    ap.add_argument("--urls-file", type=Path, default=None)
    ap.add_argument("--local-dir", type=Path, default=None)
    ap.add_argument("--keep-zips", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    log = setup_logging("uspto")
    cfg = load_config()
    ucfg = cfg["uspto"]
    headers = {"User-Agent": user_agent(cfg)}
    dl_dir = ROOT / cfg["raw_dir"] / "_downloads" / "uspto"
    dl_dir.mkdir(parents=True, exist_ok=True)
    state = ROOT / cfg["raw_dir"] / "uspto" / "_done.txt"
    state.parent.mkdir(parents=True, exist_ok=True)
    done = done_set(state)

    # Build the work list
    if args.local_dir:
        jobs = [{"fileName": p.name, "uri": None, "local": p} for p in sorted(args.local_dir.glob("*.zip"))]
    elif args.urls_file:
        urls = [u.strip() for u in args.urls_file.read_text().splitlines() if u.strip() and not u.startswith("#")]
        jobs = [{"fileName": u.rstrip("/").split("/")[-1].split("?")[0], "uri": u} for u in urls]
    else:
        api_key = os.environ.get("USPTO_API_KEY")
        if not api_key:
            raise SystemExit("Set USPTO_API_KEY (free key from data.uspto.gov), or use --urls-file / --local-dir.")
        jobs = []
        for product in ucfg["products"]:
            jobs += list_files(ucfg, product, api_key, headers, log, args.debug)
        headers["X-API-KEY"] = api_key

    if args.list_only:
        for j in jobs:
            print(j["fileName"], j.get("uri") or j.get("local"))
        return

    todo = [j for j in jobs if j["fileName"] not in done]
    if args.max_files:
        todo = todo[: args.max_files]
    log.info("%d files to process (%d already done)", len(todo), len(jobs) - len(todo))

    tot_seen = tot_kept = 0
    t0 = time.time()
    with ShardWriter("uspto", cfg["shard_docs"], cfg["raw_dir"]) as w:
        for i, j in enumerate(todo, 1):
            log.info("[%d/%d] %s", i, len(todo), j["fileName"])
            local = j.get("local")
            try:
                if local is None:
                    local = dl_dir / j["fileName"]
                    download(j["uri"], local, headers)
                s, k = process_zip(local, ucfg, w, j.get("uri") or str(local), log)
                tot_seen += s
                tot_kept += k
                mark_done(state, j["fileName"])
                if j.get("local") is None and not args.keep_zips:
                    local.unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                log.error("  FAILED %s: %s (will retry on next run)", j["fileName"], e)
            if i % 10 == 0:
                hrs = (time.time() - t0) / 3600
                log.info("progress: %d files, %d kept / %d parsed, %.0f M chars, %.1f h elapsed",
                         i, tot_kept, tot_seen, w.chars / 1e6, hrs)
    log.info("DONE: kept %d of %d patents, %.0f M chars", tot_kept, tot_seen, w.chars / 1e6)


if __name__ == "__main__":
    main()
