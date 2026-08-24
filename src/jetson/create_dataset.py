import cv2
import mediapipe as mp
import numpy as np
import time, os

os.environ["DISPLAY"] = ":1"
os.environ["XAUTHORITY"] = "/home/durimango/.Xauthority"

# ============================================================================
# ★ 촬영할 때마다 이 번호만 바꿀 것 (1~6)
#    번호가 파일명에 들어가야 학습 때 사람 단위로 나눌 수 있다.
#    이게 없으면 6명 데이터가 전부 한 사람으로 뭉쳐서 들어간다.
# ============================================================================
PERSON = 6

# ★ 카메라가 안 열리면 python check_camera.py 로 쓸 수 있는 인덱스를 먼저 확인할 것
CAM_INDEX = 0

actions = ['idle', 'print', 'zoom', 'select']
seq_length = 30
secs_for_action = 30

# ============================================================================
# 해상도
# ============================================================================
# ★ 수집할 때는 캡처 해상도를 높일 이유가 전혀 없다.
#   MediaPipe 는 아래 infer_size(480x270)만 보고, 저장되는 것도 랜드마크뿐이다.
#   2560x1440 으로 찍으면 MJPEG 디코딩 + 축소 비용만 4배가 되어 fps 가 떨어지고,
#   fps 가 떨어지면 30프레임 윈도우가 가리키는 실제 시간이 길어져서
#   학습 데이터의 성격 자체가 바뀐다.
#   중요한 것은 '화면비' 뿐이다. 16:9 이기만 하면 된다.
capture_width, capture_height = 1280, 720      # ★ 2560x1440 -> 1280x720

# 화면이 너무 느리면 960x540 까지 낮춰도 무방하다.
# capture_width, capture_height = 960, 540

infer_size = (480, 270)
# ★ 이 infer_size 는 hand_feature.py 의 COLLECT_SIZE 와 같아야 하고,
#   6명 전원을 같은 값으로 촬영해야 한다.

# 미리보기 창 크기. 원본 크기로 imshow 하면 그것만으로 fps 가 반토막 난다.
preview_size = (960, 540)

# MediaPipe hands model
mp_hands = mp.solutions.hands
mp_drawing = mp.solutions.drawing_utils
hands = mp_hands.Hands(
    max_num_hands=1,
    # ★ 0 = Lite 모델. gesture_pip.py 가 0 을 쓰므로 수집도 0 이어야 한다.
    #   기본값 1 은 느릴 뿐 아니라 랜드마크가 미세하게 달라서,
    #   학습 데이터와 추론 입력의 성격이 어긋난다.
    model_complexity=0,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5)

cap = cv2.VideoCapture(CAM_INDEX)

# ★ 카메라가 안 열렸는데 조용히 넘어가면, 아래 while 문이 통째로 건너뛰어져서
#   "결과 표가 비어 있는" 상태로 정상 종료된 것처럼 보인다. 여기서 바로 멈춘다.
if not cap.isOpened():
    raise SystemExit(
        f'\n[중단] 카메라를 열 수 없습니다 (CAM_INDEX={CAM_INDEX}).\n'
        '  1) 다른 프로그램이 카메라를 쓰고 있지 않은지 확인하세요.\n'
        '     (gesture_pip.py 가 다른 터미널에서 돌고 있으면 반드시 종료)\n'
        '  2) python check_camera.py 로 쓸 수 있는 인덱스를 확인하세요.\n'
        '  3) 그래도 안 되면 ls -l /dev/video* 로 장치가 있는지 보세요.\n')

cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, capture_width)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, capture_height)
cap.set(cv2.CAP_PROP_FPS, 30)
try:
    # 드라이버 내부 버퍼를 1로 줄여 오래된 프레임이 반환되는 것을 막는다
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
except Exception:
    pass

actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
code = int(cap.get(cv2.CAP_PROP_FOURCC))
fourcc = ''.join(chr((code >> (8 * k)) & 0xFF) for k in range(4))
print(f'요청 해상도: {capture_width} x {capture_height}')
print(f'실제 해상도: {actual_w} x {actual_h}  FOURCC={fourcc}')

