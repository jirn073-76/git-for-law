const Render = {

  home(laws) {
    const total = laws.length;
    const totalCommits = laws.reduce((s, l) => s + (l.versions || 0), 0);
    const totalSections = laws.reduce((s, l) => s + (l.sections || 0), 0);

    const hs = document.getElementById('header-stats');
    if (hs) {
      hs.innerHTML = `<span class="hs-item"><span class="hs-value">${total.toLocaleString()}</span> <span class="hs-label">Rechtstexte</span></span>`
        + `<span class="hs-sep">·</span>`
        + `<span class="hs-item"><span class="hs-value">${totalCommits.toLocaleString()}</span> <span class="hs-label">Fassungen</span></span>`
        + `<span class="hs-sep">·</span>`
        + `<span class="hs-item"><span class="hs-value">${totalSections.toLocaleString()}</span> <span class="hs-label">Paragrafen</span></span>`;
    }

    const currentSort = App._sort || 'alpha';

    return `
      <div class="sort-bar">
        <span class="sort-label">Sortieren nach:</span>
        <button class="sort-opt${currentSort === 'alpha' ? ' active' : ''}" data-sort="alpha">Name</button>
        <span class="sort-sep">|</span>
        <button class="sort-opt${currentSort === 'fassungen' ? ' active' : ''}" data-sort="fassungen">Fassungen</button>
        <span class="sort-sep">|</span>
        <button class="sort-opt${currentSort === 'paragraphen' ? ' active' : ''}" data-sort="paragraphen">Paragrafen</button>
      </div>
      <div class="law-grid">
        ${laws.map(l => this.lawCard(l)).join('')}
      </div>
      <p class="site-footer">Keine Gewähr für die Richtigkeit der Daten. Es sind keine Rechtsanspruche ableitbar. Fehler bitte an <a href="mailto:d.ramadani@ieee.org">d.ramadani@ieee.org</a>.</p>`;
  },

  lawCard(law) {
    const abbrev = Render.esc(law.abbrev);
    const name = Render.esc(law.name || '');
    const syntheticMark = law.synthetic
      ? ' <span class="synthetic-mark" title="Kein offizielles Kurzwort - plausibel ergänzt">*</span>'
      : '';
    return `
      <div class="law-card" data-abbrev="${abbrev}">
        <div class="abbrev">${abbrev}${syntheticMark}</div>
        ${name ? `<div class="law-name">${name}</div>` : ''}
        <div class="meta">
          <span>${law.versions || 0} Fassungen</span>
          <span>${(law.sections || 0).toLocaleString()} Paragrafen</span>
        </div>
      </div>`;
  },

  detail(law) {
    const abbrev = Render.esc(law.abbrev);
    const name = Render.esc(law.name || '');
    const versions = law.versions_list || [];
    const today = new Date().toISOString().slice(0, 10);
    const newestEffective = versions.reduce((a, b) => {
      if (b.fassung_vom > today) return a;
      if (a.fassung_vom > today) return b;
      return a.fassung_vom > b.fassung_vom ? a : b;
    }, versions[0]);

    const syntheticMark = law.synthetic
      ? ' <span class="synthetic-mark" title="Kein offizielles Kurzwort - plausibel ergänzt">*</span>'
      : '';

    const sidebar = `
      <div class="detail-sidebar">
        <a href="/" class="back-link" data-link>Alle Gesetze</a>
        <div class="detail-header">
          <h1>${abbrev}${syntheticMark}</h1>
          ${name ? `<div class="full-name">${name}</div>` : ''}
        </div>
        <div class="sidebar-header">${versions.length} Fassungen</div>
        <div class="change-graph">
          ${versions.map((v) => Render.changeNode(v, v.fassung_vom === newestEffective.fassung_vom)).join('')}
        </div>
      </div>`;

    const main = `
      <div class="detail-main">
        <div class="version-count">
          Von: <strong>${versions[versions.length - 1]?.fassung_vom || '—'}</strong>
          &nbsp;Bis: <strong>${newestEffective.fassung_vom}</strong>
          &nbsp;&middot;&nbsp;${versions.length} Fassungen
        </div>
        <div class="diff-controls">
          <label>Von</label>
          <select id="diff-from">
            <option value="">—</option>
            ${versions.map(v => `<option value="${v.fassung_vom}">${v.fassung_vom}</option>`).join('')}
          </select>
          <label>Bis</label>
          <select id="diff-to">
            <option value="">—</option>
            ${versions.map((v) => `<option value="${v.fassung_vom}">${v.fassung_vom}</option>`).join('')}
          </select>
          <button id="diff-btn">diff</button>
        </div>
        <div id="diff-result">
          <div class="diff-empty">Zwei Versionen auswählen, um Änderungen anzuzeigen.</div>
        </div>
      </div>`;

    return `<div class="detail-page">${sidebar}${main}</div>`;
  },

  changeNode(version, isLatest) {
    const date = Render.esc(version.fassung_vom);
    const aenderung = Render.esc(version.aenderung || '');
    const sections = version.sections;
    const changed = version.changed_sections || [];
    const changedCount = version.changed_count || 0;
    let html = `<div class="change-node" data-date="${date}" tabindex="0" role="button" aria-label="Fassung vom ${date}${isLatest ? ', aktuell' : ''}">
      <div class="fassung-date">${date}${isLatest ? '<span class="badge-aktuell">aktuell</span>' : ''}</div>`;
    if (aenderung) {
      html += `<div class="bgbl" title="${aenderung}">${aenderung}</div>`;
    }
    if (changed.length) {
      html += `<div class="changed-sections">`;
      const shown = changed.slice(0, 8);
      for (const sid of shown) {
        html += `<span class="cs-tag">${Render.esc(sid)}</span>`;
      }
      if (changedCount > 8) {
        html += `<span class="cs-more">+${changedCount - 8} mehr</span>`;
      }
      html += `</div>`;
    }
    if (sections) {
      html += `<div class="sections-count">${sections} Paragrafen</div>`;
    }
    html += `</div>`;
    return html;
  },

  diffResult(diff) {
    if (!diff || (!diff.changed_sections || !diff.changed_sections.length) && (!diff.unchanged_sections || !diff.unchanged_sections.length)) {
      return '<div class="diff-empty">Keine Änderungen.</div>';
    }

    const changed = diff.changed_sections || [];
    const unchangedCount = (diff.unchanged_sections || []).length;

    const addedAbsaetze = changed.reduce((n, s) => {
      if (!s.old_body) return n + Render._absaetze(s.new_body).length;
      if (!s.new_body) return n;
      const oa = Render._absaetze(s.old_body);
      const na = Render._absaetze(s.new_body);
      let adds = 0;
      for (const a of na) { if (!oa.includes(a)) adds++; }
      return n + adds;
    }, 0);
    const delAbsaetze = changed.reduce((n, s) => {
      if (!s.new_body) return n + Render._absaetze(s.old_body).length;
      if (!s.old_body) return n;
      const oa = Render._absaetze(s.old_body);
      const na = Render._absaetze(s.new_body);
      let dels = 0;
      for (const a of oa) { if (!na.includes(a)) dels++; }
      return n + dels;
    }, 0);

    let html = `<div class="diff-header">
      diff --git a/${Render.esc(diff.law_abbrev)}/${Render.esc(diff.from_date)} b/${Render.esc(diff.law_abbrev)}/${Render.esc(diff.to_date)}
    </div>`;
    html += `<div class="diff-stats">
      ${changed.length} sections changed, ${addedAbsaetze} insertions(+), ${delAbsaetze} deletions(-)
    </div>`;

    for (const s of changed) {
      const heading = Render.esc(s.heading);
      html += `<div class="diff-section">`;
      html += `<div class="diff-section-header">@@ ${heading} @@</div>`;

      if (s.old_body && s.new_body) {
        html += Render._absatzDiff(s.old_body, s.new_body);
      } else if (s.old_body) {
        for (const a of Render._absaetze(s.old_body)) {
          html += `<div class="diff-line diff-line-del">- ${Render.esc(a)}</div>`;
        }
      } else if (s.new_body) {
        for (const a of Render._absaetze(s.new_body)) {
          html += `<div class="diff-line diff-line-add">+ ${Render.esc(a)}</div>`;
        }
      }
      html += `</div>`;
    }

    return html;
  },

  _absaetze(text) {
    if (!text) return [];
    return text.split(/(?=\(\d+[a-z]?\))/).filter(p => p.trim()).map(p => p.trim());
  },

  _absatzDiff(oldBody, newBody) {
    const oldA = Render._absaetze(oldBody);
    const newA = Render._absaetze(newBody);
    const lcs = Render._lcs(oldA, newA);

    let html = '';
    let oi = 0, ni = 0, li = 0;

    while (oi < oldA.length || ni < newA.length) {
      if (li < lcs.length && oi < oldA.length && oldA[oi] === lcs[li] &&
          ni < newA.length && newA[ni] === lcs[li]) {
        // Matching paragraph — show as context
        html += `<div class="diff-line diff-line-context">  ${Render.esc(oldA[oi])}</div>`;
        oi++; ni++; li++;
      } else if (li < lcs.length && oi < oldA.length && ni < newA.length &&
                 oldA[oi] !== lcs[li] && newA[ni] !== lcs[li]) {
        // Both sides differ from next LCS element — modified pair, not independent add/delete
        html += Render._wordDiffAbsatz(oldA[oi], newA[ni]);
        oi++; ni++;
      } else if (li < lcs.length && oi < oldA.length && oldA[oi] !== lcs[li]) {
        html += `<div class="diff-line diff-line-del">- ${Render.esc(oldA[oi])}</div>`;
        oi++;
      } else if (li < lcs.length && ni < newA.length && newA[ni] !== lcs[li]) {
        html += `<div class="diff-line diff-line-add">+ ${Render.esc(newA[ni])}</div>`;
        ni++;
      } else if (oi < oldA.length && ni < newA.length) {
        // Both remaining — show as modified pair with word diff
        html += Render._wordDiffAbsatz(oldA[oi], newA[ni]);
        oi++; ni++;
      } else if (oi < oldA.length) {
        html += `<div class="diff-line diff-line-del">- ${Render.esc(oldA[oi])}</div>`;
        oi++;
      } else if (ni < newA.length) {
        html += `<div class="diff-line diff-line-add">+ ${Render.esc(newA[ni])}</div>`;
        ni++;
      }
    }

    return html;
  },

  _wordDiffAbsatz(oldP, newP) {
    const oldTokens = oldP.split(/(\s+)/);
    const newTokens = newP.split(/(\s+)/);
    const lcs = Render._lcs(oldTokens, newTokens);

    let delHtml = '<div class="diff-line diff-line-del">- ';
    let addHtml = '<div class="diff-line diff-line-add">+ ';
    let oi = 0, ni = 0, li = 0;
    let hasDel = false, hasAdd = false;

    while (oi < oldTokens.length || ni < newTokens.length) {
      if (li < lcs.length && oi < oldTokens.length && oldTokens[oi] === lcs[li] &&
          ni < newTokens.length && newTokens[ni] === lcs[li]) {
        delHtml += Render.esc(lcs[li]);
        addHtml += Render.esc(lcs[li]);
        oi++; ni++; li++;
      } else if (li < lcs.length && oi < oldTokens.length && oldTokens[oi] !== lcs[li]) {
        delHtml += `<span class="diff-word-del">${Render.esc(oldTokens[oi])}</span>`;
        hasDel = true;
        oi++;
      } else if (li < lcs.length && ni < newTokens.length && newTokens[ni] !== lcs[li]) {
        addHtml += `<span class="diff-word-add">${Render.esc(newTokens[ni])}</span>`;
        hasAdd = true;
        ni++;
      } else if (oi < oldTokens.length) {
        delHtml += `<span class="diff-word-del">${Render.esc(oldTokens[oi])}</span>`;
        hasDel = true;
        oi++;
      } else if (ni < newTokens.length) {
        addHtml += `<span class="diff-word-add">${Render.esc(newTokens[ni])}</span>`;
        hasAdd = true;
        ni++;
      }
    }
    delHtml += '</div>';
    addHtml += '</div>';
    return (hasDel ? delHtml : '') + (hasAdd ? addHtml : '');
  },

  _lcs(a, b) {
    const m = a.length, n = b.length;
    const dp = new Array(m + 1);
    for (let i = 0; i <= m; i++) { dp[i] = new Uint32Array(n + 1); }
    for (let i = 1; i <= m; i++) {
      for (let j = 1; j <= n; j++) {
        if (a[i - 1] === b[j - 1]) {
          dp[i][j] = dp[i - 1][j - 1] + 1;
        } else {
          dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
        }
      }
    }
    const result = [];
    let i = m, j = n;
    while (i > 0 && j > 0) {
      if (a[i - 1] === b[j - 1]) { result.unshift(a[i - 1]); i--; j--; }
      else if (dp[i - 1][j] > dp[i][j - 1]) { i--; }
      else { j--; }
    }
    return result;
  },

  diffLoading() {
    return '<div class="loading"><div class="spinner"></div><div style="margin-top:0.5rem">diff wird berechnet ...</div></div>';
  },

  diffError(msg) {
    return `<div class="diff-empty" style="color:var(--red)">${Render.esc(msg)}</div>`;
  },

  diffEmpty() {
    return '<div class="diff-empty">Zwei Versionen auswählen, um Änderungen anzuzeigen.</div>';
  },

  stammfassung(sections, date, abbrev) {
    let options = '';
    for (const s of sections) {
      const id = Render.esc(s.section_id);
      const heading = Render.esc(s.heading || id);
      options += `<option value="sf-${id}">${heading}</option>`;
    }
    let html = `<div class="stammfassung">
      <div class="stammfassung-header">
        ${Render.esc(abbrev)} — Fassung vom ${Render.esc(date)}
        <div class="sf-jump">
          <label for="sf-jump-select">§&nbsp;</label>
          <select id="sf-jump-select" onchange="document.getElementById(this.value)?.scrollIntoView({block:'start'})">
            <option value="">—</option>
            ${options}
          </select>
          <span class="sf-section-count">${sections.length} Paragrafen</span>
        </div>
      </div>`;
    for (const s of sections) {
      const id = Render.esc(s.section_id);
      const heading = Render.esc(s.heading || '');
      html += `<div class="sf-section" id="sf-${id}">
        <div class="sf-heading">${heading || id}</div>`;
      if (s.body) {
        const absaetze = Render._absaetze(s.body);
        for (const a of absaetze) {
          html += `<div class="sf-absatz">${Render.esc(a)}</div>`;
        }
      }
      html += `</div>`;
    }
    html += `</div>`;
    return html;
  },

  loading() {
    return '<div class="loading"><div class="spinner"></div></div>';
  },

  error(msg) {
    return `<div class="error">${Render.esc(msg)}</div>`;
  },

  impressum() {
    return `
      <div class="impressum">
        <h1>Impressum & Danksagung</h1>

        <section class="impressum-section">
          <h2>Datenquelle</h2>
          <p>
            Die Rechtsdaten werden von der österreichischen Bundesregierung über die
            <a href="https://data.bka.gv.at/ris/api/v2.6/Bundesrecht" target="_blank" rel="noopener">OGD-Schnittstelle des RIS</a>
            (Rechtsinformationssystem des Bundes) bereitgestellt und sind lizenziert unter
            <a href="https://creativecommons.org/licenses/by/4.0/deed.de" target="_blank" rel="noopener">Creative Commons Namensnennung 4.0 International (CC BY 4.0)</a>.
          </p>
          <p class="impressum-attribution">
            Datenquelle: Bundeskanzleramt — RIS/OGD · CC BY 4.0
          </p>
          <p class="impressum-disclaimer">
            Aus der Verwendung der hier abgerufenen Informationen und Schnittstellen
            können keinerlei Rechtsansprüche abgeleitet werden.
          </p>
        </section>

        <section class="impressum-section">
          <h2>Weshalb das Ganze?</h2>
          <p>
            In der Software Entwicklung wird, aus gutem Grund, immer klar dokumentiert welcher Entwickler weshalb eine 
            gewisse Änderung durchgeführt hat. Diese Dokumentation passiert in Form einer sogenannten "Commit Message" - man sieht 
            zu jeder Zeile Code die gesamte Änderungshistorie, d. h. wieso und durch wen Änderungen durchgeführt wurden. 
          </p>
          <p>
            Beim Besuch einer juristischen Vorlesung kam mir der Gedanke, dass dieses Konzept 
            auch für unsere Rechtstexte sympathisch wäre. Deshalb habe ich Git for Law Austria gebaut, jede Fassung eines Gesetzes
            ist hier wie ein Commit dargestellt. 
          </p>
          <p>
            Die Autoren des Commits wären in dieser Analogie die entsprechenden Verwaltungslegisten / Abgeordnete (Abänderungsanträge) / etc., 
            die "Commit Messages" wären z. B. die Erwägungsgründe. Das sind alles jedoch Daten, die in einer strukturierten bzw. leicht maschinenlesbaren Form (noch) nicht vorliegen.  
          </p>
          <p>
            Es handelt sich hierbei also um ein persönliches Experiment an der Schnittstelle
            zwischen Recht und Technik.
          </p>
          <p>
            Der Datenbestand ist eingefroren mit Stand <strong>15. Mai 2026</strong>.
            Spätere Gesetzesänderungen sind nicht enthalten.
          </p>
        </section>

        <section class="impressum-section">
          <h2>Offenlegung gemäß §&nbsp;25 Mediengesetz</h2>
          <p>
            <strong>Verantwortlicher:</strong><br>
            Ing. Dionis Ramadani, BSc MSc LL.M.<br>
            <a href="mailto:d.ramadani@ieee.org">d.ramadani@ieee.org</a>
          </p>
        </section>

        <p class="impressum-back">
          <a href="/" data-link>← Zurück</a>
        </p>
      </div>`;
  },

  esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }
};
