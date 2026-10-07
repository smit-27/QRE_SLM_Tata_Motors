"""Shared helpers for all harvesters.

Every harvester writes records in ONE schema so the processing stage can treat
all sources the same way:

    id, source, subsource, url, licence, retrieved_at, vertical, title, text,
    meta (dict), sha256 (of text)

Output: data/raw/<source>/part-00000.jsonl.gz, part-00001.jsonl.gz, ...
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("harvest")


def setup_logging(name: str) -> logging.Logger:
    (ROOT / "docs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(ROOT / "docs" / f"harvest_{name}.log")],
    )
    return logging.getLogger(name)


def load_config() -> dict:
    with open(ROOT / "configs" / "sources.yaml") as f:
        return yaml.safe_load(f)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()


def user_agent(cfg: dict) -> str:
    return f"QRE-corpus-harvester/1.0 (research; contact: {cfg.get('contact_email', 'unknown')})"


def download(urls, dest: Path, headers: dict | None = None, retries: int = 4,
             chunk: int = 1 << 20, timeout: int = 120) -> str:
    """Download the first working URL to dest with resume support.

    `urls` may be a string or a list of fallbacks. Returns the URL used.
    Skips the download if dest already exists and is non-empty.
    """
    if isinstance(urls, str):
        urls = [urls]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        log.info("exists, skipping download: %s", dest)
        return urls[0]

    part = dest.with_suffix(dest.suffix + ".part")
    last_err = None
    for url in urls:
        for attempt in range(1, retries + 1):
            try:
                h = dict(headers or {})
                pos = part.stat().st_size if part.exists() else 0
                if pos:
                    h["Range"] = f"bytes={pos}-"
                with requests.get(url, headers=h, stream=True, timeout=timeout,
                                  allow_redirects=True) as r:
                    if r.status_code == 416:      # already complete
                        break
                    if r.status_code == 200 and pos:
                        pos = 0                   # server ignored Range; restart
                        part.unlink(missing_ok=True)
                    r.raise_for_status()
                    total = int(r.headers.get("Content-Length", 0)) + pos
                    with open(part, "ab" if pos else "wb") as f:
                        done = pos
                        last_log = time.time()
                        for b in r.iter_content(chunk_size=chunk):
                            f.write(b)
                            done += len(b)
                            if time.time() - last_log > 15:
                                pct = f"{100 * done / total:.1f}%" if total else "?"
                                log.info("  %s: %.1f MB (%s)", dest.name, done / 1e6, pct)
                                last_log = time.time()
                part.rename(dest)
                log.info("downloaded %s (%.1f MB) from %s", dest.name, dest.stat().st_size / 1e6, url)
                return url
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("download failed (%s, attempt %d/%d): %s", url, attempt, retries, e)
                time.sleep(min(60, 5 * attempt))
        log.warning("giving up on %s, trying next fallback", url)
    raise RuntimeError(f"all URLs failed for {dest.name}: {last_err}")


class ShardWriter:
    """Writes schema records to rotating gzip JSONL shards."""

    REQUIRED = ("id", "source", "url", "licence", "text")

    def __init__(self, source: str, shard_docs: int = 50000, raw_dir: str = "data/raw",
                 prefix: str = "part"):
        self.dir = ROOT / raw_dir / source
        self.dir.mkdir(parents=True, exist_ok=True)
        self.source = source
        self.shard_docs = shard_docs
        self.prefix = prefix
        existing = sorted(self.dir.glob(f"{prefix}-*.jsonl.gz"))
        self.idx = len(existing)
        self.n_in_shard = 0
        self.total = 0
        self.chars = 0
        self.fh = None

    def _open(self):
        path = self.dir / f"{self.prefix}-{self.idx:05d}.jsonl.gz"
        self.fh = gzip.open(path, "wt", encoding="utf-8")
        self.idx += 1
        self.n_in_shard = 0

    def write(self, rec: dict):
        for k in self.REQUIRED:
            if not rec.get(k):
                raise ValueError(f"record missing '{k}': {str(rec)[:200]}")
        rec.setdefault("source", self.source)
        rec.setdefault("subsource", None)
        rec.setdefault("retrieved_at", now_iso())
        rec.setdefault("vertical", None)
        rec.setdefault("title", None)
        rec.setdefault("meta", {})
        rec["sha256"] = sha256(rec["text"])
        if self.fh is None or self.n_in_shard >= self.shard_docs:
            self.close()
            self._open()
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.n_in_shard += 1
        self.total += 1
        self.chars += len(rec["text"])

    def close(self):
        if self.fh:
            self.fh.close()
            self.fh = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def clean_ws(text: str) -> str:
    """Collapse whitespace but keep paragraph breaks."""
    lines = [" ".join(l.split()) for l in text.replace("\r", "\n").split("\n")]
    out, blank = [], False
    for l in lines:
        if l:
            out.append(l)
            blank = False
        elif not blank and out:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def done_set(path: Path) -> set:
    return set(path.read_text().split()) if path.exists() else set()


def mark_done(path: Path, key: str):
    with open(path, "a") as f:
        f.write(key + "\n")
        f.flush()
        os.fsync(f.fileno())