# 첫 프레임을 실제로 읽어본다. 열리기만 하고 못 읽는 경우가 있다.
ret, test_img = cap.read()
if not ret or test_img is None:
    cap.release()
    raise SystemExit('\n[중단] 카메라는 열렸지만 프레임을 읽지 못했습니다.\n'
                     '  python check_camera.py 로 지원 해상도를 확인하세요.\n')

if (actual_w, actual_h) != (capture_width, capture_height):
    print(f'\n[경고] 요청한 해상도가 적용되지 않았습니다.')
    print(f'       카메라가 {actual_w}x{actual_h} 로 동작합니다.')
    print(f'       화면비가 {actual_w/actual_h:.3f} 이므로, hand_feature.py 의')
    print(f'       COLLECT_SIZE 도 같은 화면비로 맞춰야 합니다.')
    print(f'       (v4l2-ctl -d /dev/video{CAM_INDEX} --list-formats-ext 로 지원 목록 확인)\n')

if fourcc.strip() != 'MJPG':
    print(f'[경고] FOURCC 가 MJPG 가 아닙니다({fourcc}). 고해상도에서 fps 가')
    print(f'       크게 떨어질 수 있습니다.\n')

print(f'촬영 대상: P{PERSON:02d}')

created_time = int(time.time())
os.makedirs('dataset', exist_ok=True)

summary = []

while cap.isOpened():
    for idx, action in enumerate(actions):
        data = []
        stamps = []
        seg_ids = []          # ★ 손이 끊긴 구간을 구분하는 번호
        seg = 0
        prev_detected = False

        # 안내 화면도 미리보기 크기로만 그린다
        ret, img = cap.read()
        wait_view = cv2.flip(cv2.resize(img, preview_size), 1)
        cv2.putText(wait_view, f'P{PERSON:02d} - Waiting for {action.upper()}...',
                    org=(10, 30), fontFace=cv2.FONT_HERSHEY_SIMPLEX,
                    fontScale=0.8, color=(255, 255, 255), thickness=2)
        cv2.imshow('img', wait_view)
        cv2.waitKey(5000)

        start_time = time.time()
        frame_count = 0

        while time.time() - start_time < secs_for_action:
            ret, img = cap.read()
            if not ret:
                continue
            frame_count += 1

            # ★ 축소를 먼저 하고 그 다음에 뒤집는다.
            #   원본(예: 1280x720)을 통째로 뒤집으면 매 프레임 92만 픽셀을
            #   복사하지만, 480x270 을 뒤집는 것은 13만 픽셀이라 7배 싸다.
            #   가로 뒤집기와 축소는 순서를 바꿔도 결과가 같다.
            img_small = cv2.resize(img, infer_size, interpolation=cv2.INTER_AREA)
            img_small = cv2.flip(img_small, 1)

            img_rgb = cv2.cvtColor(img_small, cv2.COLOR_BGR2RGB)
            img_rgb.flags.writeable = False       # MediaPipe 내부 복사 회피
            result = hands.process(img_rgb)

            if result.multi_hand_landmarks is not None:
                # 손이 다시 잡히면 새 구간 번호를 부여한다.
                if not prev_detected:
                    seg += 1
                prev_detected = True

                for res in result.multi_hand_landmarks:
                    joint = np.zeros((21, 4))
                    for j, lm in enumerate(res.landmark):
                        joint[j] = [lm.x, lm.y, lm.z, lm.visibility]

                    # Compute angles between joints
                    v1 = joint[[0,1,2,3,0,5,6,7,0,9,10,11,0,13,14,15,0,17,18,19], :3]
                    v2 = joint[[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20], :3]
                    v = v2 - v1  # [20, 3]
                    v = v / np.linalg.norm(v, axis=1)[:, np.newaxis]

                    angle = np.arccos(np.einsum('nt,nt->n',
                        v[[0,1,2,4,5,6,8,9,10,12,13,14,16,17,18],:],
                        v[[1,2,3,5,6,7,9,10,11,13,14,15,17,18,19],:]))  # [15,]

                    angle = np.degrees(angle)

                    angle_label = np.array([angle], dtype=np.float32)
                    angle_label = np.append(angle_label, idx)

                    d = np.concatenate([joint.flatten(), angle_label])

                    data.append(d)
                    stamps.append(time.time())
                    seg_ids.append(seg)

                    # ★ 원본이 아니라 작은 이미지에 그린다
                    mp_drawing.draw_landmarks(img_small, res,
                                              mp_hands.HAND_CONNECTIONS)
            else:
                prev_detected = False

            # ★ 미리보기는 작은 이미지를 확대해서 보여준다.
            #   원본 크기로 imshow 하면 그것만으로 fps 가 크게 떨어진다.
            view = cv2.resize(img_small, preview_size,
                              interpolation=cv2.INTER_NEAREST)
            elapsed = time.time() - start_time
            live_fps = frame_count / elapsed if elapsed > 0 else 0
            cv2.putText(view,
                        f'P{PERSON:02d} {action.upper()}  '
                        f'{elapsed:4.1f}/{secs_for_action}s  '
                        f'{live_fps:4.1f}fps  det={len(data)}',
                        (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 255, 0) if live_fps >= 12 else (0, 165, 255),
                        2, cv2.LINE_AA)
            cv2.imshow('img', view)
            if cv2.waitKey(1) == ord('q'):
                break

        data = np.array(data)
        stamps = np.array(stamps)
        seg_ids = np.array(seg_ids)
        print(action, data.shape)

        tag = f'{action}_p{PERSON:02d}_{created_time}'
        # np.save(os.path.join('dataset', f'raw_{tag}'), data)
        # np.save(os.path.join('dataset', f'time_{tag}'), stamps)

        # ── 시퀀스 생성 ──────────────────────────────────────────────────
        # ★ 손이 끊긴 구간을 넘나드는 윈도우는 만들지 않는다.
        #   예전 코드는 검출된 프레임만 이어 붙여 잘랐기 때문에, 중간에 손을
        #   놓치면 시간적으로 뚝 떨어진 두 조각이 한 시퀀스가 되어 버렸다.
        #   추론 때는 그런 입력이 들어오지 않으므로 학습에 잡음만 된다.
        # ★ range 끝에 +1 을 했다. 예전 코드는 마지막 윈도우 하나를 빠뜨렸다.
        full_seq_data = []
        for s in np.unique(seg_ids):
            block = data[seg_ids == s]
            for i in range(len(block) - seq_length + 1):
                full_seq_data.append(block[i:i + seq_length])

        full_seq_data = np.array(full_seq_data)
        print(action, full_seq_data.shape)
        np.save(os.path.join('dataset', f'seq_{tag}'), full_seq_data)

        # ── 촬영 품질 리포트용 집계 ──────────────────────────────────────
        fps = len(data) / secs_for_action
        n_seg = len(np.unique(seg_ids)) if len(seg_ids) else 0
        summary.append((action, len(data), fps, n_seg, len(full_seq_data)))
    break

