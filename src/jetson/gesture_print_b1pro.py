import os
import time
import threading
from collections import deque

import cv2
import numpy as np

os.environ.setdefault("DISPLAY", ":1")
os.environ.setdefault("XAUTHORITY", "/home/durimango/.Xauthority")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import mediapipe as mp                                    # noqa: E402
import tensorflow as tf                                   # noqa: E402
from tensorflow.keras.models import Sequential            # noqa: E402
from tensorflow.keras.layers import (Input, LSTM, Dense,  # noqa: E402
                                     Dropout)

from hand_feature import make_feature, aspect_of          # noqa: E402

# ★ B1 Pro 전환: 모듈 이름이 niimbot_b1 → niimbot_b1pro 로 바뀌었습니다.
import niimbot_b1pro as niimbot_b1pro                                # noqa: E402
from niimbot_b1pro import NiimbotPrintWorker, ensure_sounds, play  # noqa: E402

try:
    import Jetson.GPIO as GPIO                            # noqa: E402
except Exception:
    GPIO = None

# ★ 대기(STANDBY) 화면: Z7-20 → SPI 로 들어오는 CNN 입력(128x128 gray) 뷰어
from cnn_image import Z7SpiViewer, make_standby_canvas  # noqa: E402

os.environ["DISPLAY"] = ":1"
os.environ["XAUTHORITY"] = "/home/durimango/.Xauthority"


CAM_INDEX       = 0
CAPTURE_WIDTH   = 2560
CAPTURE_HEIGHT  = 1440
CAPTURE_FPS     = 30        # 카메라에 요청할 FPS (30~60)
CAPTURE_FOURCC  = 'MJPG'
FLIP_HORIZONTAL = True      # 거울 모드

# --- 인식(추론) 파이프라인 ---
# ★ 320x240 은 4:3 인데 수집은 480x270(16:9) 이었습니다.
#   MediaPipe 의 x 는 가로폭, y 는 세로폭으로 각각 나뉘어 정규화되므로
#   화면비가 다르면 같은 손이 다른 좌표값을 냅니다. 320x180 으로 바꾸면
#   수집과 화면비가 같아지면서 픽셀 수는 오히려 320x240 보다 25% 적습니다.
INFER_WIDTH     = 320       # ★ 240 → 180 (16:9 유지 + 더 빠름)
INFER_HEIGHT    = 180
INFER_ASPECT    = aspect_of(INFER_WIDTH, INFER_HEIGHT)
INFER_MAX_FPS   = 30        # 인식 스레드 상한 FPS (0 이면 제한 없음)
TRACK_CONF      = 0.5       # ★ MediaPipe 추적 신뢰도 하한.
                            #   0.3 까지 낮췄었는데 얼굴/물체를 손으로 오검출한
                            #   상태로 추적을 계속 유지하는 부작용이 있어 복원.
DETECT_CONF     = 0.7       # ★ MediaPipe 손 '검출' 신뢰도 하한 (기존 0.5).
                            #   경량 모델(complexity 0)이 얼굴을 손으로 잡는
                            #   오검출을 줄입니다.
HAND_SCORE_MIN  = 0.85      # ★ handedness 분류 점수 하한. 진짜 손은 보통
                            #   0.95+ 이고 얼굴 오검출은 이보다 낮게 나옵니다.
                            #   이 값 미만이면 '손 없음'으로 처리.

# --- 모델 ---
# ★ 학습 노트북에서 고른 FEAT_MODE 와 반드시 같아야 합니다.
#   models/model.v5.*.json 의 feature_mode 값을 그대로 적으세요.
FEAT_MODE       = 'norm'    # 'norm'(78차원) | 'legacy'(99차원)
MODEL_WEIGHTS   = f'models/model.v6.{FEAT_MODE}.weights.h5'
ACTIONS         = ['idle', 'print', 'zoom', 'select']
SEQ_LENGTH      = 30
FEATURE_DIM     = 78 if FEAT_MODE == 'norm' else 99

# --- 후처리 ---
CONF_THRESHOLD  = 0.85      # ★ 0.75 → 0.85 (오인식으로 게이지가 제멋대로
                            #   차는 문제가 있어 다시 올림. v6 모델은 확신이
                            #   높아서 반응 속도 손해가 거의 없습니다)
STABLE_COUNT    = 3         # ★ 2 → 3 (연속 N회 동일하면 동작 확정)
RESULT_TTL      = 0.6       # ★ 0.4 → 0.6 (인식 FPS가 낮을 때 타이머가
                            #   억울하게 리셋되는 것을 방지)

# --- 반응속도 관련 ---
HAND_LOST_SEC    = 0.5      # 손이 이 시간 이상 연속으로 안 보여야 버퍼를 비움
MIN_WINDOW_RATIO = 0.4      # 윈도우의 이 비율만 모여도 추론 시작

# --- Z7-20 GPIO 핸드셰이크 ---
PIN_TRIG         = 15       # Z7 -> Jetson (입력). gpiochip0 line 85  / PN.01
PIN_RUN          = 16       # Jetson -> Z7 (출력). gpiochip0 line 126 / PY.04
IDLE_TIMEOUT_SEC = 3.0      # idle 제스처가 이만큼 지속되면 세션 종료

# --- 줌 토글 ---
ZOOM_HOLD_SEC   = 2.0       # zoom 제스처를 몇 초 유지하면 토글할지
ZOOM_SCALE      = 2         # 확대 배율 (2 = 2배)

# --- 필터 선택 ---
SELECT_HOLD_SEC = 1.0       # select 제스처를 몇 초 유지하면 메뉴를 열지/닫을지
FILTER_HOLD_SEC = 1.0       # 메뉴에서 한 필터 위에 몇 초 머무르면 적용할지
FILTER_ZONE_Y   = 0.62      # 손 중심의 정규화 y 가 이 값보다 아래(크면)여야
                            # '하단 메뉴 위에 있다'로 판정.
                            # ★ 0.70 은 화면 가장자리라 MediaPipe 가 손을 자주
                            #   놓쳤습니다. 살짝 올려서 선택 존을 넓힘.
FILTER_HOVER_GRACE = 0.4    # ★ 존에서 손을 잠깐(추적 끊김 등) 놓쳐도 이 시간
                            #   안에 돌아오면 게이지를 유지합니다
FILTER_ZONE_HYST   = 0.04   # ★ 존 경계 히스테리시스(정규화 x). 경계에 걸친
                            #   손이 옆 존으로 튀며 게이지가 리셋되는 것을 방지
MENU_EXIT_IDLE_SEC = 2.0    # ★ 메뉴에서 나가는 유일한 방법: idle 을 이만큼
                            #   유지. (select 로 닫는 방식은 잔상 오작동이
                            #   많아 폐지 — 메뉴는 '모달'로 동작합니다)

# --- ★ 인쇄용 필터 파라미터 ---
#   B1 Pro 는 흑백 감열(1비트 디더링) 인쇄라 색/왜곡 필터는 인쇄물에서
#   의미가 없습니다. 명암·선 구조를 바꾸는 필터로 구성합니다.
CARTOON_LEVELS     = 7      # 만화 색 단계 수 (줄일수록 더 만화 같음)
CARTOON_PROC_SCALE = 2      # 만화 계산 축소 배율 (2 = 절반 해상도에서 계산.
                            #   1 로 올리면 선이 가늘어지는 대신 ~5배 느림)
HALFTONE_CELLS     = 90     # 망점 개수 (화면 가로 기준). 클수록 점이 작아짐
HALFTONE_PROC_SCALE = 2     # 망점 계산 축소 배율 (2 = 절반 해상도에서 계산)

# ============================================================================
# ★★★ 신규: 사진 촬영 → 인화 ★★★
#   print 제스처를 PRINT_HOLD_SEC 초 유지 → 3·2·1 카운트다운 →
#   찰칵(플래시+셔터음) → 그 순간 화면을 54x86mm 라벨로 인쇄 (B1 Pro)
# ============================================================================
PRINT_HOLD_SEC     = 2.0    # print 제스처 유지 시간 (요청: 2초)
PRINT_HOLD_GRACE   = 0.35   # 인식이 순간적으로 끊겨도 이 시간 안에 돌아오면
                            # 유지 타이머를 살려 둡니다 (2초 채우기가 훨씬 쉬워짐)
COUNTDOWN_SEC      = 3      # 카운트다운 초 (요청: 3초, 3·2·1 표시)
FLASH_SEC          = 0.45   # 흰 플래시가 사라지는 시간
REVIEW_SEC         = 2.2    # 찍은 사진을 화면 가운데 보여 주는 시간
PRINT_COOLDOWN_SEC = 3.0    # 인화 후 재촬영 금지 시간 (연타 방지)
COPIES_MAX         = 6      # ★ 한 번에 뽑을 수 있는 최대 매수 (1~6 키)
COPIES_SELECT_TIMEOUT = 20.0
                            # ★ 촬영 후 매수 선택을 기다리는 시간(초).
                            #   아무 키도 안 누르면 1장으로 진행합니다.
CAPTURE_WITH_UI    = False  # True 면 게이지/디버그 글자까지 통째로 인쇄합니다.
                            # False = 필터/줌만 반영된 '깨끗한 사진'을 인쇄
# 인쇄 예상 소요 시간(초). 화면 진행 막대에만 쓰는 값이라 대충 맞으면 됩니다.
PRINT_EST_SEC      = 15.0   # main() 에서 덮어씁니다. 실측값으로 잡는 게 정확합니다.

# --- 손 전용 미니 화면 PiP ---
PIP_SCALE       = 4         # 원본의 1/PIP_SCALE 크기 (2560x1440 → 640x360)
PIP_MARGIN      = 16        # 화면 오른쪽/아래 가장자리와의 간격 (px)

# --- ★ 대기(STANDBY) 화면: Z7-20 SPI 영상 ---
#   세션이 닫혀 있는 동안(trig LOW) CNN 입력 128x128 gray 를 확대 표시합니다.
#   SPI 클럭/읽기 주기 등 세부 설정은 cnn_image.py 상단에 있습니다.
STANDBY_VIEW_SIZE = 1440    # 128x128 을 몇 px 정사각형으로 키워 보여줄지

# --- 디버그 ---
SHOW_DEBUG      = False     # FPS / 클래스별 확률 표시 (실행 중 'd' 키로 토글)

# ★★ 가장 중요한 수정 ★★
#   이 값은 '데이터를 수집할 때의 fps' 여야 합니다. 추론 fps 가 아닙니다.
TRAIN_FPS       = 15.0      # ★ 9.6 → 15.0
WINDOW_SEC      = SEQ_LENGTH / TRAIN_FPS             # = 2.0초
MIN_WINDOW_SEC  = WINDOW_SEC * MIN_WINDOW_RATIO      # ★ 추론 시작 최소 길이


