"""Network check: confirms this machine can reach every data source, WITHOUT
downloading the big files. Run it on the laptop now and on the Spark later
(corporate proxies/firewalls are the most common failure).

Usage:
    python -m harvest.netcheck
"""
from __future__ import annotations

import os

import requests

from .common import load_config, user_agent


def probe(url, headers, method="HEAD"):
    try:
        r = requests.request(method, url, headers=headers, timeout=30, allow_redirects=True, stream=True)
        size = r.headers.get("Content-Length")
        size = f"{int(size)/1e6:,.1f} MB" if size and size.isdigit() else "size unknown"
        r.close()
        return r.status_code, size
    except Exception as e:  # noqa: BLE001
        return None, type(e).__name__


def main():
    cfg = load_config()
    headers = {"User-Agent": user_agent(cfg)}
    rows = []
    for name, d in cfg["nhtsa"]["datasets"].items():
        zips = [("data zip " + u.rsplit("/", 1)[-1], [u]) for u in d["parts"]] if d.get("parts") \
            else [("data zip", d["urls"])]
        for kind, urls in [("dictionary", d["dictionary"])] + zips:
            ok = None
            for u in urls:
                code, info = probe(u, headers)
                if code == 200:
                    ok = (u, code, info)
                    break
            rows.append((f"NHTSA {name} {kind}", *(ok or (urls[0], code, info))))
    code, info = probe("https://www.ecfr.gov/api/versioner/v1/titles.json", headers, "GET")
    rows.append(("eCFR API", "https://www.ecfr.gov/api/versioner/v1/titles.json", code, info))
    ucfg = cfg["uspto"]
    key = os.environ.get("USPTO_API_KEY")
    url = f"{ucfg['api_base']}/api/v1/datasets/products/{ucfg['product_id']}"
    h = {**headers, **({"X-API-KEY": key} if key else {})}
    code, info = probe(url, h, "GET")
    rows.append(("USPTO API" + ("" if key else " (no key set)"), url, code, info))

    print(f"{'Check':34} {'Status':8} {'Info':16} URL")
    bad = 0
    for name, u, code, info in rows:
        status = "OK" if code == 200 else (str(code) if code else "ERROR")
        if code != 200:
            bad += 1
        print(f"{name:34} {status:8} {info:16} {u}")
    print()
    if bad:
        print(f"{bad} check(s) not OK.")
        print("- ERROR on everything: no internet or a proxy is needed (set HTTPS_PROXY / HTTP_PROXY).")
        print("- 404 on NHTSA: the file moved; update the URL in configs/sources.yaml.")
        print("- USPTO 401/403: API key missing or invalid (expected until you register).")
    else:
        print("All sources reachable.")


if __name__ == "__main__":
    main()
