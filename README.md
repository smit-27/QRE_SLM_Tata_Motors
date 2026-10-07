# QRE corpus harvest kit (Layer 2 of the plan)

Harvests the new (starred) sources from the framework into one shared schema.
NTRS, EverySpec, NIST, arXiv and DTIC stay on the existing qa-graphrag
harvester; `adapt_existing.py` converts their output into the same schema.

## Setup (laptop)
    python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    # edit configs/sources.yaml: set contact_email

Point `data/` at the 4 TB drive (symlink or move the repo there).

## Test first (any machine, including a Windows laptop)
    python -m harvest.selftest    # offline: checks every parser on synthetic data (expect ALL CHECKS PASSED)
    python -m harvest.netcheck    # online: checks every source is reachable, downloads nothing big

## Run order
| Step | Command | Time (typical) | Output |
|---|---|---|---|
| 1. NHTSA test | `python -m harvest.nhtsa --limit 1000` | minutes | data/raw/nhtsa/ |
| 2. NHTSA full | `python -m harvest.nhtsa --force` | < 1 h | complaints, recalls, investigations |
| 3. eCFR / FMVSS | `python -m harvest.ecfr` | minutes | data/raw/ecfr/ |
| 4. USPTO key | register at data.uspto.gov, then `export USPTO_API_KEY=...` | - | - |
| 5. USPTO list | `python -m harvest.uspto --list-only --debug` | seconds | file list |
| 6. USPTO test | `python -m harvest.uspto --max-files 2` | ~10-20 min | data/raw/uspto/ |
| 7. USPTO full | `python -m harvest.uspto` (resumable, re-run after any failure) | 1-3 days | CPC-filtered patents |
| 8. Existing sources | `python -m harvest.adapt_existing --input <path> --source ntrs --licence "US government" --vertical engineering` | varies | data/raw/<source>/ |
| 9. Manual PDFs | put UNECE / MIL-HDBK PDFs in data/manual/..., then run adapt_existing on that folder | minutes | data/raw/unece/ etc. |
| 10. Report | `python -m harvest.report` | minutes | docs/data_manifest.md |

## Schema (every record)
id, source, subsource, url, licence, retrieved_at, vertical, title, text, meta, sha256

## If something breaks
- NHTSA URL moved: edit the URL lists in configs/sources.yaml (fallbacks already included).
- NHTSA text fields not found: the script reads column names from the official
  dictionary file and prints them; put the right names in `text_fields`.
- USPTO listing empty: run with `--debug`, open docs/uspto_PTGRXML_listing.json and
  check the response shape. Fallbacks: `--urls-file` (URLs copied from the
  data.uspto.gov website) or `--local-dir` (zips downloaded by hand).
- Logs: docs/harvest_<source>.log

## Never put in data/
IATF 16949, AIAG-VDA FMEA handbook, VDA volumes, SAE J-standards, ISO standards,
or any proprietary company data. Those belong in the access-controlled GraphRAG store.
