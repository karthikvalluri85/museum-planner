# Floor graphs: provenance and method

## What a floor graph is, and why it is hand-built

A floor graph is a small map of a museum that the planner can route over. Each node is one gallery, or a run of adjacent galleries on one level with no steps between them, plus a few hubs (a great hall, a lobby) and one entrance. Each edge says how two nodes connect: walking time at a slow pace, the kind of connection (level, ramp, elevator, stairs, escalator), whether it is step-free, and, for anything that is not step-free or is an elevator, a name a visitor can find on site. Nodes also carry the wing, the floor, rough sensory tags and whether seating, restrooms or a cafe are close by. The documents follow `contracts/firestore/floor-graph.schema.json`.

No museum API publishes this. The collection APIs tell us which gallery number an object is in, but not how galleries connect, where the stairs and elevators are, or which routes avoid steps. So we build each graph by hand from the museum's own published floor plan. The product promise is that a route is never called step-free unless every edge on it is marked step-free, so the graph is deliberately cautious: a connection we could not confirm is marked not step-free, with a name saying it is unconfirmed.

## The Met Fifth Avenue (`services/ingestion-job/seed/floor_graphs/met.json`)

### Sources (all read 2026-11-13)

| Source | URL | Result | What we took |
|---|---|---|---|
| The Met Fifth Avenue museum map, official one-page PDF (mirror copy) | https://www.newyorkwelcome.net/ecms/met-fifth-avenue-map.pdf | Read in full (text layer and rendered image) | Every gallery number and its floor and department label; floors G, 1, mezzanines, 2, 3, 4, 5; elevator, stair, restroom and cafe icons; the notes "For step-free access to Asian Art, use these elevators, entering via Egyptian Art or Arms and Armor on Floor 1" and "For the accessible route to this mezzanine, please ask a security officer" (Modern and Contemporary mezzanine); the 81st Street, 82nd Street and parking garage entrances. The map still shows The Met Breuer, so it dates from about 2016 to 2020. |
| Met accessibility guide | https://www.metmuseum.org/visit-guides/accessibility | HTTP 429. Only search-result text was seen | Step-free entry is at Fifth Avenue and 81st Street and through the 80th Street parking garage; wheelchairs at the 81st Street coat check. |
| Met visitors with disabilities page | https://www.metmuseum.org/learn/visitors-with-disabilities | HTTP 429. Only search-result text was seen | The museum is accessible to wheelchair users; staff help find elevators and step-free routes; an interactive map shows step-free routes. |
| Met accessibility policy (older) | https://www.metmuseum.org/policies/accessibility | Search-result text only | About 60 stairs from the 82nd Street main entrance to the admissions desk; shuttle for step-free entry. Used only to justify marking the 82nd Street entrance as stairs. |
| Met interactive map | https://maps.metmuseum.org/ | Page loads only a JavaScript shell; no content readable | Nothing. |
| all-maps.com Met map page (third party) | https://all-maps.com/metropolitan-museum-of-art-map/ | Read; images not readable | Nothing used beyond confirming the floor names. |
| metropolitan.museum glossary (third party, via search snippet) | https://metropolitan.museum/the-museum/glossary/ | Snippet only | 448 numbered galleries at Fifth Avenue as of September 2026, used only as a coverage yardstick. |

### What is modeled

Entrance node: the 81st Street entrance (ground floor), which the Met names as the step-free entrance. The 82nd Street main entrance is a separate hub connected to the Great Hall only by the Fifth Avenue steps. 43 nodes, 58 edges, 439 gallery numbers (out of about 448 numbered galleries at Fifth Avenue), 19 wings.

### Coverage by wing

