window.EFTForge = window.EFTForge || {};

/* ============================================================
   LEADERBOARD MODULE
   Ranked list of top-rated community builds and attachments.

   Modes:   builds | attachments
   Periods: 2w (last 14 days, default) | all (all time)
   Sort:    likes (default) | dislikes (attachments only)

   Builds are clickable - clicking loads the build into the
   builder exactly like the community builds panel does.
============================================================ */

window.EFTForge.leaderboard = (function () {

    let _mode      = 'builds';
    let _period    = '2w';
    let _sort      = 'likes';
    let _category  = 'all';
    let _gunFilter = 'all';
    const _cache     = {};  // keyed by "mode:period:sort"

    const _clearMarqueeTimers = EFTForge.utils._clearMarqueeTimers;
    const _initMarqueeText    = EFTForge.utils._initMarqueeText;

    /* ===========================
       PUBLIC API
    =========================== */

    function showPanel() {
        const overlay  = document.getElementById('leaderboard-overlay');
        const backdrop = document.getElementById('leaderboard-backdrop');
        if (!overlay) return;

        // reset filter dropdowns to defaults each open
        _period    = '2w';
        _sort      = 'likes';
        _category  = 'all';
        _gunFilter = 'all';

        overlay.classList.add('visible');
        if (backdrop) backdrop.classList.add('visible');
        document.getElementById('main-container')?.setAttribute('inert', '');
        if (document.activeElement) /** @type {HTMLElement} */ (document.activeElement).blur();

        _renderControls();
        _loadData();
    }

    function hidePanel() {
        const overlay  = document.getElementById('leaderboard-overlay');
        const backdrop = document.getElementById('leaderboard-backdrop');
        if (overlay)  overlay.classList.remove('visible');
        if (backdrop) backdrop.classList.remove('visible');
        document.getElementById('main-container')?.removeAttribute('inert');
    }

    function onLangChange() {
        const overlay = document.getElementById('leaderboard-overlay');
        if (!overlay || !overlay.classList.contains('visible')) return;
        _renderControls();
        // re-render cached data in new language
        const key = _cacheKey();
        if (_cache[key]) {
            _renderEntries(_cache[key]);
        }
    }

    /* ===========================
       PRIVATE - CONTROLS
    =========================== */

    function _cacheKey() {
        return _mode + ':' + _period + ':' + _sort;
    }

    function _renderControls() {
        const t = EFTForge.lang.t;

        // update header title
        const title = document.getElementById('leaderboard-header-title');
        if (title) title.textContent = t('lb.title');

        // mode toggle
        const buildsBtn = document.getElementById('lb-mode-builds');
        const attBtn    = document.getElementById('lb-mode-att');
        if (buildsBtn) {
            const infoSpan = document.getElementById('lb-builds-info-btn');
            buildsBtn.textContent = t('lb.builds') + ' ';
            if (infoSpan) buildsBtn.appendChild(infoSpan);
            buildsBtn.classList.toggle('active', _mode === 'builds');
        }
        if (attBtn) {
            attBtn.textContent = t('lb.attachments');
            attBtn.classList.toggle('active', _mode === 'attachments');
        }

        // period dropdown
        _refreshCustomSelect('lb-period-select', [
            { value: '2w',  label: t('lb.period2w') },
            { value: 'all', label: t('lb.periodAll') },
        ], _period);

        // sort + category dropdowns - only visible for attachments
        const isAtt = _mode === 'attachments';
        const sortRow = document.getElementById('lb-sort-row');
        if (sortRow) sortRow.style.display = isAtt ? '' : 'none';

        _refreshCustomSelect('lb-sort-select', [
            { value: 'likes',    label: t('lb.sortLikes') },
            { value: 'dislikes', label: t('lb.sortDislikes') },
        ], _sort);

        const catRow = document.getElementById('lb-category-row');
        if (catRow) catRow.style.display = isAtt ? '' : 'none';

        // build category options from full known slot list
        const _allSlots = [
            'Barrel', 'Muzzle', 'Stock', 'Handguard', 'Scope',
            'Front Sight', 'Rear Sight', 'Pistol Grip', 'Magazine',
            'Gas Block', 'Foregrip', 'Ch. Handle', 'Mount', 'Tactical',
            'Bipod', 'Receiver', 'Ubgl', 'Grip', 'Trigger', 'Hammer',
            'Chamber', 'Catch',
        ];
        const catOptions = [{ value: 'all', label: t('lb.categoryAll') }];
        _allSlots.forEach(function (slot) {
            catOptions.push({ value: slot, label: t('slot.' + slot) || slot });
        });
        // ensure current _category is valid; reset if not
        if (!catOptions.find(function (o) { return o.value === _category; })) {
            _category = 'all';
        }
        _refreshCustomSelect('lb-category-select', catOptions, _category);

        // gun filter row - only visible for builds
        const gunRow = document.getElementById('lb-gun-row');
        if (gunRow) gunRow.style.display = _mode === 'builds' ? '' : 'none';
        _updateGunInputLabel();

        const infoBtn = document.getElementById('lb-builds-info-btn');
        if (infoBtn) infoBtn.dataset.tooltip = t('lb.buildsInfo');

        const capLabel = document.getElementById('lb-cap-label');
        if (capLabel) capLabel.textContent = _getCapLabel();

    }

    function _getCapLabel() {
        const t = EFTForge.lang.t;
        if (_mode === 'builds') {
            return _period === '2w' ? t('lb.top10') : t('lb.top50');
        } else {
            return _period === '2w' ? t('lb.top20') : t('lb.top100');
        }
    }

    /* ===========================
       PRIVATE - DATA LOADING
    =========================== */

    async function _loadData() {
        const key = _cacheKey();
        if (_cache[key]) {
            _renderEntries(_cache[key]);
            return;
        }

        _showLoading();

        try {
            let data;
            if (_mode === 'builds') {
                data = await EFTForge.api.fetchLeaderboardBuilds(_period);
            } else {
                data = await EFTForge.api.fetchLeaderboardAttachments(_period, _sort);
            }
            _cache[key] = data;
            _renderEntries(data);
        } catch (err) {
            console.error('[leaderboard] Load error:', err);
            _showError();
        }
    }

    /* ===========================
       PRIVATE - RENDERING
    =========================== */

    function _showLoading() {
        const body = document.getElementById('leaderboard-body');
        if (!body) return;
        const t = EFTForge.lang.t;
        body.innerHTML = '<div class="lb-loading">' + t('news.loading') + '</div>';
    }

    function _showError() {
        const body = document.getElementById('leaderboard-body');
        if (!body) return;
        const t = EFTForge.lang.t;
        body.innerHTML = '<div class="lb-empty">' + t('lb.loadError') + '</div>';
    }

    function _renderEntries(data) {
        const body = document.getElementById('leaderboard-body');
        if (!body) return;
        _clearMarqueeTimers();

        const t    = EFTForge.lang.t;
        const lang = EFTForge.state && EFTForge.state.lang;

        if (!data || data.length === 0) {
            body.innerHTML = '<div class="lb-empty">' + t('lb.empty') + '</div>';
            return;
        }

        let html = '<ol class="lb-list">';

        if (_mode === 'builds') {
            const allGuns    = (EFTForge.state && EFTForge.state.allGuns) || [];
            const buildData  = _gunFilter === 'all' ? data : data.filter(function (e) { return e.gun_id === _gunFilter; });
            if (buildData.length === 0) {
                body.innerHTML = '<div class="lb-empty">' + t('lb.empty') + '</div>';
                return;
            }
            html += buildData.map(function (entry, idx) {
                let authorName;
                if (entry.is_admin_build) {
                    authorName = lang === 'zh'
                        ? (entry.author_display_name_zh || entry.author_display_name || 'Morph1ne')
                        : (entry.author_display_name || 'Morph1ne');
                } else {
                    authorName = entry.user_display_name || t('modal.anonymousAuthor');
                }
                const rankClass = entry.rank === 1 ? ' lb-rank-gold' : entry.rank === 2 ? ' lb-rank-silver' : entry.rank === 3 ? ' lb-rank-bronze' : '';
                const gunObj    = allGuns.find(function (g) { return g.id === entry.gun_id; });
                const gunName   = (gunObj && gunObj.name) || entry.gun_name || entry.gun_id;
                const imgSrc    = entry.card_image_url || (gunObj && (gunObj.image_512_link || gunObj.icon_link)) || '';
                const imgHtml   = imgSrc
                    ? '<img class="lb-build-img" src="' + _escHtml(imgSrc) + '" alt="" loading="lazy" referrerpolicy="no-referrer">'
                    : '<div class="lb-build-img-placeholder"></div>';
                const avatarSrc = entry.is_admin_build
                    ? (proxyAvatarUrl(entry.author_avatar_url) || './news/images/devProfilePic.jpg')
                    : (proxyAvatarUrl(entry.user_avatar_url)   || './assets/images/tarkovcitizen.jpg');
                const avatarHtml = '<img class="lb-author-avatar" src="' + _escHtml(avatarSrc) + '" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.src=\'./assets/images/tarkovcitizen.jpg\';this.onerror=null;">';
                return (
                    '<li class="lb-entry lb-entry-build" style="--lb-i:' + idx + '" data-idx="' + (_cache[_cacheKey()].indexOf(entry)) + '">' +
                    '<span class="lb-rank' + rankClass + '">' + entry.rank + '</span>' +
                    imgHtml +
                    '<div class="lb-entry-info">' +
                    '<div class="lb-build-name"><span class="marquee-text">' + _escHtml(entry.build_name) + '</span></div>' +
                    '<div class="lb-build-meta">' + _escHtml(gunName) + '</div>' +
                    '<div class="lb-build-author">' + avatarHtml + '<span>' + _escHtml(authorName) + '</span></div>' +
                    '</div>' +
                    '<div class="lb-vote-count"><img src="./assets/images/icon-fir.png" class="lb-vote-icon lb-vote-icon-like"> ' + entry.like_count + '</div>' +
                    '</li>'
                );
            }).join('');
        } else {
            const voteKey       = _sort === 'likes' ? 'like_count' : 'dislike_count';
            const voteIcon      = _sort === 'likes' ? './assets/images/icon-fir.png' : './assets/images/Battlestate Games.svg';
            const voteIconClass = _sort === 'likes' ? 'lb-vote-icon lb-vote-icon-like' : 'lb-vote-icon lb-vote-icon-dislike';
            const filtered = _category === 'all' ? data : data.filter(function (e) { return e.item_category === _category; });
            if (filtered.length === 0) {
                body.innerHTML = '<div class="lb-empty">' + t('lb.empty') + '</div>';
                return;
            }
            html += filtered.map(function (entry, idx) {
                const name = (lang === 'zh' && entry.item_name_zh) ? entry.item_name_zh : (entry.item_name || entry.item_id);
                const rankClass = entry.rank === 1 ? ' lb-rank-gold' : entry.rank === 2 ? ' lb-rank-silver' : entry.rank === 3 ? ' lb-rank-bronze' : '';
                const iconHtml = entry.icon_link
                    ? '<img class="lb-att-icon" src="' + _escHtml(entry.icon_link) + '" alt="" loading="lazy">'
                    : '<div class="lb-att-icon-placeholder"></div>';
                return (
                    '<li class="lb-entry lb-entry-att" style="--lb-i:' + idx + '">' +
                    '<span class="lb-rank' + rankClass + '">' + entry.rank + '</span>' +
                    iconHtml +
                    '<div class="lb-entry-info">' +
                    '<div class="lb-att-name"><span class="marquee-text">' + _escHtml(name) + '</span></div>' +
                    '</div>' +
                    '<div class="lb-vote-count"><img src="' + voteIcon + '" class="' + voteIconClass + '"> ' + entry[voteKey] + '</div>' +
                    '</li>'
                );
            }).join('');

        }

        html += '</ol>';
        body.innerHTML = html;

        _initMarqueeText(body, { hoverOnly: true, hoverTarget: 'li' });

        // attach click handlers for build entries
        if (_mode === 'builds') {
            /** @type {NodeListOf<HTMLElement>} */ (body.querySelectorAll('.lb-entry-build')).forEach(function (el) {
                el.addEventListener('click', function () {
                    const idx = parseInt(el.dataset.idx, 10);
                    const entry = (_cache[_cacheKey()] || [])[idx];
                    if (entry) _loadBuild(entry);
                });
            });
        }
    }

    async function _loadBuild(build) {
        if (!build || !build.pairs) return;

        const t    = EFTForge.lang.t;
        const lang = EFTForge.state && EFTForge.state.lang;
        let authorName, avatarUrl;
        if (build.is_admin_build) {
            authorName = lang === 'zh'
                ? (build.author_display_name_zh || build.author_display_name || 'Morph1ne')
                : (build.author_display_name || 'Morph1ne');
            avatarUrl = proxyAvatarUrl(build.author_avatar_url) || './news/images/devProfilePic.jpg';
        } else {
            authorName = build.user_display_name || t('modal.anonymousAuthor');
            avatarUrl  = proxyAvatarUrl(build.user_avatar_url)  || null;
        }

        const communityBuildInfo = {
            pairsKey:     _pairsKey(build.pairs),
            authorName:   authorName,
            avatarUrl:    avatarUrl,
            buildName:    build.build_name,
            cardImageUrl: build.card_image_url || null,
        };

        hidePanel();

        EFTForge.api.recordBuildLoad(build.build_id);

        const payload = { g: build.gun_id, p: build.pairs, a: build.ammo_id || null };
        if (document.body.dataset.mobile !== 'true') {
            await EFTForge.tabs.createTabFromPayload(payload, build.build_name, communityBuildInfo, false);
        } else {
            await loadBuildFromPayload(payload, build.build_name);
            // set after loadBuildFromPayload (which clears communityBuild internally)
            EFTForge.state.communityBuild = communityBuildInfo;
            syncBuildDisplayName();
        }
    }

    /* ===========================
       PRIVATE - TOGGLE HANDLERS
    =========================== */

    function _setMode(mode) {
        if (_mode === mode) return;
        _mode      = mode;
        _gunFilter = 'all';
        if (_mode === 'builds') _sort = 'likes';  // builds have no dislike sort
        _renderControls();
        _loadData();
    }

    function _setPeriod(period) {
        if (_period === period) return;
        _period = period;
        _renderControls();
        _loadData();
    }

    function _setSort(sort) {
        if (_sort === sort) return;
        _sort = sort;
        _category = 'all';  // reset category when sort changes (new data fetched)
        _renderControls();
        _loadData();
    }

    function _setGunFilter(gunId) {
        if (_gunFilter === gunId) return;
        _gunFilter = gunId;
        _updateGunInputLabel();
        const cached = _cache[_cacheKey()];
        if (cached) _renderEntries(cached);
    }

    function _updateGunInputLabel() {
        const input = /** @type {HTMLInputElement | null} */ (document.getElementById('lb-gun-input'));
        if (!input) return;
        const t = EFTForge.lang.t;
        if (_gunFilter === 'all') {
            input.value = '';
            input.placeholder = t('lb.gunFilterAll');
        } else {
            const allGuns = (EFTForge.state && EFTForge.state.allGuns) || [];
            const gun = allGuns.find(function (g) { return g.id === _gunFilter; });
            input.value = gun ? gun.name : _gunFilter;
            input.placeholder = '';
        }
    }

    function _setCategory(cat) {
        if (_category === cat) return;
        _category = cat;
        const cached = _cache[_cacheKey()];
        if (cached) _renderEntries(cached);
    }

    /* ===========================
       PRIVATE - HELPERS
    =========================== */

    function _initGunSelect() {
        const input    = /** @type {HTMLInputElement | null} */ (document.getElementById('lb-gun-input'));
        const dropdown = document.getElementById('lb-gun-dropdown');
        if (!input || !dropdown) return;

        function _buildList(query) {
            const t       = EFTForge.lang.t;
            const allGuns = (EFTForge.state && EFTForge.state.allGuns) || [];
            const q       = query.trim().toLowerCase();
            dropdown.innerHTML = '';

            // "All" option
            const allOpt = document.createElement('div');
            allOpt.className = 'lb-gun-option' + (_gunFilter === 'all' ? ' selected' : '');
            allOpt.textContent = t('lb.gunFilterAll');
            allOpt.addEventListener('mousedown', function (e) {
                e.preventDefault();
                _setGunFilter('all');
                dropdown.classList.remove('open');
            });
            dropdown.appendChild(allOpt);

            const filtered = q
                ? allGuns.filter(function (g) { return g.name.toLowerCase().indexOf(q) !== -1; })
                : allGuns;

            filtered.forEach(function (gun) {
                const opt = document.createElement('div');
                opt.className = 'lb-gun-option' + (_gunFilter === gun.id ? ' selected' : '');
                opt.textContent = gun.name;
                opt.addEventListener('mousedown', function (e) {
                    e.preventDefault();
                    _setGunFilter(gun.id);
                    dropdown.classList.remove('open');
                    input.blur();
                });
                dropdown.appendChild(opt);
            });
        }

        input.addEventListener('focus', function () {
            _buildList('');
            dropdown.classList.add('open');
        });

        input.addEventListener('input', function () {
            _buildList(input.value);
            dropdown.classList.add('open');
        });

        input.addEventListener('blur', function () {
            // slight delay so mousedown on options fires first
            setTimeout(function () { dropdown.classList.remove('open'); }, 150);
            // restore display label on blur
            _updateGunInputLabel();
        });

        // clear input text when focused so user can type a new search
        input.addEventListener('focus', function () {
            if (_gunFilter !== 'all') input.value = '';
        });
    }

    function _refreshCustomSelect(id, options, currentValue) {
        const sel = /** @type {HTMLSelectElement | null} */ (document.getElementById(id));
        if (!sel) return;
        // update option labels (triggers MutationObserver in setupCustomSelect)
        const existing = document.getElementById(id + '-custom');
        if (existing) existing.remove();
        while (sel.options.length) sel.remove(0);
        options.forEach(function (opt) {
            const o = document.createElement('option');
            o.value = opt.value;
            o.textContent = opt.label;
            if (opt.value === currentValue) o.selected = true;
            sel.appendChild(o);
        });
        setupCustomSelect(id);
    }

    function _escHtml(str) {
        if (str == null) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // mirror of _pairsKey in build-manager.js - must produce identical output
    function _pairsKey(pairs) {
        if (!pairs || !pairs.length) return '';
        return pairs.map(function (p) { return p[0] + ':' + p[1]; }).sort().join(',');
    }

    /* ===========================
       SELF-INIT
    =========================== */

    function _init() {
        // wire up mode toggle buttons
        const modeButtons = [
            { id: 'lb-mode-builds', fn: function () { _setMode('builds'); } },
            { id: 'lb-mode-att',    fn: function () { _setMode('attachments'); } },
        ];
        modeButtons.forEach(function (item) {
            const el = document.getElementById(item.id);
            if (el) el.addEventListener('click', item.fn);
        });

        _initGunSelect();

        // wire up period and sort dropdowns
        const periodSel = document.getElementById('lb-period-select');
        if (periodSel) periodSel.addEventListener('change', function (e) { _setPeriod(/** @type {HTMLSelectElement} */ (e.target).value); });

        const sortSel = document.getElementById('lb-sort-select');
        if (sortSel) sortSel.addEventListener('change', function (e) { _setSort(/** @type {HTMLSelectElement} */ (e.target).value); });

        const catSel = document.getElementById('lb-category-select');
        if (catSel) catSel.addEventListener('change', function (e) { _setCategory(/** @type {HTMLSelectElement} */ (e.target).value); });

        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Escape') return;
            const overlay = document.getElementById('leaderboard-overlay');
            if (!overlay || !overlay.classList.contains('visible')) return;
            e.stopPropagation();
            hidePanel();
        }, true);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _init);
    } else {
        _init();
    }

    return { showPanel, hidePanel, onLangChange };

})();
