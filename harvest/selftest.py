"""Offline self-test: runs every harvester's parsing logic on small synthetic
files that mimic the official formats. Needs NO internet and NO API key.

Usage:
    python -m harvest.selftest

Prints PASS/FAIL per check. Everything it creates is deleted afterwards.
"""
from __future__ import annotations

import gzip
import json
import logging
import shutil
import sys
import zipfile
from pathlib import Path

from .common import ROOT, ShardWriter, load_config

TEST_RAW = "data/_selftest"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))


def read_shards(folder: Path, pattern="*.jsonl.gz"):
    recs = []
    for p in sorted(folder.glob(pattern)):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            recs += [json.loads(l) for l in f]
    return recs


def test_config():
    print("1. Config")
    cfg = load_config()
    check("sources.yaml loads", isinstance(cfg, dict))
    if "your.name@company.com" in cfg.get("contact_email", ""):
        print("  [WARN] contact_email is still the placeholder - edit configs/sources.yaml before real runs")
    for k in ("nhtsa", "uspto", "ecfr"):
        check(f"section '{k}' present", k in cfg)
    return cfg


def test_nhtsa(cfg):
    print("2. NHTSA parser")
    from . import nhtsa
    cfg = {**cfg, "raw_dir": TEST_RAW}
    dl = ROOT / TEST_RAW / "_downloads" / "nhtsa"
    dl.mkdir(parents=True, exist_ok=True)
    (dl / "complaints_dictionary.txt").write_text(
        "Field#  Name              Type/Size     Description\n"
        "1       CMPLID            CHAR(9)       ID\n"
        "2       ODINO             CHAR(9)       REF\n"
        "3       MAKETXT           CHAR(25)      MAKE\n"
        "4       MODELTXT          CHAR(256)     MODEL\n"
        "5       YEARTXT           CHAR(4)       YEAR\n"
        "6       COMPDESC          CHAR(128)     COMPONENT\n"
        "7       CDESCR            CHAR(2048)    DESCRIPTION\n", encoding="latin-1")
    rows = [
        "1\t11\tFORD\tF-150\t2018\tPOWER TRAIN\tTRANSMISSION SLIPPED AT 45 MPH AND THE VEHICLE LOST POWER ON THE HIGHWAY.",
        "2\t12\tHONDA\tCIVIC\t2019\tBRAKES\tshort",
        "3\t13\tTOYOTA\tCAMRY\t2020\tAIR BAGS\tAIR BAG LIGHT ON; DEALER COULD NOT FIND ROOT CAUSE AFTER TWO VISITS (CAFÉ TEST).",
    ]
    zname = Path(cfg["nhtsa"]["datasets"]["complaints"]["urls"][0]).name
    with zipfile.ZipFile(dl / zname, "w") as z:
        z.writestr("FLAT_CMPL.txt", ("\n".join(rows) + "\n").encode("latin-1"))
    log = logging.getLogger("selftest")
    nhtsa.harvest_dataset("complaints", cfg["nhtsa"]["datasets"]["complaints"], cfg, log, None, force=True)
    recs = read_shards(ROOT / TEST_RAW / "nhtsa", "complaints-*.jsonl.gz")
    check("dictionary parsed and records written", len(recs) == 2, f"got {len(recs)} records, expected 2")
    if recs:
        r = recs[0]
        check("narrative captured in text", "TRANSMISSION SLIPPED" in r["text"])
        check("title built from make/model/year", r["title"] and "FORD" in r["title"])
        check("schema fields present", all(k in r for k in
              ("id", "source", "url", "licence", "retrieved_at", "vertical", "text", "meta", "sha256")))
        check("non-ASCII text survives", any("CAF" in x["text"] for x in recs))


