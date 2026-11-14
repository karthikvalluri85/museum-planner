# Fixtures

Raw upstream payloads used by `SOURCE_MODE=fixture` and by the tests. They are the museum's own JSON, untouched.

## aic/artworks.json

55 REAL records fetched on 2026-11-13 from `https://api.artic.edu/api/v1/artworks/search` with `fields=` set to the fields in `contracts/upstream/aic-artwork.schema.json`. 50 are `is_on_view: true` (49 with a `Gallery NNN` title, 1 in a named space that the adapter must skip) and 5 are not on view. The record order is the order the API returned them in, sampled across several result pages.

## met/objects.json

41 REAL records fetched on 2026-11-13 from `https://collectionapi.metmuseum.org/public/collection/v1/objects/{id}`, all with a `GalleryNumber`, 24 of them highlights, from 10 departments. Nothing was trimmed.

The Met's API started answering 403 (bot protection) partway through the download, so the real set is smaller than planned and contains no off-view records. To still exercise the "no gallery" path, the file ends with 8 SYNTHETIC records: `objectID` 990000001 to 990000008, titles starting with `SYNTHETIC off-view test record`, `GalleryNumber` empty. They are not real objects and have no real gallery.

Regenerating the Met file needs a working fetch from a network the Met does not block.

## Where this data comes from and how it is licensed

- `met/objects.json`: records from The Met Collection API. The Met's API page (https://metmuseum.github.io/, read 2026-11-14) says it has waived copyright on this dataset under Creative Commons Zero and that use of the API is subject to the Met's terms and conditions. Records whose `objectID` is 990000001 or higher are synthetic test records, not Met data.
- `aic/artworks.json`: records from the Art Institute of Chicago API (https://api.artic.edu/docs/). That page could not be re-read when this note was written, so check its licensing section before reusing these records; the `short_description` text in particular may carry an attribution requirement.

No images are stored here, only links to the museums' own image servers.
