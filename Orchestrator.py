import os
import concurrent.futures
import json
from typing import List, Dict
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
import datetime
# Import from your existing file
from searchSubAgent import run_subagent, safe_print, sanitize_filename, render_markdown_to_pdf
from streamlit.runtime.scriptrunner import get_script_run_ctx
import streamlit as st
import time
import re
import traceback 
import datetime
from ddgs import DDGS
from searchSubAgent import VisionImageAgent 
# ==========================================
# 1. ORCHESTRATOR SETTINGS
# ==========================================
# Using the same proven settings from your subagent
OPENROUTER_API_KEY = ""
MODEL_NAME = "stepfun/step-3.5-flash:free"

orchestrator_llm = ChatOpenAI(
    model=MODEL_NAME, 
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
    temperature=0,
    #default_headers={
    #    "HTTP-Referer": "https://rocklab.com", # Your site or GitHub repo
    #    "X-Title": "Rock Lab Research Assistant"
    #}
)

def get_gmt_string():
    now_gmt = datetime.datetime.now(datetime.timezone.utc)
    return f"Todays date is {now_gmt.strftime('%Y-%m-%d')} and the current time is {now_gmt.strftime('%H:%M:%S')} GMT."

# ==========================================
# 2. SCHEMAS (Tools)
# ==========================================

class ResearchPlan(BaseModel):
    """Submit a research plan."""
    tasks: List[Dict[str, str]] = Field(description="List of dicts with 'search_prompt' and 'task_name'")

class ClarificationQuestions(BaseModel):
    """Submit questions to the user."""
    questions: List[str] = Field(description="List of strings")

class ProjectName(BaseModel):
    """Generate a concise, filename-safe title for the project."""
    title: str = Field(description="3-5 words, technical, no special characters. Cannot be generic like 'Research Project'")
    
# ==========================================
# 3. THE MASTER AGENT
# ==========================================

