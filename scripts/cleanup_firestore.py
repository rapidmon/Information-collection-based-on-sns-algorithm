"""Firestore에서 collection_runs, categories 컬렉션 삭제."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.infrastructure.config.settings import Settings
from src.infrastructure.database.firebase_client import get_firestore_client, init_firebase

settings = Settings()
init_firebase(
    credential_path=settings.firebase_credential_path,
    project_id=settings.firebase_project_id or None,
)
db = get_firestore_client()


def delete_collection(col_name: str):
    col = db.collection(col_name)
    docs = list(col.stream())
    for doc in docs:
        doc.reference.delete()
    print(f"{col_name}: {len(docs)}건 삭제")


delete_collection("collection_runs")
delete_collection("categories")
print("완료")
