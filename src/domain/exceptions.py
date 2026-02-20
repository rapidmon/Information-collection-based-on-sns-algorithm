"""도메인 레이어 예외 정의."""


class DomainError(Exception):
    """도메인 레이어 최상위 예외."""


class CollectionError(DomainError):
    """수집 중 발생한 오류."""


class SessionExpiredError(CollectionError):
    """SNS 로그인 세션이 만료되었을 때."""

    def __init__(self, platform: str):
        self.platform = platform
        super().__init__(f"{platform} 로그인 세션이 만료되었습니다. 수동 재로그인이 필요합니다.")


class ProcessingError(DomainError):
    """AI 처리 중 발생한 오류."""


class BriefingGenerationError(DomainError):
    """브리핑 생성 중 발생한 오류."""


class DeliveryError(DomainError):
    """이메일 등 전달 중 발생한 오류."""
