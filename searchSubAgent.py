OPENROUTER_API_KEY = ""
TARGET_CONTEXT_TOKENS = 28000
MODEL_NAME = "stepfun/step-3.5-flash:free"
BASE_URL = "https://openrouter.ai/api/v1"
providers = 'google,duckduckgo'

FINAL_ANSWER_MIN_LENGTH = 5000

import sys
import re
import threading
import datetime
# Global thread-local storage to track which agent is running in which thread
thread_context = threading.local()

def safe_print(text):
    """Prints to console and appends to a project-specific log file for UI tracking."""
    if not text:
        return
    
    # Identify the current agent and log path from thread-local storage
    task_id = getattr(thread_context, 'task_name', 'System')
    log_path = getattr(thread_context, 'log_path', "research_status.log")
    
    # 1. Console Output (Maintaining your original encoding-safe logic)
    try:
        # sys.stdout.encoding check prevents crashes on certain terminals
        encoded_text = str(text).encode(sys.stdout.encoding, errors='replace').decode(sys.stdout.encoding)
        print(f"[{task_id}] {encoded_text}")
    except:
        try:
            print(f"[{task_id}] {text}")
        except:
            pass

    # 2. File Output (The Bridge - now using the project-specific path)
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            # Format: AgentName ::: Message
            f.write(f"{task_id} ::: {text}\n")
    except:
        pass
        
def sanitize_filename(name):
    # Remove characters that are illegal in Windows filenames
    # < > : " / \ | ? *
    clean_name = re.sub(r'[<>:"/\\|?*]', '', name)
    # Also strip leading/trailing spaces and dots which can cause issues
    clean_name = clean_name.strip().strip('.')
    # Fallback if the name becomes empty after stripping
    return clean_name if len(clean_name) > 0 else "output_report"



import os
import requests
import tiktoken
from typing import TypedDict, Annotated, Dict, List, Optional
from pydantic import BaseModel, Field

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, ToolMessage, AIMessage, BaseMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from ddgs import DDGS
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from fake_useragent import UserAgent

# New robust PDF imports
from pathlib import Path
from markdown import markdown
try:
    from weasyprint import HTML, CSS
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False

def render_markdown_to_pdf(md_path: str, pdf_path: str):
    if not WEASYPRINT_AVAILABLE:
        safe_print(" [PDF ERROR] weasyprint not installed.")
        return

    try:
        # 1. Read Markdown
        with open(md_path, "r", encoding="utf-8") as f:
            md_text = f.read()

        # 2. Convert to HTML
        html_text = markdown(
            md_text,
            extensions=["fenced_code", "tables", "toc", "codehilite", "extra"]
        )

        # 3. Locate CSS
        css_file = Path("styles/style.css")
        if not css_file.exists():
            css_file = Path("style.css")

        # 4. Render PDF with Network Access Enabled
        # We MUST provide a base_url and use the HTML object directly 
        # to allow Google Font downloads
        html = HTML(string=html_text, base_url=str(Path.cwd()))
        
        if css_file.exists():
            html.write_pdf(
                pdf_path,
                stylesheets=[CSS(filename=str(css_file))]
            )
        else:
            safe_print(" [PDF] Warning: style.css not found, rendering with default styles.")
            html.write_pdf(pdf_path)

        safe_print(f" [PDF] Success: {pdf_path}")

    except Exception as e:
        safe_print(f" [PDF ERROR] WeasyPrint failed: {e}")
# ==========================================
# 1. SETTINGS & LLM
# ==========================================


llm = ChatOpenAI(
    model=MODEL_NAME, 
    api_key=OPENROUTER_API_KEY,
    base_url=BASE_URL,
    temperature=0
)

ua = UserAgent()

# ==========================================
# 2. INVINCIBLE UTILS
# ==========================================

def get_token_status(messages: List[BaseMessage]) -> str:
    """Returns a string formatted for safe_printing status."""
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
    except:
        encoding = tiktoken.get_encoding("cl100k_base")
    total = sum(len(encoding.encode(str(m.content or ""))) + 4 for m in messages)
    return f"[{total}/{TARGET_CONTEXT_TOKENS}]"

def get_total_tokens(messages: List[BaseMessage]) -> int:
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
    except:
        encoding = tiktoken.get_encoding("cl100k_base")
    return sum(len(encoding.encode(str(m.content or ""))) + 4 for m in messages)

