/**
 * SNS Tech Briefing - Common UI Utilities
 */

// Source badge color mapping
const SOURCE_COLORS = {
    twitter: 'bg-blue-100 text-blue-700',
    threads: 'bg-purple-100 text-purple-700',
    linkedin: 'bg-sky-100 text-sky-700',
    dcinside: 'bg-gray-100 text-gray-700',
};

/**
 * Firestore Timestamp → 포맷된 문자열
 */
export function formatTimestamp(ts, format = 'datetime') {
    if (!ts) return '';
    const date = ts.toDate ? ts.toDate() : new Date(ts);

    const pad = n => String(n).padStart(2, '0');
    const y = date.getFullYear();
    const m = pad(date.getMonth() + 1);
    const d = pad(date.getDate());
    const h = pad(date.getHours());
    const min = pad(date.getMinutes());

    if (format === 'date') return `${y}-${m}-${d}`;
    if (format === 'time') return `${h}:${min}`;
    if (format === 'short') return `${m}-${d} ${h}:${min}`;
    return `${y}-${m}-${d} ${h}:${min}`;
}

/**
 * 소스 배지 HTML 생성
 */
export function sourceBadge(source) {
    const color = SOURCE_COLORS[source] || 'bg-gray-100 text-gray-700';
    return `<span class="text-xs px-2 py-0.5 rounded ${color}">${escapeHtml(source)}</span>`;
}

/**
 * 중요도 → 별점 표시
 */
export function importanceStars(score) {
    if (!score && score !== 0) return '';
    if (score >= 0.9) return '★★★★★';
    if (score >= 0.7) return '★★★★☆';
    if (score >= 0.5) return '★★★☆☆';
    if (score >= 0.3) return '★★☆☆☆';
    return '★☆☆☆☆';
}

/**
 * HTML 이스케이프
 */
export function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 텍스트 자르기
 */
export function truncate(text, maxLen) {
    if (!text) return '';
    return text.length > maxLen ? text.slice(0, maxLen) + '...' : text;
}

/**
 * 로딩 스피너 표시
 */
export function showLoading(containerId) {
    const el = document.getElementById(containerId);
    if (el) {
        el.innerHTML = `
            <div class="flex justify-center items-center py-12">
                <div class="spinner"></div>
                <span class="ml-3 text-gray-400 text-sm">불러오는 중...</span>
            </div>`;
    }
}

/**
 * 에러 메시지 표시
 */
export function showError(containerId, message) {
    const el = document.getElementById(containerId);
    if (el) {
        el.innerHTML = `
            <div class="bg-red-50 border border-red-200 rounded-lg p-6 text-center">
                <p class="text-red-600 text-sm">${escapeHtml(message)}</p>
                <p class="text-red-400 text-xs mt-2">Firebase 설정을 확인해주세요.</p>
            </div>`;
    }
}

/**
 * 빈 상태 메시지 표시
 */
export function showEmpty(containerId, message) {
    const el = document.getElementById(containerId);
    if (el) {
        el.innerHTML = `
            <div class="bg-white rounded-lg shadow p-8 text-center text-gray-400">
                ${escapeHtml(message)}
            </div>`;
    }
}

// Category → color mapping
const CATEGORY_COLORS = {
    AI: 'bg-violet-100 text-violet-700',
    Semiconductor: 'bg-emerald-100 text-emerald-700',
    Cloud: 'bg-cyan-100 text-cyan-700',
    BigTech: 'bg-blue-100 text-blue-700',
    Startup: 'bg-pink-100 text-pink-700',
    Regulation: 'bg-red-100 text-red-700',
    Coding: 'bg-amber-100 text-amber-700',
};

// Keyword → color by position
const KW_STYLES = [
    'bg-blue-100 text-blue-800 text-base',
    'bg-indigo-100 text-indigo-700 text-sm',
    'bg-violet-100 text-violet-700 text-sm',
    'bg-slate-100 text-slate-600 text-xs',
    'bg-gray-100 text-gray-600 text-xs',
];

// Importance score badge color
function importanceBadge(score) {
    if (!score && score !== 0) return '';
    let color = 'bg-gray-50 text-gray-500';
    if (score >= 0.8) color = 'bg-red-50 text-red-600';
    else if (score >= 0.6) color = 'bg-amber-50 text-amber-600';
    return `<span class="text-xs font-bold px-2 py-0.5 rounded-full ${color}">${score.toFixed(1)}</span>`;
}

/**
 * 게시물 플립 카드 HTML 생성
 */
