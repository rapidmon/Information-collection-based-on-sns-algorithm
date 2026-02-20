"""Firebase Firestore 클라이언트 초기화.

firebase-admin SDK로 초기화한 뒤 Firestore 클라이언트를 제공한다.
서비스 계정 키 JSON 파일 또는 환경변수 기반 인증.
"""

from __future__ import annotations

import logging
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore

logger = logging.getLogger(__name__)

_db = None


def init_firebase(
    credential_path: str | None = None,
    project_id: str | None = None,
) -> None:
    """Firebase 앱 초기화.

    Args:
        credential_path: 서비스 계정 키 JSON 파일 경로.
                         None이면 GOOGLE_APPLICATION_CREDENTIALS 환경변수 사용.
        project_id: Firebase 프로젝트 ID (선택).
    """
    global _db

    if firebase_admin._apps:
        # 이미 초기화됨
        _db = firestore.client()
        return

    if credential_path and Path(credential_path).exists():
        cred = credentials.Certificate(credential_path)
    else:
        # 환경변수 GOOGLE_APPLICATION_CREDENTIALS 또는 ADC 사용
        cred = credentials.ApplicationDefault()

    options = {}
    if project_id:
        options["projectId"] = project_id

    firebase_admin.initialize_app(cred, options)
    _db = firestore.client()
    logger.info("Firebase Firestore 초기화 완료")


def get_firestore_client():
    """초기화된 Firestore 클라이언트를 반환."""
    if _db is None:
        raise RuntimeError("Firebase가 초기화되지 않았습니다. init_firebase()를 먼저 호출하세요.")
    return _db
