
import numpy as np

ACTIONS = ['idle', 'print', 'zoom', 'select']
SEQ_LENGTH = 30
FEATURE_DIM = 78
COLLECT_SIZE = (480, 270)        # 16:9. 4:3 카메라면 (480, 360)
COLLECT_ASPECT = COLLECT_SIZE[0] / COLLECT_SIZE[1]
INFER_SIZE = COLLECT_SIZE
ASPECT = COLLECT_ASPECT

def aspect_of(width, height):
    return width / height

_V1_IDX = [0, 1, 2, 3, 0, 5, 6, 7, 0, 9, 10, 11, 0, 13, 14, 15, 0, 17, 18, 19]
_V2_IDX = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
_A_IDX = [0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13, 14, 16, 17, 18]
_B_IDX = [1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15, 17, 18, 19]

def normalize_joints(joints, aspect=ASPECT):
    j = np.array(joints, dtype=np.float32, copy=True)
    j[..., 0] *= aspect
    j[..., 2] *= aspect
    j = j - j[..., 0:1, :]
    scale = np.linalg.norm(j[..., 9, :], axis=-1, keepdims=True)
    scale = np.maximum(scale, 1e-6)[..., np.newaxis]
    return j / scale

def angles_from_joints(joints):
    j = np.asarray(joints, dtype=np.float32)
    v = j[..., _V2_IDX, :] - j[..., _V1_IDX, :]
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    v = v / np.maximum(n, 1e-6)
    dot = np.sum(v[..., _A_IDX, :] * v[..., _B_IDX, :], axis=-1)
    angle = np.arccos(np.clip(dot, -1.0, 1.0))       # radian, 0~pi
    return (angle / np.pi).astype(np.float32)


def make_feature(joints, aspect=ASPECT, already_normalized=False):
    jn = joints if already_normalized else normalize_joints(joints, aspect)
    coords = np.asarray(jn, dtype=np.float32).reshape(*np.shape(jn)[:-2], 63)
    angles = angles_from_joints(jn)
    return np.concatenate([coords, angles], axis=-1)

def joints_from_landmarks(res):
    j = np.zeros((21, 3), dtype=np.float32)
    for i, lm in enumerate(res.landmark):
        j[i] = [lm.x, lm.y, lm.z]
    return j

def joints_from_raw(rows):
    r = np.asarray(rows, dtype=np.float32)
    return r[..., :84].reshape(*r.shape[:-1], 21, 4)[..., :3]

def labels_from_raw(rows):
    return np.asarray(rows, dtype=np.float32)[..., -1].astype(np.int64)
