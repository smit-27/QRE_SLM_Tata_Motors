
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
OUTPUT_DIR = ROOT / "data" / "processed"
OUTPUT_FILE = OUTPUT_DIR / "input.txt"


def main():
    if not RAW_DIR.is_dir():
        raise SystemExit(f"Raw directory not found: {RAW_DIR}")

    # Discover only the actual harvester's output format.
    # Exclude intermediate download directories.
    shards = sorted(
        path for path in RAW_DIR.rglob("*.jsonl.gz")
        if not any(part.startswith("_") for part in path.relative_to(RAW_DIR).parts)
    )

    if not shards:
        raise SystemExit(
            f"No .jsonl.gz shards found under {RAW_DIR}.\n"
            "Check that harvesting completed and inspect data/raw."
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    seen_hashes = set()
    total_records = 0
    accepted_records = 0
    duplicate_records = 0
    empty_records = 0
    malformed_lines = 0
    total_chars = 0

    with OUTPUT_FILE.open("w", encoding="utf-8", newline="\n") as out:
        for shard in shards:
            shard_records = 0

            try:
                with gzip.open(shard, "rt", encoding="utf-8") as source:
                    for line_number, line in enumerate(source, start=1):
                        if not line.strip():
                            continue

                        total_records += 1

                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            malformed_lines += 1
                            print(
                                f"WARNING: Invalid JSON: {shard.name}, "
                                f"line {line_number}"
                            )
                            continue

                        if not isinstance(record, dict):
                            malformed_lines += 1
                            continue

                        # Validate the repository's common schema.
                        if not record.get("id") or not record.get("source"):
                            malformed_lines += 1
                            continue

                        text = record.get("text")
                        if not isinstance(text, str) or not text.strip():
                            empty_records += 1
                            continue

                        text = text.strip()

                        # Prefer the harvester's content hash.
                        # Compute one if it is missing or invalid.
                        digest = hashlib.sha256(
                            text.encode("utf-8")
                        ).hexdigest()

                        if digest in seen_hashes:
                            duplicate_records += 1
                            continue

                        seen_hashes.add(digest)

                        # Include the document title as useful context.
                        title = record.get("title")
                        if isinstance(title, str) and title.strip():
                            document = title.strip() + "\n\n" + text
                        else:
                            document = text

                        # Document boundaries aid inspection and
                        # help prevent accidental text concatenation.
                        out.write(document + "\n\n")
                        accepted_records += 1
                        shard_records += 1
                        total_chars += len(document)

            except (OSError, EOFError) as exc:
                print(f"ERROR reading {shard}: {exc}")
                continue

            print(
                f"{shard.relative_to(RAW_DIR)}: "
                f"{shard_records:,} unique documents added"
            )

    if accepted_records == 0:
        OUTPUT_FILE.unlink(missing_ok=True)
        raise SystemExit(
            "No usable records were extracted. "
            "Check the warnings and raw data."
        )

    print("\n========== CORPUS SUMMARY ==========")
    print(f"Shards discovered:       {len(shards):,}")
    print(f"Records parsed:          {total_records:,}")
    print(f"Documents included:      {accepted_records:,}")
    print(f"Duplicate texts skipped: {duplicate_records:,}")
    print(f"Empty texts skipped:     {empty_records:,}")
    print(f"Malformed records:       {malformed_lines:,}")
    print(f"Corpus characters:       {total_chars:,}")
    print(f"Unique characters:       {len(set(OUTPUT_FILE.read_text(encoding='utf-8'))):,}")
    print(f"Output file:             {OUTPUT_FILE}")
    print("\n========== CORPUS PREVIEW ==========")

    with OUTPUT_FILE.open("r", encoding="utf-8") as f:
        print(f.read(1500))


if __name__ == "__main__":
    main()