- Egyptian Art: 100 to 124, 126 to 138 (131 is the Temple of Dendur, its own node). 125 is not on the map.
- The Costume Institute (ground floor): 980, 981.
- Greek and Roman Art: 150 to 169 (Floor 1), 170 to 172 (mezzanine), 173 to 176 (Floor 2).
- Africa, Oceania, and the Americas: 350 to 359, 399.
- Modern and Contemporary Art: 900 to 913 (Floor 1), 914 to 916 (mezzanine), 917 to 925 (Floor 2), 926 (Roof Garden, Floor 5).
- Exhibitions: 199 (Floor 1), 899 and 999 (Floor 2).
- European Sculpture and Decorative Arts: 500 to 534 and 536 to 556 (Floor 1), 535 (Floor 2).
- Medieval Art: 300 to 307.
- Robert Lehman Collection: 950 to 965 (964 and 965 are its exhibition galleries).
- Arms and Armor: 370 to 380.
- The American Wing: 700 to 702 and 723 to 746 (Floor 1), 707, 773, 774 (mezzanine), 703 to 706, 717 to 722, 747 to 772 (Floor 2), 708 to 716 (Floor 3).
- Lobby (Great Hall Balcony): 200, 201.
- European Paintings: 600 to 644. 19th-Century European Paintings: 800 to 830.
- Drawings, Prints and Photographs: 690 to 693, 850 to 852.
- Musical Instruments: 680 to 684.
- Ancient Near Eastern Art: 400 to 406. Islamic Art: 450 to 464.
- Asian Art: 202 to 218 and 223 to 250 (Floor 2), 219 to 222 and 251 to 253 (Floor 3).

Every number above appears on the map. Numbers placed on weaker evidence: 200 and 201 (assigned to the Great Hall Balcony from their position; they may be Asian Art), 399 (a single label beside the Africa, Oceania, and the Americas galleries; its use is unclear), 691 to 693 (grouped with Drawings and Prints from position only), 170 (placed on the Greek and Roman mezzanine; it may be on Floor 1), 535 (a lone Floor 2 European Sculpture label), 964 and 965 (Lehman exhibition galleries). Left out on purpose: 599 (Antonio Ratti Textile Center, a study room) and 125 (not shown).

### Step-free status of edges

- Supported by a source: the 81st Street entrance being step-free (Met accessibility guide); the elevators from Egyptian Art and from the Arms and Armor area up to Asian Art (map note); the steps between the Great Hall Balcony and Asian Art (inferred from the same map note, which sends step-free visitors to those elevators); the Modern mezzanine having no public step-free route (map note); every elevator edge (elevator icons on the map at both ends).
- Assumed: all level edges between nodes on the same floor of the map. The map shows them as one floor, but the Met has small level changes that the map marks only with tiny ramp and lift icons we could not read at this resolution. Also assumed: which elevator on one floor matches which on the next (matched by position on the map), and where the 81st Street elevator arrives on Floor 1.
- Marked unconfirmed (not step-free): Medieval Art to the Robert Lehman Collection.

Non-step-free edges (9): Fifth Avenue front steps at the 82nd Street main entrance; Great Hall Grand Staircase; stairs to the Greek and Roman mezzanine; stairs to the Modern and Contemporary mezzanine; steps between the Great Hall Balcony and Asian Art; two Engelhard Court staircase edges and the stairs to American Wing Floor 3 (each paired with the American Wing elevator); the unconfirmed Medieval to Lehman connection.

### Gallery nodes not reachable step-free (validator)

- `greek-mezz` (170 to 172): the map shows no elevator stop we could confirm on this mezzanine.
- `modern-mezz` (914 to 916): the map says to ask a security officer for the accessible route, so there is no public step-free route.
- `lehman` (950 to 965): the only connection is marked unconfirmed. If someone confirms the route from Medieval Art is level or ramped, change that edge.

### Known gaps