# ── ★ GPIO 얇은 래퍼 ────────────────────────────────────────────────────
_gpio_ok  = False
_sim_trig = False           # 보드 없이 시험할 때 키보드 't' 로 토글


def gpio_init():
    global _gpio_ok
    try:
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BOARD)                 # 물리 핀 번호 그대로 사용
        GPIO.setup(PIN_TRIG, GPIO.IN)
        GPIO.setup(PIN_RUN,  GPIO.OUT, initial=GPIO.LOW)
        _gpio_ok = True
        print(f"[GPIO] 준비 완료  trig=핀{PIN_TRIG}(in)  run=핀{PIN_RUN}(out)")
    except Exception as e:
        print(f"[GPIO] 초기화 실패({e}) → 키보드 't' 로만 trig 를 넣습니다.")


def gpio_trig():
    """실제 핀과 키보드 시뮬레이션을 OR 로 합쳐 읽습니다."""
    hw = bool(GPIO.input(PIN_TRIG)) if _gpio_ok else False
    return hw or _sim_trig


def gpio_run(level):
    if _gpio_ok:
        GPIO.output(PIN_RUN, GPIO.HIGH if level else GPIO.LOW)
    print(f"[GPIO] run -> {'HIGH' if level else 'LOW'}")


def gpio_close():
    # run 이 HIGH 인 채로 죽으면 Z7 이 RUN 상태에서 영영 기다립니다.
    if _gpio_ok:
        GPIO.output(PIN_RUN, GPIO.LOW)
        GPIO.cleanup()


# ============================================================================
# 2. 스레드 간 데이터 교환 객체
# ============================================================================
class LatestFrame:
    """
    '가장 최근 프레임 1장'만 유지하는 단일 슬롯 버퍼.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._frame = None
        self._seq_id = 0
        self._closed = False

    def put(self, frame):
        """생산자(캡처 스레드) 호출. 항상 즉시 반환됩니다."""
        with self._cond:
            self._frame = frame          # 이전 프레임은 버려짐 (Frame Skip)
            self._seq_id += 1
            self._cond.notify_all()

    def get(self, last_seq_id=-1, timeout=None):
        with self._cond:
            if self._seq_id == last_seq_id and not self._closed:
                self._cond.wait_for(
                    lambda: self._seq_id != last_seq_id or self._closed,
                    timeout=timeout)
            if self._frame is None or self._seq_id == last_seq_id:
                return None, last_seq_id
            return self._frame, self._seq_id

    def peek(self):
        with self._cond:
            return self._frame, self._seq_id

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()


class ResultStore:
    """인식 결과 저장소 (이미지가 아닌 '메타데이터'만 담습니다)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            'action': '?', 'conf': 0.0, 'probs': None,
            'landmarks': None, 'bbox': None, 'span': 0.0, 'stamp': 0.0,
        }

    def update(self, **kwargs):
        with self._lock:
            self._data.update(kwargs)
            self._data['stamp'] = time.time()

    def clear_hand(self):
        """손이 화면에서 사라졌을 때 랜드마크/박스만 제거."""
        with self._lock:
            self._data['landmarks'] = None
            self._data['bbox'] = None
            self._data['action'] = '?'
            self._data['conf'] = 0.0
            self._data['span'] = 0.0

    def snapshot(self):
        with self._lock:
            data = dict(self._data)
        data['fresh'] = (time.time() - data['stamp']) < RESULT_TTL
        return data


class FPSMeter:
    """이동 평균 기반 FPS 측정기."""

    def __init__(self, window=30):
        self._times = deque(maxlen=window)
        self._last = time.time()

    def tick(self):
        now = time.time()
        self._times.append(now - self._last)
        self._last = now

    @property
    def fps(self):
        if not self._times:
            return 0.0
        avg = sum(self._times) / len(self._times)
        return 1.0 / avg if avg > 0 else 0.0


# ============================================================================
# 3. 캡처 스레드 — 카메라에서 최신 프레임만 계속 밀어 넣음
# ============================================================================
class CaptureThread(threading.Thread):
    def __init__(self, render_slot, infer_slot, stop_event):
        super().__init__(daemon=True, name="CaptureThread")
        self.render_slot = render_slot
        self.infer_slot = infer_slot
        self.stop_event = stop_event
        self.ready = threading.Event()
        self.fps_meter = FPSMeter()
        self.cap = None

    def run(self):
        self.cap = cv2.VideoCapture(CAM_INDEX)
        if CAPTURE_FOURCC:
            self.cap.set(cv2.CAP_PROP_FOURCC,
                         cv2.VideoWriter_fourcc(*CAPTURE_FOURCC))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  CAPTURE_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS,          CAPTURE_FPS)
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        if not self.cap.isOpened():
            print("[Capture] 카메라를 열 수 없습니다.")
            self.stop_event.set()
            self.ready.set()
            return

        code = int(self.cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_name = "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4))
        aw = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        ah = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[Capture] 시작: {aw}x{ah}  FOURCC={fourcc_name}")
        if fourcc_name.strip() != 'MJPG':
            print("[Capture] ⚠ MJPG가 아닙니다. YUYV 1280x960은 최대 7.5fps로")
            print("           제한되어 인식 속도가 크게 떨어집니다.")

        while not self.stop_event.is_set():
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.005)
                continue
            if FLIP_HORIZONTAL:
                frame = cv2.flip(frame, 1)
            self.fps_meter.tick()
            self.render_slot.put(frame)
            self.infer_slot.put(frame)
            self.ready.set()

        self.cap.release()
        self.render_slot.close()
        self.infer_slot.close()
        print("[Capture] 종료")


# ============================================================================
# 4. 인식 스레드 — 다운스케일 → MediaPipe → LSTM
# ============================================================================
def build_model():
    model = Sequential([
        Input(shape=(SEQ_LENGTH, FEATURE_DIM)),
        LSTM(64, return_sequences=True),
        Dropout(0.3),
        LSTM(64),
        Dropout(0.3),
        Dense(32, activation='relu'),
        Dropout(0.2),
        Dense(len(ACTIONS), activation='softmax'),
    ])
    model.load_weights(MODEL_WEIGHTS)
    return model


def make_predict_fn(model):
    @tf.function(
        input_signature=[tf.TensorSpec([1, SEQ_LENGTH, FEATURE_DIM], tf.float32)],
        reduce_retracing=True)
    def _predict(x):
        return model(x, training=False)
    return _predict