def scrape_full_content(url: str, max_tokens_per_fetch: int = 30000) -> str:
    """Saves time and bandwidth by killing the connection if the page is too large."""
    try:
        headers = {"User-Agent": ua.random}
        # 1. Use stream=True to prevent automatic full download
        with requests.Session() as session:
            with session.get(url, headers=headers, timeout=15, stream=True) as resp:
                resp.raise_for_status()
                
                # 2. BYTE-LIMIT: 1MB of HTML is almost always > 30k tokens of text.
                # We stop downloading after 1,000,000 bytes to save time.
                max_bytes = 1_000_000 
                content = b""
                for chunk in resp.iter_content(chunk_size=8192):
                    content += chunk
                    if len(content) > max_bytes:
                        safe_print(f"  [NETWORK] Kill switch engaged: Page exceeds 1MB. Stopping download.")
                        resp.close() # Cut the wire!
                        break
                
                raw_html = content.decode("utf-8", errors="ignore")

        # 3. Clean up the HTML we did get
        soup = BeautifulSoup(raw_html, "html.parser")
        for tag in soup(["script", "style", "noscript", "form", "svg", "img", "iframe", "button"]):
            tag.decompose()
        
        markdown = md(str(soup.body or soup), heading_style="ATX").strip()
        
        # 4. Final Token-Based Polish
        encoding = tiktoken.get_encoding("cl100k_base")
        tokens = encoding.encode(markdown)
        
        if len(tokens) > max_tokens_per_fetch:
            markdown = encoding.decode(tokens[:max_tokens_per_fetch])
            markdown += "\n\n... (Content truncated) ..."
            
        return markdown
            
    except Exception as e:
        return f"SCRAPE_ERROR: {str(e)}"



# ==========================================
# 3. SCHEMAS & STATE
# ==========================================

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    hidden_urls: Dict[int, str]
    source_manifest: Dict[int, str] # <--- NEW: Stores {Index: "Title of Page"}

class SearchWeb(BaseModel):
    """Search for more information."""
    query: str = Field(description="Search terms")

class FetchDetails(BaseModel):
    """Read full content of a result."""
    index: int = Field(description="1-10")

class FinalResponse(BaseModel):
    """Generate final report."""
    answer: str = Field(description="Report content")

# ==========================================
# 4. PROTECTED NODES (No-Crash Pattern)
# ==========================================

def node_decide(state: AgentState):
    tokens = get_total_tokens(state["messages"])
    
    # Use the thread-local LLM
    if tokens < TARGET_CONTEXT_TOKENS:
        prompt = f"QUOTA: {tokens}/{TARGET_CONTEXT_TOKENS}. You MUST use SearchWeb for more info."
        model = thread_context.llm.bind_tools([SearchWeb], tool_choice="SearchWeb")
    else:
        prompt = f"QUOTA MET: {tokens}/{TARGET_CONTEXT_TOKENS}. Use FinalResponse now."
        model = thread_context.llm.bind_tools([FinalResponse], tool_choice="FinalResponse")

    res = model.invoke([SystemMessage(content=prompt)] + state["messages"])
    return {"messages": [res]}

def node_execute_search(state: AgentState):
    last_msg = state["messages"][-1]
    tool_call = last_msg.tool_calls[0]
    query = tool_call["args"].get("query", "more details")
    
    safe_print(f" [SEARCH] '{query}'")
    
    try:
        results = DDGS().text(query=query, backend=providers, max_results=10)
        
        # Accumulate indices
        current_urls = state.get("hidden_urls", {})
        current_manifest = state.get("source_manifest", {})
        start_idx = len(current_urls) + 1
        
        content = f"Results for '{query}':\n\n"
        for i, r in enumerate(results, start=start_idx):
            current_urls[i] = r['href']
            current_manifest[i] = r['title'] # Store the title for the LLM's lookup
            content += f"{i}. {r['title']}\n   Snippet: {r['body']}\n\n"
            
        safe_print(f"  [LOG] Added sources {start_idx} to {start_idx + len(results) - 1}")
    except Exception as e:
        content = f"Search Error: {e}"

    return {
        "messages": [ToolMessage(content=content, tool_call_id=tool_call["id"], name="SearchWeb")],
        "hidden_urls": current_urls,
        "source_manifest": current_manifest
    }

def node_agent_select(state: AgentState):
    tokens = get_total_tokens(state["messages"])
    prompt = f"QUOTA: {tokens}/{TARGET_CONTEXT_TOKENS}. Review the summaries. If useful, use FetchDetails(index=X). If they are all irrelevant, use SearchWeb(query=...) to try a better search query."
    
    # Use the thread-local LLM
    model = thread_context.llm.bind_tools([FetchDetails, SearchWeb], tool_choice="required")
    res = model.invoke([SystemMessage(content=prompt)] + state["messages"])
    return {"messages": [res]}

