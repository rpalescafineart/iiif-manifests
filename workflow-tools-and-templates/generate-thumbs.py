#!/usr/bin/env python3
import os, sys, csv, re, urllib.request, ssl

# --- TLS / certs (fixes CERTIFICATE_VERIFY_FAILED) ---
try:
    import certifi
except ImportError:
    certifi = None

def download_with_tls(url: str, out_path: str, timeout: int = 30):
    if certifi:
        ctx = ssl.create_default_context(cafile=certifi.where())
    else:
        ctx = ssl.create_default_context()
    with urllib.request.urlopen(url, context=ctx, timeout=timeout) as r, open(out_path, "wb") as f:
        f.write(r.read())

# ---------- paths / defaults ----------
BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV_NAME = "INSERT-YOUR-FILENAME-HERE.csv"
CSV_PATH = os.path.join(BASE, DEFAULT_CSV_NAME)
OUT_DIR = os.path.join(BASE, "thumbs")
DEFAULT_ID_COL = "object_id"

# ---------- helpers ----------
def lower_headers(row: dict) -> dict:
    # normalize headers to lowercase, trim whitespace, coerce None -> ""
    return {(k or "").lower().strip(): (v or "").strip() for k, v in row.items()}

def read_csv_loosely(path: str):
    # Try a couple of encodings gracefully
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    # last resort with replacement
    with open(path, newline="", encoding="latin-1", errors="replace") as f:
        return list(csv.DictReader(f))

def get_arg(flag: str, default: str | None = None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv) and not sys.argv[i+1].startswith("-"):
            return sys.argv[i+1]
        return True  # boolean flag present
    return default

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def normalize_v2_to_v3(url: str) -> str:
    return re.sub(r"/iiif/(2|v2)/", "/iiif/3/", url)

def derive_image_api_base(iiif_info: str, image_api_base: str) -> str:
    iiif_info = (iiif_info or "").strip()
    image_api_base = (image_api_base or "").strip()

    if iiif_info:
        iiif_info = normalize_v2_to_v3(iiif_info)
        # If it's an info.json, strip it to get the base
        if iiif_info.endswith("/info.json"):
            return iiif_info[:-10]
        # Some datasets store the base in iiif_info already
        if "/iiif/" in iiif_info:
            return iiif_info.rstrip("/")
    if image_api_base:
        return normalize_v2_to_v3(image_api_base.rstrip("/"))
    return ""

def build_thumb_urls(image_api_base: str, width: int = 300):
    # IIIF Image API v3 pattern
    base = image_api_base.rstrip("/")
    return (
        f"{base}/full/{width},/0/default.jpg",
        f"{base}/full/{width},/0/default.png",
    )

# ---------- main ----------
def main():
    csv_path = get_arg("--csv", CSV_PATH)
    out_dir = get_arg("--out", OUT_DIR)
    id_col  = (get_arg("--id-col", DEFAULT_ID_COL) or DEFAULT_ID_COL).lower().strip()

    if not os.path.isfile(csv_path):
        print(f"ERROR: CSV not found: {csv_path}")
        sys.exit(1)

    ensure_dir(out_dir)

    rows = read_csv_loosely(csv_path)
    if not rows:
        print(f"ERROR: CSV contains no rows: {csv_path}")
        sys.exit(1)

    saved = 0
    skipped = 0

    for raw in rows:
        row = lower_headers(raw)

        object_id = (row.get(id_col) or "").strip()
        if not object_id:
            skipped += 1
            print("[-] skip row with no object_id")
            continue

        # Prefer iiif_info to derive base, fall back to other fields
        iiif_info = (row.get("iiif_info") or row.get("info_json") or "").strip()
        image_api_base = (
            row.get("image_api_base")
            or row.get("image_service_id")
            or row.get("image_service")
            or ""
        ).strip()

        image_api_base = derive_image_api_base(iiif_info, image_api_base)
        if not image_api_base:
            skipped += 1
            print(f"[-] {object_id}: no image_api_base/iiif_info; skipping")
            continue

        thumb_url_jpg, thumb_url_png = build_thumb_urls(image_api_base, width=300)
        # Optional: show which URL we're fetching (useful for debugging SSL)
        # print(f"Fetching JPG: {thumb_url_jpg}")

        out_path = os.path.join(out_dir, f"{object_id}.jpg")
        try:
            download_with_tls(thumb_url_jpg, out_path)
            print(f"[+] saved {out_path}")
            saved += 1
            continue
        except Exception as _:
            # Fallback to PNG
            out_path = os.path.join(out_dir, f"{object_id}.png")
            try:
                download_with_tls(thumb_url_png, out_path)
                print(f"[+] saved {out_path}")
                saved += 1
            except Exception as e:
                skipped += 1
                print(f"[-] {object_id}: failed both jpg/png ({e})")

    print(f"\nDone. Saved {saved} thumbs to: {out_dir}")
    if skipped:
        print(f"Skipped {skipped} rows (see messages above).")

if __name__ == "__main__":
    main()
