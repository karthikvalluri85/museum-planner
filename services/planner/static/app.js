// Museum planner frontend. One ES module, no dependencies.
// Sections: api client, constants, pure helpers, state, render, event wiring.

// ---------------------------------------------------------------- api client

export class ApiError extends Error {
  constructor(message, { details = [], status = 0, network = false } = {}) {
    super(message);
    this.details = details;
    this.status = status;
    this.network = network;
  }
}

async function request(path, options) {
  let res;
  try {
    res = await fetch(path, options);
  } catch {
    throw new ApiError("We could not reach the planner. Check your connection and try again.", { network: true });
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    // Non-JSON body; handled below.
  }
  if (!res.ok) {
    const err = body && body.error;
    throw new ApiError(
      (err && err.message) || `The planner answered with an error (${res.status}).`,
      { details: (err && err.details) || [], status: res.status },
    );
  }
  if (body === null) throw new ApiError("The planner sent a reply we could not read.", { status: res.status });
  return body;
}

export const api = {
  listMuseums: () => request("api/museums"),
  getMuseum: (id) => request(`api/museums/${encodeURIComponent(id)}`),
  createPlan: (body) =>
    request("api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};

// ----------------------------------------------------------------- constants

export const MAX_INTERESTS = 12;
export const LATE_CHOICES = [15, 20, 30, 45];
const BUDGET_MIN = 15;
const BUDGET_MAX = 240;
const MAX_EXCLUDED = 300;

const KIND_LABEL = {
  elevator: "Elevator",
  ramp: "Ramp",
  stairs: "Stairs",
  escalator: "Escalator",
};

const FLOOR_STATUS_TEXT = {
  unverified: "Floor data: not yet checked by a person. Confirm elevators and step-free routes with museum staff.",
  partially_verified: "Floor data: partly checked by a person against the museum's published plan. Confirm with museum staff.",
  verified: "Floor data: checked by a person against the museum's published plan.",
};

// ------------------------------------------------------------- pure helpers

export function clamp(n, lo, hi) {
  return Math.min(hi, Math.max(lo, n));
}

export function formatMinutes(n) {
  return String(Math.round(n * 10) / 10);
}

/** Minutes the plan spends up to and including the stop at `index`: the walk
 *  to it, plus dwell at every stop through it. -1 means "not at a stop yet". */
export function minutesUsedThrough(plan, index) {
  if (index < 0) return 0;
  let total = plan.start.walk_minutes_to_first;
  for (let i = 0; i <= index && i < plan.stops.length; i++) {
    total += plan.stops[i].dwell_minutes;
    if (i < index) total += plan.stops[i].walk_minutes_to_next || 0;
  }
  return total;
}

/** Gallery of the current stop, or of the latest object stop before it when the
 *  current one is a rest or amenity stop. `fallback` is where the plan started. */
export function currentGalleryAt(plan, index, fallback = null) {
  for (let i = Math.min(index, plan.stops.length - 1); i >= 0; i--) {
    if (plan.stops[i].kind === "object") return plan.stops[i].gallery;
  }
  return fallback;
}

export function nextWing(plan, index) {
  const next = plan.stops[index + 1];
  return next ? next.wing : null;
}

function unique(list) {
  return [...new Set(list)];
}

/** Body for the first POST /api/plan. `form` = {museumId, interests, budget, constraints}. */
export function buildPlanRequest(form) {
  return {
    museum_id: form.museumId,
    interests: form.interests,
    time_budget_minutes: form.budget,
    constraints: form.constraints,
    current_gallery: null,
    exclude_object_ids: [],
    skip_wings: [],
  };
}

/** Body for a replan. `state` = {baseRequest, plan, currentIndex, excludedIds, skipWings, startGallery}.
 *  action = {type: "late", minutes} or {type: "skip_wing"}. */
export function buildReplanRequest(state, action) {
  const { baseRequest, plan, currentIndex, excludedIds, skipWings, startGallery } = state;
  const seen = plan.stops
    .slice(0, currentIndex + 1)
    .filter((s) => s.kind === "object" && s.object)
    .map((s) => s.object.id);
  const skip = [...skipWings];
  let budget;
  if (action.type === "late") {
    budget = action.minutes;
  } else if (action.type === "skip_wing") {
    const wing = nextWing(plan, currentIndex);
    if (!wing) throw new Error("There is no next stop to skip.");
    if (!skip.includes(wing)) skip.push(wing);
    // The budget of the plan being replaced minus what it already used.
    // For the first plan that is the original budget.
    budget = clamp(Math.floor(plan.totals.time_budget_minutes - minutesUsedThrough(plan, currentIndex)), BUDGET_MIN, BUDGET_MAX);
  } else {
    throw new Error(`Unknown replan action: ${action.type}`);
  }
  return {
    museum_id: baseRequest.museum_id,
    interests: baseRequest.interests,
    time_budget_minutes: budget,
    constraints: baseRequest.constraints,
    current_gallery: currentGalleryAt(plan, currentIndex, startGallery),
    exclude_object_ids: unique([...excludedIds, ...seen]).slice(-MAX_EXCLUDED),
    skip_wings: skip,
  };
}

/** Turn a route into display steps. Runs of level segments collapse into one
 *  "through ..." step; elevators, ramps, stairs, and escalators are named. */
export function routeSteps(segments) {
  const steps = [];
  let levelRun = [];
  const flush = () => {
    if (levelRun.length) {
      steps.push({ kind: "level", text: `Walk through ${levelRun.join(", ")}` });
      levelRun = [];
    }
  };
  for (const seg of segments) {
    if (seg.kind === "level") {
      levelRun.push(seg.to_name);
    } else {
      flush();
      const label = KIND_LABEL[seg.kind] || seg.kind;
      const name = seg.name || label;
      steps.push({
        kind: seg.kind,
        stairs: !seg.step_free,
        text: `${label}: ${name}, from ${seg.from_name} to ${seg.to_name}`,
      });
    }
  }
  flush();
  return steps;
}

export function floorStatusText(floorData) {
  return FLOOR_STATUS_TEXT[floorData.verification_status] || `Floor data status: ${floorData.verification_status}.`;
}

// --------------------------------------------------------------------- state

const state = {
  museums: [],
  museum: null, // MuseumDetail
  interests: [],
  busy: false,
  // Set once a plan exists:
  session: null, // {baseRequest, plan, currentIndex, excludedIds, skipWings, startGallery, done, resp, lateOpen, notice}
  focusKey: null,
};

// ----------------------------------------------------------------- dom utils

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  const add = (kid) => {
    if (kid === null || kid === undefined || kid === false) return;
    if (Array.isArray(kid)) kid.forEach(add);
    else el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  };
  kids.forEach(add);
  return el;
}

const $ = (id) => document.getElementById(id);

function setStatus(text) {
  $("status").textContent = text || "";
}

function showError(err) {
  const box = $("error");
  box.replaceChildren();
  if (!err) return;
  box.append(
    h("div", { class: "banner err" },
      h("h3", { text: "Something went wrong" }),
      h("p", { text: err.message }),
      err.details && err.details.length ? h("ul", {}, err.details.map((d) => h("li", { text: d }))) : null,
      err.network ? h("p", { text: "Your choices are still here. You can try again." }) : null,
    ),
  );
}

function setBusy(busy, message) {
  state.busy = busy;
  setStatus(busy ? message : "");
  for (const b of document.querySelectorAll("button[data-needs-idle]")) b.disabled = busy || b.hasAttribute("data-locked");
  $("submit").disabled = busy || !state.museum;
}

// ------------------------------------------------------------ form rendering

function renderMuseumPicker() {
  const sel = $("museum");
  sel.replaceChildren();
  if (state.museums.length === 0) {
    sel.append(h("option", { value: "", text: "No museums available yet" }));
    sel.disabled = true;
    return;
  }
  if (state.museums.length > 1) sel.append(h("option", { value: "", text: "Choose a museum" }));
  for (const m of state.museums) {
    sel.append(h("option", { value: m.id, text: m.city ? `${m.name}, ${m.city}` : m.name }));
  }
  sel.disabled = false;
}

function renderMuseumInfo() {
  const box = $("museum-info");
  box.replaceChildren();
  const m = state.museum;
  if (!m) return;
  const fd = m.floor_data;
  box.append(
    h("p", { class: `floor-status ${fd.verification_status === "verified" ? "verified" : ""}`, text: floorStatusText(fd) }),
    h("p", { text: `Floor data source: ${fd.source}` }),
    fd.notes ? h("p", { text: fd.notes }) : null,
    h("p", { text: m.data_notice }),
  );
}

function renderInterests() {
  const facets = state.museum ? state.museum.facets : [];
  const facetTags = new Set(facets.map((f) => f.tag));
  const box = $("facets");
  box.replaceChildren(
    ...facets.map((f) =>
      h("button", {
        type: "button",
        class: "chip",
        "aria-pressed": state.interests.includes(f.tag) ? "true" : "false",
        text: f.label,
        onclick: () => toggleInterest(f.tag),
      }),
    ),
  );
  $("custom-chips").replaceChildren(
    ...state.interests
      .filter((t) => !facetTags.has(t))
      .map((t) =>
        h("button", {
          type: "button",
          class: "chip custom",
          "aria-pressed": "true",
          "aria-label": `${t}, added by you. Press to remove.`,
          text: t,
          onclick: () => removeInterest(t),
        }),
      ),
  );
}

function setInterestMsg(text) {
  $("interest-msg").textContent = text || "";
}

function toggleInterest(tag) {
  if (state.interests.includes(tag)) {
    state.interests = state.interests.filter((t) => t !== tag);
    setInterestMsg("");
  } else if (state.interests.length >= MAX_INTERESTS) {
    setInterestMsg(`You can pick up to ${MAX_INTERESTS}. Remove one to add another.`);
    return;
  } else {
    state.interests = [...state.interests, tag];
    setInterestMsg("");
  }
  renderInterests();
}

function removeInterest(tag) {
  state.interests = state.interests.filter((t) => t !== tag);
  setInterestMsg("");
  renderInterests();
}

function addCustomInterest() {
  const input = $("custom-interest");
  const word = input.value.trim().toLowerCase().slice(0, 60);
  if (!word) return;
  if (state.interests.includes(word)) {
    setInterestMsg(`"${word}" is already in your list.`);
  } else if (state.interests.length >= MAX_INTERESTS) {
    setInterestMsg(`You can pick up to ${MAX_INTERESTS}. Remove one to add another.`);
    return;
  } else {
    state.interests = [...state.interests, word];
    setInterestMsg(`Added "${word}".`);
  }
  input.value = "";
  renderInterests();
  input.focus();
}

// A visit of 60 minutes or less always stays in one wing, so the Short visit box shows as on
// and locked for those, instead of being a switch that changes nothing.
const SHORT_VISIT_MAX_MINUTES = 60;

function syncShortVisit() {
  const box = $("short_visit");
  const minutes = Number(document.querySelector('input[name="time"]:checked').value);
  const automatic = minutes <= SHORT_VISIT_MAX_MINUTES;
  box.disabled = automatic;
  box.checked = automatic || !!state.wantsShortVisit;
  $("short-visit-hint").textContent = automatic
    ? `Always on for ${SHORT_VISIT_MAX_MINUTES} minutes or less: the plan stays in one wing`
    : "Stays in one wing and does it well";
}

function readForm() {
  const checked = (name) => document.querySelector(`input[name="${name}"]`).checked;
  const radio = (name) => Number(document.querySelector(`input[name="${name}"]:checked`).value);
  const limited = checked("limited_stamina");
  return {
    museumId: $("museum").value,
    interests: state.interests,
    budget: radio("time"),
    constraints: {
      step_free: checked("step_free"),
      limited_stamina: limited,
      max_walking_minutes: limited ? radio("walk") : null,
      with_young_child: checked("with_young_child"),
      low_sensory: checked("low_sensory"),
      short_visit: checked("short_visit"),
    },
  };
}

async function loadMuseum(id) {
  state.museum = null;
  state.interests = [];
  renderMuseumInfo();
  renderInterests();
  $("submit").disabled = true;
  if (!id) return;
  showError(null);
  setStatus("Loading museum details...");
  try {
    state.museum = await api.getMuseum(id);
    setStatus("");
    renderMuseumInfo();
    renderInterests();
    $("submit").disabled = state.busy;
  } catch (err) {
    setStatus("");
    showError(err);
  }
}

// ------------------------------------------------------------- plan rendering

function walkBlock(minutes, segments, label) {
  const steps = routeSteps(segments);
  return h("div", { class: "walk" },
    h("p", { text: `${label}: ${formatMinutes(minutes)} min` }),
    steps.length
      ? h("ul", {}, steps.map((s) => h("li", {}, s.stairs ? h("span", { class: "stairs-step", text: s.text }) : s.text)))
      : null,
  );
}

function stopCard(stop, displayNumber, opts) {
  const { done = false, alt = false, current = false, onHere = null, index = 0, after = null } = opts;
  const obj = stop.object;
  const li = h("li", { class: `stop ${stop.kind} ${done ? "done" : ""} ${alt ? "alt" : ""} ${current ? "current" : ""}`.replace(/\s+/g, " ").trim() });
  li.append(h("span", { class: "num", "aria-hidden": "true", text: String(displayNumber) }));

  const body = h("div", { class: "stop-body" });
  const place = [stop.node_name, stop.wing, `floor ${stop.floor}`].filter(Boolean).join(", ");

  if (stop.kind === "object" && obj) {
    body.append(h("p", { class: "gallery", text: `Stop ${displayNumber}: Gallery ${stop.gallery}` }));
    const text = h("div", {},
      h("h3", { text: obj.title }),
      obj.artist ? h("p", { class: "meta", text: obj.artist }) : null,
      obj.date ? h("p", { class: "meta", text: obj.date }) : null,
    );
    const row = h("div", { class: "title-row" });
    if (obj.image_url) {
      const img = h("img", {
        src: obj.image_url,
        loading: "lazy",
        width: 72,
        height: 72,
        alt: obj.artist ? `Photo of ${obj.title} by ${obj.artist}` : `Photo of ${obj.title}`,
      });
      img.addEventListener("error", () => img.remove());
      row.append(img);
    }
    row.append(text);
    body.append(row);
  } else {
    body.append(h("p", { class: "kind-tag", text: stop.kind === "rest" ? `Stop ${displayNumber}: Rest stop` : `Stop ${displayNumber}: Amenity` }));
    body.append(h("h3", { text: stop.node_name }));
  }

  body.append(h("p", { class: "meta", text: place }));
  body.append(h("p", { text: stop.why }));
  body.append(h("p", { class: "meta", text: `${stop.dwell_minutes} min here` }));
  if (obj && obj.source_url) {
    body.append(h("p", { class: "stop-links" },
      h("a", { href: obj.source_url, target: "_blank", rel: "noopener", text: "See it on the museum's site (opens in a new tab)" }),
    ));
  }
  if (done) body.append(h("span", { class: "done-tag", text: "Done" }));
  if (onHere) {
    body.append(h("button", {
      type: "button",
      class: "btn secondary here-btn",
      "aria-pressed": current ? "true" : "false",
      "data-focus-key": `here-${index}`,
      "data-needs-idle": "",
      text: current ? "You are here" : "I'm here",
      onclick: onHere,
    }));
  }
  li.append(body);
  if (after) li.append(after);
  return li;
}

function stopsList(plan, { alt = false, interactive = false, session = null, offset = 0 } = {}) {
  const ol = h("ol", { class: "stops", start: offset + 1 });
  plan.stops.forEach((stop, i) => {
    const current = interactive && session.currentIndex === i;
    const hasNext = stop.walk_minutes_to_next !== null && i < plan.stops.length - 1;
    ol.append(stopCard(stop, offset + i + 1, {
      alt,
      current,
      index: i,
      onHere: interactive ? () => setCurrent(i) : null,
      after: hasNext ? walkBlock(stop.walk_minutes_to_next, stop.route_to_next, "Walk to the next stop") : null,
    }));
  });
  return ol;
}

function planSummary(plan, extra = "") {
  const t = plan.totals;
  const n = plan.stops.length;
  return h("p", { class: "summary", text: `${n} ${n === 1 ? "stop" : "stops"}, ${formatMinutes(t.total_minutes)} of ${t.time_budget_minutes} minutes, ${formatMinutes(t.walking_minutes)} of them walking${extra}` });
}

function stairsList(stairs) {
  return h("ul", {}, stairs.map((s) =>
    h("li", { text: `${KIND_LABEL[s.kind] || s.kind}: ${s.name}, between ${s.from_name} and ${s.to_name}` })));
}

function accessBanner(resp, plan, askedStepFree) {
  // The confirmation is driven only by the server's plan.step_free, never by what was asked.
  if (plan.step_free === true) {
    const floorNotice = (resp.notices || []).find((n) => /floor/i.test(n));
    return h("div", { class: "banner ok" },
      h("h3", { text: "Step-free route" }),
      h("p", { text: "This route uses only elevators and ramps marked step-free in our floor data." }),
      h("p", { text: floorNotice || floorStatusText(state.museum ? state.museum.floor_data : { verification_status: "unverified" }) }),
    );
  }
  const banner = h("div", { class: "banner warn" });
  if (askedStepFree) {
    banner.append(
      h("h3", { text: "This route is not confirmed step-free" }),
      h("p", { text: "You asked for step-free, but this plan is not marked step-free. Please check the stairs below before you go." }),
    );
  } else {
    banner.append(h("h3", { text: "This route uses stairs" }));
  }
  banner.append(h("p", { text: "This route uses:" }), stairsList(plan.stairs));
  return banner;
}

function noticesFooter(notices) {
  return h("footer", { class: "notices", "aria-label": "About this plan" },
    h("h3", { text: "Please read" }),
    h("ul", {}, (notices || []).map((n) => h("li", { text: n }))),
  );
}

// What each choice on the form did to this plan, in the server's words.
function appliedNotes(resp) {
  const notes = (resp.applied && resp.applied.notes) || [];
  if (!notes.length) return null;
  return h("div", { class: "banner info applied-notes" },
    h("h3", { text: "How we used what you told us" }),
    h("ul", {}, notes.map((n) => h("li", { text: n }))),
  );
}

function renderPlanScreen() {
  const s = state.session;
  const root = $("plan-screen");
  root.replaceChildren();
  const resp = s.resp;
  const asked = !!s.baseRequest.constraints.step_free;

  root.append(h("div", { class: "plan-top" },
    h("h2", { id: "plan-heading", tabindex: "-1", text: `Your plan at ${resp.museum.name}` }),
    h("button", { type: "button", class: "btn secondary", onclick: startOver, text: "Start over" }),
  ));

  if (resp.status === "ok" && resp.plan) {
    const plan = resp.plan;
    root.append(planSummary(plan, s.done.length ? `. ${s.done.length} earlier ${s.done.length === 1 ? "stop is" : "stops are"} already done.` : ""));
    root.append(accessBanner(resp, plan, asked));
    const used = appliedNotes(resp);
    if (used) root.append(used);
    if (plan.warnings.length) {
      root.append(h("div", { class: "banner info" },
        h("h3", { text: "Good to know" }),
        h("ul", {}, plan.warnings.map((w) => h("li", { text: w }))),
      ));
    }
    if (resp.applied && resp.applied.interests_unmatched.length) {
      root.append(h("p", { class: "hint", text: `Nothing on view matched: ${resp.applied.interests_unmatched.join(", ")}.` }));
    }
    root.append(replanPanel(s));
    if (s.done.length) {
      root.append(h("p", { class: "section-label", text: "Already visited" }));
      const doneList = h("ol", { class: "stops" });
      s.done.forEach((stop, i) => doneList.append(stopCard(stop, i + 1, { done: true })));
      root.append(doneList);
    }
    root.append(h("p", { class: "section-label", text: s.done.length ? "Still ahead" : "Your stops" }));
    root.append(walkBlock(plan.start.walk_minutes_to_first, plan.start.route_to_first, `From ${plan.start.node_name}, walk to the first stop`));
    root.append(stopsList(plan, { interactive: true, session: s, offset: s.done.length }));
  } else if (resp.status === "no_step_free_route") {
    root.append(h("div", { class: "banner err" },
      h("h3", { text: "No step-free plan found" }),
      h("p", { text: resp.message || "We could not find a step-free plan for what you asked." }),
      h("p", { text: "We have not made a step-free plan. Try different interests or more time." }),
    ));
    const usedInAlternative = appliedNotes(resp);
    if (usedInAlternative) root.append(usedInAlternative);
    const alt = resp.alternative;
    if (alt) {
      root.append(h("div", { class: "alt-block" },
        h("h3", { text: "Closest alternative: this route USES STAIRS" }),
        h("p", { text: "This is not a step-free route. It uses these stairs, escalators, or other non-step-free connections:" }),
        stairsList(alt.stairs),
        planSummary(alt),
        walkBlock(alt.start.walk_minutes_to_first, alt.start.route_to_first, `From ${alt.start.node_name}, walk to the first stop`),
        stopsList(alt, { alt: true }),
      ));
    }
    root.append(h("button", { type: "button", class: "btn primary", onclick: startOver, text: "Change my answers" }));
  } else {
    root.append(h("div", { class: "banner warn" },
      h("h3", { text: "We could not build a plan" }),
      h("p", { text: resp.message || "Nothing fits what you asked. Try different interests or more time." }),
    ));
    root.append(h("button", { type: "button", class: "btn primary", onclick: startOver, text: "Change my answers" }));
  }

  root.append(noticesFooter(resp.notices));
}

function replanPanel(s) {
  const plan = s.resp.plan;
  const idx = s.currentIndex;
  const nextW = nextWing(plan, idx);
  const where = idx < 0
    ? "You are before the first stop."
    : `You are at stop ${s.done.length + idx + 1}: ${plan.stops[idx].kind === "object" ? `Gallery ${plan.stops[idx].gallery}` : plan.stops[idx].node_name}.`;
  const panel = h("section", { class: "replan", "aria-labelledby": "replan-heading" },
    h("h3", { id: "replan-heading", text: "Change the plan from where you are" }),
    h("p", { text: where }),
    h("p", { class: "hint", text: "Use \"I'm here\" on a stop below, then pick one of these." }),
  );
  if (s.notice) panel.append(h("div", { class: "banner warn", role: "status" }, h("p", { text: s.notice })));
  const row = h("div", { class: "btn-row" },
    h("button", {
      type: "button", class: "btn secondary", "data-needs-idle": "", "data-focus-key": "late",
      "aria-expanded": s.lateOpen ? "true" : "false", "aria-controls": "late-options",
      text: "Running late",
      onclick: () => { s.lateOpen = !s.lateOpen; state.focusKey = "late"; renderPlanScreen(); restoreFocus(); },
    }),
    h("button", {
      type: "button", class: "btn secondary", "data-needs-idle": "",
      disabled: !nextW,
      "data-locked": nextW ? null : "",
      text: nextW ? `Skip this wing: ${nextW}` : "Skip this wing (no stop ahead)",
      onclick: () => replan({ type: "skip_wing" }),
    }),
  );
  panel.append(row);
  if (s.lateOpen) {
    panel.append(h("div", { class: "late-options", id: "late-options" },
      h("p", { text: "How many minutes do you have left?" }),
      h("div", { class: "btn-row" }, LATE_CHOICES.map((m) =>
        h("button", { type: "button", class: "btn secondary", "data-needs-idle": "", text: `${m} min`, onclick: () => replan({ type: "late", minutes: m }) }))),
    ));
  }
  return panel;
}

function restoreFocus() {
  if (!state.focusKey) return;
  const el = document.querySelector(`[data-focus-key="${state.focusKey}"]`);
  if (el) el.focus();
  state.focusKey = null;
}

function showScreen(name) {
  $("form-screen").hidden = name !== "form";
  $("plan-screen").hidden = name !== "plan";
}

// ------------------------------------------------------------------- actions

function setCurrent(i) {
  const s = state.session;
  s.currentIndex = s.currentIndex === i ? -1 : i;
  s.notice = null;
  state.focusKey = `here-${i}`;
  renderPlanScreen();
  restoreFocus();
}

function startOver() {
  showError(null);
  setStatus("");
  state.session = null;
  showScreen("form");
  $("form-screen").scrollIntoView?.({ block: "start" });
  const first = $("museum");
  first.focus();
}

function focusPlanHeading() {
  const heading = $("plan-heading");
  if (heading) {
    heading.focus();
    heading.scrollIntoView?.({ block: "start" });
  }
}

async function onSubmit(event) {
  event.preventDefault();
  if (state.busy || !state.museum) return;
  const form = readForm();
  const body = buildPlanRequest(form);
  showError(null);
  setBusy(true, "Building your plan...");
  try {
    const resp = await api.createPlan(body);
    state.session = {
      baseRequest: { museum_id: body.museum_id, interests: body.interests, constraints: body.constraints, time_budget_minutes: body.time_budget_minutes },
      currentIndex: -1,
      excludedIds: [],
      skipWings: [],
      startGallery: null,
      done: [],
      resp,
      lateOpen: false,
      notice: null,
    };
    setBusy(false);
    showScreen("plan");
    renderPlanScreen();
    announceResult(resp);
    focusPlanHeading();
  } catch (err) {
    setBusy(false);
    showError(err);
  }
}

function announceResult(resp) {
  if (resp.status === "ok") {
    const n = resp.plan.stops.length;
    setStatus(`Your plan is ready: ${n} ${n === 1 ? "stop" : "stops"}.`);
  } else if (resp.status === "no_step_free_route") {
    setStatus("No step-free plan was found.");
  } else {
    setStatus("No plan could be built.");
  }
}

async function replan(action) {
  const s = state.session;
  if (state.busy || !s) return;
  let body;
  try {
    body = buildReplanRequest({ ...s, plan: s.resp.plan }, action);
  } catch (err) {
    s.notice = err.message;
    renderPlanScreen();
    return;
  }
  showError(null);
  setBusy(true, "Updating your plan...");
  try {
    const resp = await api.createPlan(body);
    setBusy(false);
    if (resp.status === "ok") {
      s.done = [...s.done, ...s.resp.plan.stops.slice(0, s.currentIndex + 1)];
      s.excludedIds = body.exclude_object_ids;
      s.skipWings = body.skip_wings;
      s.startGallery = body.current_gallery;
      s.resp = resp;
      s.currentIndex = -1;
      s.lateOpen = false;
      s.notice = null;
      renderPlanScreen();
      announceResult(resp);
      focusPlanHeading();
    } else {
      // Keep the plan the visitor already has; just explain.
      s.notice = (resp.message || "We could not change the plan that way.") + " Your current plan is unchanged.";
      renderPlanScreen();
      setStatus("The plan was not changed.");
    }
  } catch (err) {
    setBusy(false);
    showError(err);
  }
}

// -------------------------------------------------------------- event wiring

async function init() {
  $("museum").addEventListener("change", (e) => loadMuseum(e.target.value));
  $("plan-form").addEventListener("submit", onSubmit);
  $("add-interest").addEventListener("click", addCustomInterest);
  $("custom-interest").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault(); // Enter adds the word; it must not submit the form.
      addCustomInterest();
    }
  });
  $("limited_stamina").addEventListener("change", (e) => {
    $("walking-choice").hidden = !e.target.checked;
  });
  $("short_visit").addEventListener("change", (e) => {
    if (!e.target.disabled) state.wantsShortVisit = e.target.checked;
  });
  $("time-pills").addEventListener("change", syncShortVisit);
  syncShortVisit();

  setStatus("Loading museums...");
  try {
    const { museums } = await api.listMuseums();
    state.museums = museums;
    setStatus("");
    renderMuseumPicker();
    if (museums.length === 1) {
      $("museum").value = museums[0].id;
      await loadMuseum(museums[0].id);
    }
  } catch (err) {
    setStatus("");
    showError(err);
    renderMuseumPicker();
  }
}

if (typeof document !== "undefined") init();
