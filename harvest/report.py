"""Summarise everything harvested so far into docs/data_manifest.md.

Counts documents, characters and estimated tokens per source/subsource, checks
schema fields, and flags exact-duplicate texts (full near-dup removal happens in
the processing stage).

Usage:
    python -m harvest.report
"""
from __future__ import annotations

import gzip
import json
from collections import defaultdict
from datetime import date

from .common import ROOT, load_config

CHARS_PER_TOKEN = 4.0   # rough for English technical text; replace with real tokenizer counts later


def main():
    cfg = load_config()
    raw = ROOT / cfg["raw_dir"]
    stats = defaultdict(lambda: {"docs": 0, "chars": 0, "licence": set(), "vertical": defaultdict(int)})
    hashes, dups, bad = set(), 0, 0
    for shard in sorted(raw.glob("*/*.jsonl.gz")):
        if shard.parent.name.startswith("_"):
            continue
        with gzip.open(shard, "rt", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if not r.get("text") or not r.get("licence") or not r.get("id"):
                    bad += 1
                    continue
                key = f"{r['source']}/{r.get('subsource') or '-'}"
                s = stats[key]
                s["docs"] += 1
                s["chars"] += len(r["text"])
                s["licence"].add(r["licence"])
                s["vertical"][r.get("vertical") or "unassigned"] += 1
                h = r.get("sha256")
                if h in hashes:
                    dups += 1
                else:
                    hashes.add(h)

    lines = [f"# Data manifest (raw harvest) - {date.today()}", "",
             "| Source | Docs | Chars (M) | Est. tokens (M) | Licence | Vertical tags |",
             "|---|---:|---:|---:|---|---|"]
    tot_docs = tot_chars = 0
    for key in sorted(stats):
        s = stats[key]
        tot_docs += s["docs"]
        tot_chars += s["chars"]
        vt = ", ".join(f"{k}:{v}" for k, v in sorted(s["vertical"].items()))
        lines.append(f"| {key} | {s['docs']:,} | {s['chars']/1e6:,.1f} | {s['chars']/CHARS_PER_TOKEN/1e6:,.1f} "
                     f"| {'; '.join(sorted(s['licence']))} | {vt} |")
    lines.append(f"| **Total** | **{tot_docs:,}** | **{tot_chars/1e6:,.1f}** | "
                 f"**{tot_chars/CHARS_PER_TOKEN/1e6:,.1f}** | | |")
    lines += ["", f"- Exact duplicate texts found: {dups:,} (removed later in processing)",
              f"- Records failing schema check: {bad:,}",
              f"- Token estimate uses {CHARS_PER_TOKEN} chars/token; replace with tokenizer counts in Phase 4."]
    out = ROOT / "docs" / "data_manifest.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
