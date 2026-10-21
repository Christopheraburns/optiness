"""Placeholder page: shows the resolved contract. Replaced by H1."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from services.common.env import redacted, settings  # noqa: E402

st.set_page_config(page_title="Investigation Console", layout="wide")
st.title("Investigation Console")
st.caption("Placeholder (BURNDOWN H1). The environment contract as this container resolved it:")
st.json(redacted(settings()))
