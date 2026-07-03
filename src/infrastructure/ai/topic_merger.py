"""토픽 병합용 순수 문자열 알고리즘 (LLM 무관).

OpenAIProcessor의 클러스터링 2차 병합에서 쓰던 토큰화·유사도·union-find 후보
그룹핑을 분리한 것. 네트워크/서브프로세스 의존이 없어 단독 단위 테스트가 가능하다.
"""

from __future__ import annotations

import re

from src.domain.services.ai_processor import MergedTopic


class TopicMerger:
    """headline 토큰 유사도로 병합 후보군을 찾고, 그룹을 하나의 토픽으로 합친다."""

    _KR_SUFFIXES = re.compile(
        r'(는|은|가|이|를|을|의|에|로|와|과|며|고|도|만|서|나|든|까지|에서|으로|하며|이며'
        r'|했다|했으며|되었다|되었|하였다|있다|없다|이다|한다|된다|라고)$'
    )
    _PRODUCT_NAME_PATTERN = re.compile(r'[a-z]+\d')

    @classmethod
    def normalize_token(cls, token: str) -> str:
        """토큰을 정규화한다 (소문자, 하이픈/공백 제거, 한국어 조사 제거)."""
        t = token.lower().replace('-', '').replace(' ', '')
        t = cls._KR_SUFFIXES.sub('', t)
        return t

    @staticmethod
    def extract_key_tokens(headline: str) -> set[str]:
        """headline에서 핵심 토큰을 추출.

        - 영문 이름+버전을 하나의 토큰으로 유지 (GPT-5.5, Claude 4.7 등)
        - 한국어 조사 제거 후 어간만 추출
        - 고유명사 중심 추출
        """
        # 1단계: 영문 이름+버전번호를 하나로 묶음
        merged = re.findall(
            r'[A-Za-z][A-Za-z0-9]*(?:[- ]?\d+(?:\.\d+)*)?', headline
        )
        # 2단계: 한국어 단어 추출 (2자 이상)
        korean = re.findall(r'[가-힣]{2,}', headline)
        # 3단계: 독립 숫자+단위
        numbers = re.findall(r'\d+(?:\.\d+)?[조억만%]+', headline)

        stop_kr = {"에서", "으로", "하며", "이며", "하여", "있다", "했다", "했으며",
                   "되었", "되었다", "것으로", "대비", "전년", "동기", "기준", "기록",
                   "증가", "감소", "상회", "초과", "보고", "달러", "원으로",
                   "위해", "통해", "대한", "관한", "관련", "주요", "해당",
                   "발표", "출시", "공개", "도입"}
        stop_en = {"the", "and", "for", "with", "from", "that", "this", "are",
                   "was", "has", "have", "been", "will", "its", "new"}

        result = set()
        for t in merged:
            t = t.strip()
            if len(t) >= 2 and t.lower() not in stop_en:
                result.add(t)
        for t in korean:
            if t not in stop_kr:
                result.add(t)
        for t in numbers:
            result.add(t)

        return result

    @classmethod
    def token_similarity(cls, set_a: set[str], set_b: set[str]) -> int:
        """두 토큰 집합의 매칭 수 (정규화 + 부분 문자열 매칭).

        영문+숫자 조합 토큰(제품명/모델명: gpt5.5, claude4.7 등)이 일치하면
        가중치 2로 계산하여 단일 제품명 매칭만으로도 병합 후보가 될 수 있게 한다.
        """
        if not set_a or not set_b:
            return 0

        norm_a = {cls.normalize_token(t): t for t in set_a}
        norm_b = {cls.normalize_token(t): t for t in set_b}

        # 정규화 후 완전 일치
        matched_keys = set(norm_a.keys()) & set(norm_b.keys())
        matches = 0
        for key in matched_keys:
            if cls._PRODUCT_NAME_PATTERN.search(key):
                matches += 2
            else:
                matches += 1

        # 부분 문자열 매칭 (이미 매칭된 것 제외)
        remaining_a = {k for k in norm_a if k not in matched_keys}
        remaining_b = {k for k in norm_b if k not in matched_keys}

        for na in remaining_a:
            if len(na) < 3:
                continue
            for nb in remaining_b:
                if len(nb) < 3:
                    continue
                if na in nb or nb in na:
                    matches += 1
                    break

        return matches

    def find_merge_candidates(self, topics: list[MergedTopic]) -> list[list[int]]:
        """headline 토큰 유사도로 병합 후보군을 찾는다 (LLM 호출 없이)."""
        token_sets = [self.extract_key_tokens(t.headline) for t in topics]
        n = len(topics)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(n):
            if not token_sets[i]:
                continue
            for j in range(i + 1, n):
                if not token_sets[j]:
                    continue
                matched = self.token_similarity(token_sets[i], token_sets[j])
                smaller = min(len(token_sets[i]), len(token_sets[j]))
                ratio = matched / smaller if smaller > 0 else 0
                if matched >= 2 and ratio >= 0.4:
                    union(i, j)

        groups: dict[int, list[int]] = {}
        for i in range(n):
            root = find(i)
            groups.setdefault(root, []).append(i)

        return [indices for indices in groups.values() if len(indices) >= 2]

    @staticmethod
    def merge_topic_group(topics: list[MergedTopic], indices: list[int]) -> MergedTopic:
        """토픽 인덱스 그룹을 하나의 MergedTopic으로 병합."""
        base = topics[indices[0]]
        combined_post_ids = []
        combined_bullets = []
        combined_sources = []
        combined_urls = []
        best_score = 0.0
        best_headline = base.headline

        for idx in indices:
            t = topics[idx]
            combined_post_ids.extend(t.post_ids)
            combined_bullets.extend(t.body_bullets)
            combined_sources.extend(t.sources)
            combined_urls.extend(t.source_urls)
            if t.importance_score > best_score:
                best_score = t.importance_score
                best_headline = t.headline

        combined_sources = list(dict.fromkeys(combined_sources))
        combined_urls = list(dict.fromkeys(combined_urls))

        return MergedTopic(
            post_ids=combined_post_ids,
            headline=best_headline,
            body_bullets=combined_bullets,
            primary_category=base.primary_category,
            importance_score=best_score,
            sources=combined_sources,
            source_urls=combined_urls,
        )
