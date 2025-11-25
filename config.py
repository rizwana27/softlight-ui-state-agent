# config.py
import os
from pathlib import Path

# -------- LLM / Groq config --------

# Make sure you set this in your environment:
#   setx GROQ_API_KEY "your_key_here"
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Groq model
GROQ_MODEL = "llama-3.1-8b-instant"

# -------- Dataset / paths --------

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "dataset"
DATASET_DIR.mkdir(exist_ok=True)

# -------- Limits / heuristics --------

# Max agent steps per task
MAX_STEPS = 15

# How much visible text to send to the LLM
DOM_CHAR_LIMIT = 6000
