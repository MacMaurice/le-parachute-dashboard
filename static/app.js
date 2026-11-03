"use strict";
(() => {
  // ---------- Formats ----------
  const nf = new Intl.NumberFormat("fr-FR");
  const eur = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" });
  const pct = new Intl.NumberFormat("fr-FR", { style: "percent", maximumFractionDigits: 1 });
  const dShort = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short" });
  const dLong = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "long", year: "numeric" });
  const tHM = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const parseDay = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
  const fmtDay = (s, long) => (s ? (long ? dLong : dShort).format(parseDay(String(s).slice(0, 10))) : "—");
  const today = () => { const d = new Date(); return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
  const plural = (n, one, many) => `${nf.format(n)} ${Math.abs(n) > 1 ? many : one}`;

  // ---------- Petit utilitaire DOM ----------
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k in el && k !== "list") el[k] = v;
      else el.setAttribute(k, v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
    return el;
  }
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const svgNS = "http://www.w3.org/2000/svg";
  const s = (tag, attrs) => { const el = document.createElementNS(svgNS, tag); for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v); return el; };

  // ---------- Infobulle ----------
  const tip = $("#tip");
  function showTip(evt, title, lines) {
    tip.replaceChildren(h("div", { class: "t" }, title), ...lines.map(([color, label, value]) =>
      h("div", {}, color ? h("i", { style: `background:${color}` }) : null, `${label} : `, h("b", {}, value))));
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    let x = evt.clientX + 14, y = evt.clientY + 14;
    if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - 14;
    if (y + r.height > innerHeight - 8) y = evt.clientY - r.height - 14;
    tip.style.left = `${Math.max(8, x)}px`; tip.style.top = `${Math.max(8, y)}px`;
  }
  const hideTip = () => { tip.hidden = true; };
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // ---------- État ----------
  const state = { data: null, filtre: "Tous", recherche: "", tri: { key: "date", dir: -1 }, ouvertes: new Set() };

  async function api(path, opts = {}) {
    // X-Requested-With : en ligne, si la connexion Google a expiré, IAP répond 401 au lieu de rediriger.
    const res = await fetch(path, { headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" }, ...opts });
    if (res.status === 401) throw new Error("Connexion expirée : recharge la page pour te reconnecter.");
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = body.detail;
      throw new Error(Array.isArray(d) ? d.map((e) => e.msg).join(", ") : d || `Erreur ${res.status}`);
    }
    return body;
  }

  async function load(refresh = false) {
    const btn = $("#refresh");
    btn.disabled = true;
    try {
      state.data = await api(`/api/dashboard${refresh ? "?refresh=true" : ""}`);
      $("#error").hidden = true;
      render();
      if (!state.affiche) { state.affiche = true; if (!state.ouvertes.size) scrollTo(0, 0); }
    } catch (e) {
      $("#error").textContent = `Impossible de charger les données : ${e.message}`;
      $("#error").hidden = false;
    } finally {
      btn.disabled = false;
    }
  }

  // ---------- Rendu général ----------
  function render() {
    const d = state.data, k = d.kpis;
    $("#maj").textContent = `Stripe lu à ${tHM.format(new Date(d.maj))}`;
    const link = d.lien_paiement[0];
    const pill = $("#link-pill");
    pill.hidden = !link;
    if (link) {
      pill.className = `pill ${link.actif ? "on" : "off"}`;
      pill.textContent = link.actif ? "Vente ouverte" : "Vente fermée";
    }
    $("#tab-count-cmd").textContent = k.commandes ? nf.format(k.commandes) : "";
    $("#tab-count-cmd").classList.toggle("warn", k.doublons_a_verifier > 0);
    $("#tab-count-cmd").title = k.doublons_a_verifier ? plural(k.doublons_a_verifier, "doublon possible à vérifier", "doublons possibles à vérifier") : "";

    $$("[data-k]").forEach((el) => {
      const v = k[el.dataset.k];
      el.classList.remove("pos", "neg");
      if (v == null) { el.textContent = "—"; return; }
      if (el.dataset.fmt === "eur") el.textContent = eur.format(v);
      else if (el.dataset.k === "part_tirage") el.textContent = pct.format(v);
      else el.textContent = nf.format(v);
      if (el.dataset.sign) el.classList.add(v >= 0 ? "pos" : "neg");
    });
    $("#k-cmd").textContent = plural(k.commandes, "commande", "commandes");
    $("#k-conv").textContent = d.funnel.taux == null ? "—" : pct.format(d.funnel.taux);

    $("#alertes").replaceChildren(...d.alertes.map((a) => h("li", { class: a.niveau }, a.texte,
      a.commande ? h("button", { type: "button", class: "alert-link", onclick: () => voirCommande(a.commande) }, "Voir la commande") : null)));

    renderGauge(k);
    renderVentes(d.serie);
    renderFunnel(d.funnel);
    renderFiltre(d.funnel);
    renderBars("#bars-livraison", d.livraison, "Aucune vente en ligne pour l'instant.");
    renderBars("#bars-sources", d.sources, "Aucune réponse pour l'instant.");
    renderMots(d.commandes);
    renderCommandes();
    renderMouvements();
    renderDepenses();
    renderVersements();
    renderRentab(k);
    renderContestations();
    renderInsta();
    fillSelects();
  }

  // ---------- Jauge du tirage ----------
  function renderGauge(k) {
    const segs = [
      { label: "Vendus en ligne, à livrer", n: k.a_livrer, color: "var(--c1)" },
      { label: "Vendus en ligne, livrés", n: k.livres, color: "var(--c4)" },
      { label: "Vendus en direct", n: k.vendus_direct, color: "var(--c2)" },
      { label: "Offerts ou abîmés", n: k.offerts + k.casse, color: "var(--c3)" },
    ];
    const used = segs.reduce((a, x) => a + x.n, 0);
    const total = Math.max(k.tirage, used, 1);
    const g = $("#gauge");
    g.replaceChildren(...segs.filter((x) => x.n > 0).map((x) => {
      const el = h("div", { class: "seg", style: `width:${(x.n / total) * 100}%;background:${x.color}` });
      el.addEventListener("mousemove", (e) => showTip(e, x.label, [[null, "Jeux", nf.format(x.n)], [null, "Part du tirage", pct.format(x.n / total)]]));
      el.addEventListener("mouseleave", hideTip);
      return el;
    }));
    g.onmousemove = (e) => { if (e.target === g) showTip(e, "Disponibles à la vente", [[null, "Jeux", nf.format(k.disponibles)]]); };
    g.onmouseleave = hideTip;
    $("#gauge-legend").replaceChildren(
      ...segs.map((x) => h("li", {}, h("i", { style: `background:${x.color}` }), x.label, h("b", {}, nf.format(x.n)))),
      h("li", {}, h("i", { style: "background:var(--free);outline:1px solid var(--line)" }), "Disponibles", h("b", {}, nf.format(k.disponibles))));
  }

  // ---------- Courbe des ventes cumulées ----------
  function niceStep(max, count) {
    const raw = Math.max(max, 1) / count, p = 10 ** Math.floor(Math.log10(raw)), f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }
  function renderVentes(serie) {
    const box = $("#chart-ventes");
    box.replaceChildren();
    if (!serie.length) { box.append(h("p", { class: "none" }, "Pas encore de vente.")); return; }
    const W = Math.max(box.clientWidth, 300), H = 240, m = { t: 14, r: 64, b: 28, l: 38 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const pts = [{ jour: null, cumul: 0, en_ligne: 0, direct: 0 }, ...serie];
    const max = Math.max(...pts.map((p) => p.cumul));
    const step = niceStep(max, 4), top = Math.max(step, Math.ceil(max / step) * step);
    const x = (i) => m.l + (i / (pts.length - 1)) * iw;
    const y = (v) => m.t + ih - (v / top) * ih;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Jeux vendus cumulés par jour" });
    const grid = s("g", { class: "grid" });
    for (let v = 0; v <= top + 1e-9; v += step) {
      grid.append(s("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
      const t = s("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }); t.textContent = nf.format(v); grid.append(t);
    }
    svg.append(grid);
    const nTicks = Math.min(serie.length, Math.max(2, Math.floor(iw / 90)));
    const idx = [...new Set(Array.from({ length: nTicks }, (_, i) => Math.round(1 + (i * (serie.length - 1)) / Math.max(1, nTicks - 1))))];
    idx.forEach((i) => { const t = s("text", { x: x(i), y: H - 8, "text-anchor": i === pts.length - 1 ? "end" : "middle" }); t.textContent = fmtDay(pts[i].jour); svg.append(t); });
    // escalier : la valeur reste la même jusqu'à la vente suivante
    let dLine = `M${x(0)},${y(0)}`;
    for (let i = 1; i < pts.length; i++) dLine += `H${x(i)}V${y(pts[i].cumul)}`;
    svg.append(s("path", { class: "area", d: `${dLine}V${y(0)}H${x(0)}Z` }));
    svg.append(s("path", { class: "line", d: dLine }));
    pts.forEach((p, i) => { if (i && (p.en_ligne || p.direct)) svg.append(s("circle", { class: "dot", cx: x(i), cy: y(p.cumul), r: 4 })); });
    const last = pts[pts.length - 1];
    const end = s("text", { class: "end-label", x: x(pts.length - 1) + 8, y: y(last.cumul) + 4 }); end.textContent = plural(last.cumul, "jeu", "jeux"); svg.append(end);
    const hl = s("line", { class: "hover-line", y1: m.t, y2: m.t + ih, visibility: "hidden" });
    const hd = s("circle", { class: "dot", r: 5, visibility: "hidden" });
    svg.append(hl, hd);
    const overlay = s("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" });
    overlay.addEventListener("mousemove", (e) => {
      const r = svg.getBoundingClientRect();
      const px = ((e.clientX - r.left) / r.width) * W;
      const i = Math.min(pts.length - 1, Math.max(1, Math.round(((px - m.l) / iw) * (pts.length - 1))));
      const p = pts[i];
      hl.setAttribute("x1", x(i)); hl.setAttribute("x2", x(i)); hl.setAttribute("visibility", "visible");
      hd.setAttribute("cx", x(i)); hd.setAttribute("cy", y(p.cumul)); hd.setAttribute("visibility", "visible");
      showTip(e, fmtDay(p.jour, true), [[cssVar("--c1"), "En ligne", nf.format(p.en_ligne)], [cssVar("--c2"), "En direct", nf.format(p.direct)], [null, "Cumul", nf.format(p.cumul)]]);
    });
    overlay.addEventListener("mouseleave", () => { hl.setAttribute("visibility", "hidden"); hd.setAttribute("visibility", "hidden"); hideTip(); });
    svg.append(overlay);
    box.append(svg);
  }

  // ---------- Entonnoir du lien de paiement ----------
  function renderFunnel(f) {
    const max = Math.max(f.ouvertures, 1);
    const row = (cls, label, n) => {
      const el = h("div", { class: `f-row ${cls}` }, h("span", { class: "lbl" }, label), h("span", { class: "val" }, nf.format(n)),
        h("div", { class: "track" }, h("div", { class: "fill", style: `width:${(n / max) * 100}%` })));
      el.addEventListener("mousemove", (e) => showTip(e, label, [[null, "Sessions", nf.format(n)], [null, "Part des ouvertures", pct.format(n / max)]]));
      el.addEventListener("mouseleave", hideTip);
      return el;
    };
    $("#funnel").replaceChildren(
      row("", "Pages de paiement ouvertes", f.ouvertures),
      row("paid", "Paiements réussis", f.payees),
      h("p", { class: "f-foot" }, "Taux de conversion : ", h("b", {}, f.taux == null ? "—" : pct.format(f.taux)),
        `, soit ${plural(f.abandons, "page ouverte", "pages ouvertes")} sans paiement.`));
  }

  // ---------- Filtre des consultations internes ----------
  let filtreRempli = false;
  function renderFiltre(f) {
    $("#f-masquees").textContent = f.masquees ? `(${plural(f.masquees, "ouverture masquée", "ouvertures masquées")})` : "";
    $("#f-lien").textContent = f.lien_test || "—";
    if (!filtreRempli) {
      filtreRempli = true;
      $("#f-debut").value = (f.debut_comptage || "").slice(0, 16);
      $("#f-emails").value = (f.emails_internes || []).join(", ");
    }
  }
  $("#f-copy").addEventListener("click", async (e) => {
    const txt = $("#f-lien").textContent, btn = e.currentTarget;
    try { await navigator.clipboard.writeText(txt); btn.textContent = "Copié"; }
    catch { getSelection().selectAllChildren($("#f-lien")); btn.textContent = "Ctrl+C"; }
    setTimeout(() => (btn.textContent = "Copier"), 1500);
  });
  $("#f-save").addEventListener("click", async (e) => {
    const btn = e.currentTarget, err = $("#f-err");
    err.hidden = true; btn.disabled = true;
    try {
      await api("/api/reglages", { method: "PUT", body: JSON.stringify({
        debut_comptage: $("#f-debut").value || null,
        emails_internes: $("#f-emails").value.split(/[,;\s]+/).filter(Boolean),
      }) });
      filtreRempli = false;
      await load(false);
      btn.textContent = "Filtre enregistré"; setTimeout(() => (btn.textContent = "Enregistrer le filtre"), 1800);
    } catch (ex) { err.textContent = ex.message; err.hidden = false; }
    finally { btn.disabled = false; }
  });

  function renderBars(sel, rows, emptyText) {
    const box = $(sel);
    if (!rows.length) { box.replaceChildren(h("p", { class: "none" }, emptyText)); return; }
    const max = Math.max(...rows.map((r) => r.n), 1), total = rows.reduce((a, r) => a + r.n, 0);
    box.replaceChildren(...rows.map((r) => {
      const el = h("div", { class: "hb" }, h("span", {}, r.label), h("div", { class: "track" }, h("div", { class: "fill", style: `width:${(r.n / max) * 100}%` })), h("span", { class: "n" }, nf.format(r.n)));
      el.addEventListener("mousemove", (e) => showTip(e, r.label, [[null, "Nombre", nf.format(r.n)], [null, "Part", pct.format(r.n / total)]]));
      el.addEventListener("mouseleave", hideTip);
      return el;
    }));
  }

  function renderMots(cmds) {
    const mots = cmds.filter((c) => c.mot && c.statut !== "Remboursée");
    $("#mots").replaceChildren(...(mots.length ? mots.map((c) =>
      h("blockquote", { class: "mot" }, h("p", {}, `« ${c.mot} »`), h("footer", {}, `${c.client || "Anonyme"}, ${fmtDay(c.date, true)}`)))
      : [h("p", { class: "none" }, "Pas encore de message.")]));
  }

  // ---------- Commandes ----------
  const statutClass = (st) => ({ "En attente du stock": "st-attente", "À préparer": "st-preparer", "Expédiée": "st-livre", "Remise en main propre": "st-livre", "Remboursée": "st-rembourse", "Contestation perdue": "st-rembourse" }[st] || "st-attente");

  async function saveSuivi(o, patch, flagEl) {
    const body = { statut: o.statut, numero_suivi: o.numero_suivi, note: o.note, client: o.client, ...patch };
    try {
      await api(`/api/commandes/${encodeURIComponent(o.id)}/suivi`, { method: "PUT", body: JSON.stringify(body) });
      Object.assign(o, patch);
      if (flagEl) { flagEl.textContent = "Enregistré"; setTimeout(() => (flagEl.textContent = ""), 1800); }
      await load(false);
    } catch (e) {
      if (flagEl) { flagEl.textContent = e.message; flagEl.style.color = "var(--bad)"; }
    }
  }

  // Doublon possible encore à trancher (pas remboursé, pas marqué « Pas un doublon »)
  const DOUBLONS = "Doublons possibles";
  const aVerifier = (c) => c.doublon && !c.doublon_verifie && c.statut !== "Remboursée" && c.statut !== "Contestation perdue";

  function voirCommande(id) {
    state.filtre = "Tous"; state.recherche = ""; $("#search").value = "";
    state.ouvertes.add(id);
    if (location.hash !== "#commandes") history.pushState(null, "", "#commandes");
    showTab();
    renderCommandes();
    const tr = $(`#tbl-commandes tr[data-key="${CSS.escape(id)}"]`);
    if (tr) {
      const wrap = tr.closest(".table-wrap");
      if (wrap) wrap.scrollTop = tr.offsetTop - (wrap.querySelector("thead")?.offsetHeight || 40);
      tr.scrollIntoView({ block: "start" });
      scrollBy(0, -80);
    }
  }

  function blocDoublon(o) {
    const d = o.doublon;
    if (!d) return null;
    const flag = h("span", { class: "saved" });
    const precedente = h("button", { type: "button", class: "linkish", onclick: () => voirCommande(d.avec) },
      `${fmtDay(d.avec_date, true)} à ${d.avec_heure}`);
    if (o.doublon_verifie) {
      return h("div", { class: "doublon-box ok" }, h("b", {}, "Vérifié : ce n'est pas un doublon. "),
        "Seconde commande après celle du ", precedente, ". ",
        h("button", { type: "button", class: "linkish", onclick: () => saveSuivi(o, { doublon_verifie: false }, flag) }, "Annuler"), flag);
    }
    return h("div", { class: "doublon-box" },
      h("p", {}, h("b", {}, "Doublon possible. "), `Passée ${d.ecart} après la commande du `, precedente,
        ` (${eur.format(d.avec_montant)}${d.meme_montant ? ", même montant" : ""}) : ${d.raisons.join(", ")}.`),
      h("p", { class: "hint" }, "Demande au client s'il voulait bien deux commandes. Si c'est un doublon, rembourse celle-ci dans Stripe : elle passera en « Remboursée » et ne sera pas comptée."),
      h("button", { type: "button", class: "btn", onclick: () => saveSuivi(o, { doublon_verifie: true }, flag) }, "Pas un doublon, garder les deux"), flag);
  }

  function renderCommandes() {
    const all = state.data.commandes;
    const counts = all.reduce((a, c) => ((a[c.statut] = (a[c.statut] || 0) + 1), a), {});
    counts[DOUBLONS] = all.filter(aVerifier).length;
    const filtres = ["Tous", DOUBLONS, ...state.data.referentiels.statuts, "Remboursée", "Contestation perdue"].filter((f) => f === "Tous" || counts[f]);
    if (!filtres.includes(state.filtre)) state.filtre = "Tous";
    $("#chips-statut").replaceChildren(...filtres.map((f) => h("button", {
      class: `chip${f === DOUBLONS ? " chip-warn" : ""}`, type: "button", "aria-pressed": String(state.filtre === f),
      onclick: () => { state.filtre = f; renderCommandes(); },
    }, f, h("span", {}, nf.format(f === "Tous" ? all.length : counts[f])))));

    const q = state.recherche.trim().toLowerCase();
    const garde = (c) => state.filtre === "Tous" || (state.filtre === DOUBLONS ? aVerifier(c) : c.statut === state.filtre);
    let rows = all.filter((c) => garde(c) &&
      (!q || [c.client, c.ville, c.email, c.code_postal, c.numero_suivi].join(" ").toLowerCase().includes(q)));
    const { key, dir } = state.tri;
    rows = rows.slice().sort((a, b) => {
      const va = key === "date" ? a.created : a[key], vb = key === "date" ? b.created : b[key];
      return (typeof va === "number" ? va - vb : String(va).localeCompare(String(vb), "fr")) * dir;
    });
    $$("#tbl-commandes th[data-sort]").forEach((th) => th.setAttribute("aria-sort", th.dataset.sort === key ? (dir > 0 ? "ascending" : "descending") : "none"));
    $("#cmd-empty").hidden = rows.length > 0;
    const statuts = state.data.referentiels.statuts;
    const tbody = $("#tbl-commandes tbody");
    tbody.replaceChildren(...rows.flatMap((o) => {
      const perdue = o.statut === "Remboursée" || o.statut === "Contestation perdue";
      const flag = h("span", { class: "saved" });
      const sel = perdue ? h("span", { class: "status st-rembourse" }, o.statut)
        : h("select", { "aria-label": "Statut", class: statutClass(o.statut), onchange: (e) => saveSuivi(o, { statut: e.target.value }, flag) },
          statuts.map((st) => h("option", { value: st, selected: st === o.statut }, st)));
      const suivi = h("input", { type: "text", value: o.numero_suivi, placeholder: "—", "aria-label": "Numéro de suivi", size: 14,
        onchange: (e) => saveSuivi(o, { numero_suivi: e.target.value.trim() }, flag) });
      const suspect = aVerifier(o);
      const tr = h("tr", { class: `row${suspect ? " row-doublon" : ""}`, "data-key": o.id },
        h("td", {}, fmtDay(o.date)),
        h("td", {}, o.client, suspect ? h("span", { class: "status st-doublon", title: "Doublon possible : ouvrir la ligne pour vérifier" }, "Doublon ?") : null),
        h("td", { class: "hide-sm" }, o.ville),
        h("td", { class: "num" }, nf.format(o.quantite)), h("td", { class: "hide-sm" }, o.livraison),
        h("td", { class: "num" }, eur.format(o.montant)), h("td", {}, sel, flag), h("td", { class: "hide-sm" }, suivi));
      tr.addEventListener("click", (e) => {
        if (e.target.closest("select, input, a, button")) return;
        state.ouvertes.has(o.id) ? state.ouvertes.delete(o.id) : state.ouvertes.add(o.id);
        renderCommandes();
      });
      if (!state.ouvertes.has(o.id)) return [tr];
      const noteFlag = h("span", { class: "saved" });
      const dl = h("dl", { class: "detail-grid" },
        h("div", {}, h("dt", {}, "E-mail"), h("dd", {}, o.email || "—"), h("dt", {}, "Téléphone"), h("dd", {}, o.telephone || "—")),
        h("div", {}, h("dt", {}, "Adresse"), h("dd", {}, o.adresse || "—", h("br"), `${o.code_postal} ${o.ville}`), h("dt", {}, "Livraison"), h("dd", {}, `${o.livraison} (${eur.format(o.port)})`)),
        h("div", {}, h("dt", {}, "Connu par"), h("dd", {}, o.source), h("dt", {}, "Mot pour les auteurs"), h("dd", {}, o.mot || "—"),
          o.litige ? [h("dt", {}, "Contestation de paiement"), h("dd", {}, h("span", { class: "status st-rembourse" }, o.litige))] : null),
        h("div", {}, h("dt", {}, "Statut mis à jour le"), h("dd", {}, o.date_statut ? fmtDay(o.date_statut, true) : "—"),
          h("dt", {}, "Note interne"), h("dd", {}, h("input", { type: "text", value: o.note, placeholder: "Ex. remis au repas du 14", onchange: (e) => saveSuivi(o, { note: e.target.value.trim() }, noteFlag) }), noteFlag),
          o.recu_url ? h("dd", {}, h("a", { href: o.recu_url, target: "_blank", rel: "noopener" }, "Voir le reçu Stripe")) : null));
      const perdueOuSans = perdue || !o.doublon;
      return [tr, h("tr", { class: "detail" }, h("td", { colSpan: 8 }, perdueOuSans ? null : blocDoublon(o), dl))];
    }));
  }

  $$("#tbl-commandes th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    state.tri = { key: k, dir: state.tri.key === k ? -state.tri.dir : (k === "client" || k === "ville" ? 1 : -1) };
    renderCommandes();
  }));
  $("#search").addEventListener("input", (e) => { state.recherche = e.target.value; renderCommandes(); });

  // ---------- Suppression en deux clics ----------
  function delButton(onConfirm) {
    const b = h("button", { class: "del", type: "button" }, "Supprimer");
    let armed = false, t;
    b.addEventListener("click", async () => {
      if (!armed) { armed = true; b.classList.add("confirm"); b.textContent = "Confirmer ?"; t = setTimeout(() => { armed = false; b.classList.remove("confirm"); b.textContent = "Supprimer"; }, 3000); return; }
      clearTimeout(t); b.disabled = true;
      try { await onConfirm(); await load(false); } catch (e) { b.textContent = e.message; }
    });
    return b;
  }

  // ---------- Stock ----------
  const SIGNE = { reception: 1, vente_directe: -1, offert: -1, casse: -1, ajustement: 1 };
  function renderMouvements() {
    const types = state.data.referentiels.types_mouvement;
    const rows = state.data.mouvements;
    $("#tbl-mvt tbody").replaceChildren(...(rows.length ? rows.map((m) => {
      const sg = SIGNE[m.type], q = Number(m.quantite) * (sg || 1);
      const qTxt = sg ? `${q > 0 ? "+" : q < 0 ? "−" : ""}${nf.format(Math.abs(q))}` : nf.format(q);
      return h("tr", {}, h("td", { class: "nw" }, fmtDay(m.date)), h("td", { class: "type-tag" }, types[m.type] || m.type),
        h("td", { class: `num ${sg ? (q > 0 ? "qpos" : "qneg") : ""}` }, qTxt),
        h("td", { class: "num hide-sm" }, m.montant != null ? eur.format(m.montant) : ""),
        h("td", { class: "hide-sm" }, m.note || ""),
        h("td", { class: "num" }, m.id ? delButton(() => api(`/api/mouvements/${encodeURIComponent(m.id)}`, { method: "DELETE" })) : ""));
    }) : [h("tr", {}, h("td", { colSpan: 6, class: "empty" }, "Journal vide."))]));
  }

  function renderDepenses() {
    const rows = state.data.depenses;
    $("#tbl-dep tbody").replaceChildren(...(rows.length ? rows.map((d) =>
      h("tr", {}, h("td", { class: "nw" }, fmtDay(d.date)), h("td", {}, d.categorie), h("td", { class: "num nw" }, eur.format(d.montant)),
        h("td", {}, h("span", { class: `status ${d.statut === "À payer" ? "st-preparer" : "st-livre"}` }, d.statut || "Payée")),
        h("td", { class: "hide-sm" }, d.note || ""), h("td", { class: "num" }, delButton(() => api(`/api/depenses/${encodeURIComponent(d.id)}`, { method: "DELETE" })))))
      : [h("tr", {}, h("td", { colSpan: 6, class: "empty" }, "Aucune dépense saisie."))]));
  }

  function renderRentab(k) {
    const seuil = k.seuil_rentabilite, txt = $("#rentab-txt"), g = $("#rentab-gauge");
    $("#k-seuil").textContent = seuil ? plural(seuil, "jeu", "jeux") : "—";
    if (!k.depenses) { txt.textContent = "Saisis tes dépenses ci-dessous pour calculer le seuil de rentabilité."; g.replaceChildren(); return; }
    if (!seuil) { txt.textContent = "Le seuil se calculera dès la première vente (il faut un prix moyen)."; g.replaceChildren(); return; }
    const reste = Math.max(0, seuil - k.jeux_vendus);
    const fin = reste
      ? ["Encore ", h("b", {}, nf.format(reste)), " à vendre", k.tirage ? `, le seuil représente ${pct.format(seuil / k.tirage)} du tirage.` : "."]
      : [h("b", {}, "Seuil atteint : chaque jeu vendu en plus est du bénéfice.")];
    txt.replaceChildren("Il faut vendre ", h("b", {}, plural(seuil, "jeu", "jeux")), ` pour couvrir ${eur.format(k.depenses)} de dépenses. `, ...fin);
    const fill = h("div", { class: "seg", style: `width:${Math.min(100, (k.jeux_vendus / seuil) * 100)}%;background:var(--c1)` });
    fill.addEventListener("mousemove", (e) => showTip(e, "Jeux vendus", [[null, "Vendus", nf.format(k.jeux_vendus)], [null, "Seuil", nf.format(seuil)]]));
    fill.addEventListener("mouseleave", hideTip);
    g.replaceChildren(fill);
  }

  const emptyRow = (cols, text) => [h("tr", {}, h("td", { colSpan: cols, class: "empty" }, text))];
  const VERSEMENT_CLASS = { paid: "st-livre", pending: "st-attente", in_transit: "st-preparer", canceled: "st-rembourse", failed: "st-rembourse" };
  function renderVersements() {
    const rows = state.data.versements || [];
    $("#tbl-versements tbody").replaceChildren(...(rows.length ? rows.map((v) =>
      h("tr", {}, h("td", {}, fmtDay(v.date, true)), h("td", { class: "num" }, eur.format(v.montant)),
        h("td", {}, h("span", { class: `status ${VERSEMENT_CLASS[v.code] || "st-attente"}` }, v.statut))))
      : emptyRow(3, "Aucun versement pour l'instant.")));
  }
  function renderContestations() {
    const rows = state.data.contestations || [];
    $("#tbl-contestations tbody").replaceChildren(...(rows.length ? rows.map((c) =>
      h("tr", {}, h("td", { class: "nw" }, fmtDay(c.date)), h("td", {}, c.client || "—"), h("td", { class: "num nw" }, eur.format(c.montant)),
        h("td", { class: "hide-sm" }, c.motif),
        h("td", {}, h("span", { class: `status ${c.a_traiter ? "st-rembourse" : c.statut === "Gagnée" ? "st-livre" : "st-attente"}` }, c.statut)),
        h("td", { class: "hide-sm nw" }, c.echeance ? fmtDay(c.echeance) : "—")))
      : emptyRow(6, "Aucune contestation. Tant mieux.")));
  }

  let selectsFilled = false;
  function fillSelects() {
    if (selectsFilled) return;
    selectsFilled = true;
    const ref = state.data.referentiels;
    $("#mvt-type").replaceChildren(...Object.entries(ref.types_mouvement).map(([v, l]) => h("option", { value: v, selected: v === "reception" }, l)));
    $("#dep-cat").replaceChildren(...ref.categories_depense.map((c) => h("option", { value: c }, c)));
    $("#mvt-date").value = $("#dep-date").value = today();
    syncMontant();
  }
  const syncMontant = () => { $("#mvt-montant-wrap").hidden = $("#mvt-type").value !== "vente_directe"; };
  $("#mvt-type").addEventListener("change", syncMontant);

  async function submit(btn, errEl, path, body, reset) {
    errEl.hidden = true; btn.disabled = true;
    try { await api(path, { method: "POST", body: JSON.stringify(body) }); reset(); await load(false); }
    catch (e) { errEl.textContent = e.message; errEl.hidden = false; }
    finally { btn.disabled = false; }
  }
  $("#mvt-add").addEventListener("click", (e) => {
    const montant = $("#mvt-montant").value;
    submit(e.currentTarget, $("#mvt-err"), "/api/mouvements", {
      date: $("#mvt-date").value, type: $("#mvt-type").value, quantite: parseInt($("#mvt-qte").value, 10),
      montant: montant === "" ? null : Number(montant), note: $("#mvt-note").value.trim(),
    }, () => { $("#mvt-note").value = ""; $("#mvt-montant").value = ""; $("#mvt-qte").value = "1"; });
  });
  $("#dep-add").addEventListener("click", (e) => {
    submit(e.currentTarget, $("#dep-err"), "/api/depenses", {
      date: $("#dep-date").value, categorie: $("#dep-cat").value, montant: Number($("#dep-montant").value), statut: $("#dep-statut").value, note: $("#dep-note").value.trim(),
    }, () => { $("#dep-note").value = ""; $("#dep-montant").value = ""; });
  });

  // ---------- Instagram ----------
  const fmtN = (v) => (v == null ? "—" : nf.format(v));
  function renderInsta() {
    const ig = state.data && state.data.instagram;
    const al = $("#ig-alerte");
    if (!ig) { al.textContent = "Instagram n'est pas encore relié au dashboard."; al.hidden = false; return; }
    const k = ig.kpis;
    al.hidden = !ig.alerte; al.textContent = ig.alerte || "";
    $("#ig-abonnes").textContent = fmtN(k.abonnes);
    $("#ig-abonnes-7j").textContent = k.abonnes_7j == null ? (ig.compte ? `@${ig.compte}` : "—")
      : `${k.abonnes_7j >= 0 ? "+" : ""}${nf.format(k.abonnes_7j)} en 7 jours`;
    $("#ig-portee").textContent = fmtN(k.portee_7j);
    $("#ig-inter").textContent = fmtN(k.interactions_7j);
    $("#ig-clics").textContent = fmtN(k.clics_profil_7j);
    $("#ig-clients").textContent = nf.format(k.clients_instagram);
    $("#ig-clients-sub").textContent = k.clients_source_connue
      ? `${pct.format(k.clients_instagram / k.clients_source_connue)} des ${plural(k.clients_source_connue, "commande", "commandes")} où le client a répondu`
      : "selon « Comment avez-vous connu le jeu ? »";
    renderIgProfil(ig);
    renderIgCharts(ig);
    renderIgPosts(ig);
    renderIgAudience(ig);
    renderIgHeures(ig);
    renderIgCommentaires(ig);
    const maj = ig.derniere_collecte ? new Date(ig.derniere_collecte) : null;
    $("#ig-maj").textContent = maj ? `Instagram collecté le ${dLong.format(maj)} à ${tHM.format(maj)} (deux fois par jour, vers 11 h et 23 h). Les chiffres récents se complètent dans les 48 h.`
      : "La collecte Instagram tourne deux fois par jour, vers 11 h et 23 h.";
  }

  function renderIgCharts(ig) {
    const jours = ig.jours, n = jours.length;
    const postsParId = Object.fromEntries(ig.posts.map((p) => [p.id, p]));
    const W = Math.max($("#chart-ig-portee").clientWidth, 300), m = { t: 22, r: 12, b: 8, l: 38 };
    const iw = W - m.l - m.r, bw = iw / n, x = (i) => m.l + i * bw;
    const draw = (sel, H, titre, max, bars, extra, bas) => {
      const box = $(sel); box.replaceChildren();
      const ih = H - m.t - (bas ? 26 : m.b);
      const step = niceStep(max, 3), top = Math.max(step, Math.ceil(max / step) * step);
      const y = (v) => m.t + ih - (v / top) * ih;
      const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": titre });
      const t = s("text", { class: "ch-title", x: m.l, y: 12 }); t.textContent = titre; svg.append(t);
      const grid = s("g", { class: "grid" });
      for (let v = 0; v <= top + 1e-9; v += step) {
        grid.append(s("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
        const tx = s("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }); tx.textContent = nf.format(v); grid.append(tx);
      }
      svg.append(grid);
      jours.forEach((j, i) => { if (j.posts.length) svg.append(s("line", { class: "post-mark", x1: x(i) + bw / 2, x2: x(i) + bw / 2, y1: m.t - 4, y2: m.t + ih })); });
      bars.forEach(([cls, key]) => jours.forEach((j, i) => {
        const v = j[key]; if (!v) return;
        const hgt = Math.max(2, y(0) - y(v)), w = Math.max(2, bw - 2);
        svg.append(s("path", { class: cls, d: `M${x(i) + 1},${y(0)}v${-hgt + 2}q0,-2 2,-2h${w - 4}q2,0 2,2v${hgt - 2}z` }));
      }));
      if (extra) extra(svg, y);
      if (bas) {
        const nT = Math.min(n, Math.max(2, Math.floor(iw / 80)));
        [...new Set(Array.from({ length: nT }, (_, i) => Math.round((i * (n - 1)) / (nT - 1))))].forEach((i) => {
          const tx = s("text", { x: x(i) + bw / 2, y: H - 6, "text-anchor": i === n - 1 ? "end" : i === 0 ? "start" : "middle" }); tx.textContent = fmtDay(jours[i].jour); svg.append(tx);
        });
      }
      jours.forEach((j, i) => {
        const hit = s("rect", { class: "col-hit", x: x(i), y: m.t, width: bw, height: ih });
        hit.addEventListener("mousemove", (e) => showTip(e, fmtDay(j.jour, true), [
          [cssVar("--c4"), "Portée Instagram", fmtN(j.portee)],
          ...(j.portee_non_abonnes != null ? [[null, "dont non-abonnés", fmtN(j.portee_non_abonnes)]] : []),
          [null, "Nouveaux abonnés", fmtN(j.abonnements ?? j.nouveaux_abonnes)],
          ...(j.desabonnements != null ? [[null, "Désabonnements", fmtN(j.desabonnements)]] : []),
          [null, "Clics vers le lien du profil", fmtN(j.clics_profil)],
          [cssVar("--c1"), "Ouvertures du lien de paiement", nf.format(j.ouvertures)],
          [cssVar("--c2"), "Jeux commandés", nf.format(j.jeux)],
          ...j.posts.map((id) => [null, "Publication", (postsParId[id] && postsParId[id].legende || "").slice(0, 40) || "sans légende"])]));
        hit.addEventListener("mouseleave", hideTip);
        svg.append(hit);
      });
      box.append(svg);
    };
    draw("#chart-ig-portee", 150, "Comptes touchés sur Instagram", Math.max(1, ...jours.map((j) => j.portee || 0)), [["bar-portee", "portee"]]);
    draw("#chart-ig-ventes", 176, "Lien de paiement", Math.max(1, ...jours.map((j) => Math.max(j.ouvertures, j.jeux))), [["bar-ouv", "ouvertures"]],
      (svg, y) => jours.forEach((j, i) => { if (j.jeux) svg.append(s("circle", { class: "dot-jeux", cx: x(i) + bw / 2, cy: y(j.jeux), r: Math.max(4, Math.min(5, bw / 2)) })); }), true);
    $("#ig-legend").replaceChildren(
      h("li", {}, h("i", { style: "background:var(--c4)" }), "Comptes touchés sur Instagram"),
      h("li", {}, h("i", { style: "background:var(--c1)" }), "Ouvertures du lien de paiement"),
      h("li", {}, h("i", { style: "background:var(--c2);border-radius:50%" }), "Jeux commandés"),
      h("li", {}, h("i", { style: "background:none;border-left:1px dashed var(--muted);border-radius:0;width:6px" }), "Publication"));
  }

  function renderIgPosts(ig) {
    const base = ig.base_48h;
    const vs = (v, b) => {
      if (b == null) return null;
      const r = b ? v / b : null;
      return h("span", { class: `vs${r != null && r >= 1.5 ? " up" : ""}` }, `moyenne ${nf.format(Math.round(b * 10) / 10)}`);
    };
    const rows = ig.posts.map((p) => {
      const e = p.effet;
      const type = p.type === "STORY" ? "Story" : p.type === "REELS" ? "Reel" : p.format === "CAROUSEL_ALBUM" ? "Carrousel" : "Post";
      const effetCell = (cle, b) => !e ? h("td", { class: "num", title: "Publiée avant l'ouverture des ventes" }, "—")
        : h("td", { class: "num" }, nf.format(e[cle]), e.complet ? null : h("span", { class: "vs" }, "en cours"), vs(e[cle], b));
      return h("tr", {},
        h("td", { class: "nw" }, fmtDay(p.date, true), h("span", { class: "vs" }, tHM.format(new Date(p.date)))),
        h("td", {}, h("div", { class: "ig-leg" }, vignette(p.id, "thumb"),
          h("div", {}, p.lien ? h("a", { href: p.lien, target: "_blank", rel: "noopener" }, type) : type,
            p.audio === "MUSIC" ? " avec musique" : "",
            h("small", { title: p.legende_complete || "" }, p.legende || "")))),
        h("td", { class: "num" }, fmtN(p.reach)),
        effetCell("ouvertures", base && base.ouvertures),
        effetCell("jeux", base && base.commandes),
        h("td", { class: "num hide-sm" }, fmtN(p.total_interactions)),
        h("td", { class: "num hide-sm" }, p.engagement == null ? "—" : pct.format(p.engagement)),
        h("td", { class: "num hide-sm" }, fmtN(p.shares)),
        h("td", { class: "num hide-sm" }, fmtN(p.saved)),
        h("td", { class: "num hide-sm" }, fmtN(p.follows)));
    });
    $("#tbl-ig tbody").replaceChildren(...(rows.length ? rows : emptyRow(10, "Aucune publication collectée pour l'instant.")));
  }

  // Visuel copié dans le bucket par la collecte ; masqué s'il n'existe pas (encore)
  const vignette = (id, cls) => h("img", { class: cls, src: `/api/instagram/media/${encodeURIComponent(id)}`, alt: "", loading: "lazy",
    onerror: (e) => { e.target.style.visibility = "hidden"; } });

  function renderIgProfil(ig) {
    const box = $("#ig-profil"), pr = ig.profil || {};
    box.hidden = !ig.compte;
    if (!ig.compte) return;
    box.replaceChildren(...[pr.photo ? vignette("profil") : null,
      h("div", {}, h("b", {}, `@${ig.compte}`), pr.name ? `, ${pr.name}` : "", pr.biography ? h("p", {}, pr.biography) : null)].filter(Boolean));
  }

  function renderIgAudience(ig) {
    const box = $("#ig-audience"), a = ig.audience && (ig.audience.abonnes || ig.audience.engages);
    if (!a) { box.replaceChildren(h("p", { class: "none" }, "Instagram ne donne la répartition qu'à partir de 100 abonnés.")); return; }
    $("#ig-aud-note").textContent = ig.audience.abonnes ? "Répartition de tes abonnés fournie par Instagram." : "Répartition des comptes qui ont interagi ce mois-ci (celle des abonnés n'est pas encore disponible).";
    const blocs = [["Âge", "age"], ["Genre", "genre"], ["Villes", "villes"], ["Pays", "pays"]];
    box.replaceChildren(...blocs.filter(([, k]) => (a[k] || []).length).map(([t, k], i) => h("div", {}, h("h3", {}, t), h("div", { class: "hbars", id: `ig-aud-${i}` }))));
    blocs.forEach(([, k], i) => { if ((a[k] || []).length) renderBars(`#ig-aud-${i}`, a[k], ""); });
  }

  function renderIgHeures(ig) {
    const box = $("#ig-heures"), m = ig.heures || {};
    const vals = Object.values(m);
    if (!vals.length) { box.replaceChildren(h("p", { class: "none", style: "grid-column:1/-1" }, "Disponible à partir de 100 abonnés, après la première collecte.")); return; }
    const max = Math.max(...vals), jours = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"], cells = [h("span", {})];
    for (let hr = 0; hr < 24; hr++) cells.push(h("span", { class: "h" }, hr % 3 === 0 ? `${hr}h` : ""));
    jours.forEach((j, d) => {
      cells.push(h("span", { class: "l" }, j));
      for (let hr = 0; hr < 24; hr++) {
        const v = m[`${d}-${hr}`];
        const c = h("span", { class: `c${v == null ? " vide" : ""}`, style: v == null ? "" : `opacity:${(0.12 + 0.88 * v / max).toFixed(2)}` });
        c.addEventListener("mousemove", (e) => showTip(e, `${["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"][d]} ${hr} h`, [[null, "Abonnés en ligne (moyenne)", v == null ? "—" : nf.format(v)]]));
        c.addEventListener("mouseleave", hideTip);
        cells.push(c);
      }
    });
    box.replaceChildren(...cells);
  }

  function renderIgCommentaires(ig) {
    const coms = ig.commentaires || [];
    $("#ig-com-count").textContent = ig.commentaires_sans_reponse ? `${nf.format(ig.commentaires_sans_reponse)} sans réponse` : "";
    $("#ig-com-count").classList.toggle("warn", ig.commentaires_sans_reponse > 0);
    $("#ig-commentaires").replaceChildren(...(coms.length ? coms.map((c) => h("li", {}, vignette(c.post, "thumb"),
      h("div", {},
        h("p", {}, h("b", {}, c.pseudo), " ", c.texte),
        h("small", {}, c.date ? `${fmtDay(c.date, true)} à ${tHM.format(new Date(c.date))}` : "", c.likes ? `, ${plural(c.likes, "j'aime", "j'aime")}` : "",
          c.masque ? ", masqué" : "", ", ", c.post_lien ? h("a", { href: c.post_lien, target: "_blank", rel: "noopener" }, "voir le post") : "",
          !c.repondu && c.pseudo !== ig.compte ? h("span", { class: "attente" }, ", sans réponse") : ""),
        ...c.reponses.map((r) => h("div", { class: "rep" }, h("b", {}, r.pseudo), " ", r.texte)))))
      : [h("li", { class: "none" }, "Aucun commentaire collecté pour l'instant.")]));
  }

  // ---------- Onglets ----------
  function showTab() {
    const name = (location.hash || "#apercu").slice(1);
    const tab = $$(".page").some((p) => p.dataset.page === name) ? name : "apercu";
    $$(".page").forEach((p) => (p.hidden = p.dataset.page !== tab));
    $$(".tabs a").forEach((a) => a.setAttribute("aria-selected", String(a.dataset.tab === tab)));
    if (tab === "apercu" && state.data) renderVentes(state.data.serie);
    if (tab === "instagram" && state.data && state.data.instagram) renderIgCharts(state.data.instagram);
  }
  // Les onglets sont des ancres (#apercu…) : le navigateur descendrait jusqu'à la section
  // et cacherait les alertes du haut. On reste en haut de page.
  addEventListener("hashchange", () => { showTab(); scrollTo(0, 0); });
  let rt;
  addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if (!state.data) return; renderVentes(state.data.serie); if (state.data.instagram) renderIgCharts(state.data.instagram); }, 150); });

  $("#refresh").addEventListener("click", () => load(true));
  showTab();
  load(false);
  setInterval(() => { if (!document.hidden) load(false); }, 5 * 60 * 1000);
})();
