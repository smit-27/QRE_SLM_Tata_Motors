#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
sudo apt update
sudo apt install -y unzip tmux python3-venv python3-pip curl nano git
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m harvest.selftest
python -m harvest.netcheck
df -h .