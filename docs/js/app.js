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

/**
 * 게시물 카드 HTML 생성
 */
export function renderPostCard(post) {
    const badge = sourceBadge(post.source);
    const author = escapeHtml(truncate(post.author || '', 30));
    const stars = post.importance_score
        ? `<span class="text-xs text-yellow-600">${importanceStars(post.importance_score)}</span>`
        : '';
    const time = formatTimestamp(post.collected_at, 'short');

    let contentHtml;
    if (post.summary) {
        contentHtml = `
            <p class="text-sm text-gray-800 mb-1">${escapeHtml(post.summary)}</p>
            <details class="text-xs text-gray-500">
                <summary class="cursor-pointer hover:text-gray-700">원문 보기</summary>
                <p class="mt-1 whitespace-pre-wrap">${escapeHtml(truncate(post.content_text, 500))}</p>
            </details>`;
    } else {
        contentHtml = `<p class="text-sm text-gray-700">${escapeHtml(truncate(post.content_text, 300))}</p>`;
    }

    const categories = (post.category_names || []).map(cat =>
        `<span class="text-xs px-1.5 py-0.5 bg-gray-100 rounded text-gray-600">${escapeHtml(cat)}</span>`
    ).join('');

    const link = post.url
        ? `<a href="${escapeHtml(post.url)}" target="_blank" rel="noopener" class="text-xs text-blue-500 hover:text-blue-700 mt-2 inline-block">원문 링크 →</a>`
        : '';

    return `
    <div class="bg-white rounded-lg shadow p-4 card-hover">
        <div class="flex items-center gap-2 mb-2">
            ${badge}
            <span class="text-sm font-medium">${author}</span>
            ${stars}
            <span class="text-xs text-gray-400 ml-auto">${time}</span>
        </div>
        ${contentHtml}
        ${categories ? `<div class="flex gap-1 mt-2 flex-wrap">${categories}</div>` : ''}
        ${link}
    </div>`;
}

/**
 * 스켈레톤 카드 (로딩 플레이스홀더)
 */
export function skeletonCards(count = 3) {
    return Array.from({ length: count }, () => `
        <div class="bg-white rounded-lg shadow p-4">
            <div class="flex items-center gap-2 mb-3">
                <div class="skeleton w-16 h-5"></div>
                <div class="skeleton w-24 h-4"></div>
            </div>
            <div class="skeleton w-full h-4 mb-2"></div>
            <div class="skeleton w-3/4 h-4"></div>
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
