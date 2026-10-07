// @ts-check
/* ============================================================
   DESKTOP APP WINDOW CONTROLS
   Only active inside the packaged desktop app (see desktop-settings.js for
   the same self-exit convention). The window has no native title bar (see
   decorations(false) in desktop/src-tauri/src/main.rs) - the site's own
   header bar plays that role instead:
     - the whole header is draggable, including on top of the logo, the
       Tarkov clock and the nav buttons - see _initDragAnywhere() below
     - a minimize / maximize / close button group is appended to the right
       of the header, sized/positioned via the "has-window-controls" CSS in
       index.html. Unlike the nav buttons, these never fade or collapse
       with the header's expand/collapse animation.
============================================================ */
(function () {
    if (!window.__EFTFORGE_DESKTOP__ || !window.__TAURI__) return;

    const appWindow = window.__TAURI__.window.getCurrentWindow();

    // ---------------------------------------------------------------
    // Drag anywhere in the header, including on top of interactive
    // children (logo, Tarkov clock, nav buttons) - not just the empty
    // background data-tauri-drag-region already covers.
    //
    // data-tauri-drag-region can't just be added to those elements too:
    // Tauri starts the native OS window-move loop the instant it sees
    // mousedown on an attributed element, with no movement threshold, which
    // would swallow every plain click on them, not just actual drags. So
    // this reimplements the same "drag vs. click" disambiguation manually -
    // track mousedown, and only call startDragging() once the mouse has
    // actually moved past a small threshold. A plain click (mouseup with no
    // real movement) is never touched, so the browser fires it normally.
    // Once startDragging() does fire mid-gesture, the OS's native move loop
    // takes over the mouse for the rest of the gesture exactly like it does
    // for the existing data-tauri-drag-region background - the browser
    // never sees a mouseup/click for it, so nothing else needs to
    // suppress one.
    // ---------------------------------------------------------------
    function _initDragAnywhere(header) {
        const DRAG_THRESHOLD = 4; // px of movement before a press counts as a drag
        let start = null;

        header.addEventListener("mousedown", (e) => {
            if (e.button !== 0) return;
            // Caption buttons and native form controls (the language <select>)
            // keep their own plain click/interaction behavior untouched.
            if (e.target.closest(".window-controls, select, input, textarea")) return;
            // The empty background already has its own native drag region -
            // let Tauri handle that mousedown itself rather than racing it.
            if (e.target.hasAttribute("data-tauri-drag-region")) return;
            start = { x: e.clientX, y: e.clientY };
        });

        window.addEventListener("mousemove", (e) => {
            if (!start) return;
            if (Math.hypot(e.clientX - start.x, e.clientY - start.y) < DRAG_THRESHOLD) return;
            start = null;
            appWindow.startDragging();
        });

        window.addEventListener("mouseup", () => { start = null; });
    }

    const MAXIMIZE_ICON =
        '<svg width="10" height="10" viewBox="0 0 10 10"><rect x="1.5" y="1.5" width="7" height="7" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>';
    const RESTORE_ICON =
        '<svg width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="2.5" width="6.5" height="6.5" fill="none" stroke="currentColor" stroke-width="1.1"/><path d="M2.8 2.5V1h6.5v6.5H8" fill="none" stroke="currentColor" stroke-width="1.1"/></svg>';

    function _init() {
        const header = document.querySelector("header");
        if (!header) return;

        header.setAttribute("data-tauri-drag-region", "");
        const content = header.querySelector(".header-content");
        if (content) content.setAttribute("data-tauri-drag-region", "");
        header.classList.add("has-window-controls");
        _initDragAnywhere(header);

        const controls = document.createElement("div");
        controls.className = "window-controls";
        controls.innerHTML = `
            <button class="window-btn" id="win-minimize-btn" aria-label="${t("dt.winMinimize")}">
                <svg width="10" height="10" viewBox="0 0 10 10"><line x1="1" y1="5" x2="9" y2="5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>
            </button>
            <button class="window-btn" id="win-maximize-btn" aria-label="${t("dt.winMaximize")}">${MAXIMIZE_ICON}</button>
            <button class="window-btn window-btn-close" id="win-close-btn" aria-label="${t("dt.winClose")}">
                <svg width="10" height="10" viewBox="0 0 10 10"><line x1="1" y1="1" x2="9" y2="9" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><line x1="9" y1="1" x2="1" y2="9" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>
            </button>
        `;
        header.appendChild(controls);

        const maximizeBtn = controls.querySelector("#win-maximize-btn");

        // No title attribute on any of these buttons - deliberate, the icons
        // don't need a hover tooltip on top of them.
        async function syncMaximizeIcon() {
            const maximized = await appWindow.isMaximized();
            maximizeBtn.innerHTML = maximized ? RESTORE_ICON : MAXIMIZE_ICON;
            maximizeBtn.setAttribute("aria-label", maximized ? t("dt.winRestore") : t("dt.winMaximize"));
        }

        controls.querySelector("#win-minimize-btn").addEventListener("click", () => appWindow.minimize());
        maximizeBtn.addEventListener("click", () => appWindow.toggleMaximize());
        controls.querySelector("#win-close-btn").addEventListener("click", () => appWindow.close());

        // Catches every path that changes the maximized state - the button
        // above, double-clicking the drag region, and Windows edge/Win+arrow
        // snapping.
        appWindow.onResized(syncMaximizeIcon);
        syncMaximizeIcon();

        _initCloseChoice();
    }

    // ---------------------------------------------------------------
    // Close behavior: minimize to tray vs. exit completely.
    // The close button above (and Alt+F4/taskbar close) always just asks
    // Rust to close the window - main.rs's CloseRequested handler is what
    // actually decides what happens. With no remembered choice yet (the
    // data/settings.json close_action default, "ask") it holds the close
    // and emits this event instead of closing, so this modal can ask the
    // user and call back through close_to_tray/exit_app. A remembered
    // choice ("tray"/"exit") skips this entirely and acts immediately on
    // the Rust side - this listener simply never fires that case.
    // ---------------------------------------------------------------
    function _initCloseChoice() {
        window.__TAURI__.event.listen("close-requested", _showCloseChoiceModal);
    }

    function _showCloseChoiceModal() {
        if (document.getElementById("close-choice-modal")) return;

        const overlay = _createModalOverlay("close-choice-modal", t("dt.closeTitle"), { maxWidth: "380px", closeOnBackdrop: false });
        if (!overlay) return;

        const body = document.getElementById("close-choice-modal-body");
        body.innerHTML = `
            <div style="font-size:13px; color:#bbb; line-height:1.55;">${t("dt.closeBody")}</div>
            <label style="display:flex; gap:8px; align-items:center; margin-top:12px; cursor:pointer; font-size:12px; color:#999;">
                <input type="checkbox" id="close-choice-remember" style="accent-color:#9a8866; cursor:pointer;" />
                ${t("dt.closeRemember")}
            </label>
            <div style="display:flex; gap:8px; margin-top:14px;">
                <button class="modal-btn full-width" id="close-choice-tray">${t("dt.closeTray")}</button>
                <button class="modal-btn primary full-width" id="close-choice-exit">${t("dt.closeExit")}</button>
            </div>
        `;

        const remember = () => /** @type {HTMLInputElement} */ (document.getElementById("close-choice-remember")).checked;

        // "remember" must finish writing to settings.json *before* telling
        // Rust to act - exit_app tears down this same local backend process,
        // and if that happens before the fetch()'s response comes back, the
        // write never lands: the choice silently fails to stick and the next
        // launch asks again no matter how many times "remember" was checked.
        body.querySelector("#close-choice-tray").addEventListener("click", async () => {
            if (remember()) await EFTForge.desktopSettings?.setCloseAction?.("tray");
            overlay.remove();
            window.__TAURI__.core.invoke("close_to_tray");
        });
        body.querySelector("#close-choice-exit").addEventListener("click", async () => {
            if (remember()) await EFTForge.desktopSettings?.setCloseAction?.("exit");
            overlay.remove();
            window.__TAURI__.core.invoke("exit_app");
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", _init);
    } else {
        _init();
    }
})();