cap.release()
cv2.destroyAllWindows()

print(f'\n===== P{PERSON:02d} 촬영 결과 =====')
print(f'{"동작":>10s}{"검출프레임":>12s}{"실측fps":>10s}{"끊긴횟수":>10s}{"시퀀스":>10s}')
for action, n, fps, n_seg, n_seq in summary:
    print(f'{action:>10s}{n:>12d}{fps:>10.1f}{n_seg:>10d}{n_seq:>10d}')

if summary:
    fps_all = [s[2] for s in summary]
    print(f'\n평균 실측 fps: {sum(fps_all) / len(fps_all):.1f}')
    print('이 값이 학습/추론 코드의 fps 설정과 같아야 한다.')
    print('  - train_6people.ipynb 의 CAPTURE_FPS')
    print('  - gesture_pip.py 의 TRAIN_FPS')
    if min(fps_all) < 10:
        print('\n[주의] 실측 fps가 10 미만인 동작이 있습니다.')
        print('       손 검출이 자주 실패했다는 뜻이라 재촬영을 권합니다.')
    if max(s[3] for s in summary) > 8:
        print('\n[주의] 손이 끊긴 횟수가 많습니다. 손이 화면 밖으로 나가지 않도록')
        print('       거리를 조금 벌리고 다시 촬영해 보세요.')

# import cv2
# import mediapipe as mp
# import numpy as np
# import time, os

