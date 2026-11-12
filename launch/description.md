# Product Hunt description

Museums are built for a visitor with three free hours, two good legs, no kids, and no sensory issues. Most people are not that visitor. If you use a wheelchair, the route on the map has stairs in it. If you tire quickly, the thing you came for is at the far end of the building. If you have a seven-year-old, you have forty minutes and no idea which rooms will hold their attention.

This is for the rest of us. Tell it what you like, how long you have, and what your body can do today: step-free, limited stamina, with a young child, low sensory, or a short visit. It gives you an ordered plan: each stop with its gallery number, one sentence on why it was chosen, the minutes to spend there, and the minutes to walk to the next.

Plans change, so the plan does too. Tap "Running late" or "Skip this wing" and the rest of the visit is rebuilt from where you are standing.

## How it works

1. Pick a museum, a few interests, and how much time you have.
2. Say what your body needs today.
3. Get an ordered route with gallery numbers, reasons, and walking times.
4. Running late? One tap replans the rest from your current gallery.

## Built on Cloud Run

**Ingestion job (Cloud Run job).** Pulls a museum's open collection API, normalizes every object into one schema (title, artist, date, tags, gallery, image, estimated minutes, size), and writes it to Firestore. It is a job and not a service because it runs to completion: it has a beginning and an end, holds no port open, and costs nothing between runs.

**Planner service (Cloud Run service).** Takes interests, a time budget, constraints, and optionally the gallery you are in, and returns a plan. It is stateless, so replanning is just another request, and it scales to zero between visits. It scores objects by how well their tags match your interests, weighted by your constraints, then walks the floor graph greedily, using only step-free edges when you ask for that.

**Web frontend.** Served by the planner itself from a static directory. One deployable, one URL, no build step.

**Firestore.** Holds the normalized objects and the floor graphs. The ingestion job is the only writer; the planner can only read.

**Pub/Sub.** Topic `ingest-museum`. Publishing a message with a museum id is how a museum gets added or refreshed. No redeploy.

## What exists today

**Smartify** generates audio tours from your interests and available time. It does not ask about your body, and it works at museums that partner with it.

**Infiniteach** builds apps such as Field Museum for All and PMA for All, which give autistic visitors a hand-edited visual schedule and a sensory map, for venues that commission one. They are careful, venue-specific work. They do not generate a plan, budget your time, or route around stairs.

**This** generates a plan from interests, time, and physical limits together, for museums with an open collection API, with no contract required.

## Limits

- **The floor graph is hand-built.** No collection API says which galleries connect or where the elevators are. We built that by hand for each museum from its published floor plan, as a small graph with walking minutes and a step-free flag on every connection. It has not been verified on site, and every plan says so. Confirm step-free access with museum staff.
- **Two museums today:** the Metropolitan Museum of Art and the Art Institute of Chicago. Adding one needs an adapter for its API and a floor graph.
- **Only what the museum publishes.** If the museum's data does not place an object in a gallery, we cannot plan it, and we never guess a gallery number.
- **Sensory tags are estimates** for well-known busy or bright spaces, not measurements, and not live crowd data.
- **Routing is greedy.** It finds a good route, not the best possible one.
- **Not covered yet:** blind and low-vision routing, hearing access, temporary closures, elevator outages, ticketed exhibitions.
- Built from public data. The museums have not reviewed it.
