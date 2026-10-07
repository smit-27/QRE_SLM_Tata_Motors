"""NHTSA ODI flat files -> schema records.

Downloads the official complaint / recall / investigation flat files plus their
data dictionaries, reads column names FROM the dictionary (so a layout change
at NHTSA doesn't silently shift columns), and writes one record per row.

Usage:
    python -m harvest.nhtsa                      # all three datasets
    python -m harvest.nhtsa --datasets complaints
    python -m harvest.nhtsa --limit 1000         # quick test run
"""
from __future__ import annotations

import argparse
import io
import re
import zipfile
from pathlib import Path

from .common import (ROOT, ShardWriter, clean_ws, download, load_config,
                     setup_logging, user_agent)

FIELD_RE = re.compile(r"^\s*(\d{1,3})\s+([A-Z][A-Z0-9_]*)\s+(CHAR|VARCHAR|NUMBER|DATE|INT)", re.I)


def parse_dictionary(path: Path) -> list[str]:
    """Return column names in order, parsed from NHTSA's layout .txt file."""
    fields: dict[int, str] = {}
    for line in path.read_text(encoding="latin-1", errors="replace").splitlines():
        m = FIELD_RE.match(line)
        if m:
            num, name = int(m.group(1)), m.group(2).upper()
            fields.setdefault(num, name)   # first definition wins
    if not fields:
        raise RuntimeError(f"no field definitions found in {path}")
    n = max(fields)
    names = [fields.get(i, f"FIELD_{i}") for i in range(1, n + 1)]
    return names


def pick_text_fields(names, wanted, sample_rows):
    present = [f for f in wanted if f in names]
    if present:
        return present
    # Fallback: choose columns whose average length looks like prose
    lens = {n: 0 for n in names}
    for row in sample_rows:
        for n, v in zip(names, row):
            lens[n] += len(v)
    k = max(1, len(sample_rows))
    return [n for n, total in lens.items() if total / k > 80]


def iter_rows(zip_path: Path):
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.namelist() if m.lower().endswith((".txt", ".lst"))]
        if not members:
            raise RuntimeError(f"no .txt member in {zip_path}: {zf.namelist()}")
        for m in members:
            with zf.open(m) as raw:
                for line in io.TextIOWrapper(raw, encoding="latin-1", errors="replace", newline=""):
                    line = line.rstrip("\r\n")
                    if line:
                        yield line.split("\t")


def harvest_dataset(name: str, dcfg: dict, cfg: dict, log, limit: int | None, force: bool = False):
    ncfg = cfg["nhtsa"]
    out_dir = ROOT / cfg["raw_dir"] / "nhtsa"
    old = sorted(out_dir.glob(f"{name}-*.jsonl.gz"))
    if old and not force:
        log.info("[%s] already harvested (%d shards). Use --force to redo.", name, len(old))
        return
    for f in old:
        f.unlink()
    dl_dir = ROOT / cfg["raw_dir"] / "_downloads" / "nhtsa"
    headers = {"User-Agent": user_agent(cfg)}

    dict_path = dl_dir / f"{name}_dictionary.txt"
    download(dcfg["dictionary"], dict_path, headers)
    zip_paths = []
    if dcfg.get("parts"):                      # dataset split into several zips
        for u in dcfg["parts"]:
            p = dl_dir / Path(u).name
            download(u, p, headers)
            zip_paths.append(p)
        used_url = dcfg["parts"][0].rsplit("/", 1)[0] + "/"
    else:
        p = dl_dir / Path(dcfg["urls"][0]).name
        used_url = download(dcfg["urls"], p, headers)
        zip_paths.append(p)

    names = parse_dictionary(dict_path)
    log.info("[%s] %d columns from dictionary: %s ...", name, len(names), names[:8])

    rows = (row for zp in zip_paths for row in iter_rows(zp))
    sample = []
    for row in rows:
        sample.append(row)
        if len(sample) >= 2000:
            break
    text_fields = pick_text_fields(names, dcfg["text_fields"], sample)
    title_fields = [f for f in dcfg.get("title_fields", []) if f in names]
    id_field = dcfg.get("id_field") if dcfg.get("id_field") in names else None
    log.info("[%s] text fields: %s | title fields: %s | id: %s", name, text_fields, title_fields, id_field)
    if not text_fields:
        raise RuntimeError(f"[{name}] could not determine text fields; check the dictionary")

    def all_rows():
        yield from sample
        yield from rows

    n_written = n_skipped = n_badcols = 0
    seen_ids = set()
    with ShardWriter("nhtsa", cfg["shard_docs"], cfg["raw_dir"], prefix=name) as w:
        for i, row in enumerate(all_rows()):
            if limit and n_written >= limit:
                break
            if abs(len(row) - len(names)) > 2:
                n_badcols += 1
            rec_vals = dict(zip(names, row))
            parts = [rec_vals.get(f, "").strip() for f in text_fields]
            text = clean_ws("\n\n".join(p for p in parts if p))
            if len(text) < 40:
                n_skipped += 1
                continue
            rid = rec_vals.get(id_field, "").strip() if id_field else ""
            rid = rid or f"row{i}"
            # Recalls repeat the same campaign text per make/model/year: keep campaign once
            key = rid
            if name == "recalls":
                key = rec_vals.get("CAMPNO", rid).strip() or rid
                if key in seen_ids:
                    n_skipped += 1
                    continue
                seen_ids.add(key)
            title = " ".join(rec_vals.get(f, "").strip() for f in title_fields).strip() or None
            meta = {k: v.strip() for k, v in rec_vals.items()
                    if k not in text_fields and v.strip() and len(v) < 200}
            w.write({
                "id": f"nhtsa-{name}-{key}",
                "source": "nhtsa",
                "subsource": name,
                "url": used_url,
                "licence": ncfg["licence"],
                "vertical": dcfg.get("default_vertical"),
                "title": title,
                "text": text,
                "meta": meta,
            })
            n_written += 1
            if n_written % 100000 == 0:
                log.info("[%s] %d records written", name, n_written)
        log.info("[%s] DONE: %d written, %d skipped (short/dup), %d rows with unexpected column count, %.1f M chars",
                 name, n_written, n_skipped, n_badcols, w.chars / 1e6)
    if n_badcols > 0.05 * max(1, n_written):
        log.warning("[%s] many rows have unexpected column counts - check the dictionary/layout", name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", nargs="*", default=None,
                    help="subset of: complaints recalls investigations")
    ap.add_argument("--limit", type=int, default=None, help="max records per dataset (testing)")
    ap.add_argument("--force", action="store_true", help="re-create existing shards")
    args = ap.parse_args()
    log = setup_logging("nhtsa")
    cfg = load_config()
    datasets = cfg["nhtsa"]["datasets"]
    for name in (args.datasets or list(datasets)):
        try:
            harvest_dataset(name, datasets[name], cfg, log, args.limit, args.force)
        except Exception as e:  # noqa: BLE001
            log.error("[%s] FAILED: %s", name, e)


if __name__ == "__main__":
    main()