def test_uspto(cfg):
    print("3. USPTO parser + CPC filter")
    from . import uspto

    def pat(num, cpc, body):
        return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<!DOCTYPE us-patent-grant SYSTEM "us-patent-grant-v47-2022-02-17.dtd" [ ]>\n'
                f'<us-patent-grant><us-bibliographic-data-grant><publication-reference><document-id>'
                f'<country>US</country><doc-number>{num}</doc-number><kind>B2</kind><date>20250408</date>'
                f'</document-id></publication-reference><classifications-cpc><main-cpc><classification-cpc>'
                f'<section>{cpc[0]}</section><class>{cpc[1:3]}</class><subclass>{cpc[3]}</subclass>'
                f'<main-group>1</main-group><subgroup>00</subgroup></classification-cpc></main-cpc>'
                f'</classifications-cpc><invention-title id="t">Test patent {num}</invention-title>'
                f'</us-bibliographic-data-grant><abstract><p>Monitors bearing wear &amp; vibration.</p></abstract>'
                f'<description><heading>BACKGROUND</heading><p>{body}</p></description>'
                f'<claims><claim><claim-text>1. A claim.</claim-text></claim></claims></us-patent-grant>\n')

    body = "Rolling element bearings fail by fatigue spalling under cyclic load. " * 40
    tmp = ROOT / TEST_RAW / "_downloads" / "uspto"
    tmp.mkdir(parents=True, exist_ok=True)
    zp = tmp / "ipg250408.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("ipg250408.xml", pat("11111111", "F16C", body) + pat("22222222", "A61K", body)
                   + pat("33333333", "B60R", body) + pat("44444444", "B62D", body))
    log = logging.getLogger("selftest")
    with ShardWriter("uspto", 1000, TEST_RAW) as w:
        seen, kept = uspto.process_zip(zp, cfg["uspto"], w, "test://", log)
    recs = read_shards(ROOT / TEST_RAW / "uspto")
    check("all 4 concatenated patents parsed", seen == 4, f"parsed {seen}")
    check("CPC filter keeps F16C, B60R, B62D and drops A61K", kept == 3 and
          not any("22222222" in r["id"] for r in recs), f"kept {kept}")
    if recs:
        check("claims excluded by default", "A claim." not in recs[0]["text"])
        check("CPC codes stored in meta", recs[0]["meta"].get("cpc_matched"))

    # listing-response walker with a made-up response shape
    files = []
    uspto._find_files({"bag": [{"productFileBag": {"fileDataBag": [
        {"fileName": "ipg250408.zip", "fileDownloadURI": "https://x/ipg250408.zip"}]}}]}, files)
    check("API listing parser finds file entries", files and files[0]["uri"].endswith(".zip"))


def test_ecfr():
    print("4. eCFR parser")
    from lxml import etree
    from .ecfr import PARSER, block_text
    x = (b'<ECFR><DIV5 N="571"><DIV8 N="571.135" TYPE="SECTION"><HEAD>571.135 Standard No. 135; '
         b'Light vehicle brake systems.</HEAD><P>S1. Scope. This standard specifies requirements for '
         b'service brake systems.</P><EXTRACT><P>Inner paragraph.</P></EXTRACT></DIV8></DIV5></ECFR>')
    root = etree.fromstring(x, PARSER)
    secs = list(root.iter("DIV8"))
    check("section found", len(secs) == 1)
    t = block_text(secs[0])
    check("text extracted without duplication", t.count("Inner paragraph.") == 1 and "Scope" in t)


def test_adapter():
    print("5. Existing-source adapter")
    from .adapt_existing import iter_json_records, read_html
    d = ROOT / TEST_RAW / "_adapter"
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.jsonl").write_text(json.dumps({"id": "x1", "title": "T", "text": "abc"}) + "\n", encoding="utf-8")
    recs = list(iter_json_records(d / "a.jsonl"))
    check("JSONL reading", recs and recs[0]["id"] == "x1")
    (d / "p.html").write_text("<html><body><article><p>" + "Weibull analysis of field failures. " * 20 +
                              "</p></article></body></html>", encoding="utf-8")
    check("HTML extraction", "Weibull" in read_html(d / "p.html"))
    try:
        import pypdf  # noqa: F401
        check("pypdf installed (for PDFs)", True)
    except ImportError:
        check("pypdf installed (for PDFs)", False, "pip install -r requirements.txt")


def main():
    logging.basicConfig(level=logging.WARNING)
    print(f"Python {sys.version.split()[0]} on {sys.platform}")
    check("Python 3.10 or newer", sys.version_info >= (3, 10))
    try:
        cfg = test_config()
        test_nhtsa(cfg)
        test_uspto(cfg)
        test_ecfr()
        test_adapter()
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("no unexpected errors", False, str(e))
    finally:
        shutil.rmtree(ROOT / TEST_RAW, ignore_errors=True)
    failed = [n for n, ok in results if not ok]
    print()
    if failed:
        print(f"{len(failed)} check(s) FAILED: {failed}")
        print("Send this output to get it fixed.")
        sys.exit(1)
    print(f"ALL {len(results)} CHECKS PASSED")


if __name__ == "__main__":
    main()
