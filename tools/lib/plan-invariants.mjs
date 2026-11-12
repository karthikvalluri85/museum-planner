// Invariants I1-I8 from contracts/README.md: the rules a PlanResponse must obey
// that JSON Schema cannot express. Returns a list of violation strings.
//
// request  (optional) the PlanRequest that produced the response; enables I2.
// objects  (optional) Map<object id, stored NormalizedObject>; enables the store half of I4.

const EPS = 0.051;

function segmentsOf(plan) {
  return [...plan.start.route_to_first, ...plan.stops.flatMap((s) => s.route_to_next)];
}

function sameSegment(stair, seg) {
  const forward = stair.from_node_id === seg.from_node_id && stair.to_node_id === seg.to_node_id;
  const backward = stair.from_node_id === seg.to_node_id && stair.to_node_id === seg.from_node_id;
  return stair.name === seg.name && (forward || backward);
}

function checkPlan(plan, label, objects) {
  const out = [];
  const bad = (rule, msg) => out.push(`${rule} ${label}: ${msg}`);
  const segs = segmentsOf(plan);
  const stepped = segs.filter((s) => !s.step_free);

  // I1
  if (plan.step_free !== (stepped.length === 0)) {
    bad('I1', `step_free is ${plan.step_free} but ${stepped.length} segment(s) are not step-free`);
  }
  if (plan.step_free && plan.stairs.length > 0) bad('I1', 'step_free is true but stairs is not empty');
  for (const seg of stepped) {
    if (!seg.name) bad('I1', `non-step-free segment ${seg.from_node_id} -> ${seg.to_node_id} has no name`);
    else if (!plan.stairs.some((st) => sameSegment(st, seg))) {
      bad('I1', `stairs does not name "${seg.name}" (${seg.from_node_id} -> ${seg.to_node_id})`);
    }
  }

  // I4, I5
  for (const stop of plan.stops) {
    if (stop.kind !== 'object') continue;
    if (!stop.gallery) bad('I4', `stop ${stop.order} has no gallery`);
    if (!stop.object) {
      bad('I4', `stop ${stop.order} has no object`);
      continue;
    }
    const stored = objects?.get(stop.object.id);
    if (objects && !stored) bad('I4', `stop ${stop.order}: object ${stop.object.id} is not in the store`);
    if (stored && stored.gallery !== stop.gallery) {
      bad('I4', `stop ${stop.order}: gallery "${stop.gallery}" differs from stored "${stored.gallery}"`);
    }
    const tags = new Set(stored ? stored.tags : stop.object.tags);
    for (const tag of stop.grounding.matched_tags) {
      if (!tags.has(tag)) bad('I5', `stop ${stop.order}: matched tag "${tag}" is not one of the object's tags`);
    }
    if (stop.grounding.fields.length === 0) bad('I5', `stop ${stop.order}: grounding.fields is empty`);
  }

  // I6
  const dwell = plan.stops.reduce((sum, s) => sum + s.dwell_minutes, 0);
  const walking = plan.start.walk_minutes_to_first + plan.stops.reduce((sum, s) => sum + (s.walk_minutes_to_next ?? 0), 0);
  const t = plan.totals;
  if (t.dwell_minutes !== dwell) bad('I6', `totals.dwell_minutes ${t.dwell_minutes} != sum of stops ${dwell}`);
  if (Math.abs(t.walking_minutes - walking) > EPS) bad('I6', `totals.walking_minutes ${t.walking_minutes} != sum of legs ${walking}`);
  if (Math.abs(t.total_minutes - (dwell + walking)) > EPS) bad('I6', `totals.total_minutes ${t.total_minutes} != dwell + walking ${dwell + walking}`);
  if (t.total_minutes > t.time_budget_minutes + EPS) bad('I6', `total ${t.total_minutes} exceeds budget ${t.time_budget_minutes}`);
  const legs = [
    { walk: plan.start.walk_minutes_to_first, route: plan.start.route_to_first, name: 'start' },
    ...plan.stops.map((s) => ({ walk: s.walk_minutes_to_next ?? 0, route: s.route_to_next, name: `stop ${s.order}` })),
  ];
  for (const leg of legs) {
    const routeSum = leg.route.reduce((sum, seg) => sum + seg.walk_minutes, 0);
    if (leg.walk + EPS < routeSum) bad('I6', `${leg.name}: walk minutes ${leg.walk} is less than its route segments ${routeSum}`);
  }

  // I8
  plan.stops.forEach((stop, i) => {
    if (stop.order !== i + 1) bad('I8', `stop at index ${i} has order ${stop.order}`);
    const last = i === plan.stops.length - 1;
    if (last && (stop.walk_minutes_to_next !== null || stop.route_to_next.length > 0)) bad('I8', 'last stop must have null walk_minutes_to_next and empty route_to_next');
    if (!last && stop.walk_minutes_to_next === null) bad('I8', `stop ${stop.order} is not last but walk_minutes_to_next is null`);
  });

  return out;
}

export function checkPlanInvariants(response, { request = null, objects = null } = {}) {
  const out = [];
  const { status, plan, alternative } = response;

  if (status === 'ok') {
    if (!plan) out.push('I3 status is ok but plan is null');
    if (request?.constraints?.step_free && plan && !plan.step_free) out.push('I2 step-free was requested, status is ok, but plan.step_free is false');
  } else if (status === 'no_step_free_route') {
    if (plan !== null) out.push('I3 status is no_step_free_route but plan is not null');
    if (!response.message) out.push('I3 status is no_step_free_route but message is empty');
    if (!alternative) out.push('I3 status is no_step_free_route but alternative is null');
    else if (alternative.stairs.length === 0) out.push('I3 alternative must name the stairs it uses');
  } else if (status === 'no_plannable_objects') {
    if (plan !== null || alternative !== null) out.push('I3 status is no_plannable_objects but a plan was returned');
    if (!response.message) out.push('I3 status is no_plannable_objects but message is empty');
  }

  if (plan) out.push(...checkPlan(plan, 'plan', objects));
  if (alternative) out.push(...checkPlan(alternative, 'alternative', objects));

  // I7
  const hasNotice = response.notices.some((n) => /public data/i.test(n) && /not reviewed/i.test(n));
  if (!hasNotice) out.push('I7 notices does not state that the plan is built from public data and not reviewed by the museum');

  return out;
}
