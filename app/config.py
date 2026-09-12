from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIRECTORY = PROJECT_ROOT / "data"
DATABASE_FILE = DATA_DIRECTORY / "secure_rag.db"
DATABASE_URL = f"sqlite:///{DATABASE_FILE.as_posix()}"
UPLOAD_DIRECTORY = DATA_DIRECTORY / "uploads"
VECTOR_STORE_DIRECTORY = DATA_DIRECTORY / "vector_store"
VECTOR_COLLECTION_NAME = "document_chunks"
EMBEDDING_MODEL = "BAAI/bge-small-en"
SEMANTIC_SCORE_THRESHOLD = 0.75
