/* HR Playbook — reusable, accessible UI component library (framework-free).
 *
 * Every factory returns an HTML string (composable into the existing render
 * functions). Interactive components (tabs, chips, expanders, sortable headers)
 * carry ARIA + data-hooks; call UI.enhance(rootEl) once after injecting HTML to
 * wire keyboard + click behavior (idempotent, event-delegated).
 *
 * States are first-class: UI.skeleton / UI.empty / UI.error / UI.spinner.
 */
(function (global) {
  "use strict";

  // --- safety: escape interpolated text (prevents broken markup / injection) ---
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const cx = (...xs) => xs.filter(Boolean).join(" ");
  let _id = 0;
  const uid = (p) => `${p}-${++_id}`;

  const GRADE_TONE = { "A+": "green", "A": "green", "B": "yellow", "C": "yellow",
    "D": "red", "TOO_SMALL": "gray", "Unknown": "gray", "—": "gray" };

  const UI = {
    esc, cx,

    /* ---- badge / grade pill ---- tone: green|yellow|red|blue|purple|gray */
    badge(text, tone = "gray", { title } = {}) {
      return `<span class="ui-badge ui-badge--${tone}"${title ? ` title="${esc(title)}"` : ""}>${esc(text)}</span>`;
    },
    grade(g, opts) { return UI.badge(g ?? "—", GRADE_TONE[g] || "gray", opts); },

    /* ---- tags with priority cap + accessible "+N more" ---- */
    tags(list, { max = 3 } = {}) {
      const arr = (Array.isArray(list) ? list : String(list || "").split("|")).filter(Boolean);
      if (!arr.length) return `<span class="ui-card__sub">—</span>`;
      const shown = arr.slice(0, max);
      const extra = arr.length - shown.length;
      const chips = shown.map((t) => `<span class="ui-tag">${esc(t)}</span>`).join("");
      const more = extra > 0
        ? `<span class="ui-tag ui-tagmore" tabindex="0" role="button"
             aria-label="${extra} more tags: ${esc(arr.slice(max).join(", "))}"
             title="${esc(arr.slice(max).join(", "))}">+${extra} more</span>` : "";
      return chips + more;
    },

    /* ---- confidence stars (screen-reader labelled) ---- */
    stars(n, max = 5) {
      n = Math.max(0, Math.min(max, n | 0));
      return `<span class="ui-stars" role="img" aria-label="${n} of ${max}">`
        + "★".repeat(n) + "☆".repeat(max - n) + "</span>";
    },

    /* ---- stat card ---- */
    stat(value, label) {
      return `<div class="ui-stat"><div class="ui-stat__n">${esc(value)}</div>`
        + `<div class="ui-stat__l">${esc(label)}</div></div>`;
    },

    /* ---- generic card ---- */
    card({ title, name, sub, grade, stars, body = "" } = {}) {
      return `<div class="ui-card">`
        + (title ? `<div class="ui-card__title">${esc(title)}</div>` : "")
        + (grade != null ? `<div>${UI.grade(grade)}${stars != null ? " " + UI.stars(stars) : ""}</div>` : "")
        + (name ? `<div class="ui-card__name">${esc(name)}</div>` : "")
        + (sub ? `<div class="ui-card__sub">${sub}</div>` : "")
        + body + `</div>`;
    },

    /* ---- spinner + sr-only status ---- */
    spinner(label = "Loading") {
      return `<span class="ui-spinner" aria-hidden="true"></span>`
        + `<span class="sr-only" role="status">${esc(label)}…</span>`;
    },

    /* ---- loading skeleton table (announced politely) ---- */
    skeleton(rows = 6, cols = 5) {
      const bars = Array.from({ length: rows }, () =>
        `<div class="ui-skel" style="width:${60 + Math.floor(Math.random() * 35)}%"></div>`).join("");
      return `<div class="ui-table-wrap" aria-busy="true">`
        + `<span class="sr-only" role="status">Loading ${rows} rows…</span>`
        + bars + `</div>`;
    },

    /* ---- empty / error states ---- */
    empty(message = "Nothing here yet.", { icon = "—" } = {}) {
      return `<div class="ui-state" role="status">${icon} ${esc(message)}</div>`;
    },
    error(message = "Something went wrong.", { retryLabel } = {}) {
      return `<div class="ui-state ui-state--error" role="alert">⚠ ${esc(message)}`
        + (retryLabel ? `<div><button class="ui-btn" data-ui-retry>${esc(retryLabel)}</button></div>` : "")
        + `</div>`;
    },

    /* ---- chips (single/multi toggle) ----
       items: [{value,label,pressed}]; group used for the data-hook */
    chips(items, { group = "chips" } = {}) {
      return `<div class="ui-chips" role="group" data-ui-chips="${esc(group)}">`
        + items.map((it) =>
          `<button class="ui-chip" type="button" data-value="${esc(it.value)}"`
          + ` aria-pressed="${it.pressed ? "true" : "false"}">${esc(it.label)}</button>`).join("")
        + `</div>`;
    },

    /* ---- accessible data table ----
       columns: [{key,label,sortable,align,render(row)->html}], rows, sort:{key,dir} */
    table({ columns, rows, caption, sort, empty = "No rows.", minWidth } = {}) {
      if (!rows || !rows.length) return UI.empty(empty);
      const head = columns.map((c) => {
        const sorted = sort && sort.key === c.key;
        const ariaSort = sorted ? (sort.dir < 0 ? "descending" : "ascending") : "none";
        const arrow = sorted ? (sort.dir < 0 ? " ▼" : " ▲") : "";
        const inner = c.sortable
          ? `<button class="ui-th-btn" data-ui-sort="${esc(c.key)}"
               aria-label="Sort by ${esc(c.label)}">${esc(c.label)}${arrow}</button>`
          : esc(c.label);
        return `<th scope="col"${c.sortable ? ` aria-sort="${ariaSort}"` : ""}>${inner}</th>`;
      }).join("");
      const body = rows.map((r) => "<tr>" + columns.map((c) => {
        const v = c.render ? c.render(r) : esc(r[c.key] ?? "—");
        return `<td${c.align ? ` style="text-align:${c.align}"` : ""}>${v}</td>`;
      }).join("") + "</tr>").join("");
      return `<div class="ui-table-wrap"><table class="ui-table"`
        + (minWidth ? ` style="min-width:${minWidth}px"` : "") + `>`
        + (caption ? `<caption>${esc(caption)}</caption>` : "")
        + `<thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
    },

    /* ---- tabs (WAI-ARIA tablist; arrow/Home/End keys via enhance) ----
       tabs:[{id,label,panel}]; activeId */
    tabs(tabs, { activeId, group = "tabs" } = {}) {
      const active = activeId || (tabs[0] && tabs[0].id);
      const list = `<div class="ui-tabs" role="tablist" data-ui-tabs="${esc(group)}">`
        + tabs.map((t) => `<button class="ui-tab" role="tab" id="tab-${esc(t.id)}"`
          + ` aria-controls="panel-${esc(t.id)}" aria-selected="${t.id === active}"`
          + ` tabindex="${t.id === active ? 0 : -1}" data-tab="${esc(t.id)}">${esc(t.label)}</button>`).join("")
        + `</div>`;
      const panels = tabs.map((t) => `<div role="tabpanel" id="panel-${esc(t.id)}"`
        + ` aria-labelledby="tab-${esc(t.id)}"${t.id === active ? "" : " hidden"}>${t.panel || ""}</div>`).join("");
      return list + panels;
    },

    /* ---- expandable "view details" (button + collapsible panel) ---- */
    expandable(triggerLabel, panelHtml, { open = false } = {}) {
      const pid = uid("exp");
      return `<button class="ui-exp__btn" type="button" data-ui-exp aria-expanded="${open}"`
        + ` aria-controls="${pid}">${esc(triggerLabel)} ${open ? "▴" : "▾"}</button>`
        + `<div class="ui-exp__panel" id="${pid}"${open ? "" : " hidden"}>${panelHtml}</div>`;
    },

    /* ---- mount + enhance ---- */
    mount(el, html) { (typeof el === "string" ? document.querySelector(el) : el).innerHTML = html; },

    /* Wire keyboard + click for interactive components under `root`.
       Idempotent: marks root so it only binds once. Event-delegated. */
    enhance(root = document) {
      if (root.__uiEnhanced) return; root.__uiEnhanced = true;

      root.addEventListener("click", (e) => {
        // expandable
        const exp = e.target.closest("[data-ui-exp]");
        if (exp) {
          const panel = document.getElementById(exp.getAttribute("aria-controls"));
          const open = exp.getAttribute("aria-expanded") === "true";
          exp.setAttribute("aria-expanded", String(!open));
          if (panel) panel.hidden = open;
          exp.textContent = exp.textContent.replace(/[▾▴]/, open ? "▾" : "▴");
          return;
        }
        // chip toggle
        const chip = e.target.closest(".ui-chip");
        if (chip) {
          const pressed = chip.getAttribute("aria-pressed") === "true";
          chip.setAttribute("aria-pressed", String(!pressed));
          chip.dispatchEvent(new CustomEvent("ui:chip", { bubbles: true,
            detail: { value: chip.dataset.value, pressed: !pressed } }));
          return;
        }
        // tab select
        const tab = e.target.closest('[role="tab"]');
        if (tab) { UI._selectTab(tab); return; }
      });

      // keyboard: tablist arrows/Home/End; Enter/Space on role=button spans
      root.addEventListener("keydown", (e) => {
        const tab = e.target.closest('[role="tab"]');
        if (tab) {
          const tabs = [...tab.closest('[role="tablist"]').querySelectorAll('[role="tab"]')];
          let i = tabs.indexOf(tab);
          if (e.key === "ArrowRight" || e.key === "ArrowDown") i = (i + 1) % tabs.length;
          else if (e.key === "ArrowLeft" || e.key === "ArrowUp") i = (i - 1 + tabs.length) % tabs.length;
          else if (e.key === "Home") i = 0;
          else if (e.key === "End") i = tabs.length - 1;
          else return;
          e.preventDefault(); tabs[i].focus(); UI._selectTab(tabs[i]);
          return;
        }
        if ((e.key === "Enter" || e.key === " ")
            && e.target.matches('[role="button"]:not(button)')) {
          e.preventDefault(); e.target.click();
        }
      });
    },

    _selectTab(tab) {
      const list = tab.closest('[role="tablist"]');
      list.querySelectorAll('[role="tab"]').forEach((t) => {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        const panel = document.getElementById(t.getAttribute("aria-controls"));
        if (panel) panel.hidden = !on;
      });
      tab.dispatchEvent(new CustomEvent("ui:tab", { bubbles: true, detail: { id: tab.dataset.tab } }));
    },
  };

  global.UI = UI;
})(window);
