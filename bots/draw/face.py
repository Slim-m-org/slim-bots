"""Finding a face in a picture on this machine: OpenCV's YuNet detector, or its bundled Haar frontal-face cascade if the model cannot load."""

import os

import pictures

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")  # OpenCV 4.11 warns about DNN targets on every detector it builds

MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_detection_yunet_2023mar.onnx")
SCORE = 0.85  # YuNet confidence below which a detection is ignored (a dog photo scored 0.81 on a bystander)
DETECT_SIDE = 960  # pixels on the long side the picture is shrunk to for detection
HAAR = "haarcascade_frontalface_default.xml"


def _within_bound(data):
    """Whether Pillow reads `data` and its header declares no more pixels than the bot decodes anywhere else; OpenCV allocates width*height*3 bytes from that header."""
    try:
        pictures.open_picture(data).close()
    except Exception:
        return False
    return True


def _decode(data):
    if not _within_bound(data):
        return None
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    height, width = image.shape[:2]
    if max(width, height) > DETECT_SIDE:
        scale = DETECT_SIDE / max(width, height)
        image = cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
    return image


_detector = []


def _yunet(image):
    import cv2

    if not _detector:
        _detector.append(cv2.FaceDetectorYN.create(MODEL, "", (image.shape[1], image.shape[0]), SCORE, 0.3, 5000))
    _detector[0].setInputSize((image.shape[1], image.shape[0]))
    _, faces = _detector[0].detect(image)
    return [] if faces is None else [tuple(float(v) for v in face[:4]) for face in faces]


def _haar(image):
    import cv2

    cascade = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, HAAR))
    if cascade.empty():
        return []
    found = cascade.detectMultiScale(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 1.1, 5, minSize=(40, 40))
    return [tuple(float(v) for v in face) for face in found]


def largest_face(data):
    """((x, y, w, h) as fractions of the picture, detector name) for the largest face, or None when there is none or OpenCV is missing."""
    try:
        image = _decode(data)
    except ImportError:
        return None
    if image is None:
        return None
    height, width = image.shape[:2]
    for name, detect in (("yunet", _yunet), ("haar", _haar)):
        try:
            faces = detect(image)
        except Exception:
            continue
        if faces:
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            return (max(x, 0.0) / width, max(y, 0.0) / height, min(w, width) / width, min(h, height) / height), name
    return None
