import argparse
import os
import sys
import pandas as pd
from db import init_db, upsert_dataset, insert_images

REQUIRED_COLS = ["image_name", "image_path"]

def ingest(dataset_name: str, root_dir: str, csv_path: str):
    if not os.path.isdir(root_dir):
        print(f"[ERR] root_dir does not exist: {root_dir}")
        sys.exit(1)
    if not os.path.isfile(csv_path):
        print(f"[ERR] CSV not found: {csv_path}")
        sys.exit(1)

    df = pd.read_csv(csv_path)
    for c in REQUIRED_COLS:
        if c not in df.columns:
            print(f"[ERR] Missing required column: {c}")
            sys.exit(1)

    # Build rows: (image_name, image_path, metadata_dict)
    rows = []
    other_cols = [c for c in df.columns if c not in REQUIRED_COLS]
    for _, r in df.iterrows():
        name = str(r["image_name"])
        rel_path = str(r["image_path"])
        meta = {c: (None if pd.isna(r[c]) else r[c]) for c in other_cols}
        rows.append((name, rel_path, meta))

    dataset_id = upsert_dataset(dataset_name, os.path.abspath(root_dir))
    inserted = insert_images(dataset_id, rows)
    print(f"[OK] Dataset='{dataset_name}' (id={dataset_id}). Inserted {inserted} images.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest an image CSV into the local DB.")
    parser.add_argument("--dataset", required=True, help="Dataset name (unique).")
    parser.add_argument("--root", required=True, help="Root directory containing images.")
    parser.add_argument("--csv", required=True, help="Path to metadata CSV.")
    args = parser.parse_args()

    init_db()
    ingest(args.dataset, args.root, args.csv)