# FAQ: the six questions a Product Hunt commenter will ask

## 1. How is this different from Smartify?

Smartify builds an audio tour from your interests and your time, at museums that partner with it. It is good at that. It does not ask what your body can do. We start from that question: step-free, limited stamina, with a young child, low sensory, or short on time, and the route is built around the answer. We also do not need the museum to sign anything: if a museum publishes an open collection API with gallery locations, we can plan it.

Infiniteach is the other name worth knowing. Their apps (Field Museum for All, PMA for All) give autistic visitors a hand-edited visual schedule and sensory map for a specific venue. That is deeper than what we do for sensory needs at those venues. What we add is a generated plan with a time budget and mobility routing, at museums nobody has built an app for.

## 2. Is the wheelchair route actually accurate?

It is as accurate as our floor graph, and you should know exactly what that is. No museum API says which galleries connect or where the elevators are, so we built that by hand from each museum's published floor plan. Nobody has walked it to check. The app tells you this on every plan and asks you to confirm with museum staff.

What we can promise is the logic. A route is only labeled step-free if every single connection on it is marked step-free in our graph. Where we were not sure about a connection, we marked it as not step-free. And if no step-free route exists for what you asked, the app says so plainly and names the specific stairs the best alternative needs, instead of quietly sending you to them. If you find an error, tell us: fixing the graph is a one-line change.

## 3. Which museums work?

Two today: the Metropolitan Museum of Art in New York and the Art Institute of Chicago. Both publish open collection APIs that say which gallery each object is in. Any museum with an API like that can be added. Each new museum needs two things from us: a small adapter for its API, and a hand-built floor graph, which takes about a day. The Cleveland Museum of Art is a likely next one.

## 4. Where does the data come from, and did the museums approve this?

From the museums' own public open-access APIs. The museums have not reviewed or endorsed the plans, and the app says so on every screen that shows one. We never invent a gallery number: if the museum's data does not place an object in a gallery, we leave it out.

## 5. Do you use AI to write the "why you will like this" lines?

No. Each line is assembled from the object's own tags and fields in the museum's data, for example the subject tags that matched your interests, or that the museum lists it as a highlight. That was deliberate: a language model can write a lovely sentence about the wrong object. Ours are plainer and always traceable to the data. The route is a simple greedy algorithm over the floor graph, not a model either.

## 6. What do you do with what I tell you about my body?

Nothing. There are no accounts and no cookies. Your answers are sent with the request, used to build the plan, and not stored. The planner is stateless: replanning sends your choices again from your browser because the server does not remember them.

## Likely follow-ups

- **What about crowds right now, or a broken elevator?** Not covered. Sensory tags are fixed estimates for well-known busy or bright spaces. Live closures and outages are not in any open data we have found.
- **Blind or low-vision visitors? Deaf visitors?** Not yet. The app itself is built to work with a screen reader and keyboard, but the routing does not yet know about tactile exhibits, audio description, or captioned media.
- **Why Cloud Run?** The ingester is a job that runs to completion; the planner is a stateless service that scales to zero between visits. Adding a museum is a Pub/Sub message, not a redeploy.
- **Is it open source?** Yes, under the MIT license: https://github.com/karthikvalluri85/museum-planner. The floor maps are in the repository too, and corrections to them are the most useful contribution anyone can make.