export function renderPostCard(post) {
    const source = escapeHtml(post.source || '');
    const sourceColor = SOURCE_COLORS[post.source] || 'bg-gray-100 text-gray-700';
    const author = escapeHtml(truncate(post.author || '', 20));
    const time = formatTimestamp(post.collected_at, 'short');

    // 앞면: 키워드
    const keywords = (post.keywords || []).slice(0, 5).map((kw, i) =>
        `<span class="font-semibold px-3 py-1.5 rounded-lg ${KW_STYLES[i] || KW_STYLES[4]}">${escapeHtml(kw)}</span>`
    ).join('');
    const kwSection = keywords
        ? keywords
        : '<p class="text-sm text-gray-400 italic">키워드 없음</p>';

    // 뒷면: 카테고리
    const categories = (post.category_names || []).map(cat => {
        const cc = CATEGORY_COLORS[cat] || 'bg-gray-100 text-gray-600';
        return `<span class="text-[11px] px-2 py-0.5 rounded-full ${cc}">${escapeHtml(cat)}</span>`;
    }).join('');

    // 뒷면: 본문
    const body = post.summary
        ? escapeHtml(post.summary)
        : escapeHtml(truncate(post.content_text, 300));

    // 뒷면: 원문 링크
    const link = post.url
        ? `<a href="${escapeHtml(post.url)}" target="_blank" rel="noopener"
               onclick="event.stopPropagation()"
               class="inline-flex items-center gap-1 text-xs font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 px-3 py-1.5 rounded-lg transition-colors">
               원문 보기
               <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                   <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
               </svg>
           </a>`
        : '';

    return `
    <div class="card-container cursor-pointer" onclick="this.querySelector('.card-inner').classList.toggle('flipped')">
        <div class="card-inner">
            <!-- 앞면 -->
            <div class="card-front bg-white shadow-sm border border-gray-100 flex flex-col">
                <div class="px-4 pt-3 pb-2 flex items-center justify-between">
                    <span class="text-xs font-medium px-2 py-0.5 rounded-full ${sourceColor}">${source}</span>
                    ${importanceBadge(post.importance_score)}
                </div>
                <div class="flex-1 px-4 py-3 flex flex-wrap content-center gap-2 justify-center">
                    ${kwSection}
                </div>
                <div class="px-4 py-2 border-t border-gray-50 flex items-center justify-between text-xs text-gray-400">
                    <span class="truncate max-w-[120px]">${author}</span>
                    <span class="flex items-center gap-1">
                        ${time}
                        <svg class="w-3.5 h-3.5 ml-1 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                        </svg>
                    </span>
                </div>
            </div>
            <!-- 뒷면 -->
            <div class="card-back bg-white shadow-sm border border-gray-100 flex flex-col">
                ${categories ? `<div class="px-4 pt-3 pb-1 flex gap-1.5 flex-wrap">${categories}</div>` : ''}
                <div class="flex-1 px-4 py-2 overflow-y-auto">
                    <p class="text-sm text-gray-800 leading-relaxed">${body}</p>
                </div>
                <div class="px-4 py-2.5 border-t border-gray-50 flex items-center justify-between">
                    <span class="text-xs text-gray-400 truncate max-w-[120px]">${author}</span>
                    ${link}
                </div>
            </div>
        </div>
    </div>`;
}

/**
 * 스켈레톤 카드 (로딩 플레이스홀더)
 */
export function skeletonCards(count = 6) {
    return Array.from({ length: count }, () => `
        <div class="card-container">
            <div class="card-inner">
                <div class="card-front bg-white shadow-sm border border-gray-100 flex flex-col rounded-xl p-4">
                    <div class="flex items-center justify-between mb-4">
                        <div class="skeleton w-16 h-5"></div>
                        <div class="skeleton w-10 h-5"></div>
                    </div>
                    <div class="flex-1 flex flex-wrap content-center gap-2 justify-center">
                        <div class="skeleton w-20 h-7 rounded-lg"></div>
                        <div class="skeleton w-16 h-6 rounded-lg"></div>
                        <div class="skeleton w-24 h-6 rounded-lg"></div>
                    </div>
                    <div class="flex items-center justify-between mt-4">
                        <div class="skeleton w-20 h-3"></div>
                        <div class="skeleton w-16 h-3"></div>
                    </div>
                </div>
            </div>
        </div>
    `).join('');
}

/**
 * 현재 페이지에 맞는 네비게이션 링크 활성화
 */
export function setActiveNav() {
    const path = window.location.pathname;
    document.querySelectorAll('[data-nav]').forEach(el => {
        const href = el.getAttribute('href');
        if (path.endsWith(href) || (href === 'index.html' && (path.endsWith('/') || path.endsWith('/docs/')))) {
            el.classList.add('nav-active');
        }
    });
}
