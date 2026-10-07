/* Waypost priority+ navigation controller.
 *
 * Keeps the top navigation bar on a single row (Apple/Jira "priority+" pattern):
 * items that do not fit are moved into a trailing "More" (…) dropdown instead of
 * wrapping to a second line. Below the Bootstrap `lg` breakpoint the whole menu
 * collapses into the hamburger and every item is shown inline again.
 *
 * Markup contract (see templates/base/base.html):
 *   <nav id="wpNavbar" class="navbar navbar-expand-lg ...">
 *     <button class="navbar-toggler" data-bs-target="#wpNavCollapse">
 *     <div id="wpNavCollapse" class="collapse navbar-collapse ...">
 *       <ul id="wpNavMenu" class="navbar-nav ...">   <!-- primary items -->
 *         <li class="nav-item">...</li>
 *         <li class="nav-item dropdown">...</li>
 *         <li id="wpNavMore" class="nav-item dropdown wp-nav__more">  <!-- last -->
 *           <a class="nav-link dropdown-toggle">More</a>
 *           <ul id="wpNavMoreMenu" class="dropdown-menu"></ul>
 *       <ul class="navbar-nav wp-nav__end">...</ul>  <!-- never moved -->
 *
 * Overflowed groups are flattened into the More menu (a header + their links)
 * rather than nested, because Bootstrap 5 does not position nested dropdowns.
 */
(function () {
  'use strict';

  var LG_QUERY = '(min-width: 992px)';
  var HIDDEN_CLASS = 'wp-nav-hidden';
  var MAX_PASSES = 60; // hard stop so a pathological layout can never spin forever

  function init() {
    var navbar = document.getElementById('wpNavbar');
    var menu = document.getElementById('wpNavMenu');
    var more = document.getElementById('wpNavMore');
    var moreMenu = document.getElementById('wpNavMoreMenu');
    if (!navbar || !menu || !more || !moreMenu) {
      return; // nav not present on this page (e.g. login) - nothing to do
    }

    // Snapshot the primary items once. Permissions are fixed for the page load,
    // so the DOM will not gain/lose top-level items without a navigation.
    var items = Array.prototype.filter.call(
      menu.querySelectorAll(':scope > .nav-item'),
      function (li) { return li !== more; }
    );

    var descriptors = items.map(function (li) {
      var toggle = li.querySelector(':scope > a');
      var submenu = li.querySelector(':scope > .dropdown-menu');
      var label = toggle ? toggle.textContent.trim() : '';
      if (submenu) {
        return {
          li: li,
          label: label,
          isGroup: true,
          children: Array.prototype.slice.call(submenu.children)
        };
      }
      return { li: li, label: label, isGroup: false, link: toggle };
    });

    function desktopMode() {
      if (window.matchMedia) {
        return window.matchMedia(LG_QUERY).matches;
      }
      return navbar.getBoundingClientRect().width >= 992;
    }

    // Un-hide every primary item and empty the More menu. Primary items are only
    // ever hidden (never moved out of #wpNavMenu); More shows clones of them, so
    // restoring is simply removing the hidden class.
    function reset() {
      descriptors.forEach(function (d) {
        d.li.classList.remove(HIDDEN_CLASS);
      });
      moreMenu.innerHTML = '';
      more.classList.add(HIDDEN_CLASS);
    }

    function appendMoreEntry(d) {
      if (d.isGroup) {
        var header = document.createElement('h6');
        header.className = 'dropdown-header';
        header.textContent = d.label;
        moreMenu.appendChild(header);
        d.children.forEach(function (child) {
          moreMenu.appendChild(child.cloneNode(true));
        });
      } else if (d.link) {
        var a = d.link.cloneNode(true);
        a.classList.remove('nav-link', 'dropdown-toggle');
        a.classList.add('dropdown-item');
        a.removeAttribute('data-bs-toggle');
        a.removeAttribute('role');
        moreMenu.appendChild(a);
      }
    }

    function renderMore(moved) {
      moreMenu.innerHTML = '';
      moved.forEach(appendMoreEntry);
      more.classList.toggle(HIDDEN_CLASS, moved.length === 0);
    }

    // Rect-based overflow check (independent of the menu's overflow CSS, which
    // must stay `visible` so dropdown menus are not clipped).
    function menuOverflows() {
      var menuRect = menu.getBoundingClientRect();
      var lastVisible = null;
      for (var i = 0; i < descriptors.length; i += 1) {
        if (!descriptors[i].li.classList.contains(HIDDEN_CLASS)) {
          lastVisible = descriptors[i].li;
        }
      }
      if (!lastVisible) { return false; }
      return lastVisible.getBoundingClientRect().right > menuRect.right + 1;
    }

    function layout() {
      reset();
      if (!desktopMode()) {
        return; // mobile: the collapse shows everything inline
      }
      var moved = [];
      var passes = 0;
      // Move items from the end into More until the menu no longer overflows.
      while (menuOverflows() && passes < MAX_PASSES) {
        passes += 1;
        var idx = -1;
        for (var i = descriptors.length - 1; i >= 0; i -= 1) {
          if (!descriptors[i].li.classList.contains(HIDDEN_CLASS)) { idx = i; break; }
        }
        if (idx < 0) { break; }
        descriptors[idx].li.classList.add(HIDDEN_CLASS);
        moved.unshift(descriptors[idx]);
        renderMore(moved); // re-measure with the More button now taking space
      }
      renderMore(moved);
    }

    var scheduled = false;
    function scheduleLayout() {
      if (scheduled) { return; }
      scheduled = true;
      window.requestAnimationFrame(function () {
        scheduled = false;
        layout();
      });
    }

    window.addEventListener('resize', scheduleLayout);
    window.addEventListener('orientationchange', scheduleLayout);
    if (window.matchMedia) {
      var mq = window.matchMedia(LG_QUERY);
      if (mq.addEventListener) { mq.addEventListener('change', scheduleLayout); }
      else if (mq.addListener) { mq.addListener(scheduleLayout); }
    }
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(scheduleLayout).observe(navbar);
    }
    // Re-run once webfonts settle so measurement uses final text metrics.
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(scheduleLayout).catch(function () {});
    }

    layout();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