- The map predates 2020. The Michael C. Rockefeller Wing (350 to 359) reopened in 2025 and its numbering may have changed; the Tang Wing work for Modern and Contemporary Art will change 900 to 926.
- Cafes and seating: only map-marked cafes and lounges (Petrie Court Cafe, American Wing Cafe, Balcony Lounge, Roof Garden Bar) set `near_cafe` or `has_seating`. Benches are not on the map, so most nodes say no seating even where benches exist. The Cafeteria below the Lehman Wing is not modeled.
- Restroom flags come from small icons and are approximate.
- Floor 4 (members dining) and the Uris Center are not modeled.
- Sensory tags are editorial estimates: the Great Hall (loud, crowded), the 82nd Street entrance (crowded), the glass-walled or glass-roofed Temple of Dendur, Petrie Court and Engelhard Court (bright), the Roof Garden (bright, crowded), and the busiest collections (Egyptian galleries 100 to 117, 19th-century European paintings: crowded).

## Art Institute of Chicago (`services/ingestion-job/seed/floor_graphs/aic.json`)

### Sources (all read 2026-11-13)

| Source | URL | Result | What we took |
|---|---|---|---|
| AIC Visitor Guide with floor plans LL, 1, 2, 3 (official guide layout, labelled 2025 by the host; mirror copy) | https://all-maps.com/wp-content/uploads/2025/05/The-Art-Institute-of-Chicago-Museum-Map-2025.pdf | Read in full (seven rendered pages) | Every gallery number and range per collection; elevator, ramp, stair, escalator, restroom and cafe icons per level; "All buildings connect on the first level" and "Some buildings do not connect on this level" for LL, 2 and 3; the stacked elevation page used to match elevators across levels; Michigan Avenue and Modern Wing entrances. |
| AIC museum floor plan page | https://www.artic.edu/visit/explore-on-your-own/museum-floor-plan | HTTP 403 | Nothing. |
| AIC accessibility page | https://www.artic.edu/visit/accessibility | HTTP 403 | Nothing. |
| all-maps.com AIC map page (third party) | https://all-maps.com/the-art-institute-of-chicago-museum-map/ | Read | Only the PDF link above. |

### What is modeled

Entrance node: the Modern Wing entrance on Monroe Street, which is at grade. The Michigan Avenue entrance is a street hub connected to the lobby by the front steps and by the ramp marked on the plan. 28 nodes, 38 edges, 157 gallery numbers, 15 wings.

### Coverage by wing

- Lower level: Photography 1 to 4 and 10; Thorne Miniature Rooms 11; Architecture and Design 24; Arts of Ancient Egypt 50; Textiles 57 to 59 and 61.
- Level 1: Arts of Asia 100, 101, 101A, 102 to 109, 130 to 135, 140 to 143; Prints and Drawings 124, 124A, 124B, 125, 126, 127, 127A, 127B; Arts of the Americas 136, 160 to 179; Arts of Africa 137; Chagall Windows 144; Ancient and Byzantine 150 to 154; Special Exhibitions 182 to 184; Photography, Film, and Media 186 to 188.
- Level 2: Arts of Europe 200 to 227 and 231 to 248; Arts of the Americas 249, 261 to 265, 271 to 273; Modern and Contemporary (Architecture and Design, Contemporary) 283 to 286 and 288 to 299.
- Level 3: Modern Art 389 to 399.

Numbers on weaker evidence: 100 (shown at the foot of the Grand Staircase, placed with Arts of Asia); 124 and 127 (inside the range "124 to 127", only the A and B rooms are drawn); 61 (shown beside Textiles, use unclear); numbers that appear only inside a published range and not as a label (for example 210, 214, 215, 284, 298). Not included because no source shows them: 228 to 230, 250 to 260, 266 to 270, 274 to 282, 287. Regenstein Hall special exhibitions has no number and is not modeled. Note that gallery suffixes such as "124A" are kept as written; check that the ingestion job extracts them the same way from `gallery_title`.

### Step-free status of edges

