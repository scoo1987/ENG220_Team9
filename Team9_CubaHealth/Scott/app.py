from pathlib import Path
import streamlit as st

st.set_page_config(
    page_title="Cuba Community Health",
    layout="wide",
)

folder = Path(__file__).resolve().parent

st.title("Cuba Community Health")

st.subheader("Health Conditions and Risks")
st.image(str(folder / "slide1_health_comparison.png"))

st.subheader("Preventive-Care Visits")
st.image(str(folder / "slide2_preventive_care.png"))

st.caption(
    "Source: CDC PLACES, 2025 release. "
    "Age-adjusted estimates. "
    "Black lines show 95% confidence intervals."
)
