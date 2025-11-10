#!/usr/bin/env python3
"""
make_manifests_with_thumbs.py
Generate IIIF Presentation 3 manifests + a collection from a CSV, including:
- proper v3 metadata (language maps)
- manifest-level thumbnails (UV uses these)
- robust IIIF Image API v2/v3 handling
- optional probing of info.json to get width/height when not in CSV

Usage (typical GitHub Pages layout):
  python3 make_manifests_with_thumbs.py \
      --csv /path/to/your.csv \
      --out /absolute/path/to/iiif \
      --host-base INSERT-URL-TO-YOUR-GITHUB-DIRECTORY-HERE \
      --thumbs-dir /absolute/path/to/thumbs

Folder layout expected on your site:
  https://<host-base>/
      thumbs/<object_id>.jpg|.png
      iiif/<object_id>/manifest.json
      iiif/collection.json

If your thumbs live elsewhere, adjust --host-base/--thumbs-web-prefix.
"""

import os
import sys
import csv
import json
import re
import argparse
import urllib.request
import ssl
from typing import Dict, Any, List, Tuple, Optional

# ---------- TLS (certifi if available helps avoid CERTIFICATE_VERIFY_FAILED) ----------
try:
    import certifi  # type: ignore
except Exception:
    certifi = None

def urlopen_ctx():
    if certifi:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()

# ---------- Helpers for language maps & metadata ----------
def lm(value, lang="en") -> Dict[str, List[str]]:
    """Convert string or list -> IIIF v3 language map"""
    if value is None:
        return {lang: []}
    if isinstance(value, list):
        return {lang: [str(v) for v in value if v is not None]}
    return {lang: [str(value)]}

def add_metadata(manifest: Dict[str, Any], pairs: List[Tuple[str, Any]], lang="en"):
    md = manifest.setdefault("metadata", [])
    for label, value in pairs:
        if value is None:
            continue
        # allow lists or strings
        if isinstance(value, str) and ";" in value:
            # CSV often has multi-values separated by semicolons
            value = [v.strip() for v in value.split(";") if v.strip()]
        md.append({"label": lm(label, lang), "value": lm(value, lang)})

# ---------- IIIF helpers ----------
def normalize_v2_to_v3(url: str) -> str:
    return re.sub(r"/iiif/(2|v2)/", "/iiif/3/", url)

def derive_image_api_base(iiif_info: str, image_api_base: str) -> str:
    iiif_info = (iiif_info or "").strip()
    image_api_base = (image_api_base or "").strip()
    if iiif_info:
        u = normalize_v2_to_v3(iiif_info)
        if u.endswith("/info.json"):
            return u[:-10]
        if "/iiif/" in u:
            return u.rstrip("/")
    if image_api_base:
        return normalize_v2_to_v3(image_api_base.rstrip("/"))
    return ""

def fetch_info_json(image_api_base: str, timeout: int = 20) -> Optional[Dict[str, Any]]:
    if not image_api_base:
        return None
    url = image_api_base.rstrip("/") + "/info.json"
    try:
        with urllib.request.urlopen(url, context=urlopen_ctx(), timeout=timeout) as r:
            data = r.read()
            return json.loads(data.decode("utf-8", "replace"))
    except Exception:
        return None

def pick_thumbnail(object_id: str, image_api_base: str, thumbs_fs_dir: str,
                   thumbs_web_prefix: str) -> Tuple[Optional[str], Optional[str]]:
    jpg_fs = os.path.join(thumbs_fs_dir, f"{object_id}.jpg")
    png_fs = os.path.join(thumbs_fs_dir, f"{object_id}.png")
    if os.path.isfile(jpg_fs):
        return f"{thumbs_web_prefix}/{object_id}.jpg", "image/jpeg"
    if os.path.isfile(png_fs):
        return f"{thumbs_web_prefix}/{object_id}.png", "image/png"
    if image_api_base:
        return f"{image_api_base}/full/300,/0/default.jpg", "image/jpeg"
    return None, None

def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

def read_csv_loose(path: str) -> List[Dict[str, Any]]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    with open(path, newline="", encoding="latin-1", errors="replace") as f:
        return list(csv.DictReader(f))

def lower_headers(row: Dict[str, Any]) -> Dict[str, Any]:
    return {(k or "").lower().strip(): (v if v is not None else "") for k, v in row.items()}

