import {
    collection, query, where, orderBy, limit, startAfter,
    getDocs, getDoc, doc, Timestamp
} from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js';
import { db } from './firebase-config.js';
import { API_BASE_URL } from './config.js';

/**
 * 최근 게시물 조회 (REST API)
 * @param {Object} opts - { source?, category?, searchQuery?, limitCount?, lastDoc? }
 * @returns {Promise<{ posts: Array, lastDoc: number|null }>}
 */
export async function getRecentPosts(opts = {}) {
    const { source, category, searchQuery, limitCount = 50, lastDoc: offset = 0 } = opts;

    const params = new URLSearchParams();
    if (source) params.set('source', source);
    if (category) params.set('category', category);
    if (searchQuery) params.set('q', searchQuery);
    params.set('limit', limitCount);
    params.set('offset', typeof offset === 'number' ? offset : 0);

    const res = await fetch(`${API_BASE_URL}/posts/search?${params}`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const posts = await res.json();

    const currentOffset = typeof offset === 'number' ? offset : 0;
    const nextOffset = currentOffset + posts.length;
    const hasMore = posts.length >= limitCount;

    return {
        posts,
        lastDoc: hasMore ? nextOffset : null,
    };
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
        const res = await fetch(`${API_BASE_URL}/categories`);
        if (!res.ok) throw new Error();
        return await res.json();
    } catch {
        return DEFAULT_CATEGORIES;
    }
}

/**
 * 소스별 최근 24시간 수집 건수 (REST API)
 */
export async function getSourceCounts24h() {
    const res = await fetch(`${API_BASE_URL}/stats`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    return data.source_counts_24h || {};
}

/**
 * 최근 수집 실행 기록 (REST API)
 */
export async function getRecentRuns(limitCount = 10) {
    const res = await fetch(`${API_BASE_URL}/stats`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = await res.json();
    return (data.recent_runs || []).slice(0, limitCount);
}
