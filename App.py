# app.py
import os
import json
from pathlib import Path
import streamlit as st

# Local modules
from db import (
    init_db,
    get_datasets,
    query_images,
    set_decision,
    get_marked,
)
from ingest import ingest as ingest_cli

# -------- App bootstrap --------
st.set_page_config(page_title="DatasetCleaner", layout="wide")
init_db()

# Tighten global padding & remove default header/footer space
st.markdown("""
<style>
/* shrink the main container padding */
.main .block-container {padding-top: 0.5rem; padding-bottom: 0.5rem;}
/* tighten the sidebar top padding */
section[data-testid="stSidebar"] > div {padding-top: 0.5rem;}
/* hide the default Streamlit header and footer to reclaim vertical space */
header {visibility: hidden;}
footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# Handle pending "jump to image" BEFORE any widgets are created
if "_jump" in st.session_state:
    j = st.session_state.pop("_jump")
    # Ensure defaults exist
    st.session_state.setdefault("decision_filter", "all")
    st.session_state.setdefault("order_by", "image_path")
    st.session_state.setdefault("search", "")
    st.session_state.setdefault("offset", 0)
    # Apply jump intent
    st.session_state["search"] = j.get("search", st.session_state["search"])
    st.session_state["decision_filter"] = j.get("decision_filter", st.session_state["decision_filter"])
    st.session_state["order_by"] = j.get("order_by", st.session_state["order_by"])
    st.session_state["offset"] = j.get("offset", 0)

# -------- Sidebar (left) --------
st.sidebar.title("DatasetCleaner")

# Dataset picker
datasets = get_datasets()
if not datasets:
    st.sidebar.info("No datasets yet. Use **Ingest new CSV** below.")
    ds = None
    dataset_id = None
    root_dir = ""
else:
    st.session_state.setdefault("ds_idx", 0)
    ds_idx = st.sidebar.selectbox(
        "Dataset",
        options=list(range(len(datasets))),
        format_func=lambda i: f"{datasets[i]['name']}",
        index=st.session_state["ds_idx"],
        key="ds_idx",
    )
    ds = datasets[ds_idx]
    dataset_id = ds["id"]
    root_dir = ds["root_dir"]

# Filters
st.sidebar.subheader("Filters")
st.session_state.setdefault("decision_filter", "unmarked")
decision_options = ["all", "keep", "discard", "unsure", "unmarked"]
st.sidebar.radio(
    label="Decision filter",
    options=decision_options,
    index=decision_options.index(st.session_state["decision_filter"]),
    key="decision_filter",
)

# Search
st.sidebar.subheader("Search")
st.session_state.setdefault("search", "")
st.sidebar.text_input("name or relative path", key="search")

# Dataset info + actions
st.sidebar.subheader("Dataset")
if root_dir:
    st.code(root_dir, language="text")

# Export decisions
with st.sidebar.expander("Export decisions"):
    import io
    import pandas as pd
    from db import get_export_rows

    include_unmarked = st.checkbox("Include unmarked", value=False, key="exp_inc_unmarked")
    if st.button("Prepare CSV", key="exp_prep"):
        if dataset_id is None:
            st.info("Select a dataset first.")
        else:
            rows = get_export_rows(dataset_id, include_unmarked=include_unmarked)
            if not rows:
                st.info("Nothing to export.")
            else:
                data = []
                for r in rows:
                    abs_path = os.path.join(r["root_dir"], r["image_path"])
                    data.append(
                        {
                            "dataset_name": r["dataset_name"],
                            "root_dir": r["root_dir"],
                            "image_name": r["image_name"],
                            "image_path": r["image_path"],  # relative
                            "abs_path": abs_path,           # absolute
                            "decision": r["decision"] or "",
                            "note": r["note"] or "",
                            "updated_at": r["updated_at"] or "",
                            "metadata_json": r["metadata_json"],
                        }
                    )
                df_export = pd.DataFrame(data)
                buf = io.StringIO()
                df_export.to_csv(buf, index=False)
                st.download_button(
                    "⬇️ Download CSV",
                    data=buf.getvalue().encode("utf-8"),
                    file_name=f"{ds['name']}_decisions{'_all' if include_unmarked else ''}.csv",
                    mime="text/csv",
                    key="exp_dl",
                )

# Ingest new CSV
with st.sidebar.expander("Ingest new CSV"):
    ds_name = st.text_input("Dataset name", key="ing_ds_name")
    root_dir_in = st.text_input("Root directory (images root)", key="ing_root")
    csv_path = st.text_input("CSV path", key="ing_csv")
    if st.button("Ingest", key="ing_btn"):
        if not ds_name or not root_dir_in or not csv_path:
            st.warning("Fill all fields.")
        else:
            try:
                ingest_cli(ds_name, root_dir_in, csv_path)
                st.success("Ingested. Click refresh.")
            except SystemExit:
                st.error("Ingestion failed. Check console/logs.")


with st.sidebar.expander("Import decisions (CSV)"):
    import pandas as pd
    uploaded = st.file_uploader("Choose CSV exported from DatasetCleaner", type=["csv"], key="imp_csv")
    prefer_newer = st.checkbox("Only overwrite if CSV is newer (recommended)", value=True, key="imp_newer")
    if uploaded is not None and dataset_id is not None:
        df = pd.read_csv(uploaded)
        if st.button("Import decisions", key="imp_go"):
            from db import bulk_import_decisions_from_rows
            stats = bulk_import_decisions_from_rows(dataset_id, df.to_dict("records"), root_dir, prefer_newer=prefer_newer)
            st.success(f"Upserted: {stats['upserted']} | Cleared: {stats['cleared']} | "
                       f"Skipped missing: {stats['skipped_missing']} | Skipped older: {stats['skipped_older']} | "
                       f"Invalid: {stats['invalid_decision']}")
            st.rerun()


if st.sidebar.button("🔄 Refresh", key="refresh_btn"):
    # st.cache_data.clear()
    # st.cache_resource.clear()
    pass  # a button press already causes a rerun


# -------- Main body: center viewer + right review list --------
if not datasets:
    st.stop()

# Paging & ordering
st.session_state.setdefault("offset", 0)
st.session_state.setdefault("order_by", "image_path")
order_by = st.session_state["order_by"]

# Query one record for the viewer
rows, total = query_images(
    dataset_id=dataset_id,
    decision_filter=st.session_state["decision_filter"],
    search_text=st.session_state["search"].strip(),
    order_by=order_by,
    limit=1,
    offset=st.session_state["offset"],
)

if total == 0:
    st.warning("No results. Try different filter/search.")
    st.stop()

# Clamp offset if filters changed
if st.session_state["offset"] >= total:
    st.session_state["offset"] = max(0, total - 1)
    rows, total = query_images(
        dataset_id,
        st.session_state["decision_filter"],
        st.session_state["search"].strip(),
        order_by,
        1,
        st.session_state["offset"],
    )

row = rows[0]
abs_path = os.path.join(root_dir, row["image_path"])

# Layout columns: big center (image + info) and slim right (review list)
center, right = st.columns([4, 1.6], gap="large")

# -------- Center viewer --------
with center:
    # st.code(abs_path, language="text")

    # Image
    if os.path.isfile(abs_path):
        st.image(abs_path, use_column_width=True)
    else:
        st.error(f"File not found:\n{abs_path}")

    # Name | Metadata | Decision
    c_name, c_meta, c_dec = st.columns([1.2, 2.5, 1])
    with c_name:
        st.markdown("**Name**")
        st.code(row["image_name"], language="text")
    with c_meta:
        st.markdown("**Metadata**")
        try:
            meta = json.loads(row["metadata_json"])
        except Exception:
            meta = {"_error": "Invalid JSON"}
        st.json(meta, expanded=True)
    with c_dec:
        st.markdown("**Decision**")
        st.code(row["decision"] or "—", language="text")

    # Decision buttons row
    b1, b2, b3, b4 = st.columns([1, 1, 1, 1])
    if b1.button("✅ Keep", use_container_width=True, key=f"keep_{row['id']}"):
        set_decision(row["id"], "keep", (row["note"] or None))
        st.rerun()
    if b2.button("🗑️ Discard", use_container_width=True, key=f"discard_{row['id']}"):
        set_decision(row["id"], "discard", (row["note"] or None))
        st.rerun()
    if b3.button("🤔 Unsure", use_container_width=True, key=f"unsure_{row['id']}"):
        set_decision(row["id"], "unsure", (row["note"] or None))
        st.rerun()
    if b4.button("♻️ Clear", use_container_width=True, key=f"clear_{row['id']}"):
        set_decision(row["id"], None, None)
        st.rerun()

    # Pager + absolute path
    pg1, pg2, pg3 = st.columns([1, 1, 3])
    if pg1.button("⟵ Prev", key="nav_prev"):
        st.session_state["offset"] = max(0, st.session_state["offset"] - 1)
        st.rerun()
    if pg2.button("Next ⟶", key="nav_next"):
        st.session_state["offset"] = min(total - 1, st.session_state["offset"] + 1)
        st.rerun()
    with pg3:
        st.caption(f"Result {st.session_state['offset'] + 1} / {total}")

# -------- Right: Review Decisions --------
with right:
    st.markdown("### Review Decisions")

    choice = st.radio(
        "Review set",
        ["All marked", "Keep", "Discard", "Unsure"],
        index=0,
        horizontal=True,
        key="review_choice",
        label_visibility="collapsed",
    )
    dec = None if choice == "All marked" else choice.lower()
    marked = get_marked(dataset_id, dec)

    if not marked:
        st.info("Nothing here yet.")
    else:
        # Build a neat table for display + a map for selection
        table_rows = []
        select_options = []   # (label, rel_path)
        for r in marked[:800]:  # cap for speed
            abs_p = os.path.join(root_dir, r["image_path"])
            # Emoji badge for readability
            decision = r["decision"]
            badge = "✅ keep" if decision == "keep" else "🗑️ discard" if decision == "discard" else "🤔 unsure"
            table_rows.append(
                {
                    "File (absolute path)": abs_p,
                    "Decision": badge,
                }
            )
            # Dropdown label – short but unique: emoji + tail of path
            tail = os.path.basename(r["image_path"])
            label = f"{badge} · …{abs_p[-80:] if len(abs_p)>80 else abs_p} ({tail})"
            select_options.append((label, r["image_path"]))

        # Show as a compact table
        import pandas as pd
        df_view = pd.DataFrame(table_rows)
        st.dataframe(
            df_view,
            use_container_width=True,
            height=420,
            hide_index=True,
        )

        # Row picker + "View"
        labels = [lbl for (lbl, _rel) in select_options]
        picked = st.selectbox(
            "Pick an item to view", labels, index=0,
            label_visibility="collapsed", key="review_pick"
        )
        if st.button("View selected", key="review_view_btn"):
            # Find rel_path for the picked label
            for lbl, rel in select_options:
                if lbl == picked:
                    st.session_state._jump = {
                        "search": rel,               # search by relative path (unique)
                        "decision_filter": "all",    # ensure it is visible
                        "order_by": "image_path",
                        "offset": 0,
                    }
                    st.rerun()
                    break

