import json
import os
import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any, Iterable

DB_PATH = os.environ.get("IMGQA_DB_PATH", "image_qa.sqlite")
VALID_DECISIONS = {"keep", "discard", "unsure"}

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript(
        """
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            root_dir TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id INTEGER NOT NULL,
            image_name TEXT NOT NULL,
            image_path TEXT NOT NULL,  -- relative to dataset root
            metadata_json TEXT NOT NULL,
            UNIQUE(dataset_id, image_path),
            FOREIGN KEY(dataset_id) REFERENCES datasets(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS decisions (
            image_id INTEGER PRIMARY KEY,
            decision TEXT CHECK(decision IN ('keep','discard','unsure')) NOT NULL,
            note TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_images_dataset ON images(dataset_id);
        CREATE INDEX IF NOT EXISTS idx_images_name ON images(image_name);
        CREATE INDEX IF NOT EXISTS idx_images_path ON images(image_path);
        """
    )
    conn.commit()

def upsert_dataset(name: str, root_dir: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("SELECT id FROM datasets WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE datasets SET root_dir = ? WHERE id = ?", (root_dir, row["id"]))
        conn.commit()
        return row["id"]
    cur.execute(
        "INSERT INTO datasets(name, root_dir, created_at) VALUES(?,?,?)",
        (name, root_dir, now),
    )
    conn.commit()
    return cur.lastrowid

def get_datasets() -> List[sqlite3.Row]:
    conn = get_conn()
    return conn.execute("SELECT * FROM datasets ORDER BY created_at DESC").fetchall()

def get_dataset(dataset_id: int) -> sqlite3.Row:
    conn = get_conn()
    return conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()

def insert_images(dataset_id: int, rows: List[Tuple[str, str, Dict[str, Any]]]) -> int:
    """
    rows: list of (image_name, image_path, metadata_dict)
    Returns number of inserted rows (skips duplicates).
    """
    conn = get_conn()
    cur = conn.cursor()
    inserted = 0
    for name, path, meta in rows:
        try:
            cur.execute(
                "INSERT OR IGNORE INTO images(dataset_id, image_name, image_path, metadata_json) VALUES (?,?,?,?)",
                (dataset_id, name, path, json.dumps(meta, ensure_ascii=False)),
            )
            if cur.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    return inserted

def set_decision(image_id: int, decision: Optional[str], note: Optional[str] = None):
    """
    decision is 'keep' | 'discard' | 'unsure'
    If decision is None, clears the decision row.
    """
    conn = get_conn()
    cur = conn.cursor()
    if decision is None:
        cur.execute("DELETE FROM decisions WHERE image_id = ?", (image_id,))
    else:
        now = datetime.utcnow().isoformat()
        cur.execute(
            """
            INSERT INTO decisions(image_id, decision, note, updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(image_id) DO UPDATE SET
              decision=excluded.decision,
              note=COALESCE(excluded.note, decisions.note),
              updated_at=excluded.updated_at
            """,
            (image_id, decision, note, now),
        )
    conn.commit()

def query_images(
    dataset_id: int,
    decision_filter: str = "unmarked",  # unmarked|keep|discard|unsure|all
    search_text: str = "",
    order_by: str = "image_name",       # image_name|image_path|random
    limit: int = 1,
    offset: int = 0,
) -> Tuple[List[sqlite3.Row], int]:
    conn = get_conn()
    params = [dataset_id]
    where = ["i.dataset_id = ?"]

    # Decision filter
    if decision_filter == "unmarked":
        where.append("d.image_id IS NULL")
    elif decision_filter in ("keep", "discard", "unsure"):
        where.append("d.decision = ?")
        params.append(decision_filter)

    # Search text across name, path, and metadata_json
    if search_text:
        st = f"%{search_text.lower()}%"
        where.append("(LOWER(i.image_name) LIKE ? OR LOWER(i.image_path) LIKE ? OR LOWER(i.metadata_json) LIKE ?)")
        params.extend([st, st, st])

    where_sql = " AND ".join(where)

    # Count
    count_sql = f"""
        SELECT COUNT(*) AS cnt
        FROM images i
        LEFT JOIN decisions d ON d.image_id = i.id
        WHERE {where_sql}
    """
    total = conn.execute(count_sql, params).fetchone()["cnt"]

    # Order
    if order_by == "random":
        order_sql = "ORDER BY RANDOM()"
    elif order_by == "image_path":
        order_sql = "ORDER BY i.image_path"
    else:
        order_sql = "ORDER BY i.image_name"

    sql = f"""
        SELECT i.*, d.decision, d.note, d.updated_at
        FROM images i
        LEFT JOIN decisions d ON d.image_id = i.id
        WHERE {where_sql}
        {order_sql}
        LIMIT ? OFFSET ?
    """
    rs = conn.execute(sql, params + [limit, offset]).fetchall()
    return rs, total

def get_marked(dataset_id: int, decision: Optional[str] = None) -> List[sqlite3.Row]:
    conn = get_conn()
    if decision:
        sql = """
          SELECT i.*, d.decision, d.note, d.updated_at
          FROM images i JOIN decisions d ON d.image_id = i.id
          WHERE i.dataset_id = ? AND d.decision = ?
          ORDER BY d.updated_at DESC
        """
        return conn.execute(sql, (dataset_id, decision)).fetchall()
    else:
        sql = """
          SELECT i.*, d.decision, d.note, d.updated_at
          FROM images i JOIN decisions d ON d.image_id = i.id
          WHERE i.dataset_id = ?
          ORDER BY d.updated_at DESC
        """
        return conn.execute(sql, (dataset_id,)).fetchall()
    
def get_export_rows(dataset_id: int, include_unmarked: bool = False):
    """
    Returns rows for CSV export:
    dataset_name, root_dir, image_name, image_path (relative), abs_path, decision, note, updated_at, metadata_json
    If include_unmarked=True, includes images with no decision (decision=None).
    """
    conn = get_conn()
    # Fetch dataset info for abs_path composition on the app side if you prefer;
    # but returning root_dir here keeps app.py simpler.
    sql = """
        SELECT
            ds.name AS dataset_name,
            ds.root_dir AS root_dir,
            i.id AS image_id,
            i.image_name,
            i.image_path,
            d.decision,
            d.note,
            d.updated_at,
            i.metadata_json
        FROM images i
        JOIN datasets ds ON ds.id = i.dataset_id
        LEFT JOIN decisions d ON d.image_id = i.id
        WHERE i.dataset_id = ?
    """
    rows = conn.execute(sql, (dataset_id,)).fetchall()
    if not include_unmarked:
        rows = [r for r in rows if r["decision"] is not None]
    return rows

def _json_field_expr():
    arch = "COALESCE(json_extract(metadata_json, '$.unique_context_archetype'), json_extract(metadata_json, '$.gt_context_archetype'), json_extract(metadata_json, '$.gt_context_archetypes'))"
    cat  = "COALESCE(json_extract(metadata_json, '$.gt_location_category'), json_extract(metadata_json, '$.location_category'), json_extract(metadata_json, '$.gt_location'))"
    return arch, cat

def get_archetype_tree(dataset_id: int):
    conn = get_conn()
    cur = conn.cursor()
    arch_expr, cat_expr = _json_field_expr()

    cur.execute(f"""
        SELECT DISTINCT {arch_expr} AS arche
        FROM images
        WHERE dataset_id=? AND {arch_expr} IS NOT NULL
        ORDER BY arche
    """, (dataset_id,))
    archetypes = [r[0] for r in cur.fetchall()]

    tree = {}
    for a in archetypes:
        cur.execute(f"""
            SELECT DISTINCT {cat_expr} AS cat
            FROM images
            WHERE dataset_id=? AND {arch_expr}=? AND {cat_expr} IS NOT NULL
            ORDER BY cat
        """, (dataset_id, a))
        tree[a] = [r[0] for r in cur.fetchall()]
    conn.close()
    return tree

def count_images_by_arch_cat(dataset_id: int, archetype: str, category: str, decision_filter: Optional[str] = None) -> int:
    """
    Returns count for (archetype, category) optionally filtered by decision:
    decision_filter in {'keep','discard','unsure','unmarked'} or None/'all'
    """
    conn = get_conn()
    cur = conn.cursor()
    arch_expr, cat_expr = _json_field_expr()

    base = f"""
        FROM images i
        LEFT JOIN decisions d ON d.image_id = i.id
        WHERE i.dataset_id=? AND {arch_expr}=? AND {cat_expr}=?
    """
    params = [dataset_id, archetype, category]

    if decision_filter in ("keep", "discard", "unsure"):
        base += " AND d.decision = ?"
        params.append(decision_filter)
    elif decision_filter == "unmarked":
        base += " AND d.image_id IS NULL"
        # no param

    cur.execute("SELECT COUNT(*) " + base, params)
    n = cur.fetchone()[0]
    conn.close()
    return n

def images_by_arch_cat(
    dataset_id: int,
    archetype: str,
    category: str,
    decision_filter: Optional[str] = None,
    order_by: str = "image_path",
    limit: int = 60,
    offset: int = 0,
):
    """
    Returns image rows (joined with decisions) for (archetype, category)
    with optional decision_filter as above.
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    arch_expr, cat_expr = _json_field_expr()

    base = f"""
        FROM images i
        LEFT JOIN decisions d ON d.image_id = i.id
        WHERE i.dataset_id=? AND {arch_expr}=? AND {cat_expr}=?
    """
    params = [dataset_id, archetype, category]

    if decision_filter in ("keep", "discard", "unsure"):
        base += " AND d.decision = ?"
        params.append(decision_filter)
    elif decision_filter == "unmarked":
        base += " AND d.image_id IS NULL"

    order_sql = "i.image_path" if order_by == "image_path" else "i.image_name"

    cur.execute(
        "SELECT i.*, d.decision, d.note, d.updated_at " + base + f" ORDER BY {order_sql} LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def _now_iso() -> str:
    """UTC timestamp suitable for updated_at."""
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def _normalize_decision(val: Optional[str]) -> Optional[str]:
    """
    Normalize incoming text to one of keep/discard/unsure or None (unmarked).
    Accepts loose casing and some common synonyms.
    """
    if val is None:
        return None
    v = str(val).strip().lower()
    if v in ("", "none", "null", "na", "unmarked"):
        return None
    # common synonyms
    if v in ("k", "keep", "kept", "keeps"):
        return "keep"
    if v in ("d", "discard", "delete", "removed", "drop"):
        return "discard"
    if v in ("u", "unsure", "maybe", "review", "revisit"):
        return "unsure"
    # anything else stays as-is and will be validated against VALID_DECISIONS
    return v

def image_path_to_id_map(dataset_id: int) -> Dict[str, int]:
    """
    Map: relative image_path (with forward slashes) -> image_id
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, image_path FROM images WHERE dataset_id=?", (dataset_id,)
    ).fetchall()
    return {r["image_path"].replace("\\", "/"): r["id"] for r in rows}

