"""CDP 기반 SNS 수집기 공통 베이스.

twitter/threads/linkedin 수집기가 공유하는 동일한 생성자와 source_name을 제공한다.
서브클래스는 클래스 속성 SOURCE와 collect()/login()/is_session_valid()를 구현한다.
(collect() 본문은 GraphQL 인터셉트 vs DOM 파싱 등으로 갈려 베이스로 올리지 않는다.)
"""

from __future__ import annotations

from src.infrastructure.config.settings import CollectorConfig, SnsCredentials


class BaseCdpCollector:
    SOURCE: str = ""

    def __init__(
        self,
        config: CollectorConfig,
        credentials: SnsCredentials | None = None,
        cdp_port: int = 9222,
    ):
        self._config = config
        self._credentials = credentials or SnsCredentials()
        self._cdp_url = f"http://127.0.0.1:{cdp_port}"

    @property
    def source_name(self) -> str:
        return self.SOURCE