def node_execute_fetch(state: AgentState):
    last_msg = state["messages"][-1]
    status = get_token_status(state["messages"])
    
    # 1. CRASH PROTECTION & DEBUG safe_print
    if not hasattr(last_msg, "tool_calls") or len(last_msg.tool_calls) == 0:
        safe_print(f"\n [DEBUG] AI failed to call tool. RAW RESPONSE CONTENT:")
        safe_print(f"--- CONTENT START ---\n{last_msg.content}\n--- CONTENT END ---")
        safe_print(f"  [RECOVERY] AI missed tool call. Retrying...")
        
        # YOUR REQUESTED ERROR MESSAGE
        error_msg = "Error: You responded with text. You MUST use the FetchDetails tool with an index, or SearchWeb tool to start another search."
        return {"messages": [SystemMessage(content=error_msg)]}

    tool_call = last_msg.tool_calls[0]
    tool_name = tool_call["name"]

    # 2. HANDLE SEARCH CALL (Pivot)
    if tool_name == "SearchWeb":
        query = tool_call["args"].get("query", "more info")
        safe_print(f" [PIVOT] AI rejected snippets. New Search: '{query}'")
        # We return a placeholder ToolMessage so the graph doesn't break
        return {"messages": [ToolMessage(content=f"Pivot: Starting new search for {query}", tool_call_id=tool_call["id"], name=tool_name)]}

    # 3. HANDLE FETCH CALL (Standard)
    try:
        idx = int(tool_call["args"].get("index", 0))
    except:
        idx = 0

    if idx == 0:
        content = "Agent skipped fetching."
    else:
        url = state.get("hidden_urls", {}).get(idx)
        if not url:
            content = f"Error: Index {idx} not found. Use SearchWeb to find fresh results."
        else:
            safe_print(f" [FETCH] Index {idx} | {status}")
            content = scrape_full_content(url)
            
    return {"messages": [ToolMessage(content=content, tool_call_id=tool_call["id"], name="FetchDetails")]}

def node_final(state: AgentState):
    tokens = get_total_tokens(state["messages"])
    manifest = state.get("source_manifest", {})
    safe_print(f" [TARGET REACHED] {tokens} tokens. Synthesizing final answer...")
    reference_table = "\n".join([f"[Source {i}]: {title}" for i, title in manifest.items()])
    
    final_instruction = f"""
    CITATION LOOKUP TABLE (Use these numbers):
    {reference_table}

    CRITICAL INSTRUCTIONS:
    1. Produce a massive, exhaustive technical report.
    2. You MUST cite your sources inline using [Source X] format.
    3. Every claim or technical detail must be followed by at least one [Source X] tag.
    4. Use the Lookup Table above to ensure the numbers match the correct information.
    5. If response is shorter than {FINAL_ANSWER_MIN_LENGTH} tokens, it will be rejected.
    """
    
    try:
        # Use the thread-local LLM
        res = thread_context.llm.invoke([SystemMessage(content=final_instruction)] + state["messages"])
        if not res.content or len(res.content.strip()) < FINAL_ANSWER_MIN_LENGTH:
            return {"messages": [AIMessage(content="RETRY_REQUIRED: Final answer was blank or too short.")]}
        return {"messages": [res]}
    except Exception as e:
        return {"messages": [AIMessage(content=f"FINAL_SYNTH_ERROR: {str(e)}")]}


def route_after_final(state: AgentState):
    """The Gatekeeper: Only allows the graph to END if the answer is valid."""
    last_msg = state["messages"][-1]
    content = last_msg.content or ""
    
    # 1. Catch Blank or 'Retry' signals
    if "RETRY_REQUIRED" in content or "FINAL_SYNTH_ERROR" in content or len(content.strip()) < 100:
        safe_print("  [VALIDATION] Blank or inadequate answer detected. Forcing retry...")
        return "retry"
    
    # 2. Catch the 'Hallucinated Search' again (just in case)
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "retry"
        
    safe_print(" [SUCCESS] Final report accepted.")
    return "end"

# ==========================================
# 5. ROBUST ROUTING
# ==========================================

