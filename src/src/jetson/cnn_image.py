# ============================================================================
# cnn_image.py — Z7-20 → Jetson SPI 영상 수신 (대기 화면용)
#
#   Z7-20 의 gray_128x128 버퍼(CNN 입력, 16384바이트)를 SPI 로 읽어 옵니다.
#   프로토콜: 커맨드 없음. "CS 잡고 16384바이트 클럭킹" = 프레임 1장.
#
#   ★★★ 전송 길이는 절대 16384를 넘으면 안 됩니다 ★★★
#     Tegra SPI 드라이버는 16KiB 경계에서 전송을 쪼개고 사이에 CS 를
#     토글합니다 (spidev bufsiz 와 무관). CS 가 풀리면 Z7 카운터가
#     리셋되므로 16384 초과 단일 전송은 성립하지 않습니다 (실측 확인).
#
#   ★ 테일 4바이트는 그래서 프레임 '안'의 마지막 4바이트입니다
#     (이미지 우하단 구석 4픽셀 자리, spi_frame_tx 개정판과 짝):
#       [16380] 0xA5 매직            [16381] probability (CNN 최종 로짓, s8)
#       [16382] {person_cnt[6:0], result}
#       [16383] XOR 체크섬 (0xA5 ^ prob ^ [16382])
#     매직/체크섬이 안 맞으면(구버전 비트스트림 등) 텔레메트리만 무시하고
#     영상은 그대로 표시합니다. 테일이 유효하면 구석 4픽셀은 이웃 픽셀로
#     메워서 표시합니다 (화면상 티가 안 남).
#     ★ v3 프로토콜: [16382] bit0 = result(사람 감지),
#       bit[7:1] = person_cnt (연속 감지 카운트, jetson_controller 진행률)
#       → 대기 화면에 "RECOGNIZING" 게이지로 표시. TRIG_N 회 차면 세션 시작.
#       (이 파일과 비트스트림은 반드시 함께 배포할 것)
#
#   ★ 중요: CS 세션 동안 Z7 쪽 spi_active 가 카메라 캡처를 동결시킵니다.
#     너무 자주 읽으면 CNN 에 새 프레임이 안 들어가 사람 감지가 늦어지므로
#     VIEW_FPS 로 읽기 주기를 제한합니다. (5MHz 기준 읽기 26ms + 휴지 74ms)
#
#   단독 테스트:  python3 cnn_image.py
# ============================================================================
import os
import time
import threading

import numpy as np

try:
    import spidev
except Exception:
    spidev = None

# --- SPI 설정 ---------------------------------------------------------------
SPI_BUS     = 0             # /dev/spidev0.0
SPI_DEV     = 0
SPI_HZ      = 5_000_000     # 실측 안정 클럭. 직결(짧은 선) 배선이면 15MHz 가능
SPI_MODE    = 0             # CPOL=0, CPHA=0 (Z7 spi_frame_tx 와 일치)

FRAME_W     = 128
FRAME_H     = 128
FRAME_BYTES = 16384         # 128*128 = 정확히 16KiB (Tegra DMA 경계와 일치)
                            # ★ 앞뒤로 단 1바이트도 늘리지 말 것 —
                            #   Tegra 드라이버가 16KiB 에서 쪼개며 CS 를 풀어
                            #   Z7 카운터가 리셋됩니다 (파일 상단 주석 참고)

TAIL_MAGIC  = 0xA5          # 테일 첫 바이트. 안 맞으면 구버전 비트스트림
TAIL_BYTES  = 4
TAIL_OFFSET = FRAME_BYTES - TAIL_BYTES      # = 16380 (프레임 안의 마지막 4바이트)
READ_BYTES  = FRAME_BYTES                   # 총 전송 길이는 기존과 동일!

TRIG_N      = 15            # ★ RTL(jetson_controller)의 TRIG_N 과 반드시 일치!
                            #   result=1 이 이 횟수 연속되면 세션 시작 (~1초).
                            #   게이지 만땅 기준값으로만 쓰임.

VIEW_FPS    = 10            # 초당 읽을 프레임 수 (CS 점유율 = 읽기시간 x FPS)
STALL_SEC   = 2.0           # 이 시간 이상 새 프레임이 없으면 화면에 경고 표시

# ★ 대기 화면은 웹캠 화면과 같은 16:9 비율로 만듭니다.
#   (WINDOW_NORMAL 창에 그대로 꽉 차게 들어감)
#   왼쪽: 영상(세로 꽉 채운 정사각), 오른쪽: SCORE/PERSON 패널
CANVAS_AR   = 16 / 9


