# Softlight UI State Capture Agent (Agent B)

This project implements **Agent B** for the Softlight take-home assignment.

Agent B:
- Receives a natural language task (from Agent A), like  
  - “On GitHub, create a new public repository named 'softlight-demo-repo'”  
  - “In Linear, create a new workspace named 'Softlight agent demo workspace'”
- Opens a real browser (Playwright + Chrome profile)
- Uses an LLM (Groq / Llama 3) to decide the **next UI action**:
  - 'click', 'type', 'wait', or 'done'
- Executes the action, and captures **before/after screenshots + metadata**
- Saves everything as a **dataset** of UI states under 'dataset/'.

The same loop works across different tasks and apps (GitHub, Linear) without hardcoding individual flows.



## How it works (high level)

- 'browser.py'  
  Thin wrapper around Playwright: 'goto', 'click', 'fill', 'screenshot', 'wait'.  
  Uses flexible selectors (role, text, common patterns) so the same logic works across multiple apps.

- 'agent.py'  
  - 'LLMAgent' reads:
    - task description  
    - current URL + visible text  
    - recent actions  
  - Asks Groq’s LLM for the **next action as JSON**.  
  - 'run_task_workflow(...)' runs the main loop:
    - snapshot DOM  
    - decide action  
    - execute  
    - take screenshots  
    - store step-by-step metadata

- 'config.py'  
  Central config: Groq model, dataset folder, max steps, DOM character limit.



## Dataset format

Each run creates a folder in 'dataset/':

'''text

dataset/

  <task-id>_<timestamp>/
  
    step_01_before.png
    
    step_01_after.png
    
    ...
    
    metadata.json
    
metadata.json contains:

task info (description, start URL, timestamp)

each step’s:

URL, chosen action (JSON from LLM), before/after screenshot paths, optional error + stopped reason

This can be consumed later by another “Agent A” or a training pipeline.

# Running locally
# Setup
git clone https://github.com/<your-username>/<repo-name>.git
cd <repo-name>

python -m venv venv

# Windows
.\venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
playwright install
Set your Groq API key

# Windows (PowerShell)
$env:GROQ_API_KEY = "your_api_key_here"

# macOS / Linux
# export GROQ_API_KEY="your_api_key_here"
Example commands

# 1. Create GitHub repo
python main.py --task "On GitHub, create a new public repository named 'softlight-demo-repo' in my account" --url "https://github.com/new" --task-id "github_create_repo"

# 2. Open Issues tab
python main.py --task "On GitHub, open the Issues tab for this repository" --url "https://github.com/<your-username>/softlight-demo-repo" --task-id "github_open_issues_tab"

# 3. Go to Settings page of Github Repo
python main.py --task "On GitHub, open the Settings tab for this repository" --url "https://github.com/<your-username>/softlight-demo-repo" --task-id "github_open_settings"

# 4. Create Linear workspace
python main.py --task "In Linear, create a new workspace named 'Softlight agent demo workspace'" --url "https://linear.app" -task-id "linear_create_workspace"

Each run will produce a new folder under dataset/ with screenshots + metadata.json.

