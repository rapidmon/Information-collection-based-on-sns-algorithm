import {
    collection, query, where, orderBy, limit, startAfter,
    getDocs, getDoc, doc, Timestamp
} from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js';
import { db } from './firebase-config.js';

/**
 * 최근 게시물 조회
 * @param {Object} opts - { source?, category?, searchQuery?, limitCount?, lastDoc? }
 * @returns {Promise<{ posts: Array, lastDoc: Object|null }>}
 */
export async function getRecentPosts(opts = {}) {
    const { source, category, searchQuery, limitCount = 50, lastDoc: cursor, includeUnprocessed = false } = opts;

    const constraints = [];

    // AI 처리 완료된 게시물만 (is_relevant == true)
    if (!includeUnprocessed) {
        constraints.push(where('is_relevant', '==', true));
    }

    if (source) {
        constraints.push(where('source', '==', source));
    }

    // 카테고리는 클라이언트 사이드에서 필터링 (복합 인덱스 불필요)

    constraints.push(orderBy('collected_at', 'desc'));

    if (cursor) {
        constraints.push(startAfter(cursor));
    }

    // 카테고리 필터 시 더 많이 가져와서 클라이언트에서 필터링
    const fetchCount = category ? limitCount * 3 : limitCount;
    constraints.push(limit(fetchCount));

    const q = query(collection(db, 'posts'), ...constraints);
    const snapshot = await getDocs(q);

    let posts = snapshot.docs.map(d => ({ id: d.id, ...d.data(), _doc: d }));

    // 클라이언트 사이드 카테고리 필터링
    if (category) {
        posts = posts.filter(p =>
            (p.category_names || []).includes(category)
        );
    }

    // Client-side text search (Firestore doesn't support full-text search)
    if (searchQuery) {
        const lower = searchQuery.toLowerCase();
        posts = posts.filter(p =>
            (p.content_text || '').toLowerCase().includes(lower) ||
            (p.summary || '').toLowerCase().includes(lower) ||
            (p.author || '').toLowerCase().includes(lower)
        );
    }

    const lastDocument = snapshot.docs.length > 0
        ? snapshot.docs[snapshot.docs.length - 1]
        : null;

    return { posts, lastDoc: lastDocument };
}

/**
 * 최신 브리핑 1건
 */
export async function getLatestBriefing() {
    const q = query(
        collection(db, 'briefings'),
        orderBy('generated_at', 'desc'),
        limit(1)
    );
    const snapshot = await getDocs(q);
    if (snapshot.empty) return null;
    const d = snapshot.docs[0];
    return { id: d.id, ...d.data() };
}

/**
 * 브리핑 목록 조회
 * @param {number} limitCount
 * @param {Object} cursor - Firestore doc snapshot for pagination
 */
export async function getBriefings(limitCount = 20, cursor = null) {
    const constraints = [orderBy('generated_at', 'desc')];

    if (cursor) {
        constraints.push(startAfter(cursor));
    }

    constraints.push(limit(limitCount));

    const q = query(collection(db, 'briefings'), ...constraints);
    const snapshot = await getDocs(q);

    const briefings = snapshot.docs.map(d => ({ id: d.id, ...d.data(), _doc: d }));
    const lastDocument = snapshot.docs.length > 0
        ? snapshot.docs[snapshot.docs.length - 1]
        : null;

    return { briefings, lastDoc: lastDocument };
}

/**
 * 브리핑 상세 (ID로 조회)
 */
export async function getBriefingById(briefingId) {
    const docRef = doc(db, 'briefings', briefingId);
    const snap = await getDoc(docRef);
    if (!snap.exists()) return null;
    return { id: snap.id, ...snap.data() };
}

/**
 * 카테고리 전체 조회
 * Firestore categories 컬렉션이 비어있으면 기본 카테고리 반환
 */
const DEFAULT_CATEGORIES = [
    { name: 'AI', name_ko: 'AI' },
    { name: 'Semiconductor', name_ko: '반도체' },
    { name: 'Cloud', name_ko: '클라우드' },
    { name: 'BigTech', name_ko: '빅테크' },
    { name: 'Startup', name_ko: '스타트업' },
    { name: 'Regulation', name_ko: '규제' },
    { name: 'Coding', name_ko: '코딩' },
];

export async function getCategories() {
    try {
        const q = query(collection(db, 'categories'));
        const snapshot = await getDocs(q);
        const categories = snapshot.docs.map(d => ({ id: d.id, ...d.data() }));
        return categories.length > 0 ? categories : DEFAULT_CATEGORIES;
    } catch {
        return DEFAULT_CATEGORIES;
    }
}

/**
 * 소스별 최근 24시간 수집 건수
 */
export async function getSourceCounts24h() {
    const since = Timestamp.fromDate(new Date(Date.now() - 24 * 60 * 60 * 1000));

    const q = query(
        collection(db, 'posts'),
        where('collected_at', '>=', since),
        orderBy('collected_at', 'desc')
    );
    const snapshot = await getDocs(q);

    const counts = {};
    snapshot.docs.forEach(d => {
        const source = d.data().source || 'unknown';
        counts[source] = (counts[source] || 0) + 1;
    });

    return counts;
}

/**
 * 최근 수집 실행 기록
 */
export async function getRecentRuns(limitCount = 10) {
    const q = query(
        collection(db, 'collection_runs'),
        orderBy('started_at', 'desc'),
        limit(limitCount)
    );
    const snapshot = await getDocs(q);
    return snapshot.docs.map(d => ({ id: d.id, ...d.data() }));
}