def _check_bufsiz():
    """spidev 커널 모듈의 버퍼 상한 확인 (xfer3 라도 bufsiz 는 넘을 수 없음)."""
    try:
        with open('/sys/module/spidev/parameters/bufsiz') as f:
            bufsiz = int(f.read().strip())
        if bufsiz < READ_BYTES:
            return (f"spidev bufsiz={bufsiz} < {READ_BYTES}. "
                    f"/boot/extlinux/extlinux.conf 의 APPEND 에 "
                    f"spidev.bufsiz=65536 추가 후 재부팅 필요")
    except Exception:
        pass                # 파일이 없으면 그냥 진행 (읽기 실패 시 에러로 잡힘)
    return None


class Z7SpiViewer(threading.Thread):
    """
    Z7-20 SPI 프레임 수신 스레드.
      - latest()  : (128x128 uint8 gray 프레임, 수신 시각) 반환
      - pause()   : 읽기 중단 (세션 활성 = 웹캠 모드일 때 호출)
      - resume()  : 읽기 재개 (세션 종료 = 대기 모드 복귀 시 호출)
      - stop()    : 스레드 종료
    """

    def __init__(self):
        super().__init__(daemon=True, name='Z7SpiViewer')
        self._lock    = threading.Lock()
        self._frame   = None            # 최신 프레임 (128x128 uint8)
        self._stamp   = 0.0             # 최신 프레임 수신 시각
        self._prob    = None            # ★ CNN 최종 로짓 (s8, -128..127)
        self._person  = None            # ★ result (사람 감지 1비트)
        self._pcnt    = 0               # ★ 연속 감지 카운트 (0..TRIG_N)
        self._tail_ok = False           # ★ 테일 매직+체크섬 검증 통과 여부
        self._run_ev  = threading.Event()   # set = 읽기 허용
        self._stop_ev = threading.Event()
        self._spi     = None
        self.error    = None            # 사람이 읽을 수 있는 상태/에러 문자열
        self._run_ev.set()              # 시작은 '읽기 허용' 상태

    # ── 외부 인터페이스 ────────────────────────────────────────────────
    def latest(self):
        with self._lock:
            return self._frame, self._stamp

    def telemetry(self):
        """★ (probability s8, person bool, person_cnt, tail_ok).
        테일이 무효면 (None, None, 0, False) — 구버전 비트스트림/수신 전."""
        with self._lock:
            if not self._tail_ok:
                return None, None, 0, False
            return self._prob, self._person, self._pcnt, True

    def pause(self):
        self._run_ev.clear()

    def resume(self):
        self._run_ev.set()

    def stop(self):
        self._stop_ev.set()
        self._run_ev.set()              # pause 대기에서 깨워서 종료시킴

    def status_text(self):
        if spidev is None:
            return "spidev module not found (pip install spidev)"
        if self.error:
            return self.error
        if self._frame is None:
            return "waiting first SPI frame..."
        return "ok"

    # ── 내부 구현 ──────────────────────────────────────────────────────
    def _open(self):
        msg = _check_bufsiz()
        if msg:
            self.error = msg
            print(f"[SPI] 경고: {msg}")
        spi = spidev.SpiDev()
        spi.open(SPI_BUS, SPI_DEV)
        spi.mode = SPI_MODE
        spi.max_speed_hz = SPI_HZ
        self._spi = spi
        self.error = None
        print(f"[SPI] 열림: /dev/spidev{SPI_BUS}.{SPI_DEV} "
              f"mode={SPI_MODE} {SPI_HZ / 1e6:.0f}MHz, "
              f"프레임 {FRAME_W}x{FRAME_H} ({FRAME_BYTES}B), "
              f"읽기 {VIEW_FPS}fps")

    def _read_frame(self):
        # 커맨드/헤더 없이 더미 0x00 을 정확히 16384 바이트 클럭킹 → 프레임 1장
        # (마지막 4바이트가 테일. 절대 16384 를 넘겨 읽지 말 것 — Tegra 분할!)
        raw = bytearray(self._spi.xfer3([0x00] * READ_BYTES))
        frame = np.frombuffer(raw, dtype=np.uint8) \
                  .reshape(FRAME_H, FRAME_W)

        # ── 테일 파싱/검증 (프레임 안의 마지막 4바이트) ──
        magic, prob_u8, cnn_b, chk = raw[TAIL_OFFSET:FRAME_BYTES]
        tail_ok = (magic == TAIL_MAGIC and
                   chk == (magic ^ prob_u8 ^ cnn_b) & 0xFF)
        prob = prob_u8 - 256 if prob_u8 > 127 else prob_u8    # u8 → s8

        # 테일이 유효하면 그 자리 4픽셀(우하단 구석)은 이웃 픽셀로 메워서
        # 화면에 잡티가 안 보이게 합니다. (원본 데이터가 아니라 표시용 보정)
        if tail_ok:
            frame = frame.copy()
            frame[-1, -TAIL_BYTES:] = frame[-1, -TAIL_BYTES - 1]
        person = bool(cnn_b & 0x01)          # bit0     = result
        pcnt   = (cnn_b >> 1) & 0x7F         # bit[7:1] = person_cnt
        return frame, prob, person, pcnt, tail_ok

    def run(self):
        if spidev is None:
            print("[SPI] spidev 모듈이 없어 대기 화면은 안내 문구만 표시합니다.")
            return

        period = 1.0 / VIEW_FPS

        while not self._stop_ev.is_set():
            # pause 상태면 여기서 대기 (0.2초마다 종료 여부 확인)
            if not self._run_ev.wait(timeout=0.2):
                continue
            if self._stop_ev.is_set():
                break

            # 디바이스가 안 열려 있으면 열기 시도 (실패 시 3초 뒤 재시도)
            if self._spi is None:
                try:
                    self._open()
                except Exception as e:
                    self.error = f"SPI open failed: {e}"
                    time.sleep(3.0)
                    continue

            t0 = time.time()
            try:
                frame, prob, person, pcnt, tail_ok = self._read_frame()
            except Exception as e:
                self.error = f"SPI read failed: {e}"
                # 디바이스를 닫고 다음 루프에서 재오픈
                try:
                    self._spi.close()
                except Exception:
                    pass
                self._spi = None
                time.sleep(1.0)
                continue

            with self._lock:
                self._frame   = frame
                self._stamp   = time.time()
                self._prob    = prob
                self._person  = person
                self._pcnt    = pcnt
                self._tail_ok = tail_ok
            self.error = None

            # ★ 읽기 주기 페이싱: CS 를 놓고 있는 동안 Z7 캡처가 재개되어
            #   CNN 에 새 프레임이 들어갑니다. 이 sleep 이 없으면 CNN 이 굶습니다.
            elapsed = time.time() - t0
            if elapsed < period:
                time.sleep(period - elapsed)

        if self._spi is not None:
            try:
                self._spi.close()
            except Exception:
                pass
        print("[SPI] 수신 스레드 종료")


