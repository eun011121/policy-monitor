"""Deduplicator — clusters items that report on the same event.

Uses title keyword overlap (Jaccard similarity) to group articles.
Each cluster keeps the highest-priority item as representative,
with others stored in its `related` list.
"""
import re
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# Source priority (lower = more authoritative)
SOURCE_PRIORITY = {
    '정책브리핑': 0,
    '국회 의안정보시스템': 1,
    # Research institutes
    'KDI': 2, 'KIPF': 2, 'KIET': 2, 'KOSBI': 2, 'KIF': 2,
    # Newspapers (lower priority than official sources)
    '조선일보': 5, '중앙일보': 5, '동아일보': 5,
    '한겨레': 5, '경향신문': 5, '매일경제': 5, '한국경제': 5,
}

SIMILARITY_THRESHOLD = 0.25  # Jaccard threshold to consider same story


class Deduplicator:
    def deduplicate(self, items: list[dict]) -> list[dict]:
        if not items:
            return []

        # Build token sets per item
        token_sets = [self._tokenize(i['title']) for i in items]

        # Union-Find clustering
        parent = list(range(len(items)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y):
            parent[find(x)] = find(y)

        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if items[i]['committee'] != items[j]['committee']:
                    continue
                # 법안은 이미 의안번호로 고유하게 식별되고, 제목에 "일부개정법률안"류
                # 공통 접미사가 많아 서로 다른 법안끼리도 Jaccard 유사도가 쉽게 임계값을
                # 넘는다(예: "농지법 일부개정법률안" vs "동물복지기본법안") — 뉴스 기사
                # 클러스터링용 로직이라 법안 간 매칭에는 부적합해서 건너뛴다.
                if items[i]['type'] == 'legislation' and items[j]['type'] == 'legislation':
                    continue
                sim = self._jaccard(token_sets[i], token_sets[j])
                if sim >= SIMILARITY_THRESHOLD:
                    union(i, j)

        # Group by cluster root
        clusters: dict[int, list[int]] = defaultdict(list)
        for i in range(len(items)):
            clusters[find(i)].append(i)

        result = []
        for root, members in clusters.items():
            if len(members) == 1:
                result.append(items[members[0]])
                continue

            # Pick representative (lowest source priority, then press_release > news)
            def rank(idx):
                item = items[idx]
                src_score = SOURCE_PRIORITY.get(item['source'], 10)
                type_score = 0 if item['type'] == 'press_release' else 1
                return (src_score, type_score)

            members_sorted = sorted(members, key=rank)
            rep = dict(items[members_sorted[0]])
            rep['related'] = [
                {'title': items[m]['title'], 'url': items[m]['url'], 'source': items[m]['source']}
                for m in members_sorted[1:]
            ]
            result.append(rep)

        logger.info(f"Dedup: {len(items)} → {len(result)} items")
        return result

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        text = re.sub(r'[^\w]', ' ', text)
        tokens = [t for t in text.split() if len(t) >= 2]
        return set(tokens)

    @staticmethod
    def _jaccard(a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)