def fast_resize(frame, w, h):
    step = max(1, min(frame.shape[1] // (w * 2), frame.shape[0] // (h * 2)))
    if step > 1:
        frame = frame[::step, ::step]
    return cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)


class RecognitionThread(threading.Thread):
    PARENT = [0, 1, 2, 3, 0, 5, 6, 7, 0, 9, 10, 11, 0, 13, 14, 15, 0, 17, 18, 19]
    CHILD  = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    ANG_A  = [0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13, 14, 16, 17, 18]
    ANG_B  = [1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15, 17, 18, 19]

    def __init__(self, infer_slot, store, stop_event, active_event):
        super().__init__(daemon=True, name="RecognitionThread")
        self.infer_slot = infer_slot
        self.store = store
        self.stop_event = stop_event
        self.active_event = active_event     # ★ 세션 게이트
        self.ready = threading.Event()
        self.fps_meter = FPSMeter()

    # ---------------------------------------------------------------- 특징 추출
    def _extract_feature(self, landmark_list):
        joint = np.zeros((21, 4), dtype=np.float32)
        for j, lm in enumerate(landmark_list.landmark):
            joint[j] = [lm.x, lm.y, lm.z, lm.visibility]

        if FEAT_MODE == 'norm':
            feature = make_feature(joint[:, :3], aspect=INFER_ASPECT)
            return feature, joint

        # ---- legacy (99차원) ----
        v1 = joint[self.PARENT, :3]
        v2 = joint[self.CHILD, :3]
        v = v2 - v1
        norm = np.linalg.norm(v, axis=1)[:, np.newaxis]
        norm[norm == 0] = 1e-6              # 0 나눗셈 방어
        v = v / norm
        dot = np.einsum('nt,nt->n', v[self.ANG_A, :], v[self.ANG_B, :])
        angle = np.degrees(np.arccos(np.clip(dot, -1.0, 1.0)))
        return np.concatenate([joint.flatten(), angle]), joint

    # ---------------------------------------------------------------- 메인 루프
    def run(self):
        mp_hands = mp.solutions.hands
        hands = mp_hands.Hands(
            max_num_hands=1,
            model_complexity=0,
            min_detection_confidence=DETECT_CONF,
            min_tracking_confidence=TRACK_CONF)

        model = build_model()
        predict = make_predict_fn(model)          # ★ tf.function 컴파일 버전

        warm = np.zeros((1, SEQ_LENGTH, FEATURE_DIM), np.float32)
        t0 = time.time()
        predict(warm)
        predict(warm)
        print(f"[Recog] 모델 준비 완료 (그래프 컴파일 {time.time()-t0:.2f}s)")
        print(f"[Recog] feature={FEAT_MODE}({FEATURE_DIM}차원)  "
              f"infer={INFER_WIDTH}x{INFER_HEIGHT}(aspect {INFER_ASPECT:.3f})")
        print(f"[Recog] 시간 기반 리샘플링: 최근 {WINDOW_SEC:.2f}초를 "
              f"{SEQ_LENGTH}칸으로 (수집 {TRAIN_FPS}fps 기준)")
        print(f"[Recog] ★ 최소 {MIN_WINDOW_SEC:.2f}초만 모여도 추론 시작 / "
              f"손 놓침 유예 {HAND_LOST_SEC:.1f}초")
        self.ready.set()

        buf = deque(maxlen=400)
        action_seq = deque(maxlen=STABLE_COUNT)
        last_seq_id = -1
        last_hand_time = 0.0
        min_interval = 1.0 / INFER_MAX_FPS if INFER_MAX_FPS > 0 else 0.0
        next_time = 0.0

        while not self.stop_event.is_set():
            frame, seq_id = self.infer_slot.get(last_seq_id, timeout=0.5)
            if frame is None:
                continue
            last_seq_id = seq_id

            if not self.active_event.is_set():
                if buf:
                    buf.clear()
                    action_seq.clear()
                    self.store.clear_hand()
                continue

            now = time.time()
            if now < next_time:
                continue
            next_time = now + min_interval

            small = fast_resize(frame, INFER_WIDTH, INFER_HEIGHT)
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            result = hands.process(rgb)
            self.fps_meter.tick()

            # ── ★ 오검출 게이트 ──────────────────────────────────────
            #   경량 손 검출기는 가끔 '얼굴'을 손으로 잡습니다. 그 랜드마크는
            #   쓰레기 값이라 LSTM 이 아무 동작이나 확신 있게 찍을 수 있으므로,
            #   handedness 분류 점수가 낮으면 손이 없는 것으로 취급합니다.
            res = None
            if result.multi_hand_landmarks:
                score = (result.multi_handedness[0].classification[0].score
                         if result.multi_handedness else 1.0)
                if score >= HAND_SCORE_MIN:
                    res = result.multi_hand_landmarks[0]

            if res is None:
                if now - last_hand_time > HAND_LOST_SEC:
                    buf.clear()
                    action_seq.clear()
                    self.store.clear_hand()
                continue

            last_hand_time = now
            feature, joint = self._extract_feature(res)
            buf.append((now, feature))

            xy = joint[:, :2].copy()                       # (21, 2) 정규화 좌표
            pad = 0.03
            bbox = (float(xy[:, 0].min() - pad), float(xy[:, 1].min() - pad),
                    float(xy[:, 0].max() + pad), float(xy[:, 1].max() + pad))
            self.store.update(landmarks=xy, bbox=bbox)

            times = np.fromiter((t for t, _ in buf), dtype=np.float64,
                                count=len(buf))
            span = float(times[-1] - times[0])
            self.store.update(span=span)

            if span < MIN_WINDOW_SEC:
                continue

            t_end = times[-1]
            targets = np.linspace(t_end - WINDOW_SEC, t_end, SEQ_LENGTH)
            idx = np.clip(np.searchsorted(times, targets), 0, len(buf) - 1)
            input_data = np.stack([buf[i][1] for i in idx])[None].astype(np.float32)

            probs = predict(input_data).numpy().squeeze()
            i_pred = int(np.argmax(probs))
            conf = float(probs[i_pred])
            self.store.update(probs=probs)

            if conf < CONF_THRESHOLD:
                continue

            action_seq.append(ACTIONS[i_pred])
            if len(action_seq) == STABLE_COUNT and len(set(action_seq)) == 1:
                self.store.update(action=action_seq[-1], conf=conf)

        hands.close()
        print("[Recog] 종료")


# ============================================================================
# 5. 줌 토글 (손 위치 중심 확대)
# ============================================================================
class ZoomController:
    def __init__(self):
        self.zoomed = False          # 현재 확대 상태인가?
        self.center = (0.5, 0.5)     # 확대 중심 (정규화 좌표)
        self.hold_start = None       # zoom 제스처가 시작된 시각
        self.wait_release = False    # 토글 후 '손 떼기'를 기다리는 중인가?

    def update(self, action, hand_center=None):
        if action != 'zoom':
            self.hold_start = None
            self.wait_release = False
            return
        if self.wait_release:
            return
        if self.hold_start is None:
            self.hold_start = time.time()
        elif time.time() - self.hold_start >= ZOOM_HOLD_SEC:
            self.zoomed = not self.zoomed
            if self.zoomed:
                self.center = hand_center if hand_center is not None else (0.5, 0.5)
            self.hold_start = None
            self.wait_release = True

    @property
    def progress(self):
        if self.hold_start is None:
            return 0.0
        return min((time.time() - self.hold_start) / ZOOM_HOLD_SEC, 1.0)


def apply_zoom(frame, center=(0.5, 0.5), scale=ZOOM_SCALE):
    h, w = frame.shape[:2]
    ch, cw = h // scale, w // scale          # 잘라낼 크기
    cx = int(center[0] * w)                  # 정규화 → 픽셀 좌표
    cy = int(center[1] * h)
    x1 = cx - cw // 2
    y1 = cy - ch // 2
    x1 = min(max(x1, 0), w - cw)             # 화면 밖으로 나가지 않게 클램프
    y1 = min(max(y1, 0), h - ch)
    crop = frame[y1:y1 + ch, x1:x1 + cw]
    return cv2.resize(crop, (w, h), interpolation=cv2.INTER_LINEAR)


# ============================================================================
# ★ 5.5  통합 HUD — 모든 '몇 초 유지' 게이지를 한 가지 문법으로 통일
#
#   예전에는 줌/필터/촬영 게이지가 우상단에 색색의 막대로 제각각 쌓였는데,
#   이제는:
#     - 유지 게이지  : 화면 상단 중앙의 원형 링 하나 (한 번에 한 동작만
#                      진행되므로 슬롯 하나면 충분합니다)
#     - 상태 배지    : 우상단 알약(pill) 모양 미니 배지 (ZOOM x2, CARTOON 등)
#     - 세션 종료    : 하단 중앙의 얇은 게이지 (idle 지속 시에만 스르륵 등장)
# ============================================================================
ACCENT_ZOOM   = (255, 200, 0)     # BGR
ACCENT_FILTER = (0, 140, 255)
ACCENT_PRINT  = (120, 255, 0)
HUD_DARK      = (24, 21, 18)      # 반투명 배경용 다크 톤
HUD_TRACK     = (64, 60, 56)      # 게이지 트랙(빈 부분)
HUD_TEXT      = (235, 235, 235)


def _rounded_fill(img, p1, p2, color, r):
    """모서리 둥근 사각형 채우기 (cv2 기본 제공이 없어서 직접)."""
    x1, y1 = p1
    x2, y2 = p2
    r = max(1, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    cv2.rectangle(img, (x1 + r, y1), (x2 - r, y2), color, -1)
    cv2.rectangle(img, (x1, y1 + r), (x2, y2 - r), color, -1)
    for cx, cy in ((x1 + r, y1 + r), (x2 - r, y1 + r),
                   (x1 + r, y2 - r), (x2 - r, y2 - r)):
        cv2.circle(img, (cx, cy), r, color, -1, cv2.LINE_AA)


def _blend_roi(canvas, x1, y1, x2, y2, draw_fn, alpha=0.60):
    """ROI 안에서만 draw_fn 으로 그린 것을 반투명 합성 (전체 프레임 블렌딩은
    2560x1440 에서 너무 비싸므로 작은 영역만)."""
    h, w = canvas.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return
    roi = canvas[y1:y2, x1:x2]
    over = roi.copy()
    draw_fn(over, x1, y1)
    cv2.addWeighted(over, alpha, roi, 1.0 - alpha, 0, dst=roi)


def draw_hold_ring(canvas, progress, label, color):
    """상단 중앙 원형 링 게이지. progress 0~1, 링 안에 라벨."""
    h, w = canvas.shape[:2]
    R = max(46, int(h * 0.062))
    cx, cy = w // 2, int(h * 0.16)
    pad = 18

    _blend_roi(canvas, cx - R - pad, cy - R - pad, cx + R + pad, cy + R + pad,
               lambda over, ox, oy: cv2.circle(
                   over, (cx - ox, cy - oy), R + pad - 4, HUD_DARK, -1,
                   cv2.LINE_AA))
    cv2.circle(canvas, (cx, cy), R, HUD_TRACK, 6, cv2.LINE_AA)
    if progress > 0:
        cv2.ellipse(canvas, (cx, cy), (R, R), -90, 0,
                    int(360 * min(progress, 1.0)), color, 6, cv2.LINE_AA)
    scale = R / 60.0
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, scale, 2)
    cv2.putText(canvas, label, (cx - tw // 2, cy + th // 2),
                cv2.FONT_HERSHEY_DUPLEX, scale, HUD_TEXT, 2, cv2.LINE_AA)


def draw_hold_hud(canvas, zoom_ctrl, filter_ctrl, print_ctrl):
    """진행 중인 '유지' 게이지를 골라 링 하나로 표시합니다.
    (제스처는 한 번에 하나만 인식되므로 겹칠 일이 사실상 없습니다)"""
    if print_ctrl.state == PrintController.IDLE and print_ctrl.hold_progress > 0:
        draw_hold_ring(canvas, print_ctrl.hold_progress, "PHOTO", ACCENT_PRINT)
    elif zoom_ctrl.progress > 0:
        draw_hold_ring(canvas, zoom_ctrl.progress, "ZOOM", ACCENT_ZOOM)
    elif filter_ctrl.select_progress > 0:
        label = "RESET" if filter_ctrl.active is not None else "MENU"
        draw_hold_ring(canvas, filter_ctrl.select_progress, label,
                       ACCENT_FILTER)
    elif filter_ctrl.menu_open and filter_ctrl.hover_progress > 0:
        name = FilterController.LABELS[
            FilterController.FILTERS[filter_ctrl.hover_idx]]
        draw_hold_ring(canvas, filter_ctrl.hover_progress, name, ACCENT_FILTER)
    elif filter_ctrl.exit_progress > 0:
        # ★ 메뉴에서 idle 유지로 나가는 중
        draw_hold_ring(canvas, filter_ctrl.exit_progress, "CLOSE",
                       (170, 168, 165))


def draw_status_pills(canvas, zoom_ctrl, filter_ctrl):
    """켜져 있는 상태(줌/필터)를 우상단 알약 배지로 표시합니다."""
    h, w = canvas.shape[:2]
    pills = []
    if zoom_ctrl.zoomed:
        pills.append((f"ZOOM x{ZOOM_SCALE}", ACCENT_ZOOM))
    if filter_ctrl.active is not None:
        pills.append((FilterController.LABELS[filter_ctrl.active],
                      ACCENT_FILTER))
    x_right = w - 24
    for text, color in pills:
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, 0.85, 2)
        x1, y1 = x_right - tw - 36, 26
        x2, y2 = x_right, 26 + th + 26
        _blend_roi(canvas, x1, y1, x2, y2,
                   lambda over, ox, oy: _rounded_fill(
                       over, (x1 - ox, y1 - oy), (x2 - ox, y2 - oy),
                       HUD_DARK, (y2 - y1) // 2), alpha=0.65)
        cv2.putText(canvas, text, (x1 + 18, y2 - 14),
                    cv2.FONT_HERSHEY_DUPLEX, 0.85, color, 2, cv2.LINE_AA)
        x_right = x1 - 12


def draw_idle_ui(canvas, idle_since):
    """idle 이 이어질 때 하단 중앙에 세션 종료 게이지를 표시합니다.
    잠깐 손을 내린 정도(0.8초 미만)로는 나타나지 않아 화면이 차분합니다."""
    if idle_since is None:
        return
    t = time.time() - idle_since
    if t < 0.8:
        return
    h, w = canvas.shape[:2]
    frac = min(1.0, t / IDLE_TIMEOUT_SEC)
    bar_w, bar_h = int(w * 0.24), 8
    x1, y1 = (w - bar_w) // 2, h - 56
    label = "ENDING SESSION"
    (tw, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.62, 2)

    _blend_roi(canvas, x1 - 24, y1 - 40, x1 + bar_w + 24, y1 + bar_h + 16,
               lambda over, ox, oy: _rounded_fill(
                   over, (x1 - 24 - ox, y1 - 40 - oy),
                   (x1 + bar_w + 24 - ox, y1 + bar_h + 16 - oy),
                   HUD_DARK, 14), alpha=0.55)
    cv2.putText(canvas, label, ((w - tw) // 2, y1 - 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.62, (170, 168, 165), 2, cv2.LINE_AA)
    _rounded_fill(canvas, (x1, y1), (x1 + bar_w, y1 + bar_h), HUD_TRACK, 4)
    if frac > 0:
        _rounded_fill(canvas, (x1, y1),
                      (x1 + max(8, int(bar_w * frac)), y1 + bar_h),
                      (140, 140, 140), 4)


# ============================================================================
# 6. 필터 토글
# ============================================================================
class FilterController:
    FILTERS = ('sobel', 'cartoon', 'halftone')
    LABELS  = {'sobel': 'SOBEL', 'cartoon': 'CARTOON', 'halftone': 'DOTS'}

    def __init__(self):
        self.menu_open = False
        self.active = None
        self.hold_start = None
        self.wait_release = False
        self.hover_idx = None
        self.hover_time = 0.0        # ★ 게이지: 시작 시각이 아니라 '누적 시간'
        self._hover_seen = 0.0       # 존 안에서 손을 마지막으로 본 시각
        self._prev_t = None          # 누적용 직전 update 시각
        self._idle_since = None      # ★ 메뉴 안에서 idle 이 시작된 시각

    def update(self, action, hand_center):
        now = time.time()
        dt = 0.0 if self._prev_t is None else min(now - self._prev_t, 0.2)
        self._prev_t = now

        # ══ 메뉴 열림 = '모달' 모드 ═════════════════════════════════════
        #   메인 루프가 zoom/print 갱신을 멈추고, 여기서도 select 는 아무
        #   일도 하지 않습니다. 나가는 길은 딱 두 가지:
        #     1) 필터 존에 손을 올려 게이지 완주 → 적용 후 닫힘
        #     2) idle 을 MENU_EXIT_IDLE_SEC 유지 → 그냥 닫힘
        if self.menu_open:
            self.hold_start = None
            self.wait_release = True     # 닫힌 직후 select 잔상 재오픈 방지

            if action == 'idle':
                if self._idle_since is None:
                    self._idle_since = now
                elif now - self._idle_since >= MENU_EXIT_IDLE_SEC:
                    self.menu_open = False
                    self._idle_since = None
                    self._reset_hover()
                    return
            else:
                self._idle_since = None

            # 호버는 손 '위치'만 봅니다. 제스처 종류는 완전히 무시 —
            # 메뉴를 select 유지로 연 직후 손 모양이 select 로 남아 있어도
            # 게이지가 그대로 차오릅니다.
            idx = self._zone_of(hand_center)
            if idx is None:
                # 추적 끊김/경계 이탈은 짧으면 유예: 게이지 얼려 두고 대기
                if self.hover_idx is not None and \
                        now - self._hover_seen > FILTER_HOVER_GRACE:
                    self._reset_hover()
                return
            self._hover_seen = now
            if idx != self.hover_idx:
                self.hover_idx = idx
                self.hover_time = 0.0
                return
            self.hover_time += dt
            if self.hover_time >= FILTER_HOLD_SEC:
                name = self.FILTERS[idx]
                self.active = None if self.active == name else name
                self.menu_open = False
                self._idle_since = None
                self._reset_hover()
            return

        # ══ 메뉴 닫힘: select 유지 → 메뉴 열기 / 필터 리셋 ═══════════════
        self._reset_hover()
        self._idle_since = None
        if action != 'select':
            self.hold_start = None
            self.wait_release = False
        elif not self.wait_release:
            if self.hold_start is None:
                self.hold_start = now
            elif now - self.hold_start >= SELECT_HOLD_SEC:
                if self.active is not None:
                    self.active = None       # 필터 켜져 있으면 먼저 리셋
                else:
                    self.menu_open = True
                self.hold_start = None
                self.wait_release = True

    def _reset_hover(self):
        self.hover_idx = None
        self.hover_time = 0.0

    @property
    def exit_progress(self):
        """메뉴 안에서 idle 유지로 나가는 게이지 (0~1)."""
        if not self.menu_open or self._idle_since is None:
            return 0.0
        return min((time.time() - self._idle_since) / MENU_EXIT_IDLE_SEC, 1.0)

    def _zone_of(self, hand_center):
        if hand_center is None:
            return None
        cx, cy = hand_center
        if cy < FILTER_ZONE_Y:
            return None
        idx = int(cx * 3)
        if idx < 0 or idx > 2:
            return None
        # ★ 경계 히스테리시스: 채우고 있던 존의 경계 ±HYST 안에서는
        #   옆 존으로 넘어가지 않고 원래 존을 유지합니다.
        if self.hover_idx is not None and idx != self.hover_idx:
            lo = self.hover_idx / 3.0 - FILTER_ZONE_HYST
            hi = (self.hover_idx + 1) / 3.0 + FILTER_ZONE_HYST
            if lo <= cx <= hi:
                return self.hover_idx
        return idx

    @property
    def select_progress(self):
        if self.hold_start is None:
            return 0.0
        return min((time.time() - self.hold_start) / SELECT_HOLD_SEC, 1.0)

    @property
    def hover_progress(self):
        if self.hover_idx is None:
            return 0.0
        return min(self.hover_time / FILTER_HOLD_SEC, 1.0)


# ★ 망점 거리 맵 캐시. 픽셀별 '자기 셀 중심까지의 거리'는 프레임 내용과
#   무관하게 (해상도, 셀 크기)에만 의존하므로 한 번만 만들어 재사용합니다.
_HALFTONE_DIST_CACHE = {}


def _halftone_dist(h, w, cell):
    key = (h, w, cell)
    d = _HALFTONE_DIST_CACHE.get(key)
    if d is None:
        n_rows = -(-h // cell) * cell               # cell 배수로 올림
        n_cols = -(-w // cell) * cell
        ys = (np.arange(n_rows, dtype=np.float32) % cell) - (cell - 1) / 2.0
        xs = (np.arange(n_cols, dtype=np.float32) % cell) - (cell - 1) / 2.0
        d = np.sqrt(ys[:, None] ** 2 + xs[None, :] ** 2)[:h, :w]
        _HALFTONE_DIST_CACHE[key] = d
    return d


def apply_filter(frame, name):
    """
    ★ 필터 구성 원칙: B1 Pro 인쇄는 흑백 1비트(Floyd-Steinberg 디더링)라서
      색·왜곡 필터는 인쇄물에 의미가 없습니다. 세 필터 모두 '명암/선 구조'
      필터라 라벨로 뽑았을 때 각자 다른 인상이 나옵니다.
        sobel    : 윤곽선만 남는 반전 느낌 (어두운 배경, 극적)
        cartoon  : 만화 (굵은 검은 윤곽선 + 색 면 단순화, 인쇄에서 제일 잘 나옴)
        halftone : 신문 망점 (레트로 인쇄물 느낌)
    """
    if name == 'sobel':
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        mag = np.clip(mag, 0, 255).astype(np.uint8)
        return cv2.cvtColor(mag, cv2.COLOR_GRAY2BGR)

    if name == 'cartoon':
        # 만화: 색 면을 뭉갠 뒤(양방향 필터) 몇 단계로 끊고, 그 위에
        # adaptive threshold 로 뽑은 굵은 검은 윤곽선을 얹습니다.
        # ★ 전부 절반 해상도에서 계산하고 마지막에 확대 — 원본 해상도로
        #   돌리면 시각적 차이는 거의 없는데 ~5배 느립니다 (300ms/frame).
        h, w = frame.shape[:2]
        s = CARTOON_PROC_SCALE
        hw, hh = w // s, h // s
        half = cv2.resize(frame, (hw, hh), interpolation=cv2.INTER_AREA)
        small = cv2.resize(half, (hw // 2, hh // 2),
                           interpolation=cv2.INTER_AREA)
        for _ in range(2):
            small = cv2.bilateralFilter(small, 9, 75, 75)
        up = cv2.resize(small, (hw, hh), interpolation=cv2.INTER_LINEAR)
        step = 256 // CARTOON_LEVELS
        lut = np.clip((np.arange(256) // step) * step + step // 2,
                      0, 255).astype(np.uint8)
        quant = lut[up]
        gray = cv2.medianBlur(cv2.cvtColor(half, cv2.COLOR_BGR2GRAY), 5)
        edges = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                      cv2.THRESH_BINARY, 9, 2)
        out = cv2.bitwise_and(quant, quant, mask=edges)
        return cv2.resize(out, (w, h), interpolation=cv2.INTER_LINEAR)

    if name == 'halftone':
        # 밝기에 비례한 크기의 검은 점을 격자로 찍는 망점.
        # 절반 해상도에서 계산 후 NEAREST 확대 (점 모양이 뭉개지지 않음).
        h, w = frame.shape[:2]
        s = HALFTONE_PROC_SCALE
        ph, pw = h // s, w // s
        small = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        cell = max(4, pw // HALFTONE_CELLS)
        gh, gw = -(-ph // cell), -(-pw // cell)
        mean = cv2.resize(gray, (gw, gh),
                          interpolation=cv2.INTER_AREA).astype(np.float32)
        radius = (1.0 - mean / 255.0) * (cell * 0.72)   # 어두울수록 큰 점
        r_up = np.repeat(np.repeat(radius, cell, 0), cell, 1)[:ph, :pw]
        out = np.where(_halftone_dist(ph, pw, cell) <= r_up,
                       0, 255).astype(np.uint8)
        out = cv2.resize(out, (w, h), interpolation=cv2.INTER_NEAREST)
        return cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)

    return frame


def draw_filter_ui(canvas, filter_ctrl):
    """하단 필터 메뉴. 게이지/상태 표시는 통합 HUD 가 맡으므로
    여기서는 메뉴가 열렸을 때의 버튼만 그립니다."""
    if not filter_ctrl.menu_open:
        return
    h, w = canvas.shape[:2]

    # ★ 판정 존(FILTER_ZONE_Y 아래)과 비슷하게 버튼도 키워서
    #   '보이는 목표'와 '실제 판정 영역'의 괴리를 줄입니다.
    y1, y2 = int(h * 0.72), int(h * 0.96)
    for i, name in enumerate(FilterController.FILTERS):
        x1 = int(w * (i / 3)) + 14
        x2 = int(w * ((i + 1) / 3)) - 14
        hover = (filter_ctrl.hover_idx == i and filter_ctrl.hover_progress > 0)

        def _draw(over, ox, oy, x1=x1, x2=x2, hover=hover):
            _rounded_fill(over, (x1 - ox, y1 - oy), (x2 - ox, y2 - oy),
                          HUD_DARK, 22)
            if hover:
                fill = int((x2 - x1) * filter_ctrl.hover_progress)
                if fill > 10:
                    _rounded_fill(over, (x1 - ox, y1 - oy),
                                  (x1 + fill - ox, y2 - oy),
                                  (30, 96, 190), 22)         # 선명한 주황 필
        _blend_roi(canvas, x1, y1, x2, y2, _draw, alpha=0.72)

        if filter_ctrl.active == name:
            border = ACCENT_FILTER
        elif hover:
            border = (150, 120, 80)
        else:
            border = (86, 82, 78)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), border, 2, cv2.LINE_AA)

        label = FilterController.LABELS[name]
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, 1.1, 2)
        tx = x1 + ((x2 - x1) - tw) // 2
        ty = y1 + ((y2 - y1) + th) // 2
        col = ACCENT_FILTER if filter_ctrl.active == name else HUD_TEXT
        cv2.putText(canvas, label, (tx, ty),
                    cv2.FONT_HERSHEY_DUPLEX, 1.1, col, 2, cv2.LINE_AA)
        if filter_ctrl.active == name:
            cv2.circle(canvas, (x1 + 26, (y1 + y2) // 2), 7,
                       ACCENT_FILTER, -1, cv2.LINE_AA)   # ON 점 표시

    # ★ 나가는 방법 안내 (메뉴는 모달이라 이 두 가지뿐입니다)
    hint = "HOLD HAND ON A FILTER   /   REST 2s TO CLOSE"
    (tw, _), _ = cv2.getTextSize(hint, cv2.FONT_HERSHEY_SIMPLEX, 0.62, 2)
    cv2.putText(canvas, hint, ((w - tw) // 2, y1 - 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.62, (185, 183, 180), 2, cv2.LINE_AA)


# ============================================================================
# ★ 6.5  촬영 → 인화 상태 기계 (신규)
# ============================================================================
def compose_photo(frame, zoom_ctrl, filter_ctrl):
    """
    '인쇄할 그림'을 만듭니다.
    필터와 확대는 반영하되, 게이지/디버그 글자/PiP 같은 UI 는 넣지 않습니다.
    (메인 화면 canvas 는 디버그 텍스트를 확대 전에 그려 넣기 때문에
     그걸 그대로 인쇄하면 글자가 사진 위에 커다랗게 박혀 나옵니다)
    """
    img = frame.copy()
    if filter_ctrl.active is not None:
        img = apply_filter(img, filter_ctrl.active)
    if zoom_ctrl.zoomed:
        img = apply_zoom(img, zoom_ctrl.center)
    return img


class PrintController:
    """
    print 제스처 → 카메라 타이머 → 인화 까지의 상태 기계.

        idle ──(print 2초 유지)──> count(3초, 3·2·1) ──> flash(찰칵)
          ▲                                                   │
          │                        select(★ 1~6 키로 매수 선택) ◄┘
          │                            │  (20초 무입력 = 1장)
          └────── cooldown ◄────── review(찍은 사진 미리보기) ◄┘

    ★ 매수 선택: 여러 명이 같이 찍으면 한 사람당 한 장씩 나눠 줘야 하므로
      촬영 직후 1~6 키로 '같은 사진'을 몇 장 뽑을지 고릅니다.

    설계 노트
      - '2초 유지' 판정은 인식이 순간적으로 끊겨도 PRINT_HOLD_GRACE 안에
        돌아오면 이어집니다. 인식 FPS가 10~15 정도라 한두 프레임 놓치는 일이
        흔한데, 그때마다 타이머를 0으로 되돌리면 2초를 채우기가 매우 어렵습니다.
      - 실제 캡처는 카운트다운이 0이 되는 프레임의 '원본 화면'에서 뜹니다.
        플래시가 덮이기 전이라 하얗게 뜨지 않습니다.
      - 프린터 전송은 워커 스레드가 맡으므로 여기서는 submit 만 하고 넘어갑니다.
    """

    IDLE, COUNT, FLASH, SELECT, REVIEW = ('idle', 'count', 'flash',
                                          'select', 'review')

    def __init__(self, worker):
        self.worker = worker
        self.state = self.IDLE
        self.hold_start = None
        self.last_print_seen = 0.0
        self.wait_release = False
        self.t_state = 0.0
        self.captured = None          # 인쇄로 넘긴 BGR 프레임 (미리보기용)
        self.last_captured = None     # ★ 마지막으로 찍은 사진 (r 키 재인쇄용)
        self._next_beep = 0
        self.cooldown_until = 0.0
        self.copies = 1               # ★ 이번 촬영에서 선택된 인쇄 매수
        self._paper = None            # 미리보기 썸네일 캐시
        self._paper_key = None

    def review_paper(self, pw, ph):
        """
        REVIEW 화면용 '인화지 썸네일'을 만들어 캐시합니다.

        ★ 성능 주의: 2560x1440 프레임을 rotate + resize 하면 프레임당 14 ms 입니다.
          미리보기 2.2초 동안 매 프레임 다시 만들면 렌더 FPS 가 반토막 납니다.
          그림은 어차피 고정이므로 딱 한 번만 만들어 재사용합니다.

        ★ 회전 방향(90도 시계)은 niimbot_b1pro 의 54x86 프리셋 회전각과
          같은 값이므로 인쇄물과 미리보기의 방향이 일치합니다.
        """
        key = (pw, ph, id(self.captured))
        if self._paper_key == key and self._paper is not None:
            return self._paper
        rot = cv2.rotate(self.captured, cv2.ROTATE_90_CLOCKWISE)
        rh, rw = rot.shape[:2]
        s = min(pw / rw, ph / rh)
        thumb = cv2.resize(rot, (max(1, int(rw * s)), max(1, int(rh * s))),
                           interpolation=cv2.INTER_AREA)
        paper = np.full((ph, pw, 3), 245, np.uint8)      # 흰 인화지
        oy = (ph - thumb.shape[0]) // 2
        ox = (pw - thumb.shape[1]) // 2
        paper[oy:oy + thumb.shape[0], ox:ox + thumb.shape[1]] = thumb
        self._paper, self._paper_key = paper, key
        return paper

    # ------------------------------------------------------------------ 상태
    @property
    def busy(self):
        """촬영 시퀀스가 진행 중인가? (세션 자동 종료를 막는 데도 씁니다)"""
        return self.state != self.IDLE

    @property
    def hold_progress(self):
        if self.hold_start is None:
            return 0.0
        return min((time.time() - self.hold_start) / PRINT_HOLD_SEC, 1.0)

    @property
    def count_remain(self):
        return max(0.0, COUNTDOWN_SEC - (time.time() - self.t_state))

    @property
    def cooldown_remain(self):
        return max(0.0, self.cooldown_until - time.time())

    @property
    def blocked_reason(self):
        """지금 촬영을 시작할 수 없다면 그 이유, 가능하면 None."""
        if self.state != self.IDLE:
            return '촬영 진행 중'
        if self.worker.busy:
            # ★ 인쇄 한 장에 10초 이상 걸립니다(블루투스 전송 + 완료 대기).
            #   그 사이에 또 찍으면 큐에 쌓여 한참 뒤에 나오므로 막습니다.
            return '인쇄 중'
        if self.cooldown_remain > 0:
            return f'쿨다운 {self.cooldown_remain:.1f}s'
        return None

    # ------------------------------------------------------------------ 시작
    def force_start(self):
        """키보드 'p' 로 강제 촬영 (프린터/제스처 시험용)."""
        if self.blocked_reason is None:
            self._enter_count()
            return True
        return False

    def reprint_last(self):
        """
        ★ 키보드 'r' — 마지막에 찍은 사진을 '다시 찍지 않고' 인쇄합니다.
        인쇄 설정(농도·속도 등)을 바꿔 가며 시험할 때
        매번 제스처 잡고 3초 세는 걸 반복하지 않아도 됩니다.
        """
        if self.last_captured is None:
            print('[Photo] 재인쇄할 사진이 없습니다. 먼저 한 장 찍으세요.')
            return False
        if self.worker.busy:
            print('[Photo] 아직 인쇄 중입니다.')
            return False
        print('[Photo] 마지막 사진 재인쇄')
        self.worker.submit(self.last_captured)
        return True

    @property
    def select_remain(self):
        """SELECT 상태에서 자동 진행(1장)까지 남은 시간."""
        return max(0.0, COPIES_SELECT_TIMEOUT - (time.time() - self.t_state))

    def select_copies(self, n):
        """★ 키보드 1~6 — 방금 찍은 사진을 n장 인쇄합니다.

        SELECT 상태(촬영 직후)에서만 동작합니다. 그 외 상태에서 숫자 키를
        눌러도 아무 일도 일어나지 않아 오인쇄 위험이 없습니다.
        """
        if self.state != self.SELECT or self.captured is None:
            return False
        self._start_print(max(1, min(int(n), COPIES_MAX)))
        return True

    def _start_print(self, n):
        """선택된 매수로 인쇄를 시작하고 REVIEW 로 넘어갑니다."""
        self.copies = n
        self.worker.submit(self.captured, copies=n)
        self.state = self.REVIEW
        self.t_state = time.time()
        print(f"[Photo] 인화 큐로 전송 ({n}장)")

    def _enter_count(self):
        self.state = self.COUNT
        self.t_state = time.time()
        self.hold_start = None
        self.wait_release = True
        self._next_beep = COUNTDOWN_SEC
        play('beep')
        print(f"[Photo] 카운트다운 시작 ({COUNTDOWN_SEC}초)")

    # ------------------------------------------------------------------ 갱신
    def update(self, action, grab_frame):
        """
        매 프레임 호출.
          action     : 확정된 동작 이름
          grab_frame : 호출하면 '인쇄할 BGR 프레임'을 돌려주는 함수
        """
        now = time.time()
        if action == 'print':
            self.last_print_seen = now

        # ── IDLE : print 제스처 2초 유지 감시 ──────────────────────────
        if self.state == self.IDLE:
            lost = now - self.last_print_seen        # print 를 못 본 시간
            if lost > PRINT_HOLD_GRACE:
                self.hold_start = None               # 유예 시간도 넘김 → 리셋
                self.wait_release = False            # 손을 뗀 것으로 인정
                return
            # 인쇄가 끝나기 전에는 게이지도 차지 않게 합니다.
            # (차오르다 아무 일도 안 일어나면 사용자가 고장으로 오해합니다)
            if self.wait_release or self.blocked_reason is not None:
                self.hold_start = None
                return
            if self.hold_start is None:
                self.hold_start = now
            elif now - self.hold_start >= PRINT_HOLD_SEC:
                self._enter_count()
            return

        # ── COUNT : 3 · 2 · 1 ────────────────────────────────────────────
        if self.state == self.COUNT:
            remain = self.count_remain
            k = int(np.ceil(remain))
            if 0 < k < self._next_beep:
                self._next_beep = k
                play('beep')                          # 매 초 '삐'
            if remain <= 0.0:
                self.captured = grab_frame()          # ★ 이 순간의 화면
                self.last_captured = self.captured    # 'r' 재인쇄용으로 보관
                play('shutter')                       # 찰칵
                # ★ 여기서 바로 인쇄하지 않습니다. 플래시 후 매수 선택(1~6)으로.
                self.state = self.FLASH
                self.t_state = now
                print("[Photo] 촬영 완료 → 매수 선택 대기 (1~6 키)")
            return

        # ── FLASH : 흰 화면이 사라지는 동안 ──────────────────────────────
        if self.state == self.FLASH:
            if now - self.t_state >= FLASH_SEC:
                self.state = self.SELECT
                self.t_state = now
            return

        # ── ★ SELECT : 인쇄 매수 선택 (1~6 키) ──────────────────────────
        #   busy=True 인 상태라 세션 idle 타이머는 여기서 멈춰 있습니다.
        #   무입력이 COPIES_SELECT_TIMEOUT 을 넘으면 1장으로 진행합니다.
        if self.state == self.SELECT:
            if now - self.t_state >= COPIES_SELECT_TIMEOUT:
                print("[Photo] 매수 선택 없음 → 기본 1장으로 진행")
                self._start_print(1)
            return

        # ── REVIEW : 찍은 사진 미리보기 ─────────────────────────────────
        if self.state == self.REVIEW:
            if now - self.t_state >= REVIEW_SEC:
                self.state = self.IDLE
                self.t_state = now
                self.cooldown_until = now + PRINT_COOLDOWN_SEC
                self.captured = None
                self._paper = None
                self._paper_key = None
            return


def _put_center(canvas, text, cy, scale, color, thick,
                font=cv2.FONT_HERSHEY_DUPLEX):
    """가운데 정렬 + 검은 외곽선. 어떤 배경에서도 읽히게 합니다."""
    h, w = canvas.shape[:2]
    (tw, th), _ = cv2.getTextSize(text, font, scale, thick)
    x = (w - tw) // 2
    y = int(min(max(cy + th / 2, th + 4), h - 6))       # 화면 밖으로 안 나가게
    cv2.putText(canvas, text, (x, y), font, scale, (0, 0, 0),
                thick + max(4, thick // 2), cv2.LINE_AA)
    cv2.putText(canvas, text, (x, y), font, scale, color, thick, cv2.LINE_AA)


# 흰 화면(플래시)용 캐시. 2560x1440 배열을 매 프레임 새로 만들면
# 11 MB 짜리 할당이 초당 30번 일어나 캡처 스레드까지 느려집니다.
_WHITE_CACHE = {}


def _white_like(canvas):
    key = canvas.shape
    arr = _WHITE_CACHE.get(key)
    if arr is None:
        arr = np.full(key, 255, np.uint8)
        _WHITE_CACHE[key] = arr
    return arr


def draw_print_ui(canvas, pc):
    """
    촬영 관련 UI. 다른 모든 요소(PiP 포함) 위에 그려야 하므로
    메인 루프의 '가장 마지막'에 호출합니다.
    """
    h, w = canvas.shape[:2]

    # (1) print 유지 게이지 → 통합 HUD 의 상단 링(draw_hold_hud)이 대신합니다.

    # ── (2) 카운트다운 3 · 2 · 1 ────────────────────────────────────────
    #   통합 HUD 와 같은 문법: 반투명 다크 디스크 + 얇은 링 + 담백한 흰 숫자.
    #   (예전의 노란 대형 숫자 + 팝 애니메이션은 산만해서 폐기)
    if pc.state == PrintController.COUNT:
        remain = pc.count_remain
        n = max(1, int(np.ceil(remain)))
        frac = remain - (n - 1)            # 현재 숫자가 남긴 비율 1.0 → 0.0

        # 화면을 차분하게 디밍 (cv2.subtract 는 80ms — convertScaleAbs 는 2ms)
        cv2.convertScaleAbs(canvas, dst=canvas, alpha=0.55)

        R = int(min(h, w) * 0.16)
        cx, cy = w // 2, int(h * 0.44)
        pad = int(R * 0.30)

        _blend_roi(canvas, cx - R - pad, cy - R - pad,
                   cx + R + pad, cy + R + pad,
                   lambda over, ox, oy: cv2.circle(
                       over, (cx - ox, cy - oy), R + pad - 6, HUD_DARK, -1,
                       cv2.LINE_AA), alpha=0.60)

        # 남은 시간 링 (12시 방향에서 시계방향으로 줄어듦, 마지막 1초는 초록)
        ring = ACCENT_PRINT if n == 1 else (240, 240, 240)
        cv2.circle(canvas, (cx, cy), R, HUD_TRACK, 8, cv2.LINE_AA)
        if frac > 0:
            cv2.ellipse(canvas, (cx, cy), (R, R), -90, 0, int(360 * frac),
                        ring, 8, cv2.LINE_AA)

        # 숫자: 흰색, 아주 은은한 스케일 변화만
        digit = str(n)
        d_scale = (R * (1.02 + 0.06 * frac)) / 22.0
        thick = max(2, int(d_scale * 1.2))
        (dw, dh), _ = cv2.getTextSize(digit, cv2.FONT_HERSHEY_DUPLEX,
                                      d_scale, thick)
        cv2.putText(canvas, digit, (cx - dw // 2, cy + dh // 2),
                    cv2.FONT_HERSHEY_DUPLEX, d_scale, HUD_TEXT, thick,
                    cv2.LINE_AA)

        # 작은 캡션 (링 아래)
        cap_txt = "GET READY"
        (cw2, _), _ = cv2.getTextSize(cap_txt, cv2.FONT_HERSHEY_SIMPLEX,
                                      0.85, 2)
        cv2.putText(canvas, cap_txt, ((w - cw2) // 2, cy + R + pad + 44),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.85, (185, 183, 180), 2,
                    cv2.LINE_AA)
        return

    # ── (3) 셔터 플래시 ─────────────────────────────────────────────────
    if pc.state == PrintController.FLASH:
        t = time.time() - pc.t_state
        a = max(0.0, 1.0 - t / FLASH_SEC)
        a = a ** 0.6                                   # 처음엔 확 밝고 빠르게 감쇠
        cv2.addWeighted(_white_like(canvas), a, canvas, 1.0 - a, 0, dst=canvas)
        # 안쪽으로 수축하는 흰 테두리 (셔터막 느낌)
        b = int(min(h, w) * 0.06 * (1.0 - a))
        if b > 0:
            cv2.rectangle(canvas, (b, b), (w - b, h - b), (255, 255, 255), 6)
        return

    # ── (3.5) ★ 인쇄 매수 선택 (1~6 키) ─────────────────────────────────
    #   통합 HUD 와 같은 디자인 언어: 반투명 다크 + 라운드 + 절제된 색.
    #   "PRESS 1-6" 같은 문구 대신 키캡 6개를 직접 보여 줍니다.
    if pc.state == PrintController.SELECT and pc.captured is not None:
        cv2.convertScaleAbs(canvas, dst=canvas, alpha=0.40)

        # 찍힌 사진을 인화지 모양으로 보여 주면서 매수를 고르게 합니다.
        ph = int(h * 0.46)
        pw = int(ph * niimbot_b1pro.LABEL_W_MM / niimbot_b1pro.LABEL_H_MM)
        paper = pc.review_paper(pw, ph)
        x1 = (w - pw) // 2
        y1 = int(h * 0.07)
        y2, x2 = min(h, y1 + ph), x1 + pw
        canvas[y1:y2, x1:x2] = paper[:y2 - y1]
        cv2.rectangle(canvas, (x1, y1), (x2 - 1, y2 - 1),
                      (250, 250, 250), 2, cv2.LINE_AA)

        # 제목 (작고 차분하게)
        title = "HOW MANY COPIES?"
        t_scale = max(0.8, h / 1250.0)
        (tw, th), _ = cv2.getTextSize(title, cv2.FONT_HERSHEY_SIMPLEX,
                                      t_scale, 2)
        ty = y2 + int(h * 0.055)
        cv2.putText(canvas, title, ((w - tw) // 2, ty),
                    cv2.FONT_HERSHEY_SIMPLEX, t_scale, HUD_TEXT, 2,
                    cv2.LINE_AA)

        # 1~6 키캡: 라운드 다크 버튼 + 흰 숫자
        cap = int(h * 0.085)                       # 키캡 한 변
        gap = int(cap * 0.28)
        row_w = COPIES_MAX * cap + (COPIES_MAX - 1) * gap
        rx = (w - row_w) // 2
        ry = ty + int(h * 0.025)
        r = int(cap * 0.22)
        for i in range(COPIES_MAX):
            kx = rx + i * (cap + gap)

            def _key(over, ox, oy, kx=kx):
                _rounded_fill(over, (kx - ox, ry - oy),
                              (kx + cap - ox, ry + cap - oy),
                              (96, 92, 88), r)             # 옅은 테두리
                _rounded_fill(over, (kx + 2 - ox, ry + 2 - oy),
                              (kx + cap - 2 - ox, ry + cap - 2 - oy),
                              HUD_DARK, max(2, r - 2))     # 키캡 몸통
            _blend_roi(canvas, kx, ry, kx + cap, ry + cap, _key, alpha=0.78)

            digit = str(i + 1)
            d_scale = cap / 58.0
            (dw, dh), _ = cv2.getTextSize(digit, cv2.FONT_HERSHEY_DUPLEX,
                                          d_scale, 2)
            cv2.putText(canvas, digit,
                        (kx + (cap - dw) // 2, ry + (cap + dh) // 2),
                        cv2.FONT_HERSHEY_DUPLEX, d_scale, HUD_TEXT, 2,
                        cv2.LINE_AA)

        # 무입력 자동 진행: 문구로 세는 대신 얇은 게이지가 줄어듭니다
        frac = max(0.0, pc.select_remain / COPIES_SELECT_TIMEOUT)
        by = ry + cap + int(h * 0.028)
        _rounded_fill(canvas, (rx, by), (rx + row_w, by + 6), HUD_TRACK, 3)
        fill = int(row_w * frac)
        if fill > 8:
            _rounded_fill(canvas, (rx, by), (rx + fill, by + 6),
                          (170, 168, 165), 3)

        hint = "NO KEY  =  1 COPY"
        (hw2, _), _ = cv2.getTextSize(hint, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        cv2.putText(canvas, hint, ((w - hw2) // 2, by + 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (168, 165, 162), 2,
                    cv2.LINE_AA)
        return

    # ── (4) 촬영 결과 미리보기 ──────────────────────────────────────────
    if pc.state == PrintController.REVIEW and pc.captured is not None:
        t = time.time() - pc.t_state
        cv2.convertScaleAbs(canvas, dst=canvas, alpha=0.45)

        # ★ 인화지 비율을 niimbot_b1pro 프리셋(54x86)에서 그대로 가져옵니다.
        #   하드코딩(50/90)을 없애서 용지를 바꿔도 미리보기가 자동으로 맞습니다.
        #   (썸네일은 캐시됨)
        ph = int(h * 0.62)
        pw = int(ph * niimbot_b1pro.LABEL_W_MM / niimbot_b1pro.LABEL_H_MM)
        paper = pc.review_paper(pw, ph)

        # 아래에서 위로 슬쩍 올라오는 등장 애니메이션 (0.35초)
        rise = int(60 * max(0.0, 1.0 - t / 0.35))
        x1 = (w - pw) // 2
        y1 = int(h * 0.20) + rise
        y2, x2 = min(h, y1 + ph), x1 + pw
        canvas[y1:y2, x1:x2] = paper[:y2 - y1]
        cv2.rectangle(canvas, (x1, y1), (x2 - 1, y2 - 1), (255, 255, 255), 3)

        cap_txt = f"{niimbot_b1pro.LABEL_W_MM} x {niimbot_b1pro.LABEL_H_MM} mm"
        if pc.copies > 1:
            cap_txt = f"PRINTING x{pc.copies}   " + cap_txt
        _put_center(canvas, cap_txt,
                    y2 + 46, 1.1, (200, 200, 200), 2,
                    cv2.FONT_HERSHEY_SIMPLEX)
        return


def draw_printer_status(canvas, worker, pc):
    """
    프린터 상태를 화면 우하단 위쪽에 한 줄로 계속 표시합니다.
    (촬영 상태와 무관하게 항상 보여야 사용자가 인쇄가 되는지 알 수 있습니다)
    """
    h, w = canvas.shape[:2]
    st = worker.status
    if st == 'printing':
        # 인쇄 한 장이 10초 이상이라 진행 막대를 같이 보여 줍니다.
        # (아무 표시도 없으면 멈춘 줄 알고 다시 찍으려 합니다)
        # ★ 여러 장 인쇄 시 예상 시간을 매수만큼 늘려 막대가 일찍 차지 않게.
        done, total = worker.job
        est = PRINT_EST_SEC * max(1, total)
        frac = min(1.0, worker.status_age / est) if est > 0 else 0.0
        bar_w, bx, by = 320, 12, h - 76
        cv2.rectangle(canvas, (bx, by), (bx + bar_w, by + 14), (60, 60, 60), -1)
        cv2.rectangle(canvas, (bx, by), (bx + int(bar_w * frac), by + 14),
                      (0, 200, 255), -1)
        txt = (f"PRINTING {done}/{total}  {worker.message}"
               f"  ({worker.status_age:.0f}/{est:.0f}s)")
        col = (0, 200, 255)
    elif st == 'ok' and worker.status_age < 4.0:
        txt, col = f"PRINTED  #{worker.printed_count}  ({worker.message})", (0, 255, 120)
    elif st == 'error' and worker.status_age < 8.0:
        txt, col = f"PRINT ERROR: {worker.message[:60]}", (0, 80, 255)
    elif pc.cooldown_remain > 0:
        txt, col = f"cooldown {pc.cooldown_remain:.1f}s", (150, 150, 150)
    else:
        return
    cv2.putText(canvas, txt, (12, h - 48),
                cv2.FONT_HERSHEY_SIMPLEX, 0.72, col, 2, cv2.LINE_AA)


# ============================================================================
# 7. 오버레이 렌더링 (메인 스레드에서 호출)
# ============================================================================
HAND_CONNECTIONS = mp.solutions.hands.HAND_CONNECTIONS
COLOR_POINT = (0, 220, 255)
COLOR_LINE  = (0, 160, 90)
COLOR_BOX   = (255, 200, 0)
COLOR_TEXT  = (255, 255, 255)


def draw_hand(canvas, result):
    h, w = canvas.shape[:2]
    if not (result['fresh'] and result['landmarks'] is not None):
        return

    pts = (result['landmarks'] * np.array([w, h])).astype(np.int32)
    for a, b in HAND_CONNECTIONS:
        cv2.line(canvas, tuple(pts[a]), tuple(pts[b]), COLOR_LINE, 1, cv2.LINE_AA)
    for p in pts:
        cv2.circle(canvas, tuple(p), 2, COLOR_POINT, -1, cv2.LINE_AA)

    if result['bbox'] is not None:
        x1, y1, x2, y2 = result['bbox']
        p1 = (max(0, int(x1 * w)), max(0, int(y1 * h)))
        p2 = (min(w - 1, int(x2 * w)), min(h - 1, int(y2 * h)))
        cv2.rectangle(canvas, p1, p2, COLOR_BOX, 1)
        label = f"{result['action'].upper()}  {result['conf']:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        ly = max(th + 6, p1[1] - 4)
        cv2.rectangle(canvas, (p1[0], ly - th - 6),
                      (p1[0] + tw + 8, ly + 3), COLOR_BOX, -1)
        cv2.putText(canvas, label, (p1[0] + 4, ly - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)


def draw_debug(canvas, result, render_fps, recog_fps, capture_fps):
    """FPS/버퍼/확률 디버그 텍스트를 메인 화면 좌상단에 그립니다."""
    if not SHOW_DEBUG:
        return

    cv2.putText(canvas, f"Capture: {capture_fps:5.1f} FPS", (12, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 255, 0) if capture_fps > 20 else (0, 165, 255),
                2, cv2.LINE_AA)
    cv2.putText(canvas, f"Render : {render_fps:5.1f} FPS", (12, 58),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)

    n_real = recog_fps * WINDOW_SEC
    ok = n_real >= SEQ_LENGTH * 0.6
    cv2.putText(canvas,
                f"Recog  : {recog_fps:5.1f} FPS"
                f"  ({n_real:.0f}/{SEQ_LENGTH} real)",
                (12, 86), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 255, 255) if ok else (0, 100, 255), 2, cv2.LINE_AA)

    span = result.get('span', 0.0)
    if span < MIN_WINDOW_SEC:
        buf_txt = f"Buffer : {span:4.2f}s  (wait {MIN_WINDOW_SEC:.2f}s)"
        buf_col = (0, 100, 255)
    elif span < WINDOW_SEC:
        buf_txt = f"Buffer : {span:4.2f}s  (partial, padded)"
        buf_col = (0, 200, 255)
    else:
        buf_txt = f"Buffer : {span:4.2f}s  (full {WINDOW_SEC:.2f}s)"
        buf_col = (0, 255, 0)
    cv2.putText(canvas, buf_txt, (12, 114),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, buf_col, 2, cv2.LINE_AA)

    probs = result['probs']
    if probs is not None:
        for i, (name, p) in enumerate(zip(ACTIONS, probs)):
            cv2.putText(canvas, f"{name}: {p:.2f}", (12, 148 + i * 24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 255, 0) if p > CONF_THRESHOLD else (180, 180, 180),
                        1, cv2.LINE_AA)


def make_pip(frame, result):
    h, w = frame.shape[:2]
    pw, ph = w // PIP_SCALE, h // PIP_SCALE
    pip = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_AREA)
    draw_hand(pip, result)
    cv2.rectangle(pip, (0, 0), (pw - 1, ph - 1), COLOR_BOX, 2)
    return pip


def paste_pip(canvas, pip, menu_open):
    h, w = canvas.shape[:2]
    ph, pw = pip.shape[:2]
    x2 = w - PIP_MARGIN
    if menu_open:
        y2 = int(h * 0.82) - 44
    else:
        y2 = h - PIP_MARGIN
    x1, y1 = x2 - pw, y2 - ph
    canvas[y1:y2, x1:x2] = pip


# ============================================================================
# 8. 메인 — 렌더링 전용 루프
# ============================================================================
def main():
    global _sim_trig

    cv2.setNumThreads(4)
    gpio_init()                          # ★ GPIO 준비
    ensure_sounds()                      # ★ 삐/찰칵 wav 준비

    stop_event   = threading.Event()
    active_event = threading.Event()     # ★ 세션이 열려 있는 동안만 set
    render_slot  = LatestFrame()
    infer_slot   = LatestFrame()
    store        = ResultStore()

    capture = CaptureThread(render_slot, infer_slot, stop_event)
    recog   = RecognitionThread(infer_slot, store, stop_event, active_event)
    printer = NiimbotPrintWorker(threading.Event())      # ★ 인화 워커
    spi_view = Z7SpiViewer()             # ★ Z7-20 SPI 영상 수신 (대기 화면용)

    capture.start()
    capture.ready.wait(timeout=5.0)     # 첫 프레임이 들어올 때까지 대기
    recog.start()
    printer.start()
    spi_view.start()

    # ── ★ B1 Pro 전환: 인화 설정 요약 ──────────────────────────────────
    #   PRINT_EST_SEC 은 화면 진행 막대 전용 예상치입니다.
    #   예전처럼 PRINT_LINES_PER_SEC 으로 계산하지 않습니다. 지금은 전송
    #   속도 무제한(0)이라 계산식이 무의미하고, 실제 소요 시간은
    #   블루투스 전송 + 인쇄 완료 폴링까지 포함되기 때문입니다.
    #   한 장 인쇄 후 터미널의 '[Print] 완료 (X.Xs)' 실측값에 +2초 정도로
    #   맞춰 넣으면 진행 막대가 정확해집니다.
    global PRINT_EST_SEC
    lw, lh = niimbot_b1pro.label_size_px()
    PRINT_EST_SEC = 15.0
    print(f"[Photo] 인화 설정: {niimbot_b1pro.LABEL_W_MM}x{niimbot_b1pro.LABEL_H_MM}mm "
          f"→ {lw}x{lh}px, 연결={niimbot_b1pro.connection_summary()}, "
          f"모델={niimbot_b1pro.PRINTER_MODEL}, 농도={niimbot_b1pro.PRINT_DENSITY}")
    print(f"[Photo] 한 장 예상 약 {PRINT_EST_SEC:.0f}초 (진행 막대 기준값)")

    # ★ 웹캠/대기 화면 모두 모니터 전체를 덮는 전체화면으로
    from cnn_image import setup_fullscreen_window
    setup_fullscreen_window('Gesture')
    # cv2.namedWindow('Gesture', cv2.WINDOW_NORMAL)
    # cv2.resizeWindow('Gesture', CAPTURE_WIDTH, CAPTURE_HEIGHT)

    render_fps = FPSMeter()
    last_render_id = -1
    zoom_ctrl   = ZoomController()
    filter_ctrl = FilterController()
    print_ctrl  = PrintController(printer)              # ★ 촬영 상태 기계

    # ★ 세션 상태
    active     = False       # 인식 세션이 열려 있는가
    trig_prev  = False       # 직전 trig 레벨 (상승 엣지 검출용)
    idle_since = None        # idle 이 시작된 시각

    print("[Main] 렌더링 시작")
    print("[Main]   q / ESC : 종료")
    print("[Main]   t       : trig 시뮬레이션 (보드 없이 세션 열기)")
    print("[Main]   p       : 촬영 강제 시작 (제스처 없이 인화 시험)")
    print("[Main]   r       : ★ 마지막 사진을 다시 인쇄 (재촬영 없이)")
    print(f"[Main]   1~6     : ★ 촬영 직후 같은 사진을 몇 장 뽑을지 선택 "
          f"(무입력 {COPIES_SELECT_TIMEOUT:.0f}초 = 1장)")
    print(f"[Main] 대기 중 — Z7 SPI 영상(CNN 입력) 표시, "
          f"핀{PIN_TRIG} HIGH 에 웹캠으로 전환합니다.")

    def handle_key(key):
        """키 처리. True 를 돌려주면 메인 루프를 종료합니다."""
        global _sim_trig, SHOW_DEBUG
        if key in (ord('q'), 27):
            return True
        if key == ord('d'):
            SHOW_DEBUG = not SHOW_DEBUG
            print(f"[Main] 디버그 표시 {'ON' if SHOW_DEBUG else 'OFF'}")
        elif key == ord('t'):
            _sim_trig = not _sim_trig
            print(f"[GPIO] (sim) trig -> {'HIGH' if _sim_trig else 'LOW'}")
        elif key == ord('p'):
            if active:
                if not print_ctrl.force_start():
                    print(f"[Photo] 지금은 촬영할 수 없습니다 "
                          f"({print_ctrl.blocked_reason})")
            else:
                print("[Photo] 세션이 닫혀 있습니다. 먼저 't' 로 여세요.")
        elif key == ord('r'):
            # ★ 인쇄 설정을 바꿔 가며 시험할 때 재촬영 없이 같은 사진을 다시 인쇄
            print_ctrl.reprint_last()
        elif ord('1') <= key <= ord('6'):
            # ★ 촬영 직후 매수 선택 화면에서만 동작합니다.
            print_ctrl.select_copies(key - ord('0'))
        return False

    try:
        while not stop_event.is_set():
            # ── ★ GPIO 핸드셰이크: 세션 열기 (표시 모드와 무관하게 항상) ──
            trig = gpio_trig()
            if not active and trig and not trig_prev:   # 상승 엣지 → 세션 시작
                active = True
                idle_since = None
                active_event.set()
                spi_view.pause()        # ★ SPI 읽기 중단 → Z7 캡처 동결 해제
                gpio_run(True)          # "동작 시켰다" 응답
                print("[Session] 시작 → 웹캠 화면 전환")
            trig_prev = trig

            # ── ★ STANDBY: Z7-20 SPI 영상(CNN 입력 128x128) 표시 ─────────
            #   ★ 전시용이라 STANDBY/핀 안내 문구는 표시하지 않습니다.
            if not active:
                canvas = make_standby_canvas(spi_view, STANDBY_VIEW_SIZE)
                cv2.imshow('Gesture', canvas)
                if handle_key(cv2.waitKey(30) & 0xFF):
                    break
                continue

            # ── 이하: 세션 활성 = 기존 웹캠 경로 ─────────────────────────
            frame, seq_id = render_slot.peek()   # 논블로킹
            if frame is None:
                cv2.waitKey(1)
                continue

            # ★ 새 프레임이 없으면 아무것도 그리지 않습니다.
            if seq_id == last_render_id:
                if handle_key(cv2.waitKey(3) & 0xFF):
                    break
                continue

            last_render_id = seq_id
            render_fps.tick()

            # 원본은 캡처 스레드가 공유하고 있으므로 반드시 복사본에 그립니다.
            canvas = frame.copy()
            result = store.snapshot()

            action = result['action'] if result['fresh'] else '?'
            hand_center = None
            if result['fresh'] and result['bbox'] is not None:
                bx1, by1, bx2, by2 = result['bbox']
                hand_center = ((bx1 + bx2) * 0.5, (by1 + by2) * 0.5)

            # ── ★ 세션 종료 판정 (idle 지속) ─────────────────────────────
            # ★ 촬영/인화가 진행 중이면 idle 타이머를 돌리지 않습니다.
            #   3초 카운트다운 중에 손을 내리면 idle 로 잡혀서 세션이
            #   끊기는 사고를 막습니다.
            if print_ctrl.busy or printer.busy:
                idle_since = None
            elif filter_ctrl.menu_open:
                # ★ 메뉴는 모달: 메뉴 안의 idle 은 '메뉴 닫기'로만 쓰이고
                #   세션 종료 타이머는 돌지 않습니다. (메뉴가 idle 로 닫힌
                #   직후부터 3초를 새로 셉니다)
                idle_since = None
            elif action == 'idle':
                if idle_since is None:
                    idle_since = time.time()
                elif time.time() - idle_since >= IDLE_TIMEOUT_SEC:
                    active = False
                    idle_since = None
                    active_event.clear()
                    gpio_run(False)             # 하강 엣지 = 완료 통보
                    spi_view.resume()           # ★ 대기 화면(SPI 읽기) 재개
                    zoom_ctrl = ZoomController()      # 다음 세션은 초기 상태로
                    filter_ctrl = FilterController()
                    _keep = print_ctrl.last_captured   # 재인쇄용은 살려 둡니다
                    print_ctrl = PrintController(printer)
                    print_ctrl.last_captured = _keep
                    print("[Session] 종료 (idle 지속) → SPI 대기 화면 전환")
            else:
                idle_since = None

            # 세션이 열려 있을 때만 제스처가 UI 를 조작합니다.
            # ★ 카운트다운/플래시 중에는 줌·필터를 잠급니다.
            #   포즈를 잡는 동안 손 모양이 흔들려 화면이 바뀌면
            #   정작 찍히는 그림이 의도와 달라집니다.
            if active and not print_ctrl.busy:
                filter_ctrl.update(action, hand_center)
                # ★ 메뉴가 열려 있으면 줌은 '?' 를 넣어 홀드를 리셋시킵니다.
                #   (호출 자체를 건너뛰면 hold_start 가 얼어붙었다가 메뉴가
                #    닫힌 순간 이어서 차오르는 사고가 납니다)
                zoom_ctrl.update(
                    '?' if filter_ctrl.menu_open else action, hand_center)

            # 0) 가장 먼저 필터를 원본 영상에 적용합니다.
            if filter_ctrl.active is not None:
                canvas = apply_filter(canvas, filter_ctrl.active)

            # 1) ★ 메인 화면에는 디버그 텍스트만 그립니다.
            draw_debug(canvas, result, render_fps.fps,
                       recog.fps_meter.fps, capture.fps_meter.fps)

            # 2) 그 다음에 확대를 적용합니다.
            if zoom_ctrl.zoomed:
                canvas = apply_zoom(canvas, zoom_ctrl.center)

            # 3) 게이지/상태/메뉴는 확대 이후에 그려서 항상 보이게 합니다.
            #    ★ 통합 HUD: 유지 게이지는 상단 링, 상태는 우상단 배지,
            #      세션 종료(idle)는 하단 게이지로 정리했습니다.
            draw_hold_hud(canvas, zoom_ctrl, filter_ctrl, print_ctrl)
            draw_status_pills(canvas, zoom_ctrl, filter_ctrl)
            draw_filter_ui(canvas, filter_ctrl)
            if not (print_ctrl.busy or printer.busy):
                draw_idle_ui(canvas, idle_since)

            draw_printer_status(canvas, printer, print_ctrl)

            # 4) ★ 손 전용 PiP 미니 화면
            pip = make_pip(frame, result)
            paste_pip(canvas, pip, filter_ctrl.menu_open)

            # 5) ★ 촬영 상태 기계 갱신.
            #    화면 합성이 끝난 '뒤'에 호출해야 CAPTURE_WITH_UI=True 일 때
            #    게이지/PiP 까지 포함된 최종 화면이 그대로 캡처됩니다.
            #    (기본값 False 면 UI 없는 깨끗한 그림이 인쇄됩니다)
            if active:
                # ★ 메뉴가 열려 있는 동안은 print 홀드도 쌓이지 않게 합니다.
                print_ctrl.update(
                    '?' if filter_ctrl.menu_open else action,
                    lambda: (canvas.copy() if CAPTURE_WITH_UI
                             else compose_photo(frame, zoom_ctrl, filter_ctrl)))

            # 6) ★★ 촬영 UI 는 반드시 맨 마지막 ★★
            #    카운트다운 숫자와 플래시는 PiP 를 포함한 모든 것 위에 와야 합니다.
            draw_print_ui(canvas, print_ctrl)

            cv2.imshow('Gesture', canvas)
            if handle_key(cv2.waitKey(1) & 0xFF):
                break

    except KeyboardInterrupt:
        pass
    finally:
        # ── 안전한 종료 절차 ──
        stop_event.set()
        active_event.clear()
        render_slot.close()
        infer_slot.close()
        recog.join(timeout=2.0)
        capture.join(timeout=2.0)
        spi_view.stop()                 # ★ SPI 수신 스레드 정리
        spi_view.join(timeout=2.0)
        # 인쇄 중이면 잠깐 기다려 줍니다 (중간에 끊으면 용지가 낭비됩니다)
        if printer.busy:
            print("[Main] 인쇄가 끝나기를 기다리는 중...")
            for _ in range(60):
                if not printer.busy:
                    break
                time.sleep(0.5)
        printer.close()
        printer.join(timeout=3.0)
        gpio_close()                    # ★ run 을 반드시 LOW 로 내리고 정리
        cv2.destroyAllWindows()
        print("[Main] 정상 종료")


if __name__ == '__main__':
    main()
