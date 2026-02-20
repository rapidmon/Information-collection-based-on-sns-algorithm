import hashlib
import re
import unicodedata


def compute_content_hash(text: str) -> str:
    """콘텐츠 텍스트의 정규화된 SHA-256 해시를 계산한다.

    정규화: 소문자, 공백 통일, URL 제거, 유니코드 정규화.
    동일 내용의 게시물이 다른 포맷으로 수집되더라도 같은 해시를 생성.
    """
    normalized = text.lower()
    normalized = re.sub(r"https?://\S+", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = unicodedata.normalize("NFC", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