def route_after_decide(state: AgentState):
    last_msg = state["messages"][-1]
    tokens = get_total_tokens(state["messages"])
    
    # 1. Check if we have a tool call
    if hasattr(last_msg, "tool_calls") and len(last_msg.tool_calls) > 0:
        tool_name = last_msg.tool_calls[0]["name"]
        
        # 2. ENFORCEMENT: If tokens are low, we ONLY allow "search"
        if tokens < TARGET_CONTEXT_TOKENS:
            if tool_name == "SearchWeb":
                return "search"
            else:
                # The AI tried to use a different tool to quit early. Force it back.
                safe_print(f"  [ENFORCER] AI tried to quit at {tokens} tokens. Forcing more research.")
                return "decide" 
        
        # 3. If tokens are high enough, allow it to proceed to final
        return "search" if tool_name == "SearchWeb" else "final"

    # 4. If no tool call at all, retry the decision
    return "decide"

def route_after_search(state: AgentState):
    # If the last message is a ToolMessage, it means the search execution SUCCEEDED.
    # If it's a SystemMessage, it means the search execution node detected an error and wants a retry.
    if state["messages"][-1].type == "tool":
        return "select"
    return "decide"

def route_after_fetch(state: AgentState):
    # Same logic: if tool succeeded, go back to decide. If errored, retry select.
    if state["messages"][-1].type == "tool":
        return "decide"
    return "select"

# ==========================================
# 6. GRAPH ASSEMBLY
# ==========================================

builder = StateGraph(AgentState)

# 1. Define all Nodes
builder.add_node("decide", node_decide)
builder.add_node("search_exec", node_execute_search)
builder.add_node("select_idx", node_agent_select)
builder.add_node("fetch_exec", node_execute_fetch)
builder.add_node("final", node_final)

# 2. Define Starting Point
builder.add_edge(START, "decide")

# 3. Decision Gate (Search Loop vs Synthesis)
builder.add_conditional_edges("decide", route_after_decide, {
    "search": "search_exec", 
    "final": "final",
    "decide": "decide"
})

# 4. Search Loop Edges
builder.add_conditional_edges("search_exec", route_after_search, {
    "select": "select_idx", 
    "decide": "decide"
})

builder.add_edge("select_idx", "fetch_exec")

builder.add_conditional_edges("fetch_exec", route_after_fetch, {
    "decide": "decide", 
    "select": "select_idx"
})

# 5. THE CRITICAL UPDATE: Final Answer Gatekeeper
# We remove builder.add_edge("final", END) and replace it with:
builder.add_conditional_edges(
    "final", 
    route_after_final, 
    {
        "retry": "final", # Loops back into the final node to try writing again
        "end": END       # Only exits if validation passes
    }
)

app = builder.compile()
# ==========================================
# 7. MAIN
# ==========================================
def test():
    prompt = input("What would you like to ask the oracle : ")
    if not len(prompt)>0:
        prompt = "prove that all motherly feeling is perverted on the basis of jouissance by lacan"
    safe_print(f"You queried for: {prompt}")
    
    filename_raw = input("And what shall the file be named? Dont enter extension just the name. : ")
    if not len(filename_raw)>0:
        filename_raw = "output"
    filename = sanitize_filename(filename_raw)
    safe_print(f"file name request set to: {filename}")

    safe_print("Oracle is now starting, sit back and wait for the file.")  
    
    try:
        app.invoke(
            {"messages": [("user", prompt)], "hidden_urls": {}},
            {"recursion_limit": 5000} 
        )
    except Exception as e:
        safe_print(f"FATAL: {e}")

# --- searchSubAgent.py ---
import traceback
from streamlit.runtime.scriptrunner import add_script_run_ctx