class MasterOrchestrator:
    def __init__(self):
        self.llm = orchestrator_llm 
    
    def generate_project_name(self, query: str, clarifications: str):
        """Generates a professional project title based on the topic."""
        model_with_tools = self.llm.bind_tools([ProjectName], tool_choice="ProjectName")
        prompt = f"Initial Query: {query}\nClarifications: {clarifications}\nGenerate a short, professional project title to be used as a filename."
        res = model_with_tools.invoke([SystemMessage(content=prompt)])
        if res.tool_calls:
            return res.tool_calls[0]["args"].get("title", "Research_Project")
        return "Research_Project"
        
    def get_single_clarification(self, initial_query: str, history: str, search_context: str):
        """Generates ONE targeted question based on what we've found so far."""
        time_context = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S GMT")
        prompt = f"""
        Current Time: {time_context}
        Original Topic: {initial_query}
        History of Conversation: {history}
        Preliminary Research Found: {search_context}

        Based on the research context, ask ONE specific clarification question to narrow down the next search.
        Keep it brief and technical.
        """
        res = self.llm.invoke([SystemMessage(content=prompt)])
        return res.content

    def quick_search(self, query: str):
        """A fast, non-agentic search to provide context for the next question."""
        safe_print(f"[MASTER] Performing quick search for context: {query}")
        try:
            results = DDGS().text(query=query, max_results=5)
            context = "\n".join([f"- {r['title']}: {r['body']}" for r in results])
            return context if context else "No specific web results found."
        except Exception as e:
            return f"Search error: {e}"

    def get_user_clarifications(self, initial_query: str):
        """Returns a list of questions instead of prompting in CLI."""
        safe_print(f"\n[ORCHESTRATOR] Analyzing initial query: {initial_query}")
        model_with_tools = self.llm.bind_tools([ClarificationQuestions], tool_choice="ClarificationQuestions")
        time_context = get_gmt_string()
        prompt = f"{time_context}\nUser wants research on: {initial_query}. Generate 1-3 clarification questions to narrow the scope."
        res = model_with_tools.invoke([SystemMessage(content=prompt)])
        
        if res.tool_calls:
            return res.tool_calls[0]["args"].get("questions", [])
        return []

    def plan_research(self, initial_query: str, clarifications: str):
        """Uses a retry loop to ensure a research plan is generated."""
        safe_print("\n[ORCHESTRATOR] Planning parallel research modules...")
        
        model_with_tools = self.llm.bind_tools([ResearchPlan], tool_choice="ResearchPlan")
        
        time_context = get_gmt_string()
        plan_prompt = f"""
        {time_context}
        Initial Request: {initial_query}
        User Clarifications: {clarifications}
        Break this into 3-10 technical research modules for sub-agents. 
        You MUST provide at least 3 distinct tasks.
        """
        
        # RETRY LOOP (3 Attempts)
        for attempt in range(3):
            try:
                res = model_with_tools.invoke([SystemMessage(content=plan_prompt)])
                tasks = []
                if res.tool_calls:
                    tasks = res.tool_calls[0]["args"].get("tasks", [])
                
                if tasks and len(tasks) > 0:
                    return tasks
                
                safe_print(f" [RETRY] Plan attempt {attempt+1} returned no tasks. Retrying...")
            except Exception as e:
                safe_print(f" [RETRY] Error during planning attempt {attempt+1}: {e}")
        
        return [] # Return empty if all 3 attempts fail
    
    
    def execute_subagents(self, tasks: List[Dict], output_dir=".", placeholders=None, config=None):
        if not tasks:
            return []
    
        # UNIQUE LOG FILE PER PROJECT (Point 2)
        # We place the log inside the specific project folder to avoid collisions between users/sessions
        status_log_path = os.path.join(output_dir, "research_status.log")
        
        with open(status_log_path, "w", encoding="utf-8") as f:
            f.write("") 
    
        safe_print(f"\n[ORCHESTRATOR] Dispatching {len(tasks)} sub-agents...")
        ctx = get_script_run_ctx()
        results = []
        
        ui_elements = {}
        for i, t in enumerate(tasks):
            name = t['task_name']
            if placeholders and i < len(placeholders):
                with placeholders[i]:
                    st.markdown(f"### ✨ {name}")
                    status_obj = st.status(f"Initializing {name}...", state="running")
                    progress_bar = st.progress(0)
                    ui_elements[name] = {"status": status_obj, "progress": progress_bar}
    
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            future_to_task = {
                executor.submit(run_subagent, t['search_prompt'], t['task_name'], output_dir, None, ctx, config): t['task_name'] 
                for t in tasks
            }
    
            # LIVE MONITORING LOOP
            while True:
                all_done = all(f.done() for f in future_to_task.keys())
                
                try:
                    # Read from the project-specific log file
                    with open(status_log_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    
                    latest_updates = {}
                    for line in lines:
                        if " ::: " in line:
                            agent, msg = line.split(" ::: ", 1)
                            latest_updates[agent.strip()] = msg.strip()
    
                    for name, ui in ui_elements.items():
                        if name in latest_updates:
                            msg = latest_updates[name]
                            ui["status"].update(label=f"{name}: {msg}")
                            match = re.search(r"\[(\d+)/(\d+)\]", msg)
                            if match:
                                curr, total = map(int, match.groups())
                                ui["progress"].progress(min(curr / total, 1.0))
                except:
                    pass 
    
                if all_done:
                    for name, ui in ui_elements.items():
                        ui["status"].update(label=f"✅ {name}: Completed", state="complete")
                        ui["progress"].progress(1.0)
                    break
                time.sleep(1)
    
            for future in future_to_task:
                task_name = future_to_task[future]
                try:
                    data = future.result()
                    if isinstance(data, str) and not data.startswith("Error in"):
                        results.append({"task": task_name, "content": data})
                except Exception as exc:
                    if task_name in ui_elements:
                        ui_elements[task_name]["status"].update(label=f"❌ {task_name}: Failed", state="error")
                    safe_print(f" [EXCEPTION] {task_name}: {exc}")
        
        # Cleanup the project-specific log
        if os.path.exists(status_log_path):
            os.remove(status_log_path)
            
        return results

    def finalize_report(self, original_query: str, all_results: List[Dict], project_title: str, vision_config=None):
        safe_print("\n[ORCHESTRATOR] Synthesizing final master report...")
        
        combined_context = ""
        for r in all_results:
            combined_context += f"\n\n--- MODULE: {r['task']} ---\n{r['content']}\n"
    
        # --- SEQUENTIAL IMAGE RETRIEVAL WITH HISTORY ---
        image_assets = []
        search_history = [] # Tracks [(Query, Caption), ...]
        
        if vision_config and vision_config.get("enabled"):
            try:
                vision_agent = VisionImageAgent(
                    api_key=vision_config["api_key"],
                    base_url=vision_config["base_url"],
                    model_name=vision_config["model_name"]
                )
                
                for i in range(10): # Aim for 10 images
                    # 1. GENERATE QUERY (Master Turn - uses full context)
                    prev_assets = json.dumps(image_assets, indent=2)
                    query_prompt = f"""
                    REPORT DATA: {combined_context}
                    
                    PREVIOUS SEARCHES AND RESULTS:
                    {json.dumps(search_history, indent=2) if search_history else "None"}
                    
                    TASK: Generate a SINGLE, highly specific technical or conceptual image search query for the NEXT image.
                    
                    RULES:
                    1. The query must explore a DIFFERENT, non-redundant aspect of the report than previous searches.
                    2. Aim for diverse visual types (diagrams, graphs, conceptual visuals, historical photos if relevant).
                    3. Return ONLY the search query text.
                    """
                    query = self.llm.invoke([SystemMessage(content=query_prompt)]).content.strip().replace('"', '')
                    
                    safe_print(f" [MASTER] Turn {i+1}: Generating search for '{query}'")
                    
                    # 2. SEARCH & VISION JUDGE (Vision Turn - now with full master_context)
                    asset = vision_agent.find_and_verify_single_image(query, combined_context) # Pass full context here
                    
                    if asset:
                        image_assets.append(asset)
                        search_history.append({"query": query, "result": asset['description']})
                        safe_print(f" [SUCCESS] Asset {len(image_assets)} confirmed.")
                    else:
                        search_history.append({"query": query, "result": "REJECTED - No high-quality visual found"})
                        safe_print(f" [SKIPPED] No professional visual for '{query}'.")

            except Exception as e:
                safe_print(f" [LIMIT/ERROR] Image loop salvaged at {len(image_assets)} assets: {e}")

        # --- INTEGRATED WRITING PROMPT ---
        assets_md = "\n".join([f"- ![{a['description']}]({a['url']})" for a in image_assets])
        synthesis_prompt = f"""
        # {original_query}
        
        DATA: 
        {combined_context}
        
        AVAILABLE VISUAL ASSETS:
        {assets_md}
        
        INSTRUCTIONS:
        1. Write a massive, professional technical report.
        2. You MUST integrate the images provided in the assets list into the flow of the text.
        3. Refer to them clearly (e.g., "The diagram in Figure 1 illustrates...", "As shown below...").
        4. Place the Markdown image tag immediately after the paragraph that references it.
        """
        return self.llm.invoke([SystemMessage(content=synthesis_prompt)]).content

    def update_settings(self, api_key: str, base_url: str, model_name: str):
        """Pydantic-safe way to update the LLM."""
        from langchain_openai import ChatOpenAI
        self.llm = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=0,
            #default_headers={
            #    "HTTP-Referer": "https://rocklab.com", # Your site or GitHub repo
            #    "X-Title": "Rock Lab Research Assistant"
            #}
        )
        