def screen_size():
    """xrandr 로 현재 모니터 해상도를 알아냅니다. 실패 시 (1920, 1080).
    (호출하는 앱이 DISPLAY 를 설정해 두므로 SSH 셸에서도 동작합니다)"""
    try:
        import re
        import subprocess
        out = subprocess.check_output(
            ['xrandr'], stderr=subprocess.DEVNULL, timeout=3).decode()
        for line in out.splitlines():
            if '*' in line:                 # 현재 활성 모드 줄
                mm = re.search(r'(\d+)x(\d+)', line)
                if mm:
                    return int(mm.group(1)), int(mm.group(2))
    except Exception:
        pass
    return 1920, 1080


def setup_fullscreen_window(win_name):
    """★ 창을 모니터 전체를 덮게 만듭니다. (메인 앱/단독 실행 공용)

    GTK 백엔드(Jetson) 주의: 창이 '실체화'되기 전에 FULLSCREEN 속성을
    주면 조용히 무시됩니다 → 더미 imshow 로 먼저 띄운 뒤 적용하고,
    창 매니저가 힌트를 거부하는 경우를 대비해 화면 해상도로 강제
    리사이즈까지 함께 겁니다. 반환값: (화면폭, 화면높이)
    """
    import cv2
    w, h = screen_size()
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
    cv2.imshow(win_name, np.zeros((360, 640, 3), dtype=np.uint8))  # 실체화
    cv2.waitKey(50)
    cv2.setWindowProperty(win_name, cv2.WND_PROP_FULLSCREEN,
                          cv2.WINDOW_FULLSCREEN)
    cv2.moveWindow(win_name, 0, 0)
    cv2.resizeWindow(win_name, w, h)
    cv2.waitKey(50)
    return w, h


