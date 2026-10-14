"""Settings for the whole pipeline, read once from the environment and the project's .env file."""
import os
from pathlib import Path
from dotenv import load_dotenv

# the project folder (one level above this package), so paths work wherever the app is started from
ROOT = Path(__file__).resolve().parent.parent
# load .env from the project folder; real environment variables win over the file (handy in Docker and CI)
load_dotenv(ROOT / ".env", override=False)

# where downloaded pages, the SQLite database and the vector store live (all rebuilt by the pipeline, never committed)
DATA = Path(os.environ.get("PL_DATA", ROOT / "data"))
RAW = DATA / "raw"                       # trimmed copies of the EPO pages, one file per page
DB_PATH = DATA / "patentlaw.db"          # provisions, chunks, cross-references and the full-text index
CHROMA_PATH = DATA / "chroma"            # the vector store

# Groq: an OpenAI-compatible API. The big model writes answers, the small one plans searches (separate rate limits)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_URL = os.environ.get("PL_GROQ_URL", "https://api.groq.com/openai/v1")
ANSWER_MODEL = os.environ.get("PL_ANSWER_MODEL", "openai/gpt-oss-120b")
PLAN_MODEL = os.environ.get("PL_PLAN_MODEL", "openai/gpt-oss-20b")

# embeddings run locally in Ollama: free, private, and the same vectors every time
OLLAMA_URL = os.environ.get("PL_OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("PL_EMBED_MODEL", "nomic-embed-text")

# the EPO asks crawlers to wait 1 second between requests (robots.txt: Crawl-delay: 1)
CRAWL_DELAY = float(os.environ.get("PL_CRAWL_DELAY", "1.0"))
USER_AGENT = "PatentLawRAG/0.1 (research prototype; polite crawler, 1 req/s)"

# how much law the answering model gets to read: Groq's free tier allows 8K tokens a minute for gpt-oss-120b,
# so the sources plus the question plus the answer must stay well under that
CONTEXT_TOKENS = int(os.environ.get("PL_CONTEXT_TOKENS", "3200"))
ANSWER_TOKENS = int(os.environ.get("PL_ANSWER_TOKENS", "3000"))


######
# Configuration module: every path, model name and limit in one place, with defaults that work on a laptop.
# Override anything with an environment variable or a line in .env (e.g. PL_ANSWER_MODEL=qwen/qwen3.8-27b).
