const App = {
  content: document.getElementById('content'),
  search: document.getElementById('search'),
  themeToggle: document.getElementById('theme-toggle'),
  _sort: 'alpha',

  async init() {
    this._initTheme();
    this.content.addEventListener('click', e => this._delegate(e));
    this.content.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') {
        const node = e.target.closest('.change-node');
        if (node) { e.preventDefault(); this._selectVersion(node); }
      }
    });
    this.search.addEventListener('input', this._debounce(e => this._onSearch(e), 150));
    this.themeToggle.addEventListener('click', () => this._toggleTheme());
    window.addEventListener('popstate', () => this._route());
    await this._route();
  },

  _initTheme() {
    const saved = localStorage.getItem('theme');
    if (saved) {
      document.documentElement.setAttribute('data-theme', saved);
    } else if (window.matchMedia('(prefers-color-scheme: light)').matches) {
      document.documentElement.setAttribute('data-theme', 'light');
    }
    this._updateThemeIcon();
  },

  _toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    this._updateThemeIcon();
  },

  _updateThemeIcon() {
    const theme = document.documentElement.getAttribute('data-theme') || 'dark';
    this.themeToggle.textContent = theme === 'light' ? '◐' : '◑';
  },

  async _route() {
    const path = window.location.pathname;
    const params = new URLSearchParams(window.location.search);
    const match = path.match(/^\/law\/([^/]+)\/?$/);
    if (match) {
      this.search.removeAttribute('autofocus');
      await this._showDetail(decodeURIComponent(match[1]), params.get('from'), params.get('to'));
    } else if (path === '/impressum') {
      this.search.removeAttribute('autofocus');
      await this._showImpressum();
    } else {
      this.search.setAttribute('autofocus', '');
      await this._showHome();
    }
  },

  _navigate(url) {
    history.pushState(null, '', url);
    this._route();
  },

  _updateUrl(from, to) {
    const abbrev = decodeURIComponent(window.location.pathname.split('/')[2]);
    let url = `/law/${abbrev}`;
    const params = new URLSearchParams();
    if (from) params.set('from', from);
    if (to) params.set('to', to);
    const qs = params.toString();
    if (qs) url += '?' + qs;
    history.replaceState(null, '', url);
  },

  _selectVersion(node) {
    const date = node.dataset.date;
    const from = document.getElementById('diff-from');
    const to = document.getElementById('diff-to');
    const abbrev = decodeURIComponent(window.location.pathname.split('/')[2]);
    if (from && to) {
      const today = new Date().toISOString().slice(0, 10);
      const shift = window.event && window.event.shiftKey;
      if (shift) {
        // shift reverses the default
        if (date > today) { from.value = date; } else { to.value = date; }
      } else {
        if (date > today) { to.value = date; } else { from.value = date; }
      }
      document.querySelectorAll('.change-node.selected').forEach(el => el.classList.remove('selected'));
      if (from.value) {
        document.querySelector(`.change-node[data-date="${from.value}"]`)?.classList.add('selected');
      }
      if (to.value) {
        document.querySelector(`.change-node[data-date="${to.value}"]`)?.classList.add('selected');
      }
      if (from.value && to.value && from.value !== to.value) {
        this._updateUrl(from.value, to.value);
        this._doDiff();
      } else if (from.value) {
        this._updateUrl(from.value, null);
        this._showStammfassung(abbrev, from.value);
      }
    }
  },

  async _delegate(e) {
    const node = e.target.closest('.change-node');
    if (node) {
      this._selectVersion(node);
      return;
    }

    const card = e.target.closest('.law-card');
    if (card) {
      const abbrev = card.dataset.abbrev;
      this._navigate(`/law/${abbrev}`);
      return;
    }

    if (e.target.matches('[data-link]')) {
      e.preventDefault();
      this._navigate(e.target.getAttribute('href'));
      return;
    }

    if (e.target.id === 'diff-btn') {
      const from = document.getElementById('diff-from');
      const to = document.getElementById('diff-to');
      if (from && to) {
        this._updateUrl(from.value, to.value);
        document.querySelectorAll('.change-node.selected').forEach(el => el.classList.remove('selected'));
        if (from.value) {
          document.querySelector(`.change-node[data-date="${from.value}"]`)?.classList.add('selected');
        }
        if (to.value) {
          document.querySelector(`.change-node[data-date="${to.value}"]`)?.classList.add('selected');
        }
      }
      await this._doDiff();
      return;
    }

    const sortBtn = e.target.closest('.sort-opt');
    if (sortBtn) {
      const newSort = sortBtn.dataset.sort;
      if (newSort && newSort !== this._sort) {
        this._sort = newSort;
        await this._showHome();
      }
      return;
    }
  },

  async _showHome() {
    this.content.innerHTML = Render.loading();
    try {
      const laws = await API.listLaws();
      this._sortLaws(laws);
      document.title = 'Git for Law | Austria';
      this.content.innerHTML = Render.home(laws);
      const q = this.search.value;
      if (q) this._filterCards(q);
    } catch (err) {
      this.content.innerHTML = Render.error(`Fehler beim Laden: ${err.message}`);
    }
  },

  _sortLaws(laws) {
    switch (this._sort) {
      case 'fassungen':
        laws.sort((a, b) => (b.versions || 0) - (a.versions || 0) || a.abbrev.localeCompare(b.abbrev));
        break;
      case 'paragraphen':
        laws.sort((a, b) => (b.sections || 0) - (a.sections || 0) || a.abbrev.localeCompare(b.abbrev));
        break;
      default:
        laws.sort((a, b) => a.abbrev.localeCompare(b.abbrev));
        break;
    }
  },

  async _showDetail(abbrev, fromDate, toDate) {
    document.getElementById('header-stats').innerHTML = '';
    this.content.innerHTML = Render.loading();
    try {
      const law = await API.getLaw(abbrev);
      document.title = `${abbrev} — Git for Law | Austria`;
      this.content.innerHTML = Render.detail(law);
      const versions = law.versions_list || [];

      if (fromDate && toDate && fromDate !== toDate) {
        document.getElementById('diff-from').value = fromDate;
        document.getElementById('diff-to').value = toDate;
        document.querySelector(`.change-node[data-date="${fromDate}"]`)?.classList.add('selected');
        document.querySelector(`.change-node[data-date="${toDate}"]`)?.classList.add('selected');
        await this._doDiff();
        return;
      }

      if (fromDate) {
        document.getElementById('diff-from').value = fromDate;
        document.querySelector(`.change-node[data-date="${fromDate}"]`)?.classList.add('selected');
        await this._showStammfassung(abbrev, fromDate);
        return;
      }

      if (versions.length) {
        const today = new Date().toISOString().slice(0, 10);
        const newestEffective = versions.find(v => v.fassung_vom <= today) || versions[0];
        if (newestEffective && newestEffective.fassung_vom) {
          const toSelect = document.getElementById('diff-to');
          if (toSelect) toSelect.value = newestEffective.fassung_vom;
          document.querySelector(`.change-node[data-date="${newestEffective.fassung_vom}"]`)?.classList.add('selected');
          await this._showStammfassung(abbrev, newestEffective.fassung_vom);
        }
      }
    } catch (err) {
      this.content.innerHTML = Render.error(`Fehler beim Laden von ${Render.esc(abbrev)}: ${err.message}`);
    }
  },

  async _doDiff() {
    const from = document.getElementById('diff-from').value;
    const to = document.getElementById('diff-to').value;
    const abbrev = decodeURIComponent(window.location.pathname.split('/')[2]);
    const resultDiv = document.getElementById('diff-result');
    if (!from || !to || from === to) {
      resultDiv.innerHTML = Render.diffError('Zwei unterschiedliche Daten auswählen.');
      return;
    }
    resultDiv.innerHTML = Render.diffLoading();
    try {
      const diff = await API.diff(abbrev, from, to);
      resultDiv.innerHTML = Render.diffResult(diff);
    } catch (err) {
      resultDiv.innerHTML = Render.diffError(err.message);
    }
  },

  async _showStammfassung(abbrev, date) {
    const resultDiv = document.getElementById('diff-result');
    resultDiv.innerHTML = Render.loading();
    try {
      const sections = await API.getSections(abbrev, date);
      resultDiv.innerHTML = Render.stammfassung(sections, date, abbrev);
    } catch (err) {
      resultDiv.innerHTML = Render.diffError(err.message);
    }
  },

  async _showImpressum() {
    document.getElementById('header-stats').innerHTML = '';
    document.title = 'Impressum — Git for Law | Austria';
    this.content.innerHTML = Render.impressum();
  },

  _onSearch(e) {
    const q = e.target.value.toLowerCase();
    const onHome = !window.location.pathname.startsWith('/law/') && window.location.pathname !== '/impressum';
    if (!onHome) {
      this._navigate('/');
      setTimeout(() => {
        this.search.value = q;
        this._filterCards(q);
      }, 50);
      return;
    }
    this._filterCards(q);
  },

  _filterCards(q) {
    document.querySelectorAll('.law-card').forEach(card => {
      const text = card.textContent.toLowerCase();
      card.style.display = text.includes(q) ? '' : 'none';
    });
  },

  _debounce(fn, ms) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
  }
};

document.addEventListener('DOMContentLoaded', () => App.init());
