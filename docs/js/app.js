/**
 * SNS Tech Briefing - Common UI Utilities (Dark SF Theme)
 */

// Category → badge class mapping
const CATEGORY_BADGE = {
    AI: 'badge-ai',
    Semiconductor: 'badge-semiconductor',
    Cloud: 'badge-cloud',
    BigTech: 'badge-bigtech',
    Startup: 'badge-startup',
    Regulation: 'badge-regulation',
    Coding: 'badge-coding',
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
    return `<span class="badge badge-${escapeHtml(source)}">${escapeHtml(source)}</span>`;
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
                <span class="ml-3 text-sm" style="color: var(--text-muted);">불러오는 중...</span>
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
            <div class="card p-6 text-center">
                <p class="text-sm" style="color: var(--status-error);">${escapeHtml(message)}</p>
                <p class="text-xs mt-2" style="color: var(--text-muted);">Firebase 설정을 확인해주세요.</p>
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
            <div class="card p-8 text-center" style="color: var(--text-muted);">
                ${escapeHtml(message)}
            </div>`;
    }
}

// Importance score badge
function importanceBadge(score) {
    if (!score && score !== 0) return '';
    let cls = 'badge-importance-low';
    if (score >= 0.8) cls = 'badge-importance-high';
    else if (score >= 0.6) cls = 'badge-importance-mid';
    return `<span class="badge ${cls}">${score.toFixed(1)}</span>`;
}

/**
 * Highlight keywords in text with chip styling
 */
function highlightKeywords(text, keywords) {
    if (!text || !keywords?.length) return escapeHtml(text || '');
    let result = escapeHtml(text);
    keywords.forEach((kw, i) => {
        const escaped = escapeHtml(kw);
        const rank = i + 1;
        // case-insensitive replace, wrapping with chip span
        result = result.replace(
            new RegExp(escaped.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'),
            `<span class="kw-pill kw-rank-${rank}">$&</span>`
        );
    });
    return result;
}

/**
 * 게시물 카드 HTML 생성 (플립 없음)
 */
export function renderPostCard(post) {
    const source = escapeHtml(post.source || '');
    const author = escapeHtml(truncate(post.author || '', 20));
    const time = formatTimestamp(post.collected_at, 'short');

    const titleText = truncate(post.summary || post.content_text || '', 120);
    const titleHtml = highlightKeywords(titleText, post.keywords || []);

    const categories = (post.category_names || []).map(cat => {
        const cls = CATEGORY_BADGE[cat] || 'badge-default';
        return `<span class="badge ${cls}">${escapeHtml(cat)}</span>`;
    }).join('');

    const safeUrl = (post.url || '').replace('twitter.com', 'x.com');
    const link = safeUrl
        ? `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer"
               class="post-link text-xs inline-flex items-center gap-1">
               원문 보기
               <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                   <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
               </svg>
           </a>`
        : '';

    return `
    <div class="post-card flex flex-col">
        <div class="px-4 pt-3 pb-2 flex items-center justify-between">
            <span class="badge badge-${source}">${source}</span>
            ${importanceBadge(post.importance_score)}
        </div>
        <div class="flex-1 px-4 py-2">
            <p class="card-inline-title">${titleHtml}</p>
            ${categories ? `<div class="flex flex-wrap gap-1.5 mt-3">${categories}</div>` : ''}
        </div>
        <div class="px-4 py-2.5 card-foot-divider flex items-center justify-between text-xs" style="color: var(--text-muted);">
            <div class="flex items-center gap-3">
                ${link}
                <span class="truncate max-w-[100px]">${author}</span>
            </div>
            <span>${time}</span>
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
                <div class="card-front flex flex-col rounded-xl p-4">
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
