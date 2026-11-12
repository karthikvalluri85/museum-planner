// Cross-field rules for a floor graph that JSON Schema cannot express.
// Returns { errors, stats }.

export function checkGraph(graph) {
  const errors = [];
  const ids = new Set();
  for (const node of graph.nodes) {
    if (ids.has(node.id)) errors.push(`duplicate node id "${node.id}"`);
    ids.add(node.id);
    if (node.kind !== 'gallery' && node.galleries.length > 0) errors.push(`node "${node.id}" is a ${node.kind} but lists galleries`);
    if (node.kind === 'gallery' && node.galleries.length === 0) errors.push(`gallery node "${node.id}" lists no galleries`);
  }

  const owner = new Map();
  for (const node of graph.nodes) {
    for (const gallery of node.galleries) {
      if (owner.has(gallery)) errors.push(`gallery "${gallery}" is in both "${owner.get(gallery)}" and "${node.id}"`);
      owner.set(gallery, node.id);
    }
  }

  const entrance = graph.nodes.find((n) => n.id === graph.entrance_node_id);
  if (!entrance) errors.push(`entrance_node_id "${graph.entrance_node_id}" is not a node`);
  else if (entrance.kind !== 'entrance') errors.push(`entrance node "${entrance.id}" must have kind "entrance"`);

  const all = new Map([...ids].map((id) => [id, []]));
  const stepFree = new Map([...ids].map((id) => [id, []]));
  for (const [i, edge] of graph.edges.entries()) {
    if (!ids.has(edge.from)) errors.push(`edge ${i}: unknown node "${edge.from}"`);
    if (!ids.has(edge.to)) errors.push(`edge ${i}: unknown node "${edge.to}"`);
    if (edge.from === edge.to) errors.push(`edge ${i}: self-loop on "${edge.from}"`);
    if (!ids.has(edge.from) || !ids.has(edge.to)) continue;
    all.get(edge.from).push(edge.to);
    all.get(edge.to).push(edge.from);
    if (edge.step_free) {
      stepFree.get(edge.from).push(edge.to);
      stepFree.get(edge.to).push(edge.from);
    }
  }

  const reach = (adjacency) => {
    const seen = new Set();
    if (!entrance) return seen;
    const queue = [entrance.id];
    seen.add(entrance.id);
    while (queue.length) {
      for (const next of adjacency.get(queue.shift())) {
        if (!seen.has(next)) {
          seen.add(next);
          queue.push(next);
        }
      }
    }
    return seen;
  };

  const reachable = reach(all);
  const unreachable = [...ids].filter((id) => !reachable.has(id));
  if (entrance && unreachable.length) errors.push(`not connected: unreachable from the entrance: ${unreachable.join(', ')}`);

  const reachableStepFree = reach(stepFree);
  const stairsOnly = graph.nodes.filter((n) => n.kind === 'gallery' && !reachableStepFree.has(n.id)).map((n) => n.id);

  return {
    errors,
    stats: {
      nodes: graph.nodes.length,
      edges: graph.edges.length,
      galleries: owner.size,
      wings: [...new Set(graph.nodes.map((n) => n.wing))],
      non_step_free_edges: graph.edges.filter((e) => !e.step_free).length,
      gallery_nodes_not_reachable_step_free: stairsOnly,
    },
  };
}
