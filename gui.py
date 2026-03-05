import streamlit as st
import os, datetime, base64
from Orchestrator import MasterOrchestrator, render_markdown_to_pdf
from searchSubAgent import sanitize_filename
import json
st.set_page_config(page_title="Research Assistant", page_icon="🔮", layout="wide")

# ==========================================
# 1. DEFINE HARDCODED DEFAULTS
# ==========================================
DEFAULT_KEY = "" # Leave empty for privacy, or put a default here
DEFAULT_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "stepfun/step-3.5-flash:free"

# ==========================================
# 2. INITIALIZE SESSION STATE (Runs once per user)
# ==========================================
# This ensures every user starts with the hardcoded defaults in their own private RAM
if "o_key" not in st.session_state:
    st.session_state.o_key = DEFAULT_KEY
if "o_base" not in st.session_state:
    st.session_state.o_base = DEFAULT_BASE
if "o_model" not in st.session_state:
    st.session_state.o_model = DEFAULT_MODEL

if "use_diff" not in st.session_state:
    st.session_state.use_diff = False

# This logic handles the "Sync": If independent settings are OFF, 
# it pushes Master values into the Sub-Agent values every time the app reruns.
if not st.session_state.use_diff:
    st.session_state.s_key = st.session_state.o_key
    st.session_state.s_base = st.session_state.o_base
    st.session_state.s_model = st.session_state.o_model
else:
    # If they just turned ON "Independent Settings", ensure they aren't blank
    if "s_key" not in st.session_state or st.session_state.s_key == "":
        st.session_state.s_key = st.session_state.o_key
        st.session_state.s_base = st.session_state.o_base
        st.session_state.s_model = st.session_state.o_model

# ==========================================
# 3. SIDEBAR UI
# ==========================================
with st.sidebar:
    st.header("⚙️ Global Settings")
    
    with st.expander("🤖 Orchestrator (Master)", expanded=True):
        # We don't need 'value=' because we set 'st.session_state.o_key' above
        st.text_input("Master API Key", key="o_key", type="password")
        st.text_input("Master Base URL", key="o_base")
        st.text_input("Master Model", key="o_model")

    st.divider()
    
    st.checkbox("Independent Sub-Agent Settings", key="use_diff")
    
    # This section is what you were seeing as "Blank"
    with st.expander("🕵️ Sub-Agent Settings", expanded=True):
        if st.session_state.use_diff:
            # When independent is ON, user can type here
            st.text_input("Agent API Key", key="s_key", type="password")
            st.text_input("Agent Base URL", key="s_base")
            st.text_input("Agent Model", key="s_model")
        else:
            # When independent is OFF, we show the values but they match Master
            st.info("Currently using Master Settings")
            st.caption(f"Model: {st.session_state.s_model}")
            st.caption(f"Base: {st.session_state.s_base}")

    with st.expander("🛠️ Limits & Style"):
        st.slider("Max Parallel Agents", 1, 10, key="max_agents", value=4)
        st.text_input("PDF Footer", key="footer_val", value="ROCK LAB PRIVATE LIMITED")

    # FINAL CONFIG: This is what gets passed to the research agents
    agent_config = {
        "api_key": st.session_state.s_key,
        "base_url": st.session_state.s_base,
        "model_name": st.session_state.s_model
    }

# ==========================================
# APP LOGIC
# ==========================================
if "loop_count" not in st.session_state:
    st.session_state.step = "input"
    st.session_state.loop_count = 0
    st.session_state.history = []
    st.session_state.context_accumulator = ""

# STEP 1: INITIAL TOPIC
if st.session_state.step == "input":
    st.title("Rock Lab Research Assistant")
    query = st.text_area("What is the research topic?", height=150)
    
    if st.button("Initialize Planning", use_container_width=True):
        st.session_state.master = MasterOrchestrator()
        st.session_state.master.update_settings(o_key, o_base, o_model)
        st.session_state.query = query
        st.session_state.step = "sequential_loop"
        st.rerun()

# STEP 2: SEQUENTIAL LOOP (3 Iterations)
elif st.session_state.step == "sequential_loop":
    it = st.session_state.loop_count
    st.title(f"🔍 Refining Scope (Phase {it + 1}/3)")
    
    # Generate the question if it doesn't exist for this iteration
    if f"q_text_{it}" not in st.session_state:
        with st.spinner("Master is analyzing and searching..."):
            # 1. Search based on the last answer (or initial query)
            search_query = st.session_state.history[-1] if st.session_state.history else st.session_state.query
            new_context = st.session_state.master.quick_search(search_query)
            st.session_state.context_accumulator += f"\n--- Phase {it} Research ---\n{new_context}"
            
            # 2. Generate Question
            history_text = "\n".join(st.session_state.history)
            q = st.session_state.master.get_single_clarification(
                st.session_state.query, 
                history_text, 
                new_context
            )
            st.session_state[f"q_text_{it}"] = q
    
    # --- UI DISPLAY ---
    st.info(st.session_state[f"q_text_{it}"])
    user_ans = st.text_input("Your response:", key=f"ans_{it}")
    
    # Layout for buttons
    col1, col2 = st.columns([1, 1])
    
    with col1:
        # MAIN ACTION BUTTON
        btn_label = "Continue to Next Question" if it < 2 else "Finalize & Start Research"
        if st.button(btn_label, use_container_width=True, type="primary"):
            if user_ans:
                st.session_state.history.append(f"Q: {st.session_state[f'q_text_{it}']} | A: {user_ans}")
                if st.session_state.loop_count < 2:
                    st.session_state.loop_count += 1
                    st.rerun()
                else:
                    st.session_state.clarifications = "\n".join(st.session_state.history)
                    st.session_state.step = "research"
                    st.rerun()
            else:
                st.warning("Please provide an answer or use the 'Skip' button.")

    with col2:
        # SKIP BUTTON (Visible from 2nd question onwards)
        if it > 0: 
            if st.button("⏩ Skip remaining & Run Now", use_container_width=True):
                # If they typed something in the current box before skipping, save it
                if user_ans:
                    st.session_state.history.append(f"Q: {st.session_state[f'q_text_{it}']} | A: {user_ans}")
                
                st.session_state.clarifications = "\n".join(st.session_state.history)
                st.session_state.step = "research"
                st.rerun()
                