def run_subagent(search_prompt, task_name, output_dir=".", st_placeholder=None, st_ctx=None, config=None):
    # SET THREAD-LOCAL CONTEXT (Point 1 & 2)
    thread_context.task_name = task_name 
    thread_context.log_path = os.path.join(output_dir, "research_status.log")
    
    # Initialize a fresh LLM instance specifically for this thread (Point 1)
    thread_context.llm = ChatOpenAI(
        model=config.get("model_name", MODEL_NAME),
        api_key=config.get("api_key", OPENROUTER_API_KEY),
        base_url=config.get("base_url", BASE_URL),
        temperature=0
    )

    if st_ctx:
        try:
            add_script_run_ctx(st_ctx)
        except Exception:
            pass

    def update_ui(progress, text):
        if st_placeholder:
            try:
                st_placeholder.progress(progress, text=text)
            except Exception:
                safe_print(f"[{task_name}] {text}")

    update_ui(5, f"Initializing {task_name}...")

    filename = sanitize_filename(task_name)
    md_path = os.path.join(output_dir, f"{filename}.md")
    pdf_path = os.path.join(output_dir, f"{filename}.pdf")
    
    now_gmt = datetime.datetime.now(datetime.timezone.utc)
    time_msg = f"Todays date is {now_gmt.strftime('%Y-%m-%d')} and the current time is {now_gmt.strftime('%H:%M:%S')} GMT."

    try:
        update_ui(15, f"🔍 {task_name}: Researching...")
        
        # Invoke the graph (it will use the nodes above which reference thread_context.llm)
        final_state = app.invoke(
            {"messages": [SystemMessage(content=time_msg),("user", search_prompt)], "hidden_urls": {}, "source_manifest": {}},
            {"recursion_limit": 5000} 
        )
        
        report_text = final_state["messages"][-1].content
        url_map = final_state.get("hidden_urls", {})

        def inject_links(match):
            idx = int(match.group(1))
            url = url_map.get(idx)
            return f"[[Source {idx}]({url})]" if url else f"[Source {idx}]"

        final_message = re.sub(r"\[Source (\d+)\]", inject_links, report_text)
        
        safe_print(f" [POST-PROCESS] Linked {len(url_map)} sources in final Markdown.")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(final_message)
        
        render_markdown_to_pdf(md_path, pdf_path)
        update_ui(100, f"✅ {task_name}: Finished.")
        return final_message
    except Exception as e:
        err_detail = traceback.format_exc()
        safe_print(f"!!! CRITICAL ERROR IN SUB-AGENT [{task_name}]:\n{err_detail}")
        if st_placeholder:
            st_placeholder.error(f"❌ {task_name} failed. Check console.")
        return f"Error in {task_name}: {str(e)}"

import io
import base64
import requests
from PIL import Image
from ddgs import DDGS
from langchain_openai import ChatOpenAI
import json

class VisionImageAgent:
    def __init__(self, api_key, base_url, model_name):
        self.llm = ChatOpenAI(model=model_name, api_key=api_key, base_url=base_url)

    def get_image_assets(self, context_text):
        """Finds 10 validated images and returns them as a list of dictionaries."""
        safe_print("[VISION] Identifying 10 high-quality visual assets...")
        
        # 1. Generate search queries based on the combined context
        query_prompt = f"""
        Review this research data and generate 15 distinct, technical image search queries 
        that would help visualize the concepts described. 
        Return ONLY a comma-separated list of queries.
        
        DATA SUMMARY: {context_text[:2000]}
        """
        query_job = self.llm.invoke([SystemMessage(content=query_prompt)])
        queries = [q.strip() for q in query_job.content.split(",")]

        found_assets = []
        with DDGS() as ddgs:
            for q in queries:
                if len(found_assets) >= 10: break
                
                # Fetch more per query to increase chances of finding >320x240
                results = list(ddgs.images(q, max_results=8))
                for r in results:
                    try:
                        resp = requests.get(r['image'], timeout=5)
                        
                        # A. REQUIREMENT: Min size 320x240 check (Python level)
                        img = Image.open(io.BytesIO(resp.content))
                        width, height = img.size
                        if width < 320 or height < 240:
                            continue

                        # B. REQUIREMENT: Resample to 320x240 for Model Judgment
                        b64_resampled = self._resample_for_model(resp.content)
                        if not b64_resampled: continue

                        # C. REQUIREMENT: Vision Model Validation
                        check_msg = {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": f"Is this image a relevant, high-quality technical illustration for the query '{q}'? Reply ONLY with 'YES' or 'NO'."},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_resampled}"}}
                            ]
                        }
                        
                        validation = self.llm.invoke([check_msg]).content
                        
                        if "YES" in validation.upper():
                            # Create a concise description for the final writer
                            desc_prompt = f"Create a 5-word professional caption for an image representing: {q}"
                            description = self.llm.invoke([SystemMessage(content=desc_prompt)]).content.strip().replace('"', '')
                            
                            found_assets.append({
                                "url": r['image'],
                                "description": description
                            })
                            safe_print(f" [VISION] Asset {len(found_assets)} validated: {description}")
                            break # Move to next search query
                    except:
                        continue

        return found_assets

    def _resample_for_model(self, image_bytes):
        """Resamples to 320x240 for the model's judgment as requested."""
        try:
            img = Image.open(io.BytesIO(image_bytes))
            if img.mode != 'RGB':
                img = img.convert('RGB')
            img_resampled = img.resize((320, 240), Image.Resampling.LANCZOS)
            buffered = io.BytesIO()
            img_resampled.save(buffered, format="JPEG")
            return base64.b64encode(buffered.getvalue()).decode('utf-8')
        except:
            return None

if __name__ == "__main__":  
    test()
    