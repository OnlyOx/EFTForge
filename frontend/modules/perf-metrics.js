window.EFTForge = window.EFTForge || {};

/* ============================================================
   PERFORMANCE METRICS
   Shared by the desktop settings modal (desktop-settings.js) and the
   About dialog on the website (showAboutDialog in app.js) - both call
   mount(el) when their Performance section becomes visible and unmount()
   when the modal closes, so the 1s poll only ever runs while something
   is on screen to show it.

   Desktop: real OS-level process memory/CPU via the get_perf_metrics Tauri
   command (desktop/src-tauri/src/main.rs, sysinfo-backed) - covers both
   this webview process and the eftforge-backend sidecar.

   Website: no OS process to query, so this falls back to what the browser
   exposes - performance.memory (Chromium-only; the JS heap, not real
   system memory) and a rAF-measured FPS counter, which works everywhere.
============================================================ */
(function () {
    const _isDesktop = !!(window.EFTForge.config && window.EFTForge.config.IS_DESKTOP) && !!window.__TAURI__;

    // Page-load time, not modal-open time - "uptime" should track the whole
    // session, not reset every time the settings modal is reopened.
    const _sessionStartMs = Date.now();

    let _pollTimer = null;
    let _fpsRaf = null;
    let _fpsFrames = 0;
    let _fpsWindowStart = 0;
    let _lastFps = 0;
    let _observer = null;

    function _fmtMb(mb) {
        return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`;
    }

    function _fmtUptime(totalSecs) {
        const secs = Math.floor(totalSecs);
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        const s = secs % 60;
        if (h > 0) return `${h}h ${m}m ${s}s`;
        if (m > 0) return `${m}m ${s}s`;
        return `${s}s`;
    }

    // Approximate - actual on-disk/engine storage overhead varies, but UTF-16
    // char count x2 bytes is close enough to size relative to localStorage's
    // ~5-10MB per-origin quota, which is all this is meant to warn about.
    function _localStorageBytes() {
        try {
            let bytes = 0;
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                bytes += (key.length + (localStorage.getItem(key) || "").length) * 2;
            }
            return bytes;
        } catch {
            return null;
        }
    }

    function _fmtBytes(bytes) {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / 1048576).toFixed(2)} MB`;
    }

    function _rowsHtml(rows) {
        return rows.map(([label, value]) => `
            <div class="hidden-stat-row">
                <span class="hidden-stat-label">${label}</span>
                <span class="hidden-stat-value">${value}</span>
            </div>`).join("");
    }

    // Rows meaningful in both environments - plain browser-side signals, no
    // Tauri/OS access needed.
    function _commonRows() {
        const rows = [
            [t("perf.domNodes"), String(document.getElementsByTagName("*").length)],
            [t("perf.uptime"), _fmtUptime((Date.now() - _sessionStartMs) / 1000)],
        ];
        const lsBytes = _localStorageBytes();
        if (lsBytes !== null) rows.push([t("perf.localStorage"), _fmtBytes(lsBytes)]);
        return rows;
    }

    async function _renderDesktop(el) {
        try {
            const m = await window.__TAURI__.core.invoke("get_perf_metrics");
            const rows = [
                [t("perf.appMem"), _fmtMb(m.app.mem_mb)],
                [t("perf.appCpu"), `${m.app.cpu_pct.toFixed(1)}%`],
            ];
            if (m.backend) {
                rows.push([t("perf.backendMem"), _fmtMb(m.backend.mem_mb)]);
                rows.push([t("perf.backendCpu"), `${m.backend.cpu_pct.toFixed(1)}%`]);
                rows.push([t("perf.backendUptime"), _fmtUptime(m.backend.uptime_secs)]);
            } else {
                rows.push([t("perf.backendMem"), t("perf.backendNotRunning")]);
            }
            rows.push([t("perf.sysMem"), `${_fmtMb(m.sys_used_mem_mb)} / ${_fmtMb(m.sys_total_mem_mb)}`]);
            rows.push(..._commonRows());
            el.innerHTML = _rowsHtml(rows);
        } catch (e) {
            el.innerHTML = `<div style="color:#f44336; font-size:12px;">${escapeHtml(String(e))}</div>`;
        }
    }

    function _renderWebsite(el) {
        const rows = [[t("perf.fps"), String(_lastFps)]];
        const mem = /** @type {any} */ (performance).memory; // Chromium only
        if (mem) {
            rows.push([t("perf.jsHeapUsed"), _fmtMb(mem.usedJSHeapSize / 1048576)]);
            rows.push([t("perf.jsHeapLimit"), _fmtMb(mem.jsHeapSizeLimit / 1048576)]);
        }
        rows.push(..._commonRows());
        el.innerHTML = _rowsHtml(rows);
        if (!mem) {
            el.innerHTML += `<div style="font-size:11px; color:#666; margin-top:6px; line-height:1.5;">${t("perf.memUnsupported")}</div>`;
        }
    }

    function _fpsTick(now) {
        _fpsFrames++;
        if (now - _fpsWindowStart >= 1000) {
            _lastFps = Math.round((_fpsFrames * 1000) / (now - _fpsWindowStart));
            _fpsFrames = 0;
            _fpsWindowStart = now;
        }
        _fpsRaf = requestAnimationFrame(_fpsTick);
    }

    /* Starts the 1s refresh loop, rendering into el immediately and on
       every tick after. Safe to call again without a matching unmount() -
       it tears down any previous loop first.

       watchEl, if given, is watched for removal from the document (a modal
       overlay element) - once it's gone the loop stops itself, so callers
       don't need their own close/backdrop/Escape handling just to avoid
       polling forever behind a closed modal. */
    function mount(el, watchEl) {
        unmount();
        if (_isDesktop) {
            _renderDesktop(el);
            _pollTimer = setInterval(() => _renderDesktop(el), 1000);
        } else {
            _fpsFrames = 0;
            _fpsWindowStart = performance.now();
            _fpsRaf = requestAnimationFrame(_fpsTick);
            _renderWebsite(el);
            _pollTimer = setInterval(() => _renderWebsite(el), 1000);
        }
        if (watchEl) {
            _observer = new MutationObserver(() => {
                if (!watchEl.isConnected) unmount();
            });
            _observer.observe(document.body, { childList: true });
        }
    }

    function unmount() {
        if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
        if (_fpsRaf) { cancelAnimationFrame(_fpsRaf); _fpsRaf = null; }
        if (_observer) { _observer.disconnect(); _observer = null; }
    }

    window.EFTForge.perfMetrics = { mount, unmount };
})();