def first_present(row: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = row.get(k.lower())
        if v:
            s = str(v).strip()
            if s:
                return s
    return ""

# ---------- Main build ----------
def build_manifest(
    row: Dict[str, Any],
    manifest_id: str,
    canvas_id: str,
    page_id: str,
    anno_id: str,
    image_api_base: str,
    image_url: str,
    width: int,
    height: int,
    label: str,
    thumb_id: Optional[str],
    thumb_fmt: Optional[str],
    lang: str = "en",
) -> Dict[str, Any]:

    manifest: Dict[str, Any] = {
        "@context": "http://iiif.io/api/presentation/3/context.json",
        "id": manifest_id,
        "type": "Manifest",
        "label": lm(label, lang),
    }

    # ---- RIGHTS / ATTRIBUTION (v3 shape) ----
    # Pull from common CSV columns; adapt names if yours differ.
    rights_text = first_present(row, "attribution", "credit", "rights_statement", "rightsstatement", "rights_text")
    rights_uri  = first_present(row, "rights", "license")  # prefer a URL here

    # If we have any human-readable statement, show it prominently
    if rights_text:
        manifest["requiredStatement"] = {
            "label": lm("Attribution", lang),
            "value": lm(rights_text, lang)
        }

    # If we have a URI that looks like a license, set it on 'rights'
    if rights_uri and rights_uri.lower().startswith(("http://", "https://")):
        manifest["rights"] = rights_uri

    # ---- Manifest-level thumbnail (ARRAY) ----
    if thumb_id and thumb_fmt:
        manifest["thumbnail"] = [{
            "id": thumb_id,
            "type": "Image",
            "format": thumb_fmt,
            "width": 300,
            "height": 300
        }]

    # ---- Canvas + Painting Annotation (v3) ----
    canvas = {
        "id": canvas_id,
        "type": "Canvas",
        "width": width,
        "height": height,
        "items": [{
            "id": page_id,
            "type": "AnnotationPage",
            "items": [{
                "id": anno_id,
                "type": "Annotation",
                "motivation": "painting",
                "target": canvas_id,
                "body": {
                    "id": image_url,
                    "type": "Image",
                    "format": "image/jpeg",
                    "service": [{
                        "id": image_api_base,
                        "type": "ImageService3",
                        "profile": "level1"
                    }]
                }
            }]
        }]
    }

    # (Optional but helps some clients) Canvas-level thumbnail too
    if thumb_id and thumb_fmt:
        canvas["thumbnail"] = [{
            "id": thumb_id,
            "type": "Image",
            "format": thumb_fmt,
            "width": 300,
            "height": 300
        }]

    manifest["items"] = [canvas]

    # ---- Metadata (still v3 language maps) ----
    metadata_pairs: List[Tuple[str, Any]] = []
    def add_pair(label_key: str, *csv_keys: str):
        value = first_present(row, *csv_keys)
        if value:
            metadata_pairs.append((label_key, value))

    add_pair("Creator", "creator", "artist", "maker", "author")
    add_pair("Date", "date", "year")
    add_pair("Medium", "medium", "materials", "material")
    add_pair("Dimensions", "dimensions", "size")
    add_pair("Identifier", "identifier", "id", "accession_number", "accession")
    # You can keep a human-readable “Rights” duplicate in metadata too (optional)
    if rights_text:
        metadata_pairs.append(("Rights", rights_text))

    if metadata_pairs:
        add_metadata(manifest, metadata_pairs, lang=lang)

    return manifest


def main():
    parser = argparse.ArgumentParser(description="Build IIIF v3 manifests and collection with thumbnails.")
    parser.add_argument("--csv", required=True, help="Path to the CSV containing records")
    parser.add_argument("--out", required=True, help="Output directory for iiif manifests (e.g., .../iiif)")
    parser.add_argument("--host-base", required=False, default="https://INSERT-URL-TO-YOUR-GITHUB-DIRECTORY-HERE",
                        help="Public site root, without trailing slash (default: GitHub Pages root)")
    parser.add_argument("--thumbs-dir", required=False, help="Local filesystem path to thumbnails folder")
    parser.add_argument("--thumbs-web-prefix", required=False,
                        help="Public URL prefix for thumbnails (default: <host-base>/thumbs)")
    parser.add_argument("--id-col", required=False, default="object_id", help="CSV column name for object id")
    parser.add_argument("--lang", required=False, default="en", help="Language code for labels/metadata")
    parser.add_argument("--collection-label", required=False, default="Collection",
                        help="Label for the generated collection")
    parser.add_argument("--probe-info", action="store_true",
                        help="Fetch info.json to fill missing width/height")
    args = parser.parse_args()

    csv_path = args.csv
    out_dir = args.out.rstrip("/")
    host_base = args.host_base.rstrip("/")
    manifest_base = f"{host_base}/iiif"
    collection_id = f"{manifest_base}/collection.json"
    lang = args.lang

    thumbs_fs_dir = args.thumbs_dir if args.thumbs_dir else os.path.join(os.path.dirname(csv_path), "thumbs")
    thumbs_web_prefix = args.thumbs_web_prefix if args.thumbs_web_prefix else f"{host_base}/thumbs"
    id_col = args.id_col.lower().strip()

    if not os.path.isfile(csv_path):
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    ensure_dir(out_dir)

    rows = read_csv_loose(csv_path)
    if not rows:
        print("ERROR: CSV appears empty.", file=sys.stderr)
        sys.exit(1)

    manifests_written = 0
    collection_items: List[Dict[str, Any]] = []

    for raw in rows:
        row = lower_headers(raw)
        object_id = row.get(id_col, "").strip()
        if not object_id:
            print("[-] skipping row with no object_id")
            continue

        # Label/title
        label = first_present(row, "label", "title", "name") or object_id

        # Derive IIIF image service base
        iiif_info = first_present(row, "iiif_info", "info_json")
        image_api_base = derive_image_api_base(iiif_info, first_present(row, "image_api_base", "image_service_id", "image_service"))
        if not image_api_base:
            print(f"[-] {object_id}: no image_api_base/iiif_info; skipping")
            continue

        # Width/height from CSV or probe info.json
        width_str = first_present(row, "width")
        height_str = first_present(row, "height")
        width = int(width_str) if width_str.isdigit() else 0
        height = int(height_str) if height_str.isdigit() else 0

        if (width == 0 or height == 0) and args.probe_info:
            info = fetch_info_json(image_api_base)
            if info:
                # v3 or v2 info.json both usually contain width/height
                width = int(info.get("width") or width or 0)
                height = int(info.get("height") or height or 0)

        if width == 0 or height == 0:
            # Provide sane fallback to keep manifest valid; UV can still render
            width, height = (2000, 2000)

        # Image URL for body: use Image API default.jpg with full/max
        image_url = f"{image_api_base}/full/max/0/default.jpg"

        # IDs
        manifest_id = f"{manifest_base}/{object_id}/manifest.json"
        canvas_id   = f"{manifest_base}/{object_id}/canvas/1"
        page_id     = f"{manifest_base}/{object_id}/page/1"
        anno_id     = f"{manifest_base}/{object_id}/annotation/1"

        # Thumbnail selection
        thumb_id, thumb_fmt = pick_thumbnail(object_id, image_api_base, thumbs_fs_dir, thumbs_web_prefix)

        # Build manifest
        manifest = build_manifest(
            row=row,
            manifest_id=manifest_id,
            canvas_id=canvas_id,
            page_id=page_id,
            anno_id=anno_id,
            image_api_base=image_api_base,
            image_url=image_url,
            width=width, height=height,
            label=label,
            thumb_id=thumb_id, thumb_fmt=thumb_fmt,
            lang=lang,
        )

        # Write manifest file
        obj_dir = os.path.join(out_dir, object_id)
        ensure_dir(obj_dir)
        mf_path = os.path.join(obj_dir, "manifest.json")
        with open(mf_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        manifests_written += 1

        # Add to collection (with a small item entry, can include thumbnail too)
        item_entry: Dict[str, Any] = {
            "id": manifest_id,
            "type": "Manifest",
            "label": lm(label, lang),
        }
        if thumb_id and thumb_fmt:
            item_entry["thumbnail"] = [{
                "id": thumb_id, "type": "Image", "format": thumb_fmt, "width": 300, "height": 300
            }]
        collection_items.append(item_entry)

    # Build collection.json
    collection = {
        "@context": "http://iiif.io/api/presentation/3/context.json",
        "id": collection_id,
        "type": "Collection",
        "label": lm(args.collection_label or "Collection", lang),
        "items": collection_items,
    }
    coll_path = os.path.join(out_dir, "collection.json")
    with open(coll_path, "w", encoding="utf-8") as f:
        json.dump(collection, f, ensure_ascii=False, indent=2)

    print(f"Done. Wrote {manifests_written} manifests to {out_dir}")
    print(f"Collection: {collection_id}")
    print("Tip: hard-refresh the viewer or append ?ts=<timestamp> to bypass cache.")
    
if __name__ == "__main__":
    main()