# STEP 3: EXECUTION
elif st.session_state.step == "research":
    st.title("🛰️ Parallel Research")
    
    # --- NEW: Generate Project Name and Folder ---
    if "folder" not in st.session_state:
        with st.spinner("Naming project..."):
            raw_title = st.session_state.master.generate_project_name(
                st.session_state.query, 
                st.session_state.get('clarifications', '')
            )
            project_title = sanitize_filename(raw_title)
            #ts = datetime.datetime.now().strftime("%H%M") # Still keep short TS to avoid collisions
            st.session_state.project_title = project_title
            st.session_state.folder = f"{project_title}"
            os.makedirs(st.session_state.folder, exist_ok=True)

    if "final_report" not in st.session_state:
        # (update settings)
        
        full_context = st.session_state.get('clarifications','') + "\n" + st.session_state.context_accumulator
        tasks = st.session_state.master.plan_research(st.session_state.query, full_context)
        
        if not tasks:
            st.error("Could not generate a research plan. Check your Master API key and Topic.")
            st.stop()

        tasks = tasks[:max_agents]
        # Create fresh slots for this run
        slots = [st.container(border=True) for _ in tasks] 

        
        sub_reports = st.session_state.master.execute_subagents(
            tasks, 
            output_dir=st.session_state.folder, 
            placeholders=slots,
            config=agent_config
        )
        
        if not sub_reports:
            st.error("All research modules failed to return data. Check the terminal for detailed tracebacks.")
            st.stop()

        # 4. Final Synth
        with st.spinner("Master is synthesizing final report..."):
            st.session_state.final_report = st.session_state.master.finalize_report(
                st.session_state.query, 
                sub_reports,
                st.session_state.project_title
            )
            report_name = st.session_state.project_title
            m_md = os.path.join(st.session_state.folder, f"{report_name}.md")
            m_pdf = os.path.join(st.session_state.folder, f"{report_name}.pdf")
            
            with open(m_md, "w", encoding="utf-8") as f:
                f.write(st.session_state.final_report)
            
            # SAFE CALL
            try:
                render_markdown_to_pdf(m_md, m_pdf)
            except Exception as e:
                st.warning(f"Master PDF failed to generate due to special characters, but Markdown is available.")
            
            st.rerun()

    # STEP 4: RESULTS
    st.success("Analysis Complete!")

    # --- 1. PREVIEWER SECTION (TOP) ---
    st.subheader("🔍 Live Document Preview")
    
    # Get all .md files
    md_files = sorted([f for f in os.listdir(st.session_state.folder) if f.endswith(".md")])
    
    if md_files:
        # Default to the MASTER_REPORT if it exists
        default_idx = md_files.index("MASTER_REPORT.md") if "MASTER_REPORT.md" in md_files else 0
        
        selected_md = st.selectbox(
            "Select a research module to read:", 
            md_files, 
            index=default_idx
        )
        
        if selected_md:
            with open(os.path.join(st.session_state.folder, selected_md), "r", encoding="utf-8") as f:
                md_content = f.read()
            
            # Display Markdown in a bordered container for a clean "paper" look
            with st.container(border=True):
                st.markdown(md_content, unsafe_allow_html=True)

    st.divider()

    # --- 2. DOWNLOAD SECTION (BOTTOM, ONE PER LINE) ---
    st.subheader("🛠️ Document Management")
    
    md_files = sorted([f for f in os.listdir(st.session_state.folder) if f.endswith(".md")])
    
    for md_name in md_files:
        pdf_name = md_name.replace(".md", ".pdf")
        md_path = os.path.join(st.session_state.folder, md_name)
        pdf_path = os.path.join(st.session_state.folder, pdf_name)
        
        # Create a clean row for each document
        with st.container(border=True):
            cols = st.columns([4, 2, 2])
            
            with cols[0]:
                st.markdown(f"**{md_name}**")
            
            with cols[1]:
                # Overwrites the PDF on disk
                if st.button(f"🔄 Regenerate", key=f"reg_{md_name}", use_container_width=True):
                    render_markdown_to_pdf(md_path, pdf_path)
                    st.toast(f"Updated {pdf_name}", icon="✅")
            
            with cols[2]:
                # Serves the PDF currently on disk
                if os.path.exists(pdf_path):
                    with open(pdf_path, "rb") as f:
                        st.download_button(
                            label="📥 Download",
                            data=f.read(),
                            file_name=pdf_name,
                            mime="application/pdf",
                            key=f"dl_{md_name}",
                            use_container_width=True
                        )
                else:
                    st.caption("No PDF found.")

    # --- 3. RESET SESSION ---
    st.divider()
    if st.button("Start New Research Project", width="stretch"):
        for key in list(st.session_state.keys()): 
            del st.session_state[key]
        st.rerun()