# ==========================================
# 4. MAIN
# ==========================================

if __name__ == "__main__":
    query = input("Topic: ") or "Room-temperature superconductors validity"
    
    # 1. Create procedural folder
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    folder_name = f"research_{timestamp}"
    os.makedirs(folder_name, exist_ok=True)

    master = MasterOrchestrator()
    
    # 2. Headless Clarifications for CLI
    questions = master.get_user_clarifications(query)
    clar_results = []
    if questions:
        print("\n--- CLARIFICATION NEEDED ---")
        for q in questions:
            ans = input(f"ORACLE: {q}\nYOU: ")
            clar_results.append(f"Q: {q} | A: {ans}")
    
    clars_text = "\n".join(clar_results)
    tasks = master.plan_research(query, clars_text)
    
    if tasks:
        sub_reports = master.execute_subagents(tasks[:5], output_dir=folder_name)
        final_output = master.finalize_report(query, sub_reports)
        
        master_md = os.path.join(folder_name, "MASTER_REPORT.md")
        master_pdf = os.path.join(folder_name, "MASTER_REPORT.pdf")
        
        with open(master_md, "w", encoding="utf-8") as f:
            f.write(final_output)
        render_markdown_to_pdf(master_md, master_pdf)
        safe_print(f"\n[SUCCESS] Folder created: {folder_name}")