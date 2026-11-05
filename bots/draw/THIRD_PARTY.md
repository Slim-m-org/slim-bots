# Third-party code and tools behind `!draw photo` and `!draw shaded`

## Used at runtime

| Component | Licence | Used for |
| --- | --- | --- |
| [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) `face_detection_yunet_2023mar.onnx` (opencv_zoo), copyright (c) 2020 Shiqi Yu | MIT (`LICENSE-yunet.txt`) | Local face detection, shipped in this directory (about 230 KB) |
| [OpenCV](https://opencv.org/) via `opencv-python-headless` (also its bundled Haar frontal-face cascade) | Apache-2.0 | Running YuNet; the Haar cascade is the fallback detector |
| NumPy | BSD-3-Clause | Required by OpenCV's Python bindings |
| Pillow | HPND | Reading, blurring and filtering pictures |

All are pinned with hashes in `bot-templates-requirements.lock.txt`.

## Studied, not copied

The owner asked for the plotter and image-to-line-drawing tools to be used as references for how to build these modes on our side.
Their algorithms were read from their published descriptions and behaviour and reimplemented; no source from the GPL projects was copied, and no source from linedraw is included.

| Tool | Licence | What was taken from it |
| --- | --- | --- |
| [LingDong-/linedraw](https://github.com/LingDong-/linedraw) (c) 2017 Lingdong Huang | MIT | The pairing of edge contours with tone hatching, and linking edge pixels into contours. Our edge pipeline (hysteresis, smoothing, a per-line quality bar) and the face-only optional hatching are our own. |
| DrawingBotV3 (Squiggle and Sketch path-finding modes) | GPL-3.0 | The idea that tone becomes line density and wave amplitude along scanlines; reimplemented in `squiggle.py` |
| potrace / potracer | GPL-2.0 | The idea of smoothing a traced outline into long curves; we use a moving average, Douglas-Peucker and one Chaikin pass instead of its Bezier fitting |
| vpype | MIT | Line merging and simplification as plotter pre-processing; the same steps are a few lines here |
| vtracer | MIT | Raster to vector shapes; not used, it makes filled outlines |

Textbook methods used as they are: the Sobel operator, hysteresis thresholding, Douglas-Peucker simplification and Chaikin corner cutting.
