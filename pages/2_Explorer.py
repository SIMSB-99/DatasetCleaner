import os
import math
import streamlit as st
from db import (
    init_db,
    get_datasets,
    get_archetype_tree,
    images_by_arch_cat,
    count_images_by_arch_cat,
    set_decision,
)

st.set_page_config(page_title="Explorer • DatasetCleaner", layout="wide")
init_db()

# ---- apply pending resets BEFORE widgets are created ----
if "_explorer_pending" in st.session_state:
    p = st.session_state.pop("_explorer_pending")
    if p.get("reset_filter"):
        st.session_state["explorer_decision_filter"] = "all"
    if p.get("reset_page"):
        st.session_state["explorer_page"] = 0

# Densify layout
st.markdown("""
<style>
.main .block-container {padding-top: 0.6rem; padding-bottom: 0.6rem;}
section[data-testid="stSidebar"] > div {padding-top: 0.6rem;}
</style>
""", unsafe_allow_html=True)

st.title("explorer")

# ===== Sidebar: dataset + archetype/category tree =====
datasets = get_datasets()
if not datasets:
    st.sidebar.info("No datasets yet. Use the main page to ingest a CSV.")
    st.stop()

ds_idx = st.sidebar.selectbox(
    "Dataset",
    options=list(range(len(datasets))),
    format_func=lambda i: datasets[i]["name"],
)
ds = datasets[ds_idx]
dataset_id = ds["id"]
root_dir = ds["root_dir"]

tree = get_archetype_tree(dataset_id)
if not tree:
    st.sidebar.info("No archetype/category keys found in metadata_json.")
    st.stop()

st.sidebar.markdown("#### Context Archetypes")

# Selection state
st.session_state.setdefault("explorer_arch", None)
st.session_state.setdefault("explorer_cat", None)
st.session_state.setdefault("explorer_page", 0)
st.session_state.setdefault("explorer_decision_filter", "all")

for arche in tree.keys():
    with st.sidebar.expander(arche, expanded=(st.session_state["explorer_arch"] == arche)):
        for c in tree[arche]:
            if st.button(c, key=f"arch_{arche}_cat_{c}"):
                st.session_state["explorer_arch"] = arche
                st.session_state["explorer_cat"] = c
                # request resets for next rerun (before widgets instantiate)
                st.session_state["_explorer_pending"] = {"reset_filter": True, "reset_page": True}
                st.rerun()

# ===== Main: grid of thumbnails with decision + CLEAR =====
arch = st.session_state.get("explorer_arch")
cat  = st.session_state.get("explorer_cat")

if not arch or not cat:
    st.info("Pick an archetype and a location category from the left to begin.")
    st.stop()

# --- NEW: per-category decision filter ---
filter_options = ["all", "keep", "discard", "unsure", "unmarked"]
# read default safely
default_filter = st.session_state.get("explorer_decision_filter", "all")
idx = filter_options.index(default_filter) if default_filter in filter_options else 0

selected_filter = st.radio(
    "Label filter",
    filter_options,
    index=idx,
    horizontal=True,
    key="explorer_decision_filter",   # let Streamlit manage session_state
)
# use `selected_filter` (or st.session_state["explorer_decision_filter"]) below
decision_filter = None if selected_filter == "all" else selected_filter
df_arg = None if decision_filter == "all" else decision_filter

PAGE_SIZE = 48
page = st.session_state.get("explorer_page", 0)

total = count_images_by_arch_cat(dataset_id, arch, cat, decision_filter=df_arg)
num_pages = max(1, math.ceil(total / PAGE_SIZE))
# Clamp page if filter changed and shrank the result
if page > num_pages - 1:
    page = max(0, num_pages - 1)
    st.session_state["explorer_page"] = page

offset = page * PAGE_SIZE

rows = images_by_arch_cat(
    dataset_id, arch, cat,
    decision_filter=df_arg,
    order_by="image_path",
    limit=PAGE_SIZE, offset=offset
)

def badge(dec):
    return "✅ keep" if dec == "keep" else ("🗑️ discard" if dec == "discard" else ("🤔 unsure" if dec == "unsure" else "—"))

st.subheader(f"{arch} ▸ {cat}")
st.caption(f"Showing {offset + 1 if total else 0}–{min(offset + PAGE_SIZE, total)} of {total}  •  filter: {decision_filter}")

cols_per_row = 6
rows_needed = math.ceil(len(rows) / cols_per_row) if rows else 0

for r_i in range(rows_needed):
    cols = st.columns(cols_per_row, gap="small")
    for c_i in range(cols_per_row):
        idx = r_i * cols_per_row + c_i
        if idx >= len(rows):
            break
        r = rows[idx]
        abs_path = os.path.join(root_dir, r["image_path"])
        with cols[c_i]:
            st.caption(os.path.basename(r["image_path"]))
            if os.path.isfile(abs_path):
                st.image(abs_path, use_column_width=True)
            else:
                st.error("missing")
            st.caption(badge(r["decision"]))
            if st.button("Clear", key=f"clr_{r['id']}_{page}", help="Remove decision (send to unmarked)"):
                set_decision(r["id"], None, None)
                # If we were filtering to non-unmarked, removing may reduce total; keep page stable
                st.rerun()

# Pager
pg = st.columns([1, 1, 3])
if pg[0].button("⟵ Prev page", disabled=(page <= 0)):
    st.session_state["explorer_page"] = max(0, page - 1)
    st.rerun()
if pg[1].button("Next page ⟶", disabled=(page >= num_pages - 1)):
    st.session_state["explorer_page"] = min(num_pages - 1, page + 1)
    st.rerun()
with pg[2]:
    st.caption(f"Page {page + 1 if total else 0} / {num_pages}")
