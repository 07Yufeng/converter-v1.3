import streamlit as st
from converter_toolpath_v5 import convert_hp_to_mpf_text, get_conversion_report, normalize_power_head
from toolpath_length_analysis import analyze_toolpath_lengths

SHORT_MOVE_THRESHOLD_MM = 0.7

st.set_page_config(page_title="HP/LST to MPF Converter", layout="wide")
st.title("TRUMPF HP/LST to BEaM MPF Converter v1.1")

st.sidebar.header("Conversion Settings")
power_head_label = st.sidebar.radio(
    "Select laser power head",
    ["10Vx", "24Vx"],
    index=1,
    help="Controls the PUIS_SET formula and BEaM gas settings."
)
power_head = normalize_power_head(power_head_label)
st.sidebar.markdown("""
**Power formula used**

- **10Vx**: `(POWER + 194.55) / 22.487`
- **24Vx**: `(POWER + 165.73) / 21.832`
""")
if "uploader_key" not in st.session_state:
    st.session_state["uploader_key"] = 0
uploaded_file = st.file_uploader("Upload original .HP or .LST file", type=["hp", "lst", "txt"],key=f"file_uploader_{st.session_state['uploader_key']}")

if "hp_text" not in st.session_state:
    st.session_state.hp_text = ""
if "source_name" not in st.session_state:
    st.session_state.source_name = "uploaded.HP"
if "last_uploaded_name" not in st.session_state:
    st.session_state.last_uploaded_name = None
if "mpf_text" not in st.session_state:
    st.session_state.mpf_text = ""
if "report" not in st.session_state:
    st.session_state.report = None
if "length_analysis" not in st.session_state:
    st.session_state.length_analysis = None
if "last_power_head" not in st.session_state:
    st.session_state.last_power_head = power_head

if uploaded_file is not None:
    uploaded_text = uploaded_file.read().decode("utf-8", errors="replace")
    if uploaded_file.name != st.session_state.last_uploaded_name:
        st.session_state.hp_text = uploaded_text
        st.session_state.source_name = uploaded_file.name
        st.session_state.last_uploaded_name = uploaded_file.name
        st.session_state.mpf_text = ""
        st.session_state.report = None
        st.session_state.length_analysis = None

if st.button("Clear File"):
    st.session_state["uploader_key"] += 1
    st.session_state.hp_text=""
    st.rerun()
if power_head != st.session_state.last_power_head:
    st.session_state.last_power_head = power_head
    st.session_state.mpf_text = ""
    st.session_state.report = None
    st.session_state.length_analysis = None

if st.session_state.hp_text:
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Editable HP/LST Input")
        st.caption("Edit the uploaded source code before conversion. The converter parses the edited text, including toolpath commands.")
        edited_text = st.text_area("Edit source code before conversion", value=st.session_state.hp_text, height=650, key="hp_editor")
        st.session_state.hp_text = edited_text
    with col2:
        st.subheader("Converted MPF Output")
        st.caption(f"Selected power head: **{power_head_label}**")
        if st.button("Convert edited HP/LST to MPF", type="primary"):
            try:
                st.session_state.mpf_text = convert_hp_to_mpf_text(
                    st.session_state.hp_text,
                    source_name=st.session_state.source_name,
                    power_head=power_head
                )
                st.session_state.report = get_conversion_report(
                    st.session_state.hp_text,
                    source_name=st.session_state.source_name,
                    power_head=power_head
                )
                st.session_state.length_analysis = analyze_toolpath_lengths(
                    st.session_state.mpf_text,
                    threshold=SHORT_MOVE_THRESHOLD_MM
                )
                st.success("Conversion completed.")
            except Exception as e:
                st.session_state.mpf_text = ""
                st.session_state.report = None
                st.session_state.length_analysis = None
                st.error(f"Conversion failed: {e}")
        if st.session_state.report:
            with st.expander("Conversion report", expanded=False):
                st.json(st.session_state.report)
        if st.session_state.length_analysis:
            analysis = st.session_state.length_analysis
            short_moves = analysis["short_moves"]
            with st.expander(
                f"Toolpath length check (threshold {analysis['threshold']} mm) "
                f"— {len(short_moves)} short WELDFEED move(s) found",
                expanded=bool(short_moves)
            ):
                st.caption(
                    f"Checked {analysis['moves_checked']} G01/CIP move(s) under F=WELDFEED. "
                    f"Total WELDFEED path length: {analysis['total_weld_length_mm']} mm. "
                    "Moves under F=RAPIDFEED are not measured."
                )
                if short_moves:
                    st.warning(
                        f"{len(short_moves)} move(s) under F=WELDFEED are shorter than "
                        f"{analysis['threshold']} mm:"
                    )
                    st.dataframe(
                        [
                            {
                                "Line": f"N{m['n']}" if m["n"] is not None else "—",
                                "Type": m["block_type"],
                                "Length (mm)": m["length_mm"],
                                "Code": m["code"],
                            }
                            for m in short_moves
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.success("No WELDFEED moves shorter than the threshold were found.")
        if st.session_state.mpf_text:
            st.text_area("Generated MPF", value=st.session_state.mpf_text, height=650)
            output_name = st.session_state.source_name.rsplit(".", 1)[0] + f"_{power_head}_converted.MPF"
            st.download_button("Download MPF file", data=st.session_state.mpf_text, file_name=output_name, mime="text/plain")
else:
    st.info("Upload a .HP or .LST file to begin.")
