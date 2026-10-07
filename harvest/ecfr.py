"""eCFR Title 49 (FMVSS and defect-reporting parts) -> schema records.

One record per CFR section (e.g. 49 CFR 571.135), using the official eCFR API.

Usage:
    python -m harvest.ecfr
    python -m harvest.ecfr --parts 571
"""
from __future__ import annotations

import argparse
import time

import requests
from lxml import etree

from .common import ROOT, ShardWriter, clean_ws, load_config, setup_logging, user_agent

API = "https://www.ecfr.gov/api/versioner/v1"
PARSER = etree.XMLParser(recover=True, huge_tree=True)


def latest_date(title: int, headers) -> str:
    r = requests.get(f"{API}/titles.json", headers=headers, timeout=60)
    r.raise_for_status()
    for t in r.json().get("titles", []):
        if int(t.get("number", -1)) == title:
            return t.get("up_to_date_as_of") or t.get("latest_issue_date")
    raise RuntimeError(f"title {title} not found in eCFR titles list")


def block_text(el) -> str:
    parts = []
    for node in el.iter():
        if node.tag in ("P", "FP", "HD", "TH", "TD"):
            t = " ".join("".join(node.itertext()).split())
            if t:
                parts.append(t)
    return "\n\n".join(parts) or " ".join("".join(el.itertext()).split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", nargs="*", type=int, default=None)
    args = ap.parse_args()
    log = setup_logging("ecfr")
    cfg = load_config()
    ecfg = cfg["ecfr"]
    headers = {"User-Agent": user_agent(cfg)}
    title = ecfg["title"]
    date = latest_date(title, headers)
    log.info("eCFR title %s current as of %s", title, date)

    out_dir = ROOT / cfg["raw_dir"] / "ecfr"
    for old in out_dir.glob("part-*.jsonl.gz"):
        old.unlink()          # small source: always rebuild fully

    with ShardWriter("ecfr", cfg["shard_docs"], cfg["raw_dir"]) as w:
        for part in (args.parts or ecfg["parts"]):
            url = f"{API}/full/{date}/title-{title}.xml"
            r = requests.get(url, params={"part": part}, headers=headers, timeout=300)
            if r.status_code != 200:
                log.error("part %s: HTTP %s", part, r.status_code)
                continue
            raw_path = ROOT / cfg["raw_dir"] / "_downloads" / "ecfr" / f"title{title}_part{part}.xml"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(r.content)
            root = etree.fromstring(r.content, PARSER)
            sections = [d for d in root.iter("DIV8")] or [root]
            n = 0
            for sec in sections:
                head = " ".join("".join(sec.findtext("HEAD") or "").split())
                text = clean_ws(block_text(sec))
                if len(text) < 200:
                    continue
                sec_id = sec.get("N") or f"{part}-{n}"
                w.write({
                    "id": f"ecfr-{title}-{sec_id}",
                    "source": "ecfr",
                    "subsource": f"title{title}-part{part}",
                    "url": f"https://www.ecfr.gov/current/title-{title}/section-{sec_id}",
                    "licence": ecfg["licence"],
                    "vertical": ecfg.get("default_vertical"),
                    "title": head or None,
                    "text": text,
                    "meta": {"part": part, "as_of": date},
                })
                n += 1
            log.info("part %s: %d sections", part, n)
            time.sleep(1)
        log.info("DONE: %d sections, %.1f M chars", w.total, w.chars / 1e6)


if __name__ == "__main__":
    main()