def reapply_fullscreen(win_name, w, h):
    """전체화면 재적용 (첫 적용을 창 매니저가 무시했을 때 대비)."""
    import cv2
    cv2.setWindowProperty(win_name, cv2.WND_PROP_FULLSCREEN,
                          cv2.WINDOW_FULLSCREEN)
    cv2.moveWindow(win_name, 0, 0)
    cv2.resizeWindow(win_name, w, h)


def _draw_telemetry_panel(canvas, viewer, x0, pw, h):
    """★ 오른쪽 패널 (미니멀): SCORE 숫자 + 슬림 게이지 + PERSON 배지만.
    done/busy 는 테일에 실려 오지만 UI 에서는 쓰지 않습니다."""
    import cv2

    canvas[:, x0:x0 + pw] = (18, 16, 14)        # 은은한 다크 배경

    prob, person, pcnt, tail_ok = viewer.telemetry()
    cx = x0 + pw // 2                           # 패널 가운데 정렬 기준

    def center_text(text, y, font, scale, color, thick):
        (tw, _), _ = cv2.getTextSize(text, font, scale, thick)
        cv2.putText(canvas, text, (cx - tw // 2, y), font, scale,
                    color, thick, cv2.LINE_AA)

    if not tail_ok:
        center_text("NO TAIL DATA", int(h * 0.46),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 80, 255), 2)
        center_text("(old bitstream?)", int(h * 0.50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 80, 255), 2)
        return

    # ── SCORE: 작은 라벨 + 흰 숫자 + 슬림 게이지 ──
    #   result = (score >= sw*16 - 128) 이므로 sw 설정에 따라 문턱이 달라짐
    #   ★ 표시만 퍼센트입니다. 내부 값(prob)은 그대로 s8 로짓(-128..127)이고,
    #     s8 전체 범위를 0~100%로 선형 매핑해 보여줍니다 (-128→0%, +127→100%).
    #     리허설에서 "-128~127이 무슨 뜻이냐"는 질문이 나와 관람객용으로 변경.
    pct = int(round((prob + 128) * 100.0 / 255.0))
    center_text("SCORE", int(h * 0.30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (140, 140, 140), 2)
    center_text(f"{pct}%", int(h * 0.40),
                cv2.FONT_HERSHEY_DUPLEX, 3.0, (240, 240, 240), 4)

    bar_w, bar_h = int(pw * 0.55), 8
    bx, by = cx - bar_w // 2, int(h * 0.445)
    cv2.rectangle(canvas, (bx, by), (bx + bar_w, by + bar_h), (52, 48, 44), -1)
    fill = int(bar_w * pct / 100.0)
    cv2.rectangle(canvas, (bx, by), (bx + fill, by + bar_h), (0, 190, 255), -1)
    zx = bx + bar_w // 2                        # 50% 눈금 (= 원래 score 0 자리)
    cv2.line(canvas, (zx, by - 5), (zx, by + bar_h + 5), (190, 190, 190), 1)

    # ── PERSON 배지: 감지되면 초록으로 채워짐 ──
    bw, bh2 = int(pw * 0.48), int(h * 0.07)
    bx2, by2 = cx - bw // 2, int(h * 0.56)
    if person:
        cv2.rectangle(canvas, (bx2, by2), (bx2 + bw, by2 + bh2),
                      (90, 210, 80), -1, cv2.LINE_AA)
        (tw, th), _ = cv2.getTextSize("PERSON", cv2.FONT_HERSHEY_DUPLEX, 1.5, 3)
        cv2.putText(canvas, "PERSON",
                    (cx - tw // 2, by2 + (bh2 + th) // 2),
                    cv2.FONT_HERSHEY_DUPLEX, 1.5, (12, 34, 12), 3, cv2.LINE_AA)
    else:
        cv2.rectangle(canvas, (bx2, by2), (bx2 + bw, by2 + bh2),
                      (66, 62, 58), 2, cv2.LINE_AA)
        (tw, th), _ = cv2.getTextSize("NO PERSON", cv2.FONT_HERSHEY_DUPLEX, 1.2, 2)
        cv2.putText(canvas, "NO PERSON",
                    (cx - tw // 2, by2 + (bh2 + th) // 2),
                    cv2.FONT_HERSHEY_DUPLEX, 1.2, (120, 116, 112), 2, cv2.LINE_AA)

    # ── ★ RECOGNIZING 게이지: 연속 감지가 쌓이는 걸 보여줌 ──
    #   pcnt/TRIG_N 만큼 차오르다 가득 차면 하드웨어가 세션을 엽니다.
    gy = int(h * 0.70)
    gw2, gh2 = int(pw * 0.55), 10
    gx = cx - gw2 // 2
    frac = min(1.0, pcnt / float(TRIG_N))
    recognizing = pcnt > 0
    label_col = (0, 190, 255) if recognizing else (90, 86, 82)
    center_text("RECOGNIZING" if recognizing else "waiting...",
                gy - 18, cv2.FONT_HERSHEY_SIMPLEX, 0.8, label_col, 2)
    cv2.rectangle(canvas, (gx, gy), (gx + gw2, gy + gh2), (52, 48, 44), -1)
    if frac > 0:
        gcol = (90, 210, 80) if frac >= 1.0 else (0, 190, 255)
        cv2.rectangle(canvas, (gx, gy),
                      (gx + int(gw2 * frac), gy + gh2), gcol, -1)
    center_text(f"{pcnt}/{TRIG_N}", gy + 44,
                cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (150, 146, 142) if recognizing else (90, 86, 82), 2)


def make_standby_canvas(viewer, size=1024, footer=""):
    """
    대기 화면 캔버스 생성.  ★ 반환 크기: size x round(size*16/9) — 웹캠
    화면과 같은 16:9 비율이라 WINDOW_NORMAL 창에 왜곡 없이 꽉 찹니다.
      - 왼쪽 : 128x128 gray → 세로를 꽉 채운 size x size 정사각으로 확대
      - 오른쪽: SCORE(로짓) + PERSON(result) 패널
      - 프레임이 없으면: 검은 화면 + 상태 문구
    """
    import cv2                          # 사용처(메인 앱)에는 항상 cv2 가 있음

    frame, stamp = viewer.latest()

    W = int(round(size * CANVAS_AR))    # 16:9 전체 폭 (1024 → 1820)
    canvas = np.zeros((size, W, 3), dtype=np.uint8)
    if frame is None:
        cv2.putText(canvas, "NO SPI FRAME", (size // 2 - 170, size // 2 - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 2, cv2.LINE_AA)
        cv2.putText(canvas, viewer.status_text(), (24, size // 2 + 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2, cv2.LINE_AA)
    else:
        big = cv2.resize(frame, (size, size), interpolation=cv2.INTER_NEAREST)
        canvas[:, :size] = cv2.cvtColor(big, cv2.COLOR_GRAY2BGR)
        if time.time() - stamp > STALL_SEC:
            cv2.putText(canvas, f"SPI STALLED ({time.time() - stamp:.0f}s)",
                        (24, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                        (0, 0, 255), 2, cv2.LINE_AA)

    _draw_telemetry_panel(canvas, viewer, x0=size, pw=W - size, h=size)

    # ★ "CNN INPUT 128x128" 안내 문구는 전시용 화면에서 제거했습니다.
    #   footer 는 단독 테스트(python3 cnn_image.py)에서만 넘어옵니다.
    if footer:
        cv2.putText(canvas, footer, (24, size - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2, cv2.LINE_AA)
    return canvas


# ============================================================================
# 단독 테스트: Z7 배선/RTL 검증 + ★ 재학습 데이터 수집
#   python3 cnn_image.py
#     q/ESC : 종료
#     s     : 현재 프레임을 cnn_dataset/ 에 저장 (파일명에 score/판정 포함)
#     a     : ★ 자동 수집 토글 — PERSON=1 로 판정된 프레임을 전부 저장.
#             사람이 '없는데' 저장되는 파일들이 바로 오탐(hard negative)
#             학습 데이터입니다. 파일명이 _P 인데 사람이 아니면 그게 오탐.
#
#   저장되는 PNG 는 CNN 이 실제로 본 입력(128x128 gray, 동일 전처리)이라
#   재학습 데이터로 그대로 쓸 수 있습니다. (우하단 4픽셀은 테일 자리라
#   이웃값으로 메워져 있음 — 학습에 영향 없는 수준)
# ============================================================================
if __name__ == '__main__':
    os.environ.setdefault("DISPLAY", ":1")
    os.environ.setdefault("XAUTHORITY", "/home/durimango/.Xauthority")
    from datetime import datetime
    import cv2

    SAVE_DIR = 'cnn_dataset'
    os.makedirs(SAVE_DIR, exist_ok=True)

    def save_frame(viewer, tag=''):
        frame, stamp = viewer.latest()
        prob, res, _pcnt, ok = viewer.telemetry()
        if frame is None:
            return None
        ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]
        lab = ('P' if res else 'N') if ok else 'X'
        sc = f"{prob:+04d}" if ok and prob is not None else "none"
        path = os.path.join(SAVE_DIR, f"{ts}_score{sc}_{lab}{tag}.png")
        cv2.imwrite(path, frame)
        return path

    viewer = Z7SpiViewer()
    viewer.start()

    # ★ 웹캠 화면처럼 모니터 전체를 덮는 전체화면으로 시작합니다.
    #   캔버스가 16:9 라 일반적인 모니터에서 왜곡 없이 꽉 찹니다. (f 키 토글)
    #
    # ★★ GTK 백엔드(Jetson) 주의: 창이 화면에 '실체화'되기 전에
    #   setWindowProperty(FULLSCREEN) 을 부르면 조용히 무시됩니다.
    #   → 먼저 imshow+waitKey 로 창을 띄운 '뒤에' 속성을 주고,
    #     초반 몇 프레임 동안 재적용해서 확실히 먹입니다.
    WIN = 'Z7 SPI'
    SCR_W, SCR_H = setup_fullscreen_window(WIN)
    print(f"[Test] 모니터 해상도: {SCR_W}x{SCR_H}")

    def apply_fullscreen():
        reapply_fullscreen(WIN, SCR_W, SCR_H)

    fullscreen = True
    fs_retry = 10          # 초반 N 프레임 동안 전체화면 재적용
    print("[Test] q/ESC 종료 | f 전체화면 토글 | s 프레임 저장 | a 자동수집 토글")

    autosave = False
    last_saved_stamp = 0.0
    try:
        while True:
            foot = "standalone test" + ("   [AUTO-COLLECT ON]" if autosave else "")
            # ★ 캔버스를 모니터 해상도 그대로 만들어 1:1 픽셀로 꽉 채웁니다.
            #   (작게 만들면 창 매니저가 가운데에 원본 크기로 띄워 여백이 생김)
            canvas = make_standby_canvas(viewer, SCR_H, foot)
            cv2.imshow(WIN, canvas)

            # ★ 초반 몇 프레임 동안 전체화면을 재적용
            #   (창 매니저가 첫 적용을 무시하는 경우 대비)
            if fullscreen and fs_retry > 0:
                apply_fullscreen()
                fs_retry -= 1

            # 자동 수집: PERSON=1 인 '새' 프레임마다 저장
            if autosave:
                frame, stamp = viewer.latest()
                prob, res, _pc, ok = viewer.telemetry()
                if ok and res and stamp > last_saved_stamp:
                    p = save_frame(viewer, tag='_auto')
                    if p:
                        last_saved_stamp = stamp
                        print(f"[Data] 자동 저장: {p}")

            key = cv2.waitKey(30) & 0xFF
            if key in (ord('q'), 27):
                break
            elif key == ord('f'):
                fullscreen = not fullscreen
                if fullscreen:
                    apply_fullscreen()
                    fs_retry = 10          # 토글 시에도 재적용
                else:
                    cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN,
                                          cv2.WINDOW_NORMAL)
                    cv2.resizeWindow(WIN, 1280, 720)
            elif key == ord('s'):
                p = save_frame(viewer)
                print(f"[Data] 저장: {p}" if p else "[Data] 프레임 없음")
            elif key == ord('a'):
                autosave = not autosave
                print(f"[Data] 자동 수집 {'ON — PERSON 판정 프레임을 저장합니다' if autosave else 'OFF'}")
    finally:
        viewer.stop()
        viewer.join(timeout=2.0)
        cv2.destroyAllWindows()


# # ============================================================================
# # cnn_image.py — Z7-20 → Jetson SPI 영상 수신 (대기 화면용)
# #
# #   Z7-20 의 gray_128x128 버퍼(CNN 입력, 16384바이트)를 SPI 로 읽어 옵니다.
# #   프로토콜: 커맨드 없음. "CS 잡고 16384바이트 클럭킹" = 프레임 1장.
# #
# #   ★ 중요: CS 세션 동안 Z7 쪽 spi_active 가 카메라 캡처를 동결시킵니다.
# #     너무 자주 읽으면 CNN 에 새 프레임이 안 들어가 사람 감지가 늦어지므로
# #     VIEW_FPS 로 읽기 주기를 제한합니다. (5MHz 기준 읽기 26ms + 휴지 74ms)
# #
# #   단독 테스트:  python3 cnn_image.py
# # ============================================================================
# import os
# import time
# import threading

# import numpy as np

# try:
#     import spidev
# except Exception:
#     spidev = None

# # --- SPI 설정 ---------------------------------------------------------------
# SPI_BUS     = 0             # /dev/spidev0.0
# SPI_DEV     = 0
# SPI_HZ      = 5_000_000     # 실측 안정 클럭. 직결(짧은 선) 배선이면 15MHz 가능
# SPI_MODE    = 0             # CPOL=0, CPHA=0 (Z7 spi_frame_tx 와 일치)

# FRAME_W     = 128
# FRAME_H     = 128
# FRAME_BYTES = 16384         # 128*128 = 정확히 16KiB (Tegra DMA 경계와 일치)
#                             # ★ 앞에 헤더 바이트를 절대 붙이지 말 것 —
#                             #   붙는 순간 16KiB 경계가 프레임 중간으로 들어옴

# VIEW_FPS    = 10            # 초당 읽을 프레임 수 (CS 점유율 = 읽기시간 x FPS)
# STALL_SEC   = 2.0           # 이 시간 이상 새 프레임이 없으면 화면에 경고 표시


# def _check_bufsiz():
#     """spidev 커널 모듈의 버퍼 상한 확인 (xfer3 라도 bufsiz 는 넘을 수 없음)."""
#     try:
#         with open('/sys/module/spidev/parameters/bufsiz') as f:
#             bufsiz = int(f.read().strip())
#         if bufsiz < FRAME_BYTES:
#             return (f"spidev bufsiz={bufsiz} < {FRAME_BYTES}. "
#                     f"/boot/extlinux/extlinux.conf 의 APPEND 에 "
#                     f"spidev.bufsiz=65536 추가 후 재부팅 필요")
#     except Exception:
#         pass                # 파일이 없으면 그냥 진행 (읽기 실패 시 에러로 잡힘)
#     return None


# class Z7SpiViewer(threading.Thread):
#     """
#     Z7-20 SPI 프레임 수신 스레드.
#       - latest()  : (128x128 uint8 gray 프레임, 수신 시각) 반환
#       - pause()   : 읽기 중단 (세션 활성 = 웹캠 모드일 때 호출)
#       - resume()  : 읽기 재개 (세션 종료 = 대기 모드 복귀 시 호출)
#       - stop()    : 스레드 종료
#     """

#     def __init__(self):
#         super().__init__(daemon=True, name='Z7SpiViewer')
#         self._lock    = threading.Lock()
#         self._frame   = None            # 최신 프레임 (128x128 uint8)
#         self._stamp   = 0.0             # 최신 프레임 수신 시각
#         self._run_ev  = threading.Event()   # set = 읽기 허용
#         self._stop_ev = threading.Event()
#         self._spi     = None
#         self.error    = None            # 사람이 읽을 수 있는 상태/에러 문자열
#         self._run_ev.set()              # 시작은 '읽기 허용' 상태

#     # ── 외부 인터페이스 ────────────────────────────────────────────────
#     def latest(self):
#         with self._lock:
#             return self._frame, self._stamp

#     def pause(self):
#         self._run_ev.clear()

#     def resume(self):
#         self._run_ev.set()

#     def stop(self):
#         self._stop_ev.set()
#         self._run_ev.set()              # pause 대기에서 깨워서 종료시킴

#     def status_text(self):
#         if spidev is None:
#             return "spidev module not found (pip install spidev)"
#         if self.error:
#             return self.error
#         if self._frame is None:
#             return "waiting first SPI frame..."
#         return "ok"

#     # ── 내부 구현 ──────────────────────────────────────────────────────
#     def _open(self):
#         msg = _check_bufsiz()
#         if msg:
#             self.error = msg
#             print(f"[SPI] 경고: {msg}")
#         spi = spidev.SpiDev()
#         spi.open(SPI_BUS, SPI_DEV)
#         spi.mode = SPI_MODE
#         spi.max_speed_hz = SPI_HZ
#         self._spi = spi
#         self.error = None
#         print(f"[SPI] 열림: /dev/spidev{SPI_BUS}.{SPI_DEV} "
#               f"mode={SPI_MODE} {SPI_HZ / 1e6:.0f}MHz, "
#               f"프레임 {FRAME_W}x{FRAME_H} ({FRAME_BYTES}B), "
#               f"읽기 {VIEW_FPS}fps")

#     def _read_frame(self):
#         # 커맨드/헤더 없이 더미 0x00 을 16384 바이트 클럭킹 → 프레임 1장
#         raw = self._spi.xfer3([0x00] * FRAME_BYTES)
#         frame = np.frombuffer(bytearray(raw), dtype=np.uint8)
#         return frame.reshape(FRAME_H, FRAME_W)

#     def run(self):
#         if spidev is None:
#             print("[SPI] spidev 모듈이 없어 대기 화면은 안내 문구만 표시합니다.")
#             return

#         period = 1.0 / VIEW_FPS

#         while not self._stop_ev.is_set():
#             # pause 상태면 여기서 대기 (0.2초마다 종료 여부 확인)
#             if not self._run_ev.wait(timeout=0.2):
#                 continue
#             if self._stop_ev.is_set():
#                 break

#             # 디바이스가 안 열려 있으면 열기 시도 (실패 시 3초 뒤 재시도)
#             if self._spi is None:
#                 try:
#                     self._open()
#                 except Exception as e:
#                     self.error = f"SPI open failed: {e}"
#                     time.sleep(3.0)
#                     continue

#             t0 = time.time()
#             try:
#                 frame = self._read_frame()
#             except Exception as e:
#                 self.error = f"SPI read failed: {e}"
#                 # 디바이스를 닫고 다음 루프에서 재오픈
#                 try:
#                     self._spi.close()
#                 except Exception:
#                     pass
#                 self._spi = None
#                 time.sleep(1.0)
#                 continue

#             with self._lock:
#                 self._frame = frame
#                 self._stamp = time.time()
#             self.error = None

#             # ★ 읽기 주기 페이싱: CS 를 놓고 있는 동안 Z7 캡처가 재개되어
#             #   CNN 에 새 프레임이 들어갑니다. 이 sleep 이 없으면 CNN 이 굶습니다.
#             elapsed = time.time() - t0
#             if elapsed < period:
#                 time.sleep(period - elapsed)

#         if self._spi is not None:
#             try:
#                 self._spi.close()
#             except Exception:
#                 pass
#         print("[SPI] 수신 스레드 종료")


# def make_standby_canvas(viewer, size=1024, footer=""):
#     """
#     대기 화면 캔버스 생성.
#       - 프레임이 있으면: 128x128 gray → size x size 로 확대(NEAREST) 표시
#       - 없으면: 검은 화면 + 상태 문구
#     """
#     import cv2                          # 사용처(메인 앱)에는 항상 cv2 가 있음

#     frame, stamp = viewer.latest()

#     if frame is None:
#         canvas = np.zeros((size, size, 3), dtype=np.uint8)
#         cv2.putText(canvas, "NO SPI FRAME", (size // 2 - 170, size // 2 - 20),
#                     cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 2, cv2.LINE_AA)
#         cv2.putText(canvas, viewer.status_text(), (24, size // 2 + 40),
#                     cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2, cv2.LINE_AA)
#     else:
#         big = cv2.resize(frame, (size, size), interpolation=cv2.INTER_NEAREST)
#         canvas = cv2.cvtColor(big, cv2.COLOR_GRAY2BGR)
#         if time.time() - stamp > STALL_SEC:
#             cv2.putText(canvas, f"SPI STALLED ({time.time() - stamp:.0f}s)",
#                         (24, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
#                         (0, 0, 255), 2, cv2.LINE_AA)

#     cv2.putText(canvas, "CNN INPUT 128x128 (Z7-20 / SPI)", (24, size - 56),
#                 cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
#     if footer:
#         cv2.putText(canvas, footer, (24, size - 20),
#                     cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2, cv2.LINE_AA)
#     return canvas


# # ============================================================================
# # 단독 테스트: Z7 배선/RTL 검증용 뷰어
# #   python3 cnn_image.py
# # ============================================================================
# if __name__ == '__main__':
#     os.environ.setdefault("DISPLAY", ":1")
#     os.environ.setdefault("XAUTHORITY", "/home/durimango/.Xauthority")
#     import cv2

#     viewer = Z7SpiViewer()
#     viewer.start()
#     cv2.namedWindow('Z7 SPI', cv2.WINDOW_NORMAL)
#     cv2.resizeWindow('Z7 SPI', 1024, 1024)
#     print("[Test] q / ESC : 종료")
#     try:
#         while True:
#             canvas = make_standby_canvas(viewer, 1024, "standalone test")
#             cv2.imshow('Z7 SPI', canvas)
#             key = cv2.waitKey(30) & 0xFF
#             if key in (ord('q'), 27):
#                 break
#     finally:
#         viewer.stop()
#         viewer.join(timeout=2.0)
#         cv2.destroyAllWindows()