# Third-party code and tools considered for `!draw photo`

No third-party source is vendored into `bots/draw`, and the bot's runtime dependencies stay `Pillow` and `httpx`.
This lists what was evaluated for turning a photograph into a stroke-budgeted line drawing, under which licence, and why it was or was not adopted.

| Tool | Licence | What it does | Decision |
| --- | --- | --- | --- |
| [LingDong-/linedraw](https://github.com/LingDong-/linedraw) | MIT | Pen-plotter drawings from a photo: a Sobel (or OpenCV Canny) edge map linked into contours by row scanning, plus tone hatching in 16 pixel patches | Not vendored. Its contour pass is the same idea as ours (threshold an edge map, link pixels) in a slower pure-Python form with an O(n^2) merge, and its hatching lights up the background of a portrait as much as the subject. The idea of tone hatching is used, restricted to the face (see `photo_shade.py`). Copyright (c) 2017 Lingdong Huang, MIT. |
| potrace / pypotrace / potracer | GPL | Bitmap to Bezier outlines | Excluded: GPL. |
| DrawingBotV3 | GPL-3.0 | Plotter image conversion with several path-finding modes | Excluded: GPL; and it is a desktop application, not a library. |
| vpype | MIT | Line merging, simplification and ordering for plotters | Not needed: it pulls in numpy, scipy and shapely for operations the bot does in a few lines (Douglas-Peucker, Chaikin, joining). |
| vtracer | MIT | Raster to vector shapes (filled outlines) | Not used: it makes filled shapes, not centre lines, so a face becomes blobs. |
| Stippling plus a nearest-neighbour path, "TSP art" | technique | Dots whose density follows tone, joined into one line | Tried (own code, scratch only): on an official portrait with a busy background the path fills the whole frame and the face disappears at 150 to 600 strokes. |
| Squiggle scanlines | technique | One wavy line per row, amplitude and frequency following tone | Tried (own code, scratch only): the dark background dominates; the face is faint. |
| Marching-squares tone contours (numpy, contourpy: BSD-3) | technique | Isolines of local brightness | Tried inside the face: eyes and lips get outlines but the result reads as a skull-like mask. Not adopted, and numpy and contourpy were not added as dependencies. |

The chosen approach is our own and small: Sobel edges judged against their neighbourhood, hysteresis, linking, smoothing, Douglas-Peucker and Chaikin (all textbook methods), a quality bar for each line, and pen hatching of the dark features inside the face box that the crop call returns.
Pillow (HPND licence) does the image reading, blurring and filtering.
