
from pathlib import Path

source = Path("data/processed/input.txt")
destination = Path("data/processed/input_40mb.txt")

LIMIT = 40 * 1024 * 1024  # 40 MiB

with source.open("rb") as src, destination.open("wb") as dst:
    dst.write(src.read(LIMIT))

print(f"Created: {destination}")
print(f"Size: {destination.stat().st_size / (1024 * 1024):.2f} MiB")
