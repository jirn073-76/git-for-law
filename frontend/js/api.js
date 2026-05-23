const API = {
  base: '/api',

  async _get(path, params = {}) {
    const qs = new URLSearchParams(params).toString();
    const url = `${this.base}${path}${qs ? '?' + qs : ''}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    return res.json();
  },

  listLaws() {
    return this._get('/laws');
  },

  getLaw(abbrev) {
    return this._get(`/laws/${encodeURIComponent(abbrev)}`);
  },

  getVersions(abbrev) {
    return this._get(`/laws/${encodeURIComponent(abbrev)}/versions`);
  },

  getSections(abbrev, date) {
    return this._get(`/laws/${encodeURIComponent(abbrev)}/sections`, { date });
  },

  async diff(abbrev, from, to) {
    const qs = new URLSearchParams({ from, to }).toString();
    const url = `${this.base}/laws/${encodeURIComponent(abbrev)}/diff?${qs}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    return res.json();
  }
};
