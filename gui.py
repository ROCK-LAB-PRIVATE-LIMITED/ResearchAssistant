import streamlit as st
import os, datetime, base64
from Orchestrator import MasterOrchestrator, render_markdown_to_pdf
from searchSubAgent import sanitize_filename
import json
st.set_page_config(page_title="Research Assistant", page_icon="🔮", layout="wide")

# ==========================================
# CONFIGURATION PERSISTENCE
# ==========================================
CONFIG_FILE = "config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)
    st.sidebar.success("Settings saved!")

# Load existing config or set defaults
saved_prefs = load_config()

# ==========================================
# SIDEBAR: CONFIGURATION
# ==========================================
with st.sidebar:
    st.header("⚙️ Global Settings")
    
    # 1. ORCHESTRATOR SETTINGS
    with st.expander("🤖 Orchestrator (Master)", expanded=True):
        o_key = st.text_input("Master API Key", value=saved_prefs.get("o_key", ""), type="password")
        o_base = st.text_input("Master Base URL", value=saved_prefs.get("o_base", "https://openrouter.ai/api/v1"))
        o_model = st.text_input("Master Model", value=saved_prefs.get("o_model", "stepfun/step-3.5-flash:free"))

    # 2. SUB-AGENT SETTINGS
    st.divider()
    # Checkbox state also saved
    use_diff = st.checkbox("Independent Sub-Agent Settings", value=saved_prefs.get("use_diff", False))
    
    if use_diff:
        with st.expander("🕵️ Sub-Agent Settings", expanded=True):
            s_key = st.text_input("Agent API Key", value=saved_prefs.get("s_key", "sk-or-v1-..."), type="password")
            s_base = st.text_input("Agent Base URL", value=saved_prefs.get("s_base", "https://openrouter.ai/api/v1"))
            s_model = st.text_input("Agent Model", value=saved_prefs.get("s_model", "stepfun/step-3.5-flash:free"))
    else:
        s_key, s_base, s_model = o_key, o_base, o_model

    with st.expander("🛠️ Limits & Style"):
        max_agents = st.slider("Max Parallel Agents", 1, 10, saved_prefs.get("max_agents", 4))
        footer_val = st.text_input("PDF Footer", saved_prefs.get("footer_val", "ROCK LAB PRIVATE LIMITED"))

    # SAVE BUTTON
    if st.button("💾 Save Configuration", use_container_width=True):
        current_settings = {
            "o_key": o_key, "o_base": o_base, "o_model": o_model,
            "use_diff": use_diff,
            "s_key": s_key, "s_base": s_base, "s_model": s_model,
            "max_agents": max_agents, "footer_val": footer_val
        }
        save_config(current_settings)

    # Construct Agent Config
    agent_config = {
        "api_key": s_key,
        "base_url": s_base,
        "model_name": s_model
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