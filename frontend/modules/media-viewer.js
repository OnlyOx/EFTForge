window.EFTForge = window.EFTForge || {};

window.EFTForge.mediaViewer = (function () {
    let _overlay       = null;
    let _img           = null;
    let _scale         = 1;
    let _panX          = 0;
    let _panY          = 0;
    let _mdOnBackdrop  = false;

    function open(src) {
        if (_overlay) _destroy();

        _scale = 1;
        _panX  = 0;
        _panY  = 0;

        _overlay = document.createElement('div');
        _overlay.className = 'mv-overlay';

        _img = document.createElement('img');
        _img.className = 'mv-img';
        _img.src = src;
        _img.alt = '';
        _overlay.appendChild(_img);

        document.body.appendChild(_overlay);

        requestAnimationFrame(function () {
            _overlay.classList.add('mv-visible');
        });

        _overlay.addEventListener('click', _onOverlayClick);
        _overlay.addEventListener('wheel', _onWheel, { passive: false });
        _overlay.addEventListener('mousedown', _onMouseDown);
        document.addEventListener('keydown', _onKeyDown);
    }

    function close() {
        if (!_overlay) return;
        _overlay.classList.remove('mv-visible');
        const ol = _overlay;
        _overlay = null;
        _img = null;
        document.removeEventListener('keydown', _onKeyDown);
        setTimeout(function () { ol.remove(); }, 200);
    }

    function _destroy() {
        if (!_overlay) return;
        document.removeEventListener('keydown', _onKeyDown);
        _overlay.remove();
        _overlay = null;
        _img = null;
    }

    function _onOverlayClick(e) {
        if (e.target === _overlay && _mdOnBackdrop) close();
    }

    function _onKeyDown(e) {
        if (e.key === 'Escape') close();
    }

    function _onWheel(e) {
        e.preventDefault();
        const factor   = e.deltaY < 0 ? 1.15 : (1 / 1.15);
        const newScale = Math.min(Math.max(_scale * factor, 0.15), 20);

        const rect = _overlay.getBoundingClientRect();
        const mx   = e.clientX - (rect.left + rect.width  / 2);
        const my   = e.clientY - (rect.top  + rect.height / 2);

        // Zoom toward the cursor: keep the image point under the cursor stationary
        const ratio = newScale / _scale;
        _panX  = mx * (1 - ratio) + _panX * ratio;
        _panY  = my * (1 - ratio) + _panY * ratio;
        _scale = newScale;
        _applyTransform();
    }

    function _onMouseDown(e) {
        _mdOnBackdrop = e.target === _overlay;
        if (e.button !== 1) return;
        e.preventDefault();
        const startX = e.clientX - _panX;
        const startY = e.clientY - _panY;
        _overlay.classList.add('mv-panning');

        function onMove(e) {
            _panX = e.clientX - startX;
            _panY = e.clientY - startY;
            _applyTransform();
        }
        function onUp() {
            _overlay.classList.remove('mv-panning');
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        }
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    }

    function _applyTransform() {
        if (!_img) return;
        _img.style.transform = 'translate(' + _panX + 'px, ' + _panY + 'px) scale(' + _scale + ')';
    }

    return { open: open, close: close };
})();