# os.environ["DISPLAY"] = ":1"
# os.environ["XAUTHORITY"] = "/home/durimango/.Xauthority"

# actions = ['idle', 'print', 'zoom', 'select']
# seq_length = 30
# secs_for_action = 30

# # --- 지금 쓰는 카메라 (1280x960, 4:3) ---
# capture_width, capture_height = 1280, 960
# infer_size = (480, 360)

# # # --- 최종 카메라 도착 후 (2560x1440, 16:9). 위 두 줄을 주석 처리하고 사용 ---
# # capture_width, capture_height = 2560, 1440
# # infer_size = (480, 270)

# # MediaPipe hands model
# mp_hands = mp.solutions.hands
# mp_drawing = mp.solutions.drawing_utils
# hands = mp_hands.Hands(
#     max_num_hands=1,
#     min_detection_confidence=0.5,
#     min_tracking_confidence=0.5)

# cap = cv2.VideoCapture(0)

# cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
# cap.set(cv2.CAP_PROP_FRAME_WIDTH, capture_width)
# cap.set(cv2.CAP_PROP_FRAME_HEIGHT, capture_height)
# print('실제 해상도:', int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
#       'x', int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

# created_time = int(time.time())
# os.makedirs('dataset', exist_ok=True)

# while cap.isOpened():
#     for idx, action in enumerate(actions):
#         data = []

#         ret, img = cap.read()
#         img = cv2.flip(img, 1)


#         cv2.putText(img, f'Waiting for collecting {action.upper()} action...', org=(10, 30), fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=1, color=(255, 255, 255), thickness=2)
#         cv2.imshow('img', img)
#         cv2.waitKey(5000)

#         start_time = time.time()

#         while time.time() - start_time < secs_for_action:
#             ret, img = cap.read()
#             img = cv2.flip(img, 1)

#             img_small = cv2.resize(img, infer_size, interpolation=cv2.INTER_AREA)
#             img_rgb = cv2.cvtColor(img_small, cv2.COLOR_BGR2RGB)
#             result = hands.process(img_rgb)

#             # img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
#             # result = hands.process(img)
#             # img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

#             if result.multi_hand_landmarks is not None:
#                 for res in result.multi_hand_landmarks:
#                     joint = np.zeros((21, 4))
#                     for j, lm in enumerate(res.landmark):
#                         joint[j] = [lm.x, lm.y, lm.z, lm.visibility]

#                     # Compute angles between joints
#                     v1 = joint[[0,1,2,3,0,5,6,7,0,9,10,11,0,13,14,15,0,17,18,19], :3] # Parent joint
#                     v2 = joint[[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20], :3] # Child joint
#                     v = v2 - v1 # [20, 3]
#                     # Normalize v
#                     v = v / np.linalg.norm(v, axis=1)[:, np.newaxis]

#                     # Get angle using arcos of dot product
#                     angle = np.arccos(np.einsum('nt,nt->n',
#                         v[[0,1,2,4,5,6,8,9,10,12,13,14,16,17,18],:], 
#                         v[[1,2,3,5,6,7,9,10,11,13,14,15,17,18,19],:])) # [15,]

#                     angle = np.degrees(angle) # Convert radian to degree

#                     angle_label = np.array([angle], dtype=np.float32)
#                     angle_label = np.append(angle_label, idx)

#                     d = np.concatenate([joint.flatten(), angle_label])

#                     data.append(d)

#                     mp_drawing.draw_landmarks(img, res, mp_hands.HAND_CONNECTIONS)

#             cv2.imshow('img', img)
#             if cv2.waitKey(1) == ord('q'):
#                 break

#         data = np.array(data)
#         print(action, data.shape)
#         np.save(os.path.join('dataset', f'raw_{action}_{created_time}'), data)

#         # Create sequence data
#         full_seq_data = []
#         for seq in range(len(data) - seq_length):
#             full_seq_data.append(data[seq:seq + seq_length])

#         full_seq_data = np.array(full_seq_data)
#         print(action, full_seq_data.shape)
#         np.save(os.path.join('dataset', f'seq_{action}_{created_time}'), full_seq_data)
#     break

# cap.release()
# cv2.destroyAllWindows()