- Supported by the plan: the ramp near gallery 131 (between the Michigan Avenue building and the Alsdorf Galleries), the ramp near gallery 231 on level 2, the ramp at the Michigan Avenue entrance, all elevator edges (icons at both ends, matched on the stacked elevation page), and the stairs edges (stairs drawn on the plan).
- Assumed: level edges between nodes drawn as joined on the same level, including the link from Impressionism (248) to gallery 249 and the Arts of the Americas second floor.
- Marked unconfirmed: gallery 226 to gallery 240 on level 2. The plan puts a ramp near 231 nearby, which suggests a level change. No edge links the Modern Wing second floor to the main building's second floor, because the plan says some buildings do not connect on level 2.

Non-step-free edges (7): Michigan Avenue front steps (between the lions); Woman's Board Grand Staircase up to level 2; Woman's Board Grand Staircase down to the lower level; the unconfirmed 226 to 240 connection; stairs in the Arts of the Americas galleries (163 to 179) up to level 2; stairs near gallery 150 down to Arts of Ancient Egypt; Usher|Lambe Family Staircase.

### Gallery nodes not reachable step-free (validator)

None. Every gallery node has a route from the Modern Wing entrance using level, ramp and elevator edges only. That result depends on the assumed level edges listed above.

### Known gaps

- Escalators in the Modern Wing and the Nichols Bridgeway entrance are not modeled.
- Seating is only set where the plan shows a cafe or bar (The Cafe on the lower level, The Modern Bar on level 2).
- Sensory tags are editorial estimates: Michigan Avenue lobby (loud, crowded) and street entrance (crowded), Griffin Court (bright, glass roof), Impressionism 240 to 248, Arts of the Americas 1860 to 1950, and the Thorne Miniature Rooms (crowded).

## How to verify a graph

Work through this on site or against the latest published plan. Record the date and who checked in `verification.notes`.

1. Confirm the entrance node really is step-free from the street, and note the exact door.
2. For every edge with `step_free: true` and kind `level`: walk it, or find it on the plan, and confirm there is no step, threshold over about 2 cm, or lift. If there is a ramp, change kind to `ramp`. If there is any step, set `step_free: false` and give the edge a name.
3. For every elevator edge: confirm the elevator stops at both nodes' floors, is open to the public, and the name matches signage. Time the wait plus ride.
4. For every edge named "Unconfirmed connection": find out what is really there and replace it with a named stairs, ramp or level edge.
5. For every stairs edge: check there is no parallel elevator or ramp missing from the graph.
6. Spot-check gallery numbers: for each node, confirm the door signs match the `galleries` list and that no public gallery number is missing.
7. Check restroom, cafe and seating flags against what is within a minute or two of each node.
8. Run the validator and read `gallery_nodes_not_reachable_step_free`. Each one should have a reason in this document.
9. When most edges are checked, set `verification.status` to `partially_verified`. Use `verified` only when every edge has been walked, and set `verified_on` to that date.

## How to add a museum

1. Get the museum's own floor plan (PDF or printed map) and its accessibility page. Record title, URL and date read.
2. Find out how the museum's collection API writes gallery numbers, so `galleries` strings match exactly.
3. Pick the entrance: the one the museum names as step-free. Make it the `entrance` node.
4. Make one node per gallery or run of adjacent galleries on one level with no steps between them. Add hubs for halls and lobbies. List every gallery number individually.
5. Add edges. Mark `step_free: true` only when the plan or the access page supports it, or the two areas are plainly on the same level. Otherwise use `step_free: false` with a name such as "Unconfirmed connection between X and Y (may have steps)". Name every stairs, escalator and elevator edge so a visitor can find it. Model stairs and an elevator between the same two areas as two edges.
6. Set flags: wing, floor, restroom and cafe from map icons, seating only where shown, sensory tags only for well-known cases.
7. Fill `source` and `verification` (status `unverified` unless you checked a substantial part against the plan; never `verified` without walking it).
8. Save as `services/ingestion-job/seed/floor_graphs/<museum_id>.json` and validate:

   ```
   node tools/validate-contracts.mjs --graph services/ingestion-job/seed/floor_graphs/<museum_id>.json
   ```

9. Add a section to this document with sources, coverage, edge status, gaps and the validator's unreachable list.
