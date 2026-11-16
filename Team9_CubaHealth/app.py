from pathlib import Path
import streamlit as st

st.set_page_config(
    page_title="Team 9: Cuba Community Health",
    layout="wide",
)

st.title("Team 9: Cuba Community Health")

# Folder containing this top-level app.py file.
REPO = Path(__file__).resolve().parent

# Change this if your parent project folder has a different name.
PROJECT = REPO 

if not PROJECT.is_dir():
    st.error(
        f"Project folder not found: {PROJECT.name}. "
        "Check the PROJECT line in app.py."
    )
    st.stop()

# Find member folders containing PNG images, including nested folders.
members = {}

for folder in sorted(PROJECT.iterdir()):
    if folder.is_dir():
        images = sorted(
            path for path in folder.rglob("*")
            if path.is_file() and path.suffix.lower() == ".png"
        )

        if images:
            members[folder.name] = images

if not members:
    st.info(
        "No PNG graphs found. Upload graph images "
        "inside the team-member folders."
    )
    st.stop()

member = st.sidebar.selectbox(
    "Choose a team member",
    list(members),
)

images = members[member]

image = st.sidebar.selectbox(
    "Choose a graph",
    images,
    format_func=lambda path: path.relative_to(
        PROJECT / member
    ).as_posix(),
)

st.subheader(f"{member}'s graph")
st.image(str(image))

st.download_button(
    "Download this graph",
    data=image.read_bytes(),
    file_name=image.name,
    mime="image/png",
)

st.caption(
    "See each graph and the team's source documentation "
    "for data sources, reporting years, and definitions."
)