def existing_decisions_map(dataset_id: int) -> Dict[int, Tuple[Optional[str], Optional[str]]]:
    """
    Map: image_id -> (decision, updated_at ISO)
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT i.id AS image_id, d.decision, d.updated_at "
        "FROM images i LEFT JOIN decisions d ON d.image_id=i.id "
        "WHERE i.dataset_id=?", (dataset_id,)
    ).fetchall()
    return {r["image_id"]: (r["decision"], r["updated_at"]) for r in rows}

def _parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except Exception:
        return None
    
def bulk_import_decisions_from_rows(
    dataset_id: int,
    rows: Iterable[dict],
    root_dir: str,
    prefer_newer: bool = True,
) -> Dict[str, int]:
    """
    Import decisions from CSV-like rows.
    Expected columns (either is fine):
      - image_path  (relative path)  OR
      - abs_path    (absolute path; must start with root_dir)
    Optional columns:
      - decision    ('keep'|'discard'|'unsure' or empty/unmarked to clear)
      - note
      - updated_at  (ISO). If missing, current UTC time is used.

    prefer_newer=True: only overwrite existing decisions if incoming updated_at is newer.
    Returns stats dict: upserted, cleared, skipped_missing, skipped_older, invalid_decision
    """
    rel_to_id = image_path_to_id_map(dataset_id)
    existing = existing_decisions_map(dataset_id)

    def rel_from_row(r: dict) -> Optional[str]:
        # prefer explicit relative path
        p = (r.get("image_path") or "").strip()
        if p:
            return p.replace("\\", "/")
        # else try to derive from abs_path
        ap = (r.get("abs_path") or "").strip()
        if ap and root_dir:
            try:
                ap_norm = os.path.abspath(ap)
                root_norm = os.path.abspath(root_dir)
                if ap_norm.startswith(root_norm):
                    rel = os.path.relpath(ap_norm, root_norm)
                    return rel.replace("\\", "/")
            except Exception:
                return None
        return None

    conn = get_conn()
    cur = conn.cursor()

    stats = {"upserted": 0, "cleared": 0, "skipped_missing": 0, "skipped_older": 0, "invalid_decision": 0}

    for r in rows:
        rel = rel_from_row(r)
        if not rel or rel not in rel_to_id:
            stats["skipped_missing"] += 1
            continue

        image_id = rel_to_id[rel]

        # normalize and validate the incoming decision
        dec_norm = _normalize_decision(r.get("decision"))
        incoming_ts = _parse_ts(r.get("updated_at")) or datetime.utcnow()
        incoming_iso = incoming_ts.isoformat(timespec="seconds") + "Z"

        # newer-wins guard
        exist_dec, exist_ts = existing.get(image_id, (None, None))
        exist_dt = _parse_ts(exist_ts)
        if prefer_newer and exist_dt and incoming_ts <= exist_dt:
            stats["skipped_older"] += 1
            continue

        if dec_norm is None:
            # clear decision (send back to unmarked)
            cur.execute("DELETE FROM decisions WHERE image_id=?", (image_id,))
            stats["cleared"] += 1
            existing[image_id] = (None, None)
            continue

        if dec_norm not in VALID_DECISIONS:
            stats["invalid_decision"] += 1
            continue

        # upsert decision
        cur.execute(
            """
            INSERT INTO decisions(image_id, decision, note, updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(image_id) DO UPDATE SET
              decision=excluded.decision,
              note=COALESCE(excluded.note, decisions.note),
              updated_at=excluded.updated_at
            """,
            (image_id, dec_norm, (r.get("note") or None), incoming_iso),
        )
        stats["upserted"] += 1
        existing[image_id] = (dec_norm, incoming_iso)

    conn.commit()
    return stats