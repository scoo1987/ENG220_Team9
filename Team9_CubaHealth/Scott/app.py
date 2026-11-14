from pathlib import Path
import streamlit as st

st.set_page_config(
    page_title="Cuba Community Health",
    layout="wide",
)

# Locate images beside this app.py file.
folder = Path(__file__).resolve().parent

st.title("Cuba Community Health")
st.write("Comparing Cuba, Bernalillo, and Rio Rancho.")

graph = st.radio(
    "Choose a graph:",
    ["Health Conditions and Risks", "Preventive-Care Visits"],
    horizontal=True,
)

if graph == "Health Conditions and Risks":
    filename = "slide1_health_comparison.png"
else:
    filename = "slide2_preventive_care.png"

image_path = folder / filename

if image_path.is_file():
    st.image(str(image_path))
else:
    st.error(
        f"Missing image: {filename}. "
        "Upload it to the same GitHub folder as app.py."
    )

st.caption(
    "Source: CDC PLACES, 2025 release. "
    "Age-adjusted estimates. "
    "Black lines show 95% confidence intervals."
)
