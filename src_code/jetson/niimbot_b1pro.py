"""
niimbot_b1pro.py  (v13)
=======================
Niimbot(B1 Pro / B1 / B21 / B18 ...) 라벨 프린터로 '카메라 화면'을 인화하기 위한 모듈.

이 파일이 하는 일
  1) OpenCV BGR 프레임  ->  54x86 mm 라벨용 1비트 이미지(576 x 1016 px) 변환
  2) 백그라운드 스레드에서 프린터로 전송  (렌더링 루프가 절대 멈추지 않도록)
  3) 카운트다운 '삐' 소리와 셔터 '찰칵' 소리 wav 자동 생성 + 재생

변경 이력
  v2 - niimprint 패키지 없이도 동작 (프로토콜 내장)
       원본: https://github.com/AndBondStyle/niimprint · MIT · kjy00302 / AndBondStyle
     - Thread._stop 이름 충돌 버그 수정 (worker.join() 이 TypeError 로 죽던 문제)
     - PRINTER_PORT='auto' 가 Jetson 온보드 UART(ttyTHS*)를 건너뛰도록
     - `--check` 진단 모드
  v3 - ★ USB 전송 속도 제한. '앞부분만 인쇄되고 나머지 백지' 문제의 해결책.
     - `--ruler` 자 패턴으로 어디서 잘렸는지 측정
  v4 - 진행상황(0xA3) 기반 브레이크 추가 (지원하는 모델에서만, 느리게만 조절)
     - `--ruler <속도>` 로 파일 수정 없이 속도 시험
     - `--solve` 로 두 번의 측정에서 프린터 소비속도와 버퍼 크기를 역산
  v5 - 페이지 분할 시도 (B1 에서는 두 번째 페이지가 무시되어 실패)
  v6 - ★ 진행상황 조회(0xA3)가 인쇄를 죽이던 버그 수정.
       프린터가 오류 응답(type 219)을 주면 _transceive 가 예외를 던지는데,
       그게 print_image 밖으로 나가 인쇄가 2mm(첫 조회 시점)에서 중단됐습니다.
       이제 조회는 절대 예외를 내보내지 않고, 기본값도 꺼 두었습니다.
     - 기본 설정을 '실측으로 검증된 조합'으로: 단일 페이지
     - PROTOCOL_DEBUG 로 명령/응답 전체 로그
  v7 - ★ 블루투스(RFCOMM/SPP) 지원. 공식 앱과 같은 경로입니다.
       PRINTER_CONN='bluetooth' 가 기본값. 주소는 페어링 목록에서 자동 선택
       (B1 은 주소가 2개인데 Serial Port UUID 를 가진 쪽만 인쇄됩니다).
     - LABEL_PRESET 한 줄로 용지 규격 전환
  v8 - 블루투스는 한 줄(=패킷 1개)씩 write. 여러 패킷을 이어 붙이면 RFCOMM
       프레임 경계에서 프린터가 패킷을 못 맞춰 이미지를 버립니다.
     - GET_RFID 로 롤 정보 조회 (total_len/used_len 은 mm 가 아니라 '장수')
  v9 - ★★ 패킷 파서 재동기화 버그 수정 ★★
       예전 _recv 는 버퍼 맨 앞이 패킷 시작이라고 가정하고 4번째 바이트를
       길이로 읽었습니다. 앞에 잡음이 있으면 엉뚱한 길이를 읽고 break 해서
       버퍼가 영영 그 상태로 남고, 그 뒤 모든 응답을 못 봅니다.
       이제 헤더(55 55)를 먼저 찾아 잡음을 버린 뒤 길이를 읽습니다.
     - 이미지 전송 중에도 수신 버퍼를 계속 비웁니다(drain).
  v10 - ★★★ 신형(2024+) 펌웨어 프로토콜 이식 ★★★
       (a) 줄 헤더의 흑화소 카운트(3분할)를 실제 값으로 계산해 전송.
           0 으로 보내면 '찍을 게 없다'로 판단해 발열을 건너뜁니다 → 백지.
       (b) PrintStart 페이로드 7바이트 [totalPages(u16), 0 x4, pageColor(u8)]
       (c) SetDimension 페이로드 6바이트 [rows(u16), cols(u16), copies(u16)]
       (d) 전송 후 진행률(0xA3)을 폴링해 '실제 인쇄 완료'를 확인한 뒤에만
           PrintEnd 전송. 일찍 보내면 인쇄가 11mm 근처에서 끊깁니다.
       참고: https://printers.niim.blue/interfacing/print-tasks/
  v11 - ★★★ B1 Pro (300dpi) 대응 ★★★
       B1(203dpi, 8px/mm, 헤드 384dot) 전제로 박혀 있던 값들을 전부
       해상도에서 유도하도록 바꿨습니다.
       (a) PRINTER_DPI 한 줄로 203 <-> 300 전환. PX_PER_MM 은 여기서 계산.
           300dpi → 11.811 px/mm (정수가 아닙니다. 8 이 아닙니다)
       (b) MODEL_MAX_W_PX 에 'b1pro': 576 추가 (2인치 300dpi 헤드 = 48.8mm)
       (c) 여백 / 캡션 높이 / 캡션 폰트 / 자 눈금 두께를 전부 mm 또는
           헤드 폭 비례로 계산. 예전엔 px 로 박혀 있어서 해상도를 올리면
           여백이 사라지고 글씨가 콩알만 해졌습니다.
       (d) 줄 헤더 흑화소 카운트를 0~255 로 clip. 헤드가 넓어지면
           구간 합이 255 를 넘어 uint8 이 조용히 wrap 될 수 있었습니다.
       (e) _PRESETS 키/값 불일치 수정 ('54x86' 키에 (50,90,90) 이 들어가
           있었고, LABEL_PRESET='50x90' 은 아예 KeyError 였습니다)
       (f) 회전각 오타 수정: 54x86 프리셋의 세 번째 값은 '회전각'입니다.
           86 이 들어가 있어서 사진이 86도 기울어졌습니다 → 90.
       ★ 아직 실측으로 확정하지 못한 값: 헤드 폭 576dot.
         --ruler 를 한 번 찍어 실제 mm 를 재고, 아래 '해상도 검증' 참고.

  v12 - ★★★ B1 Pro 인쇄 시퀀스(v4 태스크) 대응 ★★★
       v11 은 B1 Pro 에서 PrintStart 부터 오류 응답(type 219)을 받고 죽었습니다.
       원인: B1 Pro 는 B1 과 '명령 형식 자체'가 다릅니다.
       (a) PRINT_TASK 도입. 'b1'(2024년 기종) / 'v4'(2025년 기종)
           - PrintStart  : 7바이트 → 9바이트 (speed, flag 추가)
           - SetPageSize : 6바이트 → 13바이트 (절단/분할 필드 7바이트 추가)
           - PageStart(0x03) 를 아예 보내지 않음
           - SetPageSize 뒤에 응답 안 기다리는 PrintStatus
           - PrintEnd 뒤에 응답 안 기다리는 Heartbeat
       (b) 헤드 폭 정정: 576 → 567 dot. (300dpi x 48mm = 566.9)
           유효 인쇄폭은 B1 과 같은 48mm 입니다. 54mm 용지 기준 좌우 3mm 여백.
       (c) 흑화소 카운트 3분할 기준을 '이미지 폭의 1/3' 에서
           '헤드 바이트 수 // 3' 으로 변경. 384dot 에서는 둘이 같았지만
           567dot 에서는 어긋나 세로 줄무늬가 생길 수 있습니다.
       (d) --check 에서 모델 ID(0x40)와 프로토콜 버전(0xa5)을 직접 조회해
           PRINT_TASK / DPI / 헤드 폭 설정이 맞는지 검증합니다.
       (e) type 219 오류 메시지에 오류 코드와 원인 후보를 함께 출력.
       출처: https://printers.niim.blue/interfacing/print-tasks/
             https://printers.niim.blue/interfacing/proto/

명령
  python3 niimbot_photo.py --btscan [초]    ★ 클래식(BR/EDR) 검색 — 안 잡힐 때
  python3 niimbot_photo.py --btpair <주소>  페어링 + trust 자동 실행
  python3 niimbot_photo.py --bt             블루투스 기기 목록 (종이 안 씀)
  python3 niimbot_photo.py --check          연결 진단 (종이 안 씀)
  python3 niimbot_photo.py --ruler [속도]   자 패턴 인쇄 → 길이/해상도 측정
  python3 niimbot_photo.py --solve S1 mm1 S2 mm2   안전 속도 계산
  python3 niimbot_photo.py [사진.jpg]       실제 인화 시험

해상도 규칙
  - 203dpi 모델(B1/B18/B21) : 7.992 px/mm, 헤드 384 dot (48 mm)
  - 300dpi 모델(B1 Pro)     : 11.811 px/mm, 헤드 576 dot (48.8 mm)
  - 이미지의 '가로'가 헤드 폭, '세로'가 용지가 나가는 길이입니다.
  - 54x86 mm @ 300dpi → 638 x 1016 px 를 요청하지만 가로는 헤드 상한
    576 으로 잘립니다. 즉 54mm 용지에 48.8mm 폭으로 인쇄되고 좌우에
    2.6mm 씩 흰 여백이 남습니다. 이건 정상입니다.

해상도 검증 (--ruler 인쇄물을 자로 재세요)
  - 세로: 눈금 80 자리까지가 실제로 80mm 면 OK.
          X mm 로 나왔다면 실제 해상도 = (86 * PX_PER_MM) / X px/mm
  - 가로: 왼쪽 끝 ~ 오른쪽 파선 띠 바깥쪽까지가 약 46mm 면 576dot 가 맞습니다.
          더 짧으면 MODEL_MAX_W_PX['b1pro'] 를 줄이세요.
"""

import enum
import glob
import math
import os
import queue
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
import wave
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

# ============================================================================
# 1. 설정  (여기만 바꾸면 됩니다)
# ============================================================================

# --- 프린터 ---------------------------------------------------------------
PRINTER_ENABLED = True       # False 면 PNG 로만 저장하고 인쇄는 건너뜁니다(테스트용)
PRINTER_MODEL   = 'b1pro'    # 'b1pro' | 'b1' | 'b18' | 'b21' | 'd11' | 'd110'

# ★★ 해상도. 이 한 줄이 아래 모든 픽셀 계산의 기준입니다 ★★
#   B1 Pro  → 300
#   B1/B21/B18/D11 → 203
PRINTER_DPI     = 300

# ★★ 인쇄 시퀀스(태스크). 모델마다 명령 형식이 다릅니다 ★★
#   'b1' : 2024년 출시 기종 (B1, B21, D11 ...)
#          PrintStart 7바이트 / PageStart 사용 / SetPageSize 6바이트
#   'v4' : 2025년 출시 기종 (B1 Pro, B21 Pro, D110_M, D11_H ...)
#          PrintStart 9바이트 / PageStart 안 씀 / SetPageSize 13바이트
#          + SetPageSize 뒤에 응답 안 기다리는 PrintStatus
#          + PrintEnd 뒤에 응답 안 기다리는 Heartbeat
#
#   ★ 'b1' 형식을 B1 Pro 에 보내면 PrintStart 에서 오류 응답(type 219)이
#     돌아오고 인쇄가 시작조차 되지 않습니다. 그게 이 값이 필요한 이유입니다.
#   출처: https://printers.niim.blue/interfacing/print-tasks/
PRINT_TASK      = 'v4'       # 'v4' | 'b1'

PRINT_SPEED     = 0          # v4 태스크의 PrintStart 에만 들어갑니다.
                             #   0 = 느리고 화질 좋게 (사진은 이쪽)
                             #   1 = 빠르고 화질 낮게

PRINT_BT_DUMMY_PACKETS = True
                             # 일부 Pro 기종은 블루투스에서 PrintStart /
                             # PrintEnd 직후 패킷 하나를 통째로 흘려버립니다.
                             # 그 자리에 '응답을 안 기다리는' 더미 패킷을 넣어
                             # 진짜 명령이 희생되지 않게 합니다. (부작용 없음)

# --- 연결 방식 -------------------------------------------------------------
PRINTER_CONN    = 'bluetooth'   # 'bluetooth' | 'usb'
                                # 공식 앱과 같은 경로(블루투스)를 기본값으로 둡니다.

# 블루투스
PRINTER_BT_ADDR = 'auto'     # 'auto' = 페어링된 기기 중에서 자동 선택
                             # 직접 적으려면 'E2:E1:08:03:09:87' 형식
                             # ★ B1 계열은 주소가 두 개입니다 (앞 3바이트가 회전된 형태).
                             #   'Serial Port' UUID 를 가진 쪽만 인쇄가 됩니다.
                             #   `python3 niimbot_photo.py --bt` 로 확인하세요.
PRINTER_BT_CHANNEL = 0       # 0 = 자동 탐색 (1번부터 순서대로 시도)

# USB (PRINTER_CONN='usb' 일 때만)
PRINTER_PORT    = 'auto'     # '/dev/ttyACM0' 처럼 직접 지정해도 됩니다.
                             # 'auto' 는 ttyACM* → ttyUSB* 순으로 찾고,
                             # Jetson 온보드 UART(ttyTHS*)는 후보에서 제외합니다.

PRINT_DENSITY   = 3          # 1~5
                             # ★ 300dpi 는 도트가 작아서 같은 농도라도 203dpi 보다
                             #   연하게 나옵니다. B1(203dpi)에서 2로 낮춰 쓰셨더라도
                             #   B1 Pro 에서는 3부터 다시 잡으시는 게 맞습니다.
                             #   너무 진하면 2, 흐리면 4로.

PRINT_LABEL_TYPE = 2        # 용지 종류. 프린터가 이 값으로 갭 감지 방식을 정합니다.
                             #   1  간격지(갭)   ← 일반 라벨. 기본값
                             #   2  블랙마크
                             #   3  연속지(갭 없음)
                             #   4  타공지
                             #   5  투명지
                             #   6  PVC 태그
                             #   10 블랙마크 갭
                             #   11 열수축 튜브
                             # ★ 실제 용지와 안 맞으면 PrintStart 에서
                             #   PaperOutException(코드 8) 이 납니다.
                             #   맞는 값을 모르면 1 → 3 → 2 순으로 바꿔 가며 시험하세요.

# --- 전송 속도 제한 --------------------------------------------------------
#
#   v10 부터 기본 해제(0) 입니다. 신형 프로토콜에서는 속도 제한 없이
#   한 장이 완주하는 것을 실측으로 확인했습니다.
#
#   ★ 다만 300dpi 로 올리면 데이터량이 (11.811/8)^2 ≈ 2.2배로 늘어납니다.
#     54x86 한 장 = 1016줄 x 72바이트 ≈ 73KB.
#     앞부분만 찍히고 잘리는 증상이 재발하면 `--ruler 150` 부터 시험하세요.
#
#   과거 기록 (B1 203dpi 실측):
#     - 프린터 소비속도 약 100~120줄/초 (= 13~15 mm/s), 수신 버퍼 약 2.6~4.3KB
#     - 300dpi 로 환산하면 대략 150~180줄/초가 소비속도, 안전 구간은 그 75%
PRINT_LINES_PER_SEC = 0      # 초당 몇 줄씩 흘려보낼지 (0 이면 제한 없음)
PRINT_CHUNK_LINES   = 0      # 한 번에 몇 줄씩 write 할지.
                             # 0 = 연결 방식에 맞춰 자동
                             #     블루투스 → 1줄 (패킷 경계로 정확히 자름)
                             #     USB      → 8줄
                             # ★ 블루투스(RFCOMM)는 프레임 단위로 전달되는데,
                             #   여러 패킷을 이어 붙여 보내면 프린터 펌웨어가
                             #   프레임 경계에서 패킷을 못 맞춰 이미지 줄을
                             #   통째로 버리는 경우가 있습니다. (빈 라벨 증상)

# --- 페이지 분할 (실험용) --------------------------------------------------
#   신형 프로토콜에서는 단일 페이지가 정상 동작하므로 기본 False.
#   ★ 켜면 totalPages 의미상 라벨이 페이지 수만큼 따로따로 나뉘어 나올 수
#     있으니 켜지 마세요.
PRINT_MULTIPAGE      = False
PRINT_MAX_PAGE_LINES = 448   # 한 페이지 최대 줄수
PRINT_PAGE_Y_RESET   = True  # 페이지마다 줄 번호를 0부터 다시 매길지
PRINT_PAGE_GAP_SEC   = 0.05  # 페이지 사이 간격

PRINT_USE_STATUS   = False   # 전송 '중' 진행률 브레이크 (v10 에서는 불필요.
                             # 전송 '후' 완료 대기 폴링은 항상 동작합니다)
PRINT_MAX_LEAD_MM  = 3       # 프린터보다 최대 몇 mm 앞서 나갈지.

# --- niimprint 패키지 위치 (선택) -----------------------------------------
#   이 파일에 프로토콜이 내장돼 있습니다. niimprint 가 설치돼 있어도
#   **내장 구현을 씁니다.**
NIIMPRINT_PATH  = ''

# --- 용지 / 이미지 ---------------------------------------------------------
# ── 인쇄 영역 프리셋 (한 줄만 바꾸면 전환됩니다) ──────────────────────────
#   세 번째 값은 '사진 회전각(도)' 입니다. 용지 길이가 아닙니다.
#   세로로 긴 라벨은 16:9 카메라 화면을 90도 돌려 넣는 게 맞습니다.
LABEL_PRESET    = '54x86'
_PRESETS = {           # 프리셋 이름: (가로mm, 세로mm, 사진 회전각)
    '54x86': (54, 86, 90),      # B1 Pro 포토 용지
    '50x90': (50, 90, 90),      # 구 B1 포토 용지
    '50x60': (50, 60, 0),
}
if LABEL_PRESET not in _PRESETS:
    raise KeyError(f"LABEL_PRESET '{LABEL_PRESET}' 이(가) _PRESETS 에 없습니다. "
                   f"가능한 값: {list(_PRESETS)}")
LABEL_W_MM, LABEL_H_MM, _PRESET_ROT = _PRESETS[LABEL_PRESET]

# ★★ 실제로 찍을 수 있는 길이는 용지 길이보다 짧습니다 ★★
#   프린터는 갭 센서로 라벨 시작을 감지한 뒤, 센서에서 프린트 헤드까지의
#   거리만큼 종이를 더 보낸 다음 찍기 시작합니다. 그래서 인쇄는 항상
#   라벨 위 가장자리보다 몇 mm 아래에서 시작하고, 용지 길이 전체를 보내면
#   남는 만큼이 그대로 다음 장으로 넘어갑니다. (라벨 2장이 나오는 증상)
#
#   여기에 '용지 길이 - 실제 인쇄 가능 길이' 를 넣으세요.
#   측정법: --ruler 를 찍고 첫 장에 온전히 나온 마지막 눈금이 N 이면
#           LABEL_TRIM_MM = LABEL_H_MM - N + 2  (2는 안전 여유)
LABEL_TRIM_MM   = 0.0

# ★ 해상도에서 유도. 300dpi = 11.811 px/mm (정수가 아닙니다)
PX_PER_MM       = PRINTER_DPI / 25.4

# 모델별 프린트 헤드 폭 (도트 수)
#   b1/b18/b21 : 203dpi, 384 dot = 48.0 mm
#   b1pro      : 300dpi, 567 dot = 48.0 mm   ← 576 아니고 567 입니다.
#                (300dpi x 48mm = 566.9 → 567. 유효 인쇄폭은 B1 과 같은 48mm)
MODEL_MAX_W_PX  = {'b1pro': 567, 'b1': 384, 'b18': 384, 'b21': 384,
                   'd11': 96, 'd110': 96}

PHOTO_ROTATE_DEG = _PRESET_ROT   # 프리셋이 정해 줍니다. 직접 90/180/270 으로
                                 # 덮어써도 됩니다. 인화물이 거꾸로면 +180.
PHOTO_FIT       = 'cover'    # 'cover' = 꽉 채우고 남는 부분 잘라냄
                             # 'contain' = 다 보이게 넣고 여백은 흰색

# ★ v11: 여백/캡션은 px 가 아니라 mm 로 지정합니다.
#   해상도를 바꿔도 물리적 크기가 그대로 유지됩니다.
LABEL_MARGIN_MM = 0.8        # 라벨 가장자리 흰 여백
CAPTION_H_MM    = 8.0        # 하단 캡션 띠 높이
                             # ★ 4.0 → 8.0: 로고 두 개가 들어가려면 이 정도는
                             #   되어야 인쇄에서 읽힙니다. 사진 영역이 4mm
                             #   줄어들지만 86mm 라벨에서는 티가 안 납니다.

LABEL_MARGIN_PX = int(round(LABEL_MARGIN_MM * PX_PER_MM))
CAPTION_H_PX    = int(round(CAPTION_H_MM * PX_PER_MM))

# --- 흑백 변환 튜닝 (열전사는 회색이 없어서 디더링이 전부입니다) ------------
GAMMA           = 0.70
AUTO_CONTRAST   = False
CONTRAST_GAIN   = 1.00       # 1.0 = 그대로
BRIGHTNESS_GAIN = 1.00
UNSHARP_PERCENT = 60         # 0 이면 샤프닝 안 함. 디더링 전 윤곽을 살려줍니다.
                             # ★ 300dpi 는 도트가 촘촘해 디더링 노이즈가 덜 보입니다.
                             #   결과가 거칠면 100 정도로 낮춰 보세요.

# --- 캡션(하단 띠: 로고 + 날짜) --------------------------------------------
#   배치: [하나둘셋 찰칵(팀 로고)]   날짜   [대한상공회의소 로고]
#   - 팀 로고 = 이 결과물의 '브랜드'라 읽기 순서 첫 자리(왼쪽)
#   - 날짜   = 가운데 (양쪽 로고 사이에서 대칭을 잡아 줌)
#   - 상의   = 주최 기관 크레딧 자리(오른쪽)
CAPTION_ENABLED = True
CAPTION_FORMAT  = '%Y-%m-%d'            # strftime 형식 (★ 시간은 뺐습니다)
CAPTION_TEXT    = ''                    # 비우면 날짜, 채우면 그 문구 고정

#   로고 파일은 이 스크립트와 같은 폴더에서 찾습니다. 없으면 글자만 나옵니다.
CAPTION_LOGO_LEFT  = 'team_logo_115.png'         # 팀 로고
CAPTION_LOGO_RIGHT = 'kcci_logo.png'   # 주최 기관 로고
CAPTION_LOGO_BOX   = 0.30               # 로고 하나가 차지할 최대 가로 비율

# --- 저장 / 사운드 ---------------------------------------------------------
SAVE_DIR        = 'captures'  # 원본 캡처와 라벨 이미지를 여기에 남깁니다.
SAVE_RAW        = True
SOUND_ENABLED   = True
SOUND_DIR       = 'sounds'

# --- 문제 추적용 -----------------------------------------------------------
#   True 로 두면 프린터에 보낸 명령과 받은 응답을 전부 찍습니다.
PROTOCOL_DEBUG  = False


def mm2px(mm):
    """밀리미터 -> 픽셀(도트). 해상도가 바뀌어도 이 함수만 쓰면 안전합니다."""
    return int(round(mm * PX_PER_MM))


# ============================================================================
# 2. Niimbot 프로토콜  — 내장 구현 (niimprint 없이도 동작)
#    원본 niimprint(MIT) 를 기반으로 하되 아래를 고쳤습니다.
#      (a) _packed_rows 를 numpy.packbits 로     (145ms -> 4ms)
#      (b) _recv 의 무한 루프 방어               (v9 주석 참고)
#      (c) 신형(2024+) 펌웨어 프로토콜            (v10 주석 참고)
#      (d) 흑화소 카운트 clip                     (v11 주석 참고)
# ============================================================================
try:
    import serial
    from serial.tools.list_ports import comports as _list_comports
    _SERIAL_OK = True
    _SERIAL_ERR = ''
except Exception as e:                                    # noqa: BLE001
    serial = None
    _list_comports = lambda: []                           # noqa: E731
    _SERIAL_OK = False
    _SERIAL_ERR = str(e)


class RequestCodeEnum(enum.IntEnum):
    GET_INFO = 64            # 0x40
    HEARTBEAT = 220          # 0xDC
    SET_LABEL_TYPE = 35      # 0x23
    SET_LABEL_DENSITY = 33   # 0x21
    START_PRINT = 1          # 0x01
    END_PRINT = 243          # 0xF3
    START_PAGE_PRINT = 3     # 0x03
    END_PAGE_PRINT = 227     # 0xE3
    SET_DIMENSION = 19       # 0x13
    GET_PRINT_STATUS = 163   # 0xA3
    GET_RFID = 26            # 0x1A
    CANCEL_PRINT = 218       # 0xDA


# 프린터가 type 219(0xDB) 로 돌려주는 오류 코드.
# 출처: niimbluelib src/packets/payloads.ts PrinterErrorCode
PRINTER_ERROR_CODES = {
    0x01: '커버 열림',
    0x02: '용지 없음',
    0x03: '배터리 부족',
    0x04: '배터리 이상',
    0x05: '사용자 취소',
    0x06: '데이터 오류',
    0x07: '과열',
    0x08: '용지 배출 이상 (용지가 감지되지 않거나 용지 종류가 안 맞음)',
    0x09: '프린터 사용 중',
    0x0a: '프린트 헤드 없음',
    0x0b: '온도 낮음',
    0x0c: '프린트 헤드 헐거움',
    0x0d: '리본 없음',
    0x0e: '리본 종류 오류',
    0x0f: '사용한 리본',
    0x10: '용지 종류 불일치',
    0x11: '용지 설정 실패',
    0x12: '인쇄 모드 설정 실패',
    0x13: '농도 설정 실패',
    0x14: 'RFID 쓰기 실패',
    0x15: '여백 설정 실패',
    0x16: '통신 이상',
    0x17: '연결 끊김',
    0x18: '캔버스 파라미터 오류',
    0x19: '회전 파라미터 이상',
    0x1a: 'JSON 파라미터 이상',
    0x1c: '용지 확인 필요',
    0x1d: 'RFID 태그 미기록',
    0x1e: '농도 설정 미지원',
    0x1f: '인쇄 모드 미지원',
    0x20: '라벨 재질 설정 오류',
    0x21: '라벨 재질 설정 미지원',
    0x22: 'RFID 쓰기 미지원',
    0x32: '잘못된 페이지',
    0x33: '잘못된 리본 페이지',
    0x34: '수신 타임아웃',
    0x35: '비정품 리본',
}

LABEL_TYPE_NAMES = {
    1: '간격지(갭)', 2: '블랙마크', 3: '연속지', 4: '타공지',
    5: '투명지', 6: 'PVC 태그', 10: '블랙마크 갭', 11: '열수축 튜브',
}


class _Packet:
    """55 55 | type | len | data... | checksum | AA AA"""

    __slots__ = ('type', 'data')

    def __init__(self, type_, data):
        self.type = type_
        self.data = data

    @classmethod
    def from_bytes(cls, pkt):
        assert pkt[:2] == b"\x55\x55"
        assert pkt[-2:] == b"\xaa\xaa"
        type_, len_ = pkt[2], pkt[3]
        data = pkt[4:4 + len_]
        checksum = type_ ^ len_
        for i in data:
            checksum ^= i
        assert checksum == pkt[-3]
        return cls(type_, data)

    def to_bytes(self):
        checksum = self.type ^ len(self.data)
        for i in self.data:
            checksum ^= i
        return bytes((0x55, 0x55, self.type, len(self.data),
                      *self.data, checksum, 0xAA, 0xAA))

    def __repr__(self):
        return f'<Packet type={self.type} len={len(self.data)}>'


class _SerialTransport:
    def __init__(self, port):
        if not _SERIAL_OK:
            raise RuntimeError(f'pyserial 을 불러올 수 없습니다: {_SERIAL_ERR}\n'
                               f'  → pip3 install pyserial')
        self._serial = serial.Serial(port=port, baudrate=115200, timeout=0.5)

    def read(self, length):
        return self._serial.read(length)

    def write(self, data):
        n = self._serial.write(data)
        # write_timeout 이 걸려 있으면 일부만 쓰이고 조용히 넘어갈 수 있습니다.
        # 그러면 인쇄물 중간이 통째로 비는데 원인을 찾기가 아주 어렵습니다.
        if n is not None and n != len(data):
            raise IOError(f'시리얼 쓰기가 잘렸습니다: {n}/{len(data)} 바이트')
        return n

    def flush(self):
        """커널 송신 버퍼가 실제로 다 나갈 때까지 기다립니다(속도 제한의 전제)."""
        try:
            self._serial.flush()
        except Exception:                                 # noqa: BLE001
            pass

    def drain(self):
        """수신 버퍼에 쌓인 것을 비차단으로 비웁니다. 버린 바이트 수 반환."""
        try:
            n = getattr(self._serial, 'in_waiting', 0)
            if n:
                self._serial.read(n)
                return n
        except Exception:                                 # noqa: BLE001
            pass
        return 0

    def close(self):
        try:
            self._serial.close()
        except Exception:                                 # noqa: BLE001
            pass


class _BluetoothTransport:
    """
    RFCOMM(SPP) 소켓. 공식 앱이 쓰는 것과 같은 경로입니다.

    USB(CDC) 와의 차이
      - 링크 자체가 느리고, send() 가 상대 버퍼에 맞춰 실제로 블로킹됩니다.
        즉 흐름 제어가 링크 레벨에서 걸리므로 USB 처럼 데이터가 통째로
        사라지는 일이 잘 없습니다.
      - 대신 무선이라 간섭에 약합니다. 인쇄 도중 끊기면 라벨이 반만 나옵니다.
    """

    def __init__(self, address, channel=0, timeout=0.6, connect_timeout=12.0):
        if not hasattr(socket, 'AF_BLUETOOTH'):
            raise RuntimeError('이 파이썬은 블루투스 소켓을 지원하지 않습니다.')
        chans = [channel] if channel else list(range(1, 11))
        last = None
        for ch in chans:
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                                 socket.BTPROTO_RFCOMM)
            try:
                sock.settimeout(connect_timeout)
                sock.connect((address, ch))
                sock.settimeout(timeout)
                self._timeout = timeout
                self._sock = sock
                self.channel = ch
                self.address = address
                return
            except Exception as e:                        # noqa: BLE001
                last = e
                try:
                    sock.close()
                except Exception:                         # noqa: BLE001
                    pass
        raise RuntimeError(
            f'블루투스 연결 실패 ({address}): {last}\n'
            f'  1) 페어링됐는지:  bluetoothctl paired-devices\n'
            f'  2) 주소가 맞는지: python3 niimbot_photo.py --bt\n'
            f'     (B1 계열은 주소가 2개인데 Serial Port UUID 를 가진 쪽만 됩니다)\n'
            f'  3) 프린터 전원 / 다른 기기와 연결돼 있지 않은지')

    def read(self, length):
        try:
            return self._sock.recv(length)
        except (socket.timeout, TimeoutError):
            return b''
        except OSError:
            return b''

    def write(self, data):
        self._sock.sendall(data)          # 전부 보내거나 예외
        return len(data)

    def flush(self):
        pass                              # sendall 이 이미 다 보낸 뒤 반환

    def drain(self):
        """
        ★★ 블루투스에서 '빈 라벨'이 나오는 문제 방지 장치 ★★

        인쇄 중에 우리는 소켓을 읽지 않습니다. USB(CDC) 는 호스트 커널이
        수신 엔드포인트를 알아서 계속 비워 주므로 문제가 없지만,
        RFCOMM 은 크레딧 기반 흐름 제어라 **받는 쪽이 안 읽으면 크레딧이
        바닥나고, 그러면 프린터의 송신 루틴이 블로킹**됩니다.
        그 상태로 프린터 펌웨어가 멈추면 들어오는 이미지 줄을 처리하지
        못하고 통째로 흘려버립니다. 종이는 명령대로 나오니 결과는 백지.

        그래서 이미지를 보내는 동안에도 주기적으로 수신 버퍼를 비웁니다.
        """
        total = 0
        try:
            self._sock.setblocking(False)
            while True:
                b = self._sock.recv(4096)
                if not b:
                    break
                total += len(b)
        except (BlockingIOError, InterruptedError):
            pass
        except (socket.timeout, TimeoutError):
            pass
        except OSError:
            pass
        finally:
            try:
                self._sock.settimeout(self._timeout)
            except Exception:                             # noqa: BLE001
                pass
        return total

    def close(self):
        try:
            self._sock.close()
        except Exception:                                 # noqa: BLE001
            pass


class _PrinterClient:
    def __init__(self, transport):
        self._transport = transport
        self._packetbuf = bytearray()

    # ------------------------------------------------------------ 하이레벨
    def print_image(self, image, density=3, verbose=True, rate=None):
        """
        라벨 한 장을 인쇄합니다. (신형 2024+ 펌웨어 프로토콜)

        신형 인쇄 시퀀스 (https://printers.niim.blue):
          SetDensity → SetLabelType → PrintStart[totalPages u16, 0x4, color]
          → (페이지마다) PageStart → SetPageSize[rows, cols, copies]
                         → 줄 데이터(흑화소 카운트 포함) → PageEnd
          → 진행률(0xA3) 폴링으로 완료 확인 → PrintEnd
        """
        width = image.width
        packed, counts = self._packed_rows(
            image, MODEL_MAX_W_PX.get(PRINTER_MODEL.lower(), width))
        total = int(packed.shape[0])

        # ── 페이지 나누기 (신형 프로토콜에서는 단일 페이지로 충분) ──────
        if PRINT_MULTIPAGE and total > PRINT_MAX_PAGE_LINES:
            n_pages = int(math.ceil(total / float(PRINT_MAX_PAGE_LINES)))
            base = total // n_pages
            sizes = [base] * n_pages
            for i in range(total - base * n_pages):
                sizes[i] += 1
        else:
            sizes = [total]

        head_px = MODEL_MAX_W_PX.get(PRINTER_MODEL.lower(), width)

        self.set_label_density(density)
        self.set_label_type(PRINT_LABEL_TYPE)
        self.start_print(len(sizes))       # 총 페이지 수를 함께 통보
        if PRINT_TASK == 'v4' and PRINT_BT_DUMMY_PACKETS:
            # PrintStart 직후 첫 패킷을 흘리는 기종 대비용 더미.
            # 응답을 기다리지 않으므로 지원하지 않는 모델에서도 무해합니다.
            self._send_no_wait(RequestCodeEnum.GET_PRINT_STATUS, b'\x01')

        if PRINT_CHUNK_LINES:
            chunk = max(1, int(PRINT_CHUNK_LINES))
        else:
            # 블루투스는 1줄씩(=패킷 하나씩), USB 는 8줄씩 묶어 보냅니다.
            chunk = 1 if isinstance(self._transport, _BluetoothTransport) else 8
        lps = float(rate if rate is not None else (PRINT_LINES_PER_SEC or 0))
        use_status = bool(PRINT_USE_STATUS)
        lead = max(8, mm2px(PRINT_MAX_LEAD_MM))
        status_fail = 0
        brake_time = 0.0
        drained = 0

        if verbose and len(sizes) > 1:
            print(f'[Print] {total}줄을 {len(sizes)}페이지로 분할: '
                  + ' + '.join(f'{n}줄({n / PX_PER_MM:.0f}mm)' for n in sizes))

        t_all = time.time()
        offset = 0
        for pi, n_lines in enumerate(sizes):
            # ★ v4 태스크는 PageStart(0x03) 를 쓰지 않습니다.
            if PRINT_TASK != 'v4':
                self.start_page_print()
            # 페이지마다 그 페이지의 줄수로 크기를 다시 알려 줍니다.
            self.set_dimension(n_lines, width)
            if PRINT_TASK == 'v4':
                # 공식 앱과 같은 순서: SetPageSize 직후 응답을 기다리지 않는
                # PrintStatus 를 한 번 던집니다.
                self._send_no_wait(RequestCodeEnum.GET_PRINT_STATUS, b'\x01')

            t0 = time.time()
            for i in range(0, n_lines, chunk):
                hi = min(i + chunk, n_lines)
                parts = []
                for k in range(i, hi):
                    y = k if PRINT_PAGE_Y_RESET else offset + k
                    # 흑화소 카운트(3분할)를 실제 값으로 보냅니다.
                    # 신형 펌웨어는 이 값으로 발열량을 정하므로 0 이면 백지.
                    c0, c1, c2 = counts[offset + k]
                    parts.append(_Packet(
                        0x85,
                        struct.pack('>H3BB', y, int(c0), int(c1), int(c2), 1)
                        + packed[offset + k].tobytes()).to_bytes())
                self._transport.write(b''.join(parts))
                if hasattr(self._transport, 'flush'):
                    self._transport.flush()
                # 프린터가 보내오는 것을 계속 비워 줍니다.
                # 안 비우면 블루투스에서 프린터가 막혀 이미지를 버립니다.
                if hasattr(self._transport, 'drain'):
                    drained += self._transport.drain()

                # (1) 고정 속도 페이싱 (기본 꺼짐. 잘릴 때만 켜세요)
                if lps > 0:
                    wait = t0 + hi / lps - time.time()
                    if wait > 0:
                        time.sleep(wait)

                # (2) 진행 상황 기반 브레이크 (느리게만 조절)
                if use_status and hi % (chunk * 2) == 0 and hi < n_lines:
                    printed = self._printed_lines(n_lines)
                    if printed is None:
                        status_fail += 1
                        if status_fail >= 2:
                            use_status = False
                            if verbose:
                                print('[Print] 이 모델은 진행상황 조회(0xA3)를 '
                                      '지원하지 않아 고정 속도만 씁니다.')
                        continue
                    status_fail = 0
                    t_brake = time.time()
                    while hi - printed > lead:
                        time.sleep(0.05)
                        if time.time() - t_brake > 15.0:      # 안전 탈출
                            use_status = False
                            break
                        nxt = self._printed_lines(n_lines)
                        if nxt is None:
                            break
                        printed = nxt
                    brake_time += time.time() - t_brake

            self.end_page_print()
            offset += n_lines
            if pi < len(sizes) - 1 and PRINT_PAGE_GAP_SEC > 0:
                time.sleep(PRINT_PAGE_GAP_SEC)

        dt = time.time() - t_all
        if verbose:
            extra = f', 대기 {brake_time:.1f}s' if brake_time > 0.05 else ''
            print(f'[Print] 이미지 전송 {dt:.1f}s  '
                  f'({total}줄 / {len(sizes)}페이지, '
                  f'실측 {total / max(dt, 1e-6):.0f}줄/초'
                  + (f', 설정 {lps:.0f}줄/초' if lps > 0 else ', 속도 무제한')
                  + f'{extra})')
            print(f'[Print] 전송 중 프린터가 보낸 데이터: {drained} 바이트'
                  + ('  ← 안 비우면 여기서 막힙니다' if drained else
                     '  (프린터가 아무것도 안 보냄)'))

        # ── 인쇄가 '실제로' 끝날 때까지 진행률(0xA3)을 폴링한 뒤에만
        #    PrintEnd 를 보냅니다. 전송 완료 != 인쇄 완료입니다.
        #    (일찍 종료 명령을 보내면 신형 펌웨어는 인쇄를 그 자리에서
        #     끊어 버립니다. '11mm 만 찍히고 배출' 증상이 그것이었습니다)
        if verbose:
            print('[Print] 인쇄 완료 대기 중 (진행률 폴링)...')
        t_poll = time.time()
        miss = 0
        done_seen = 0
        last_state = None
        while time.time() - t_poll < 60.0:
            time.sleep(0.3)
            st = self.get_print_status()
            if st is None:
                miss += 1
                if miss >= 5:
                    break               # 조회 미지원 → 그냥 종료 시퀀스로
                continue
            miss = 0

            if st['state'] is not None and st['state'] != last_state:
                last_state = st['state']
                if last_state not in (0, 1):
                    name = PRINTER_ERROR_CODES.get(last_state, '알 수 없음')
                    print(f'[Print] ★ 프린터 상태 {last_state} — {name}')
                    if last_state == 0x08:
                        print('[Print]   보낸 줄 수가 라벨 길이를 넘었습니다. '
                              'LABEL_TRIM_MM 을 늘리세요 '
                              f'(지금 {LABEL_TRIM_MM}mm, '
                              f'인쇄 길이 {label_print_h_mm():.0f}mm).')

            # ★★ v13: 완료 판정은 progress1 만 봅니다 ★★
            #   예전에는 progress1 과 progress2 가 둘 다 100 이 되기를 기다렸는데,
            #   이 모델은 progress2 를 아예 쓰지 않고 항상 0 입니다.
            #   그래서 인쇄가 다 끝나도 루프가 안 끝나고 타임아웃까지
            #   GET_PRINT_STATUS 를 계속 던지는 상태가 됐습니다.
            if st['progress1'] >= 100 or st['progress2'] >= 100:
                done_seen += 1
                if done_seen >= 2:      # 두 번 연속 확인되면 확실합니다
                    break
            else:
                done_seen = 0

        deadline = time.time() + 30.0
        while not self.end_print():
            if time.time() > deadline:
                raise TimeoutError('end_print 응답이 오지 않습니다')
            time.sleep(0.1)

        if PRINT_TASK == 'v4' and PRINT_BT_DUMMY_PACKETS:
            # PrintEnd 직후에도 패킷 하나를 흘리는 기종이 있습니다.
            self._send_no_wait(RequestCodeEnum.HEARTBEAT, b'\x01')

    @staticmethod
    def _packed_rows(image, head_px=None):
        """1비트 이미지 -> (비트팩 배열, 줄별 흑화소 카운트).

        반환:
          packed : (h, ceil(w/8)) uint8  — 줄 데이터 비트팩
          counts : (h, 3) uint8          — 3분할 구간별 검은 픽셀 수

        신형 펌웨어는 줄 헤더의 흑화소 카운트를 실제로 사용합니다(발열량 결정).
        0 으로 보내면 '찍을 게 없다'로 판단해 발열을 건너뛰고 용지만 이송합니다.

        ★ v12: 분할 기준을 '이미지 폭의 1/3' 이 아니라
          **헤드 바이트 수 // 3** 으로 바꿨습니다. 위키(niimbluelib) 기준이
          `printhead size / 8 / 3` 바이트이고, 384dot 에서는 둘이 우연히
          같았지만(16바이트=128px) 567dot 에서는 달라집니다.
            567dot → 71바이트 → 청크 23바이트(184px) / 23 / 나머지 199px
          경계가 어긋나면 발열량이 구간별로 틀어져 세로 줄무늬가 생깁니다.
        """
        img = ImageOps.invert(image.convert('L')).convert('1')
        arr = np.array(img, dtype=bool)             # (h, w)  True = 인쇄
        packed = np.packbits(arr, axis=1)           # (h, ceil(w/8))

        w = arr.shape[1]
        head_px = int(head_px or w)
        head_bytes = (head_px + 7) // 8
        chunk_px = max(8, (head_bytes // 3) * 8)    # 바이트 경계에 맞춘 구간 폭

        b0 = min(chunk_px, w)
        b1 = min(2 * chunk_px, w)
        counts = np.column_stack([
            arr[:, 0:b0].sum(axis=1),
            arr[:, b0:b1].sum(axis=1),
            arr[:, b1:].sum(axis=1),
        ])
        # 헤더에서 각 1바이트이므로 255 가 상한입니다. wrap 되면 그 줄만
        # 발열량이 엉뚱해져 흐리게 나옵니다.
        counts = np.clip(counts, 0, 255).astype(np.uint8)
        return packed, counts

    # ------------------------------------------------------------ 저수준
    def _send(self, packet):
        self._transport.write(packet.to_bytes())

    def _recv(self):
        """
        수신 스트림에서 패킷을 꺼냅니다.

        ★★ 여기에 심각한 버그가 있었습니다 (v9 에서 수정) ★★
          예전 코드는 버퍼 맨 앞이 패킷 시작이라고 **가정하고** 4번째 바이트를
          길이로 읽었습니다. 앞에 잡음이 섞이면 엉뚱한 값을 길이로 읽고,
          그 값이 남은 바이트보다 크면 'break' 로 빠져나옵니다.
          그러면 버퍼가 영원히 그 상태로 남아 **그 뒤의 모든 응답을 못 봅니다.**

        지금은 먼저 헤더(55 55)를 찾아 앞의 잡음을 버리고 나서 길이를 읽습니다.
        """
        packets = []
        buf = self._packetbuf
        buf.extend(self._transport.read(1024))

        while True:
            i = buf.find(b'\x55\x55')
            if i < 0:
                # 헤더가 없음. 다음 조각에서 55 55 가 이어질 수 있으니
                # 마지막 1바이트만 남기고 버립니다.
                if len(buf) > 1:
                    del buf[:len(buf) - 1]
                break
            if i > 0:
                del buf[:i]                  # 헤더 앞의 잡음 제거
            if len(buf) < 5:
                break                        # 길이 바이트가 아직 안 왔음
            pkt_len = buf[3] + 7
            if len(buf) < pkt_len:
                break                        # 패킷이 아직 덜 왔음
            try:
                pkt = _Packet.from_bytes(bytes(buf[:pkt_len]))
            except AssertionError:
                del buf[:2]                  # 가짜 헤더 → 건너뛰고 다시 찾음
                continue
            self._log_recv(pkt)
            packets.append(pkt)
            del buf[:pkt_len]

        # 버퍼가 비정상적으로 커지면(응답을 계속 못 맞추는 상황) 잘라 냅니다.
        if len(buf) > 4096:
            del buf[:len(buf) - 512]
        return packets

    def _log_recv(self, pkt):
        if PROTOCOL_DEBUG:
            print(f'  [proto] <- type={pkt.type}(0x{pkt.type:02x}) '
                  f'{pkt.data.hex()}')

    def _send_no_wait(self, reqcode, data):
        """응답을 기다리지 않고 던지기만 하는 명령.

        v4 태스크에서 공식 앱이 이렇게 보내는 자리가 몇 군데 있습니다.
        실패해도 인쇄를 막으면 안 되므로 예외를 삼킵니다.
        """
        try:
            if PROTOCOL_DEBUG:
                print(f'  [proto] -> (no-wait) 0x{int(reqcode):02x} {data.hex()}')
            self._send(_Packet(int(reqcode), data))
        except Exception:                                 # noqa: BLE001
            pass

    def _transceive(self, reqcode, data, respoffset=1, retries=6):
        respcode = respoffset + reqcode
        if PROTOCOL_DEBUG:
            name = getattr(RequestCodeEnum(reqcode), 'name', hex(reqcode)) \
                if reqcode in [int(x) for x in RequestCodeEnum] else hex(reqcode)
            print(f'  [proto] -> {name} {data.hex()}')
        self._send(_Packet(reqcode, data))
        for _ in range(retries):
            for packet in self._recv():
                if packet.type == 219:
                    code = packet.data[0] if packet.data else -1
                    name = PRINTER_ERROR_CODES.get(code, '알 수 없는 코드')
                    msg = (f'프린터가 오류를 반환했습니다 — {name} '
                           f'(코드 {code}/0x{code:02x}, 명령 0x{int(reqcode):02x})')
                    if code in (0x01, 0x02, 0x08, 0x10, 0x1c, 0x11):
                        msg += (
                            '\n  이건 프로토콜 문제가 아니라 용지/기구 문제입니다.'
                            '\n  1) 커버를 열었다가 딱 소리 나게 다시 닫으세요.'
                            ' (닫을 때 갭 보정이 다시 돕니다)'
                            '\n  2) 라벨 한 장을 배출구 밖으로 완전히 빼 두세요.'
                            '\n  3) 감열면(인쇄면)이 아래를 향하게 물렸는지 확인.'
                            f'\n  4) PRINT_LABEL_TYPE 이 {PRINT_LABEL_TYPE}'
                            f'({LABEL_TYPE_NAMES.get(PRINT_LABEL_TYPE, "?")})'
                            ' 인데 실제 용지와 다를 수 있습니다.'
                            '\n     1,3,2 순으로 바꿔 가며 시험해 보세요.'
                            '\n  5) 전원을 껐다 켠 뒤 다시 시도.')
                    raise ValueError(msg)
                if packet.type == 0:
                    raise NotImplementedError('지원하지 않는 명령 (type 0)')
                if packet.type == respcode:
                    return packet
            time.sleep(0.1)
        if PROTOCOL_DEBUG:
            print(f'  [proto] <- (응답 없음, {retries}회 재시도)')
        return None

    def _expect(self, name, packet):
        if packet is None:
            raise TimeoutError(f'{name}: 프린터가 응답하지 않습니다 '
                               f'(전원/용지/케이블 확인)')
        return packet

    # ------------------------------------------------------------ 명령들
    def set_label_type(self, n):
        p = self._expect('set_label_type',
                         self._transceive(RequestCodeEnum.SET_LABEL_TYPE,
                                          bytes((n,)), 16))
        return bool(p.data[0])

    def set_label_density(self, n):
        p = self._expect('set_label_density',
                         self._transceive(RequestCodeEnum.SET_LABEL_DENSITY,
                                          bytes((n,)), 16))
        return bool(p.data[0])

    def start_print(self, total_pages=1):
        """PrintStart. ★ 페이로드 길이가 모델마다 다릅니다 ★

          1바이트 : D11, B21, D110 (구형)
          7바이트 : [totalPages(u16), 0 x4, pageColor(u8)]
                    → 2024년 기종 (B1, B21, D11)          PRINT_TASK='b1'
          9바이트 : [totalPages(u16), 0 x4, pageColor(u8), speed(u8), flag(u8)]
                    → 2025년 기종 (B1 Pro, B21 Pro, D110_M)  PRINT_TASK='v4'

        형식이 안 맞으면 프린터가 오류 응답(type 219)을 돌려주고 인쇄가
        시작조차 되지 않습니다. B1 Pro 에 7바이트를 보내면 정확히 그렇습니다.

        pageColor 는 0 = 흑백. B1 Pro 는 적/흑 2색을 지원하므로 이 바이트가
        실제 의미를 가지지만, 흑백 인화만 할 거면 0 으로 둡니다.
        speed 는 0 = 느리고 화질 좋게 / 1 = 빠르고 화질 낮게.
        """
        if PRINT_TASK == 'v4':
            data = struct.pack('>HBBBBBBB',
                               int(total_pages),        # totalPages
                               0, 0, 0, 0,              # always 0
                               0,                       # pageColor
                               int(PRINT_SPEED) & 0xFF,  # speed
                               0)                       # some flag
        else:
            data = struct.pack('>HBBBBB', int(total_pages), 0, 0, 0, 0, 0)
        p = self._expect('start_print',
                         self._transceive(RequestCodeEnum.START_PRINT, data))
        return bool(p.data[0])

    def end_print(self):
        p = self._transceive(RequestCodeEnum.END_PRINT, b'\x01')
        return bool(p.data[0]) if p else False

    def start_page_print(self):
        p = self._expect('start_page_print',
                         self._transceive(RequestCodeEnum.START_PAGE_PRINT, b'\x01'))
        return bool(p.data[0])

    def end_page_print(self):
        p = self._expect('end_page_print',
                         self._transceive(RequestCodeEnum.END_PAGE_PRINT, b'\x01'))
        return bool(p.data[0])

    def set_dimension(self, rows, cols, copies=1):
        """SetPageSize. ★ 이것도 길이가 모델마다 다릅니다 ★

          4바이트  : [rows(u16), cols(u16)]                      (구형)
          6바이트  : [rows, cols, copies]                        PRINT_TASK='b1'
          13바이트 : [rows, cols, copies, cutHeight(u16), cutType(u8),
                     0(u8), sendAll(u8), partHeight(u16)]        PRINT_TASK='v4'

        cols(가로 도트 수)는 반드시 헤드 폭 이하여야 합니다.
        v4 의 뒤쪽 7바이트는 절단/분할 관련이고 전부 0 이면 '통짜 한 장'입니다.
        """
        base = struct.pack('>HHH', rows, cols, copies)
        data = base + b'\x00' * 7 if PRINT_TASK == 'v4' else base
        p = self._expect('set_dimension',
                         self._transceive(RequestCodeEnum.SET_DIMENSION, data))
        return bool(p.data[0])

    def get_print_status(self):
        """
        현재 인쇄 진행 상황. 지원하지 않으면 None.

        ★★ 절대 예외를 밖으로 내보내면 안 됩니다 ★★
          이 조회는 '있으면 좋은' 보조 기능인데, _transceive 가 프린터의
          오류 응답(type 219)에 ValueError 를 던지도록 되어 있습니다.
          그게 print_image 밖으로 튀어나가면 인쇄가 그 자리에서 중단됩니다.
        """
        try:
            p = self._transceive(RequestCodeEnum.GET_PRINT_STATUS, b'\x01',
                                 respoffset=16, retries=2)
        except Exception:                                 # noqa: BLE001
            return None
        if p is None or len(p.data) < 4:
            return None
        page, pr1, pr2 = struct.unpack('>HBB', p.data[:4])
        # ★ v13: 7번째 바이트는 프린터 상태/오류 코드입니다.
        #   정상 인쇄 중에는 1, 라벨 끝을 넘어가면 8(용지 배출 이상)로 바뀝니다.
        state = p.data[6] if len(p.data) >= 7 else None
        return {'page': page, 'progress1': pr1, 'progress2': pr2,
                'state': state, 'raw': p.data.hex()}

    def _printed_lines(self, total):
        """
        '지금까지 몇 줄 찍었나'를 추정합니다.
        값이 0~100 범위를 벗어나면 이 모델은 다른 의미로 쓰는 것이므로
        신뢰하지 않고 None 을 돌려줘서 브레이크를 꺼 버립니다.
        """
        st = self.get_print_status()
        if st is None:
            return None
        pr = max(st['progress1'], st['progress2'])
        if not (0 <= pr <= 100):
            return None
        return int(pr / 100.0 * total)

    def get_rfid(self):
        """
        롤에 붙은 RFID 태그를 읽습니다. 종이를 전혀 쓰지 않습니다.

        ★ 주의: total_len / used_len 은 **밀리미터가 아니라 '라벨 장수'** 입니다.
          라벨의 물리적 크기(mm)는 RFID 로 알 수 없습니다. 자로 재야 합니다.
        """
        try:
            p = self._transceive(RequestCodeEnum.GET_RFID, b'\x01', retries=3)
        except Exception:                                 # noqa: BLE001
            return None
        if p is None or not p.data or p.data[0] == 0:
            return None
        try:
            d = p.data
            out = {'uuid': d[0:8].hex()}
            i = 8
            n = d[i]; i += 1
            out['barcode'] = d[i:i + n].decode(errors='replace'); i += n
            n = d[i]; i += 1
            out['serial'] = d[i:i + n].decode(errors='replace'); i += n
            total, used, type_ = struct.unpack('>HHB', d[i:i + 5])
            out.update(total_len=total, used_len=used, type=type_)
            return out
        except Exception:                                 # noqa: BLE001
            return {'raw': p.data.hex()}

    def get_printer_identity(self):
        """
        모델 ID 와 프로토콜 버전을 물어봅니다. (종이 안 씀)

        어느 인쇄 태스크를 써야 하는지 추측 대신 확인할 수 있는 유일한 방법입니다.
          PrinterInfo(0x40, [0x08])   → 모델 ID (big-endian u16)
          PrinterStatusData(0xa5,[1]) → 응답 data[11]*100 + data[12] = 프로토콜 버전

        알려진 모델 ID
          0x1000 B1      protocol 3  → PRINT_TASK 'b1'   203dpi  384dot
          0x1001 B1 Pro  protocol 5  → PRINT_TASK 'v4'   300dpi  567dot
          0x1002 B1 SE   protocol 3  → 'b1'
          0x1200 M2-H    protocol 4  → 'b1'              300dpi  567dot
        """
        out = {'model_id': None, 'protocol': None}
        try:
            p = self._transceive(RequestCodeEnum.GET_INFO, b'\x08',
                                 respoffset=8, retries=3)
            if p is not None and p.data:
                d = p.data
                out['model_id'] = (d[0] << 8) if len(d) == 1 else \
                    int.from_bytes(d[:2], 'big')
        except Exception:                                 # noqa: BLE001
            pass
        try:
            p = self._transceive(0xA5, b'\x01', respoffset=16, retries=3)
            if p is not None and len(p.data) >= 13:
                out['protocol'] = p.data[11] * 100 + p.data[12]
        except Exception:                                 # noqa: BLE001
            pass
        return out

    def heartbeat(self):
        """연결 확인용. 응답이 오면 프린터가 살아 있는 것입니다."""
        p = self._transceive(RequestCodeEnum.HEARTBEAT, b'\x01', retries=4)
        if p is None:
            return None
        d = p.data
        out = {'raw_len': len(d), 'closingstate': None,
               'powerlevel': None, 'paperstate': None}
        if len(d) == 20:
            out['paperstate'] = d[18]
        elif len(d) == 13:
            out.update(closingstate=d[9], powerlevel=d[10], paperstate=d[11])
        elif len(d) == 19:
            out.update(closingstate=d[15], powerlevel=d[16], paperstate=d[17])
        elif len(d) == 10:
            out.update(closingstate=d[8], powerlevel=d[9])
        elif len(d) == 9:
            out['closingstate'] = d[8]
        return out


# --- 참고: 외부 niimprint 가 설치돼 있는지 (정보 표시용) ---------------------
#   실제 인쇄는 항상 위의 내장 구현을 씁니다.
_BACKEND = 'builtin'
_NIIMPRINT_PRESENT = False

if NIIMPRINT_PATH and NIIMPRINT_PATH not in sys.path:
    sys.path.insert(0, NIIMPRINT_PATH)

try:
    import niimprint as _niimprint_mod                    # noqa: F401
    _NIIMPRINT_PRESENT = True
except Exception:                                         # noqa: BLE001
    pass


# ============================================================================
# 3. 포트 찾기
# ============================================================================
def list_serial_ports():
    """
    사용 가능한 시리얼 포트 목록. (경로, 설명) 튜플의 리스트.
    Jetson 온보드 UART(ttyTHS*)와 콘솔(ttyS*)은 프린터가 아니므로 제외합니다.
    """
    found = {}
    for p in _list_comports():
        found[p.device] = p.description
    for pat in ('/dev/ttyACM*', '/dev/ttyUSB*', '/dev/niimbot'):
        for path in glob.glob(pat):
            found.setdefault(path, '')
    out = []
    for path, desc in sorted(found.items()):
        base = os.path.basename(path)
        if base.startswith(('ttyTHS', 'ttyS', 'ttyprintk')):
            continue                       # 온보드 UART / 커널 콘솔
        out.append((path, desc))
    return out


def resolve_port(port=None):
    """'auto' 면 후보 중 하나를 고르고, 아니면 그대로 돌려줍니다."""
    port = port or PRINTER_PORT
    if port and port != 'auto':
        return port
    cands = list_serial_ports()
    if not cands:
        raise RuntimeError(
            "시리얼 포트를 찾지 못했습니다.\n"
            "  1) 프린터 전원을 켜고 USB 케이블을 꽂았는지\n"
            "  2) 충전 전용 케이블이 아닌지 (가장 흔한 원인)\n"
            "  3) ls /dev/ttyACM*  로 직접 확인해 보세요")
    # /dev/niimbot(udev 심볼릭 링크) > ttyACM > ttyUSB 순으로 선호
    for pref in ('/dev/niimbot', 'ttyACM', 'ttyUSB'):
        for path, _ in cands:
            if path == pref or os.path.basename(path).startswith(pref):
                return path
    return cands[0][0]


# ---------------------------------------------------------------- 블루투스
def _bluetoothctl(*args, timeout=12):
    try:
        r = subprocess.run(['bluetoothctl', *args], capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout or ''
    except Exception:                                     # noqa: BLE001
        return ''


def bt_candidates():
    """
    페어링된 기기 목록에서 프린터 후보를 찾습니다.
    반환: [{'addr','name','spp','connected'}, ...]  spp=True 인 쪽이 정답입니다.

    ★ B1 계열은 블루투스 주소가 두 개 보입니다. 앞 3바이트가 회전된 형태
      (AA:BB:CC:.. 와 CC:AA:BB:..) 인데, 'Serial Port' UUID 를 가진 쪽으로만
      인쇄가 됩니다. 그래서 UUID 까지 확인합니다.
    """
    out = _bluetoothctl('devices')
    if not out:
        out = _bluetoothctl('paired-devices')
    devs = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0] == 'Device':
            devs.append((parts[1], parts[2] if len(parts) > 2 else ''))

    result = []
    for addr, name in devs:
        info = _bluetoothctl('info', addr)
        result.append({
            'addr': addr,
            'name': name,
            'spp': 'Serial Port' in info,
            'connected': 'Connected: yes' in info,
            'paired': 'Paired: yes' in info,
        })
    return result


def resolve_bt_addr(addr=None):
    """'auto' 면 페어링 목록에서 Serial Port 를 가진 기기를 고릅니다."""
    addr = addr or PRINTER_BT_ADDR
    if addr and addr.lower() != 'auto':
        return addr.upper()

    cands = bt_candidates()
    if not cands:
        raise RuntimeError(
            '페어링된 블루투스 기기가 없습니다.\n'
            '  bluetoothctl 로 먼저 페어링하세요:\n'
            '    bluetoothctl\n'
            '    scan on          (B1... 같은 이름을 찾습니다)\n'
            '    pair  <주소>\n'
            '    trust <주소>\n'
            '    scan off / exit')

    def score(d):
        n = (d['name'] or '').upper()
        s = 0
        if d['spp']:
            s += 100                      # Serial Port 를 가진 쪽이 최우선
        # ★ 설정한 기종(PRINTER_MODEL)과 이름이 정확히 일치하면 가산점.
        #   B1 과 B1 Pro 가 둘 다 페어링돼 있으면 SPP+이름만으로는 동점이라
        #   목록 순서에 따라 엉뚱한 기종이 잡혔습니다. 기기 이름은
        #   'B1 Pro-I108030689' 꼴이므로 '-I' 앞부분을 기종명으로 봅니다.
        model_part = n.split('-I')[0].replace(' ', '').replace('-', '')
        if model_part == PRINTER_MODEL.upper():
            s += 50
        if any(k in n for k in ('B1', 'B21', 'B18', 'D11', 'D110', 'NIIMBOT',
                                'PRO')):
            s += 10
        if d['paired']:
            s += 1
        return s

    best = max(cands, key=score)
    if not best['spp']:
        names = ', '.join(f"{d['addr']}({d['name']})" for d in cands)
        raise RuntimeError(
            'Serial Port UUID 를 가진 기기를 못 찾았습니다.\n'
            f'  후보: {names}\n'
            '  python3 niimbot_photo.py --bt  로 확인한 뒤\n'
            '  PRINTER_BT_ADDR 에 직접 적어 주세요.')
    return best['addr'].upper()


# ---------------------------------------------------------------- 통합 연결
def open_transport(conn=None):
    """PRINTER_CONN 에 따라 알맞은 transport 를 열고 (transport, 설명) 반환."""
    conn = (conn or PRINTER_CONN).lower()
    if conn == 'bluetooth':
        addr = resolve_bt_addr()
        tr = _BluetoothTransport(addr, PRINTER_BT_CHANNEL)
        return tr, f'bluetooth {addr} (ch {tr.channel})'
    port = resolve_port()
    return _SerialTransport(port), f'usb {port}'


def connection_summary():
    """화면/로그에 한 줄로 띄울 연결 설정 요약."""
    if (PRINTER_CONN or '').lower() == 'bluetooth':
        return f'bluetooth {PRINTER_BT_ADDR}'
    return f'usb {PRINTER_PORT}'


# ============================================================================
# 4. 사운드 (numpy 로 wav 를 만들고 aplay 로 재생)
# ============================================================================
_SOUND_PLAYER = None
_SOUND_PATHS = {}
_SOUND_PROCS = []


def _write_wav(path, samples, rate=44100):
    data = np.clip(samples, -1.0, 1.0)
    data = (data * 32000).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


def _make_beep(rate=44100):
    """카운트다운용 짧은 전자음 (1000 Hz, 90 ms)."""
    n = int(rate * 0.09)
    t = np.arange(n) / rate
    tone = np.sin(2 * np.pi * 1000 * t) * 0.6
    tone += np.sin(2 * np.pi * 2000 * t) * 0.15
    env = np.minimum(1.0, np.minimum(t / 0.004, (t[-1] - t) / 0.02))
    return tone * env


def _make_shutter(rate=44100):
    """'찰칵' — 미러가 올라갔다 내려오는 두 번의 클릭."""
    rng = np.random.default_rng(7)

    def click(dur, amp, decay):
        n = int(rate * dur)
        t = np.arange(n) / rate
        noise = np.diff(rng.standard_normal(n), prepend=0.0)   # 간단한 하이패스
        body = np.sin(2 * np.pi * 2600 * t) * 0.35
        return (noise * 0.8 + body) * amp * np.exp(-t / decay)

    total = np.zeros(int(rate * 0.22))
    a = click(0.05, 1.00, 0.010)
    b = click(0.06, 0.65, 0.014)
    total[:len(a)] += a
    off = int(rate * 0.065)
    total[off:off + len(b)] += b
    return total * 0.9


def _make_done(rate=44100):
    """인쇄 완료 알림 (두 음)."""
    seg = []
    for freq, dur in ((880, 0.10), (1320, 0.16)):
        n = int(rate * dur)
        t = np.arange(n) / rate
        env = np.minimum(1.0, np.minimum(t / 0.005, (t[-1] - t) / 0.04))
        seg.append(np.sin(2 * np.pi * freq * t) * 0.5 * env)
    return np.concatenate(seg)


def ensure_sounds(directory=SOUND_DIR):
    """wav 파일이 없으면 만들고, 재생기(aplay/paplay)를 찾아 둡니다."""
    global _SOUND_PLAYER
    if not SOUND_ENABLED:
        return
    os.makedirs(directory, exist_ok=True)
    makers = {'beep': _make_beep, 'shutter': _make_shutter, 'done': _make_done}
    for name, fn in makers.items():
        path = os.path.join(directory, f'{name}.wav')
        if not os.path.exists(path):
            try:
                _write_wav(path, fn())
            except Exception as e:                        # noqa: BLE001
                print(f'[Sound] {name}.wav 생성 실패: {e}')
                continue
        _SOUND_PATHS[name] = path

    for cand in ('aplay', 'paplay', 'afplay'):
        if shutil.which(cand):
            _SOUND_PLAYER = cand
            break
    if _SOUND_PLAYER is None:
        print('[Sound] aplay/paplay 를 찾지 못했습니다 → 무음으로 동작합니다.')
        print('        (설치: sudo apt install alsa-utils)')
    else:
        print(f'[Sound] 준비 완료 ({_SOUND_PLAYER})')


def play(name):
    """논블로킹 재생. 소리가 안 나도 프로그램은 절대 멈추지 않습니다."""
    if not SOUND_ENABLED or _SOUND_PLAYER is None:
        return
    path = _SOUND_PATHS.get(name)
    if not path:
        return
    _SOUND_PROCS[:] = [p for p in _SOUND_PROCS if p.poll() is None]
    if len(_SOUND_PROCS) > 6:
        return
    try:
        cmd = ([_SOUND_PLAYER, '-q', path] if _SOUND_PLAYER == 'aplay'
               else [_SOUND_PLAYER, path])
        _SOUND_PROCS.append(subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    except Exception:                                     # noqa: BLE001
        pass


# ============================================================================
# 5. 프레임 -> 라벨 이미지
# ============================================================================
def label_print_h_mm():
    """실제로 찍을 길이(mm). 용지 길이에서 헤드 오프셋만큼 뺀 값입니다."""
    return max(5.0, LABEL_H_MM - LABEL_TRIM_MM)


def label_size_px(model=None):
    """
    (가로, 세로) 픽셀 크기. 가로는 프린트 헤드 상한으로 잘립니다.

    54mm x 11.811 = 638 px 를 요청해도 b1pro 헤드는 576 dot 이므로
    576 이 반환됩니다. 즉 용지 좌우에 흰 여백이 남습니다.
    잘린 사실은 label_size_requested_px() 와 비교하면 알 수 있습니다.
    """
    model = (model or PRINTER_MODEL).lower()
    max_w = MODEL_MAX_W_PX.get(model, 384)
    w = min(mm2px(LABEL_W_MM), max_w)
    h = mm2px(label_print_h_mm())
    return w, h


def label_size_requested_px():
    """헤드 상한을 적용하기 '전'의 크기. 진단 출력용."""
    return mm2px(LABEL_W_MM), mm2px(label_print_h_mm())


# ★ 캡션 로고 캐시. 한 번 읽어 그레이(L)로 변환해 두고 재사용합니다.
#   실패(파일 없음 등)도 None 으로 캐시해서 경고가 한 번만 나오게 합니다.
_CAPTION_LOGO_CACHE = {}


def _load_caption_logo(filename):
    if filename in _CAPTION_LOGO_CACHE:
        return _CAPTION_LOGO_CACHE[filename]
    logo = None
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            filename)
        img = Image.open(path)
        if 'A' in img.getbands():                 # 투명 배경 → 흰색 합성
            bg = Image.new('RGBA', img.size, (255, 255, 255, 255))
            img = Image.alpha_composite(bg, img.convert('RGBA'))
        logo = ImageOps.autocontrast(img.convert('L'), cutoff=0)
    except Exception as e:
        print(f'[Label] 캡션 로고를 못 읽었습니다({filename}): {e} '
              f'→ 글자만 인쇄합니다')
    _CAPTION_LOGO_CACHE[filename] = logo
    return logo


def frame_to_label(bgr, model=None):
    """
    OpenCV BGR 프레임 -> 인쇄용 1비트 PIL 이미지.
    (54x86 @ 300dpi 기준 576 x 1016)

    처리 순서
      회전 -> (여백/캡션 제외한) 내부 영역에 맞춰 fit -> 흰 캔버스에 합성
      -> 그레이 -> 오토콘트라스트 -> 대비/밝기 -> 언샤프 -> 캡션 -> 디더링
    """
    W, H = label_size_px(model)

    img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    if PHOTO_ROTATE_DEG % 360:
        # PIL 은 반시계 방향이라 부호를 뒤집어 '시계 방향'으로 맞춥니다.
        img = img.rotate(-PHOTO_ROTATE_DEG, expand=True)

    m = max(0, LABEL_MARGIN_PX)
    cap_h = CAPTION_H_PX if CAPTION_ENABLED else 0
    inner_w = max(1, W - 2 * m)
    inner_h = max(1, H - 2 * m - cap_h)

    if PHOTO_FIT == 'contain':
        fitted = ImageOps.pad(img, (inner_w, inner_h), method=Image.LANCZOS,
                              color=(255, 255, 255), centering=(0.5, 0.5))
    else:
        fitted = ImageOps.fit(img, (inner_w, inner_h), method=Image.LANCZOS,
                              centering=(0.5, 0.5))

    canvas = Image.new('RGB', (W, H), (255, 255, 255))
    canvas.paste(fitted, (m, m))

    gray = canvas.convert('L')
    if GAMMA != 1.0:
        lut = [round((i / 255.0) ** GAMMA * 255) for i in range(256)]
        gray = gray.point(lut)
    if AUTO_CONTRAST:
        gray = ImageOps.autocontrast(gray, cutoff=1)
    if CONTRAST_GAIN != 1.0:
        gray = ImageEnhance.Contrast(gray).enhance(CONTRAST_GAIN)
    if BRIGHTNESS_GAIN != 1.0:
        gray = ImageEnhance.Brightness(gray).enhance(BRIGHTNESS_GAIN)
    if UNSHARP_PERCENT > 0:
        # ★ 언샤프 반경도 해상도에 비례해야 같은 정도로 보입니다.
        radius = max(1.0, 1.4 * (PX_PER_MM / 8.0))
        gray = gray.filter(ImageFilter.UnsharpMask(
            radius=radius, percent=UNSHARP_PERCENT, threshold=3))

    if CAPTION_ENABLED and cap_h > 0:
        # 캡션은 cv2 로 그립니다. PIL 기본 폰트는 라벨에 비해 너무 작습니다.
        # ★ v11: 폰트 크기/두께를 헤드 폭에 비례시킵니다.
        arr = np.array(gray)
        band_top = H - m - cap_h
        arr[band_top:H, :] = 255                          # 흰 띠
        text = CAPTION_TEXT or datetime.now().strftime(CAPTION_FORMAT)
        k = W / 384.0
        scale = 0.62 * k
        thick = max(1, int(round(1.0 * k)))
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX,
                                      scale, thick)
        tx = max(2, (W - tw) // 2)
        ty = band_top + (cap_h + th) // 2                 # 띠 세로 중앙 정렬
        cv2.putText(arr, text, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, 0, thick, cv2.LINE_AA)
        gray = Image.fromarray(arr)

        # ── ★ 좌우 로고: [팀 로고]  날짜  [주최 기관 로고] ──────────────
        pad = max(2, int(round(0.6 * PX_PER_MM)))         # 띠 안 여백 0.6mm
        box_w = int(W * CAPTION_LOGO_BOX)
        box_h = cap_h - 2 * pad
        for path, side in ((CAPTION_LOGO_LEFT, 'left'),
                           (CAPTION_LOGO_RIGHT, 'right')):
            logo = _load_caption_logo(path)
            if logo is None:
                continue
            lw, lh = logo.size
            s = min(box_w / lw, box_h / lh)
            logo = logo.resize((max(1, int(lw * s)), max(1, int(lh * s))),
                               Image.LANCZOS)
            lx = (m + pad if side == 'left'
                  else W - m - pad - logo.size[0])
            ly = band_top + (cap_h - logo.size[1]) // 2
            gray.paste(logo, (lx, ly))

    # convert('1') 은 기본이 Floyd-Steinberg 디더링입니다. 사진에는 이게 정답입니다.
    return gray.convert('1')


# ============================================================================
# 6. 백그라운드 인쇄 워커
# ============================================================================
class NiimbotPrintWorker(threading.Thread):
    """
    submit(bgr) 로 프레임을 던져 넣으면 알아서
      PNG 저장 -> 라벨 이미지 변환 -> 프린터 전송
    까지 처리합니다. 메인 루프는 status/message 만 읽어서 화면에 띄우면 됩니다.

    status : 'idle' | 'printing' | 'ok' | 'error'

    ★ 주의: 이 클래스는 threading.Thread 를 상속합니다.
      내부 변수 이름을 `self._stop` 으로 쓰면 안 됩니다.
      Thread._stop() 이 실제 메서드라서, join() 이 그것을 호출할 때
      "TypeError: 'Event' object is not callable" 로 죽습니다.
    """

    def __init__(self, stop_event=None):
        super().__init__(daemon=True, name='PrintWorker')
        self._q = queue.Queue(maxsize=2)
        self._stopping = stop_event or threading.Event()   # ← _stop 금지!
        self._lock = threading.Lock()
        self._status = 'idle'
        self._message = ''
        self._stamp = 0.0
        self._count = 0
        self._transport = None
        self._client = None
        self._port = None
        self._job_done = 0       # ★ 이번 작업에서 지금까지 인쇄된 장수
        self._job_total = 1      # ★ 이번 작업의 총 매수

    # ------------------------------------------------------------ 상태 조회
    def _set(self, status, message=''):
        with self._lock:
            self._status = status
            self._message = message
            self._stamp = time.time()

    @property
    def status(self):
        with self._lock:
            return self._status

    @property
    def message(self):
        with self._lock:
            return self._message

    @property
    def status_age(self):
        with self._lock:
            return time.time() - self._stamp

    @property
    def busy(self):
        return self.status == 'printing'

    @property
    def printed_count(self):
        with self._lock:
            return self._count

    @property
    def job(self):
        """★ (지금까지 인쇄된 장수, 총 매수). 진행 표시용."""
        with self._lock:
            return self._job_done, self._job_total

    # ------------------------------------------------------------ 작업 투입
    def submit(self, bgr, copies=1):
        """큐가 꽉 차 있으면 조용히 무시합니다(연타 방지).

        ★ copies : 같은 이미지를 몇 장 연속 인쇄할지 (1~).
          여러 명이 같이 찍은 사진을 한 사람당 한 장씩 나눠 줄 때 씁니다.
        """
        try:
            self._q.put_nowait((bgr.copy(), max(1, int(copies))))
            return True
        except queue.Full:
            print('[Print] 이미 인쇄 대기 중이라 이번 요청은 건너뜁니다.')
            return False

    # ------------------------------------------------------------ 연결 관리
    def _connect(self):
        if self._client is not None:
            return self._client
        self._transport, desc = open_transport()
        self._client = _PrinterClient(self._transport)
        self._port = desc
        lim = (f'{PRINT_LINES_PER_SEC}줄/초' if PRINT_LINES_PER_SEC
               else '무제한')
        print(f'[Print] 프린터 연결됨: {desc}  (전송 속도 {lim})')
        return self._client

    def _disconnect(self):
        try:
            if self._transport is not None:
                if hasattr(self._transport, 'close'):
                    self._transport.close()
                else:
                    self._transport._serial.close()
        except Exception:                                 # noqa: BLE001
            pass
        self._transport = None
        self._client = None

    # ------------------------------------------------------------ 메인 루프
    def run(self):
        os.makedirs(SAVE_DIR, exist_ok=True)
        while not self._stopping.is_set():
            try:
                item = self._q.get(timeout=0.3)
            except queue.Empty:
                continue
            if item is None:
                break
            bgr, copies = item
            self._handle(bgr, copies)
        self._disconnect()
        print('[Print] 종료')

    def _handle(self, bgr, copies=1):
        copies = max(1, int(copies))
        with self._lock:
            self._job_done, self._job_total = 0, copies

        self._set('printing', '이미지 변환 중')
        tag = datetime.now().strftime('%Y%m%d_%H%M%S')
        try:
            if SAVE_RAW:
                cv2.imwrite(os.path.join(SAVE_DIR, f'{tag}_raw.png'), bgr)
            label = frame_to_label(bgr)
            label_path = os.path.join(SAVE_DIR, f'{tag}_label.png')
            label.save(label_path)
        except Exception as e:                            # noqa: BLE001
            self._set('error', f'이미지 변환 실패: {e}')
            print(f'[Print] 이미지 변환 실패: {e}')
            return

        if not PRINTER_ENABLED:
            self._set('ok', f'저장만 함 → {os.path.basename(label_path)}')
            print(f'[Print] (인쇄 비활성) 저장 완료: {label_path}')
            return

        density = PRINT_DENSITY
        if PRINTER_MODEL.lower() in ('b18', 'd11', 'd110') and density > 3:
            density = 3

        # ★ 같은 라벨을 copies 장 연속 인쇄합니다.
        #   한 장 = 완결된 인쇄 시퀀스(PrintStart~PrintEnd). 프로토콜의
        #   copies 필드에 기대지 않아 어느 펌웨어에서든 확실히 동작합니다.
        #   중간에 한 장이 (2회 재시도 후에도) 실패하면 남은 장수는 포기합니다.
        #   — 용지 걸림/용지 소진 상태에서 계속 밀어 넣지 않기 위해서입니다.
        t_job = time.time()
        for ci in range(1, copies + 1):
            cp = f'{ci}/{copies} ' if copies > 1 else ''
            last_err = None
            printed = False
            for attempt in (1, 2):
                try:
                    self._set('printing', f'{cp}({attempt}/2)')
                    t0 = time.time()
                    client = self._connect()
                    client.print_image(label, density=density)
                    dt = time.time() - t0
                    with self._lock:
                        self._count += 1
                        self._job_done = ci
                    print(f'[Print] {cp}완료 ({dt:.1f}s)  {label_path}')
                    printed = True
                    break
                except Exception as e:                    # noqa: BLE001
                    last_err = e
                    print(f'[Print] {cp}실패({attempt}/2): '
                          f'{type(e).__name__}: {e}')
                    traceback.print_exc()  # ★ 어느 명령에서 죽었는지 보이게
                    self._disconnect()                    # 다음 시도는 새 연결로
                    time.sleep(0.6)
            if not printed:
                self._set('error',
                          f'{cp}{type(last_err).__name__}: {last_err}')
                return
            if ci < copies:
                time.sleep(0.4)        # 라벨 배출/갭 보정 여유

        dt_all = time.time() - t_job
        n = f'{copies}장 ' if copies > 1 else ''
        self._set('ok', f'{n}{dt_all:.1f}s 만에 인쇄 완료')
        play('done')

    def close(self):
        self._stopping.set()
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass


# ============================================================================
# 7. 진단 / 단독 실행
# ============================================================================
def bt_sibling_addrs(addr):
    """
    B1 계열은 블루투스 주소가 여러 개로 보입니다.
    마지막 3바이트는 같고 앞 3바이트만 회전된 형태입니다.
      AA:BB:CC:.. / CC:AA:BB:.. / BB:CC:AA:..
    그중 'Serial Port' UUID 를 가진 하나로만 인쇄가 됩니다.
    """
    parts = addr.upper().split(':')
    if len(parts) != 6:
        return [addr.upper()]
    a, b, c = parts[0], parts[1], parts[2]
    tail = ':'.join(parts[3:])
    out = []
    for head in ((a, b, c), (c, a, b), (b, c, a)):
        cand = ':'.join(head) + ':' + tail
        if cand not in out:
            out.append(cand)
    return out


def bt_pair(addr, timeout=45):
    """
    페어링 + trust 를 자동으로 실행하고 결과를 판정합니다.
      python3 niimbot_photo.py --btpair 06:07:02:17:9B:5B
    """
    if not shutil.which('bluetoothctl'):
        print('bluetoothctl 이 없습니다 → sudo apt install bluez')
        return False

    addr = addr.upper()
    sibs = bt_sibling_addrs(addr)
    print('=' * 62)
    print(f' 페어링: {addr}')
    print('=' * 62)
    if len(sibs) > 1:
        print('이 프린터는 주소가 여러 개로 보일 수 있습니다(앞 3바이트 회전).')
        print('  회전 형태: ' + ', '.join(sibs[1:]))
        print('  지금 주소가 안 되면 위 주소들도 같은 방법으로 시도하세요.\n')

    script = ('power on\n'
              'agent on\n'
              'default-agent\n'
              f'pair {addr}\n')
    try:
        proc = subprocess.Popen(['bluetoothctl'], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        proc.stdin.write(script)
        proc.stdin.flush()
        time.sleep(min(timeout, 20))
        proc.stdin.write(f'trust {addr}\ninfo {addr}\n')
        proc.stdin.flush()
        time.sleep(4)
        proc.stdin.write('exit\n')
        proc.stdin.flush()
        out = proc.communicate(timeout=10)[0] or ''
    except Exception as e:                                # noqa: BLE001
        print(f'실행 실패: {e}')
        return False

    interesting = [ln.strip() for ln in out.splitlines()
                   if any(k in ln for k in ('Pairing', 'Paired', 'Trusted',
                                            'UUID', 'Failed', 'successful',
                                            'AlreadyExists', 'Connected'))]
    for ln in dict.fromkeys(interesting):
        print('  ' + ln)

    print()
    info = _bluetoothctl('info', addr)
    paired = 'Paired: yes' in info
    spp = 'Serial Port' in info
    print(f'  페어링됨      : {"예" if paired else "아니오"}')
    print(f'  Serial Port   : {"예 ★ 이 주소로 인쇄합니다" if spp else "아니오"}')

    if paired and spp:
        print('\n준비 완료. 다음을 실행하세요:')
        print('    python3 niimbot_photo.py --check')
        print('    python3 niimbot_photo.py --ruler')
        return True
    if paired and not spp:
        print('\n페어링은 됐지만 Serial Port UUID 가 없습니다.')
        print('회전 주소들도 페어링해 보세요:')
        for a in sibs[1:]:
            print(f'    python3 niimbot_photo.py --btpair {a}')
        return False
    print('\n페어링 실패.')
    print('  - 프린터 전원이 켜져 있는지')
    print('  - PC/핸드폰이 프린터를 잡고 있지 않은지 (한 번에 한 기기만)')
    print('  - 먼저 검색을 돌려 보세요: python3 niimbot_photo.py --btscan 30')
    return False


def bt_scan(seconds=20):
    """
    ★ BR/EDR(클래식) 검색.

    'PC 에서는 보이는데 Jetson 에서는 안 보인다' 의 가장 흔한 원인:
      Niimbot B1 계열은 **클래식(BR/EDR)** 기기인데, BlueZ 의 기본 검색은
      설정에 따라 **BLE 만** 훑는 경우가 있습니다. 그러면 아무리 기다려도
      목록에 안 뜹니다.

    그래서 검색 전에 transport 를 bredr 로 명시합니다.
    """
    if not shutil.which('bluetoothctl'):
        print('bluetoothctl 이 없습니다 → sudo apt install bluez')
        return False

    print(f'BR/EDR(클래식) 모드로 {seconds}초 검색합니다...')
    print('※ 프린터 전원을 켜 두고, 핸드폰/PC 와 연결돼 있지 않은지 확인하세요.\n')

    script = ('power on\n'
              'agent on\n'
              'menu scan\n'
              'transport bredr\n'
              'back\n'
              'scan on\n')
    try:
        proc = subprocess.Popen(['bluetoothctl'], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        proc.stdin.write(script)
        proc.stdin.flush()
        t_end = time.time() + seconds
        seen = {}
        while time.time() < t_end:
            time.sleep(1.0)
            left = int(t_end - time.time())
            print(f'\r  검색 중... {left:2d}초 남음   발견 {len(seen)}개', end='')
            for d in bt_candidates():
                if d['addr'] not in seen:
                    seen[d['addr']] = d
                    print(f"\r  + {d['addr']}  {d['name']}" + ' ' * 20)
        print('\r' + ' ' * 50 + '\r', end='')
        try:
            proc.stdin.write('scan off\nexit\n')
            proc.stdin.flush()
            proc.wait(timeout=5)
        except Exception:                                 # noqa: BLE001
            proc.kill()
    except Exception as e:                                # noqa: BLE001
        print(f'검색 실패: {e}')
        return False

    print()
    return bt_list()


def bt_list():
    """페어링된 블루투스 기기와 프린터 후보를 보여 줍니다. (종이 안 씀)"""
    print('=' * 62)
    print(' 블루투스 기기 목록')
    print('=' * 62)
    if not shutil.which('bluetoothctl'):
        print('bluetoothctl 이 없습니다 → sudo apt install bluez')
        return False
    cands = bt_candidates()
    if not cands:
        print('페어링된 기기가 없습니다.\n')
        print('  bluetoothctl')
        print('    power on')
        print('    scan on            ← B1... 같은 이름이 뜰 때까지 기다립니다')
        print('    pair  <주소>')
        print('    trust <주소>')
        print('    scan off')
        print('    exit')
        return False
    print(f"{'주소':<20} {'이름':<22} {'SerialPort':<11} {'연결됨'}")
    print('-' * 62)
    for d in cands:
        print(f"{d['addr']:<20} {(d['name'] or '')[:20]:<22} "
              f"{'예 ★' if d['spp'] else '아니오':<11} "
              f"{'예' if d['connected'] else '아니오'}")
    print()
    spp = [d for d in cands if d['spp']]
    if spp:
        print(f"→ 인쇄에 쓸 주소: {spp[0]['addr']}")
        print("   PRINTER_BT_ADDR 는 'auto' 그대로 두면 이걸 자동으로 씁니다.")
    else:
        print('★ Serial Port UUID 를 가진 기기가 없습니다.')
        print('  B1 계열은 주소가 두 개 보입니다(앞 3바이트가 회전된 형태).')
        print('  둘 다 페어링한 뒤 다시 확인해 보세요.')
    return bool(spp)


def check():
    print('=' * 62)
    print(' Niimbot 연결 진단')
    print('=' * 62)

    W, H = label_size_px()
    RW, RH = label_size_requested_px()
    print(f'프리셋        : {LABEL_PRESET}  '
          f'(용지 {LABEL_W_MM} x {LABEL_H_MM} mm, '
          f'인쇄 {LABEL_W_MM} x {label_print_h_mm():.0f} mm, '
          f'트림 {LABEL_TRIM_MM}mm, 회전 {PHOTO_ROTATE_DEG}도)')
    print(f'해상도        : {PRINTER_DPI} dpi  =  {PX_PER_MM:.3f} px/mm')
    print(f'라벨 이미지   : {W} x {H} px')
    if W < RW:
        print(f'  ※ 가로 요청 {RW}px 이 헤드 상한 {W}px 으로 잘렸습니다.')
        print(f'     용지 {LABEL_W_MM}mm 중 {W / PX_PER_MM:.1f}mm 만 인쇄되고 '
              f'좌우에 {(LABEL_W_MM - W / PX_PER_MM) / 2:.1f}mm 씩 여백이 남습니다.')
    print(f'프린터 모델   : {PRINTER_MODEL}   농도 {PRINT_DENSITY}')
    _task_desc = ('PrintStart 9B / PageStart 안씀 / SetPageSize 13B'
                  if PRINT_TASK == 'v4' else
                  'PrintStart 7B / PageStart 사용 / SetPageSize 6B')
    print(f'인쇄 태스크   : {PRINT_TASK}  ({_task_desc})')
    print(f'여백/캡션     : {LABEL_MARGIN_PX}px / {CAPTION_H_PX}px '
          f'({LABEL_MARGIN_MM}mm / {CAPTION_H_MM}mm)')
    print(f'연결 방식     : {PRINTER_CONN}')
    print(f'프로토콜      : v13 (2024/2025 펌웨어 양쪽, 300dpi 대응)')
    print(f'한 장 데이터  : {H * ((W + 7) // 8) / 1024:.0f} KB  ({H}줄)')
    if PRINT_LINES_PER_SEC:
        print(f'전송 속도제한 : {PRINT_LINES_PER_SEC}줄/초 → '
              f'{H / max(PRINT_LINES_PER_SEC, 1):.1f}초 예상')
    else:
        print('전송 속도제한 : 없음')

    conn = (PRINTER_CONN or '').lower()
    if conn == 'bluetooth':
        print()
        if not bt_list():
            return False
        try:
            addr = resolve_bt_addr()
        except Exception as e:                            # noqa: BLE001
            print(f'\n{e}')
            return False
        print(f'\n선택된 주소   : {addr}')
        print('연결을 시도합니다...')
        try:
            tr = _BluetoothTransport(addr, PRINTER_BT_CHANNEL)
        except Exception as e:                            # noqa: BLE001
            print(f'★ {e}')
            return False
        print(f'RFCOMM 연결됨 (채널 {tr.channel})')
    else:
        print(f'pyserial      : '
              f'{"OK" if _SERIAL_OK else "없음 → pip3 install pyserial"}')
        ports = list_serial_ports()
        print(f'\n후보 포트     : {len(ports)}개')
        for pth, d in ports:
            ok = os.access(pth, os.R_OK | os.W_OK)
            print(f'  - {pth:20} {d[:34]:34} {"쓰기가능" if ok else "★권한없음"}')
        if not ports:
            print('  (없음) 프린터 전원 / USB 케이블(데이터용인지) 을 확인하세요.')
            return False
        try:
            port = resolve_port()
            tr = _SerialTransport(port)
        except Exception as e:                            # noqa: BLE001
            print(f'\n연결 실패: {e}')
            return False
        print(f'\n선택된 포트   : {port}')

    print('\n프린터에 heartbeat 를 보냅니다...')
    try:
        cl = _PrinterClient(tr)
        hb = cl.heartbeat()
        ident = cl.get_printer_identity()
        st = cl.get_print_status()
        rf = cl.get_rfid()
    finally:
        tr.close()

    if hb is None:
        print('★ 응답이 없습니다.')
        if conn == 'bluetooth':
            print('   - 프린터가 다른 기기(핸드폰 앱)와 연결돼 있지 않은지 확인하세요.')
            print('     한 번에 한 기기만 붙을 수 있습니다.')
            print('   - 주소가 두 개 중 틀린 쪽일 수 있습니다 (--bt 로 확인).')
        else:
            print('   - sudo systemctl disable --now ModemManager')
        return False

    print(f'응답 OK  {hb}')
    if hb.get('powerlevel') is not None:
        print(f'   배터리 레벨 : {hb["powerlevel"]}')
    if hb.get('paperstate') is not None:
        print(f'   용지 상태   : {hb["paperstate"]}')
    print(f'   진행상황 조회(0xA3): {"지원  " + str(st) if st else "미지원"}')

    # ── 모델 ID 로 인쇄 태스크 설정이 맞는지 확인 ────────────────────
    _KNOWN = {
        0x1000: ('B1', 'b1', 203, 384),
        0x1001: ('B1 Pro', 'v4', 300, 567),
        0x1002: ('B1 SE', 'b1', 203, 384),
        0x1200: ('M2-H', 'b1', 300, 567),
    }
    mid, proto = ident.get('model_id'), ident.get('protocol')
    print()
    if mid is None:
        print('모델 ID       : 조회 실패 (설정값을 그대로 씁니다)')
    else:
        known = _KNOWN.get(mid)
        label = known[0] if known else '알 수 없는 모델'
        print(f'모델 ID       : 0x{mid:04x} ({label})'
              + (f'  프로토콜 v{proto}' if proto else ''))
        if known:
            _, want_task, want_dpi, want_head = known
            head_now = MODEL_MAX_W_PX.get(PRINTER_MODEL.lower())
            if want_task != PRINT_TASK:
                print(f'  ★ PRINT_TASK 가 {PRINT_TASK!r} 인데 이 모델은 '
                      f'{want_task!r} 를 써야 합니다.')
                print(f'     그대로 두면 PrintStart 에서 type 219 오류가 납니다.')
            if want_dpi != PRINTER_DPI:
                print(f'  ★ PRINTER_DPI 가 {PRINTER_DPI} 인데 이 모델은 '
                      f'{want_dpi} 입니다.')
            if head_now != want_head:
                print(f'  ★ 헤드 폭이 {head_now} 로 설정돼 있는데 이 모델은 '
                      f'{want_head} dot 입니다.')
            if (want_task == PRINT_TASK and want_dpi == PRINTER_DPI
                    and head_now == want_head):
                print('  설정이 모델과 일치합니다.')

    # ── 롤에 물려 있는 '진짜' 라벨 규격 (종이 안 씀) ──────────────────
    print()
    if rf is None:
        print('라벨 롤       : RFID 를 못 읽음 (구형 롤이거나 미지원)')
    else:
        tot, used = rf.get('total_len'), rf.get('used_len')
        print(f'라벨 롤       : 바코드 {rf.get("barcode")}  '
              f'시리얼 {rf.get("serial")}')
        if isinstance(tot, int) and isinstance(used, int):
            print(f'  남은 라벨   : {tot - used}장  (전체 {tot}장 중 {used}장 사용)')
        print('  ※ 이 숫자는 장수이지 mm 가 아닙니다.')
        print(f'  라벨의 실제 크기는 자로 재서 LABEL_PRESET 을 맞추세요. '
              f'(지금: {LABEL_PRESET})')

    print('\n모든 점검 통과.')
    print('  python3 niimbot_photo.py --ruler   ← 자 눈금으로 해상도 검증')
    print('\n※ 인쇄가 이상하면 PROTOCOL_DEBUG = True 로 두고 --ruler 를 한 번')
    print('  돌린 뒤 로그 전체를 보여 주세요. 명령/응답이 다 찍힙니다.')
    return True


def _self_test(path=None):
    ensure_sounds()
    W, H = label_size_px()
    lim = f'{PRINT_LINES_PER_SEC}줄/초' if PRINT_LINES_PER_SEC else '무제한'
    print(f'라벨 크기: {W} x {H} px  '
          f'({LABEL_W_MM} x {label_print_h_mm():.0f} mm @ {PX_PER_MM:.3f} px/mm, '
          f'{PRINTER_DPI}dpi)  전송 속도 {lim}')

    if path:
        bgr = cv2.imread(path)
        if bgr is None:
            print(f'이미지를 읽을 수 없습니다: {path}')
            return
    else:
        # 16:9 테스트 패턴 (그라데이션 + 격자 + 문구)
        bgr = np.zeros((720, 1280, 3), np.uint8)
        bgr[:] = np.linspace(0, 255, 1280, dtype=np.uint8)[None, :, None]
        for x in range(0, 1280, 80):
            cv2.line(bgr, (x, 0), (x, 720), (0, 0, 0), 2)
        for y in range(0, 720, 80):
            cv2.line(bgr, (0, y), (1280, y), (0, 0, 0), 2)
        cv2.putText(bgr, 'NIIMBOT TEST', (120, 400),
                    cv2.FONT_HERSHEY_DUPLEX, 3.0, (255, 255, 255), 8, cv2.LINE_AA)
        cv2.putText(bgr, 'NIIMBOT TEST', (120, 400),
                    cv2.FONT_HERSHEY_DUPLEX, 3.0, (0, 0, 0), 3, cv2.LINE_AA)

    worker = NiimbotPrintWorker()
    worker.start()
    worker.submit(bgr)
    time.sleep(0.5)
    while worker.busy:
        print(f'  ... {worker.message}')
        time.sleep(1.0)
    print(f'결과: {worker.status} / {worker.message}')
    worker.close()
    worker.join(timeout=5.0)


def ruler_label():
    """
    ★ 해상도 검증 / '어디서 잘렸는지' 측정용 라벨.
      10 mm 마다 굵은 선과 숫자, 5 mm 마다 중간선을 넣고
      오른쪽에는 5mm 간격 파선 띠를 둡니다.

      ★ v11: 선 두께, 폰트 크기, 띠 위치를 전부 해상도/헤드폭 비례로
        계산합니다. 예전엔 384px 기준 픽셀값이 박혀 있어서 576px 에서
        눈금이 어색하게 나왔습니다.
    """
    W, H = label_size_px()
    arr = np.full((H, W), 255, np.uint8)

    k = W / 384.0                       # 헤드 폭 비례 계수
    bar_w = max(8, mm2px(3.8))          # 오른쪽 파선 띠 폭
    bar_r = max(4, mm2px(2.0))          # 오른쪽 여백
    bar_x1 = W - bar_r - bar_w
    bar_x2 = W - bar_r
    long_line_end = max(1, bar_x1 - max(4, int(round(6 * k))))

    thick_major = max(2, int(round(3 * k)))
    thick_mid = max(1, int(round(2 * k)))
    thick_minor = max(1, int(round(1 * k)))
    font_scale = 1.0 * k
    font_thick = max(2, int(round(3 * k)))

    # 오른쪽: 5 mm 간격 파선. 통짜 검정으로 하면 헤드가 과하게 가열됩니다.
    ruler_h = int(label_print_h_mm())
    for mm in range(0, ruler_h, 10):
        y1 = min(H - 1, mm2px(mm))
        y2 = min(H - 1, mm2px(mm + 5))
        cv2.rectangle(arr, (bar_x1, y1), (bar_x2, y2), 0, -1)

    for mm in range(0, ruler_h + 1):
        y = min(H - 1, mm2px(mm))
        if mm % 10 == 0:
            cv2.line(arr, (0, y), (long_line_end, y), 0, thick_major)
            # 마지막 눈금은 아래로 쓸 자리가 없으니 위쪽에 씁니다.
            dy_up = int(round(12 * k))
            dy_dn = int(round(30 * k))
            ty = y - dy_up if y + int(round(32 * k)) >= H else y + dy_dn
            cv2.putText(arr, f'{mm}', (int(round(14 * k)), ty),
                        cv2.FONT_HERSHEY_SIMPLEX, font_scale, 0,
                        font_thick, cv2.LINE_AA)
        elif mm % 5 == 0:
            cv2.line(arr, (0, y), (W // 3, y), 0, thick_mid)
        else:
            cv2.line(arr, (0, y), (W // 8, y), 0, thick_minor)
    return Image.fromarray(arr).convert('1')


def ruler_test(rate=None):
    """
    자 패턴을 인쇄해서 해상도와 전송 완주 여부를 측정합니다.
      python3 niimbot_photo.py --ruler        (설정값 사용)
      python3 niimbot_photo.py --ruler 150    (이번만 150줄/초로)
    """
    os.makedirs(SAVE_DIR, exist_ok=True)
    label = ruler_label()
    path = os.path.join(SAVE_DIR, 'ruler_label.png')
    label.save(path)
    print(f'자 패턴 저장: {path}')

    if not PRINTER_ENABLED:
        print('PRINTER_ENABLED=False 라 인쇄는 건너뜁니다.')
        return True

    lps = rate if rate is not None else PRINT_LINES_PER_SEC
    W, H = label_size_px()
    # 진단용 명령이므로 명령/응답 로그를 항상 켭니다.
    global PROTOCOL_DEBUG
    _dbg_save, PROTOCOL_DEBUG = PROTOCOL_DEBUG, True
    tr, desc = open_transport()
    eta = f'{H / lps:.1f}초 예상' if lps else '속도 무제한'
    print(f'{desc} / {H}줄 ({eta}) 로 인쇄합니다...')
    try:
        _PrinterClient(tr).print_image(label, density=PRINT_DENSITY, rate=rate)
    finally:
        tr.close()
        PROTOCOL_DEBUG = _dbg_save

    print()
    print('─' * 62)
    print(' 인쇄물을 자로 재세요')
    print('─' * 62)
    print(f'  [길이] 첫 장에 온전히 나온 마지막 눈금이 N 이면')
    print(f'         LABEL_TRIM_MM = {LABEL_H_MM} - N + 2  로 고치세요.')
    print(f'         (지금 트림 {LABEL_TRIM_MM}mm → {H}줄 = '
          f'{label_print_h_mm():.0f}mm 인쇄)')
    print(f'         라벨 2장에 걸쳐 나오면 트림이 부족한 것입니다.')
    print(f'  [해상도] 눈금 사이가 실제 10mm 인지 확인하세요.')
    print(f'         전체가 X mm 로 나왔다면 실제 해상도는 {H} / X px/mm 입니다.')
    print(f'  [가로] 왼쪽 끝 ~ 오른쪽 파선 띠 바깥까지가 '
          f'약 {W / PX_PER_MM:.1f}mm 면 헤드 폭 {W}dot 가 맞습니다.')
    print()
    print('  그 밖의 증상')
    print('    · 중간에 가로줄이 비거나 뭉갬 → 무선 간섭. 프린터를 가까이 두세요.')
    print('    · 흐릿함 → PRINT_DENSITY 를 4~5 로 올리세요.')
    print('    · 특정 지점에서 매번 끊김 → PRINT_LINES_PER_SEC 를 150 부터 시험.')
    return True


def solve(s1, mm1, s2, mm2):
    """
    서로 다른 두 전송속도에서의 '잘린 위치'로 프린터의
    소비속도 C(줄/초)와 수신버퍼 B(줄)를 계산합니다.

      P = S*B/(S-C)   →   B = P*(S-C)/S
      두 식을 연립하면 C 가 하나로 정해집니다.
    """
    W, H = label_size_px()
    row_bytes = (W + 7) // 8 + 6         # 줄 데이터 + 패킷 오버헤드
    p1, p2 = mm1 * PX_PER_MM, mm2 * PX_PER_MM
    print(f'입력: {s1}줄/초 → {mm1}mm({p1:.0f}줄) / {s2}줄/초 → {mm2}mm({p2:.0f}줄)')
    if mm1 >= label_print_h_mm() or mm2 >= label_print_h_mm():
        print('\n한쪽이 끝까지 인쇄됐습니다. 그 속도가 이미 안전 구간입니다.')
        ok = s1 if mm1 >= label_print_h_mm() else s2
        print(f'→ PRINT_LINES_PER_SEC = {ok} 로 두세요.')
        return True
    # B = p1*(s1-C)/s1 = p2*(s2-C)/s2  →  C 에 대해 정리
    den = p1 / s1 - p2 / s2
    if abs(den) < 1e-9:
        print('\n두 측정값이 같은 비율이라 분리되지 않습니다. 속도 차이를 더 크게 주세요.')
        return False
    C = (p1 - p2) / den
    B = p1 * (s1 - C) / s1
    if C <= 0 or B <= 0:
        print('\n계산 결과가 물리적으로 말이 안 됩니다(측정 오차일 수 있습니다).')
        print('두 측정 모두 같은 밀도/같은 용지로 다시 재 보세요.')
        return False
    safe = C * 0.75
    print(f'\n프린터 소비속도 C = {C:.0f}줄/초  ({C / PX_PER_MM:.1f} mm/s)')
    print(f'프린터 수신버퍼 B = {B:.0f}줄  ({B * row_bytes / 1024:.1f} KB)')
    print(f'\n→ 권장  PRINT_LINES_PER_SEC = {int(safe // 5 * 5)}  '
          f'(C의 75%, 한 장 {H / max(safe, 1):.1f}초)')
    return True


if __name__ == '__main__':
    argv = sys.argv[1:]
    arg = argv[0] if argv else None
    if arg in ('--check', '-c', 'check'):
        sys.exit(0 if check() else 1)
    if arg in ('--bt', '--bluetooth', 'bt'):
        sys.exit(0 if bt_list() else 1)
    if arg in ('--btscan', 'btscan', '--scan'):
        sec = int(argv[1]) if len(argv) > 1 else 20
        sys.exit(0 if bt_scan(sec) else 1)
    if arg in ('--btpair', 'btpair', '--pair'):
        if len(argv) < 2:
            print('사용법: --btpair 06:07:02:17:9B:5B')
            sys.exit(2)
        sys.exit(0 if bt_pair(argv[1]) else 1)
    if arg in ('--ruler', '-r', 'ruler'):
        r = float(argv[1]) if len(argv) > 1 else None
        sys.exit(0 if ruler_test(r) else 1)
    if arg in ('--solve', 'solve'):
        if len(argv) != 5:
            print('사용법: --solve <속도1> <mm1> <속도2> <mm2>')
            print('  예:   --solve 300 27 150 55')
            sys.exit(2)
        sys.exit(0 if solve(*map(float, argv[1:5])) else 1)
    _self_test(arg)


# """
# niimbot_b1pro.py  (v13)
# =======================
# Niimbot(B1 Pro / B1 / B21 / B18 ...) 라벨 프린터로 '카메라 화면'을 인화하기 위한 모듈.

# 이 파일이 하는 일
#   1) OpenCV BGR 프레임  ->  54x86 mm 라벨용 1비트 이미지(576 x 1016 px) 변환
#   2) 백그라운드 스레드에서 프린터로 전송  (렌더링 루프가 절대 멈추지 않도록)
#   3) 카운트다운 '삐' 소리와 셔터 '찰칵' 소리 wav 자동 생성 + 재생

# 변경 이력
#   v2 - niimprint 패키지 없이도 동작 (프로토콜 내장)
#        원본: https://github.com/AndBondStyle/niimprint · MIT · kjy00302 / AndBondStyle
#      - Thread._stop 이름 충돌 버그 수정 (worker.join() 이 TypeError 로 죽던 문제)
#      - PRINTER_PORT='auto' 가 Jetson 온보드 UART(ttyTHS*)를 건너뛰도록
#      - `--check` 진단 모드
#   v3 - ★ USB 전송 속도 제한. '앞부분만 인쇄되고 나머지 백지' 문제의 해결책.
#      - `--ruler` 자 패턴으로 어디서 잘렸는지 측정
#   v4 - 진행상황(0xA3) 기반 브레이크 추가 (지원하는 모델에서만, 느리게만 조절)
#      - `--ruler <속도>` 로 파일 수정 없이 속도 시험
#      - `--solve` 로 두 번의 측정에서 프린터 소비속도와 버퍼 크기를 역산
#   v5 - 페이지 분할 시도 (B1 에서는 두 번째 페이지가 무시되어 실패)
#   v6 - ★ 진행상황 조회(0xA3)가 인쇄를 죽이던 버그 수정.
#        프린터가 오류 응답(type 219)을 주면 _transceive 가 예외를 던지는데,
#        그게 print_image 밖으로 나가 인쇄가 2mm(첫 조회 시점)에서 중단됐습니다.
#        이제 조회는 절대 예외를 내보내지 않고, 기본값도 꺼 두었습니다.
#      - 기본 설정을 '실측으로 검증된 조합'으로: 단일 페이지
#      - PROTOCOL_DEBUG 로 명령/응답 전체 로그
#   v7 - ★ 블루투스(RFCOMM/SPP) 지원. 공식 앱과 같은 경로입니다.
#        PRINTER_CONN='bluetooth' 가 기본값. 주소는 페어링 목록에서 자동 선택
#        (B1 은 주소가 2개인데 Serial Port UUID 를 가진 쪽만 인쇄됩니다).
#      - LABEL_PRESET 한 줄로 용지 규격 전환
#   v8 - 블루투스는 한 줄(=패킷 1개)씩 write. 여러 패킷을 이어 붙이면 RFCOMM
#        프레임 경계에서 프린터가 패킷을 못 맞춰 이미지를 버립니다.
#      - GET_RFID 로 롤 정보 조회 (total_len/used_len 은 mm 가 아니라 '장수')
#   v9 - ★★ 패킷 파서 재동기화 버그 수정 ★★
#        예전 _recv 는 버퍼 맨 앞이 패킷 시작이라고 가정하고 4번째 바이트를
#        길이로 읽었습니다. 앞에 잡음이 있으면 엉뚱한 길이를 읽고 break 해서
#        버퍼가 영영 그 상태로 남고, 그 뒤 모든 응답을 못 봅니다.
#        이제 헤더(55 55)를 먼저 찾아 잡음을 버린 뒤 길이를 읽습니다.
#      - 이미지 전송 중에도 수신 버퍼를 계속 비웁니다(drain).
#   v10 - ★★★ 신형(2024+) 펌웨어 프로토콜 이식 ★★★
#        (a) 줄 헤더의 흑화소 카운트(3분할)를 실제 값으로 계산해 전송.
#            0 으로 보내면 '찍을 게 없다'로 판단해 발열을 건너뜁니다 → 백지.
#        (b) PrintStart 페이로드 7바이트 [totalPages(u16), 0 x4, pageColor(u8)]
#        (c) SetDimension 페이로드 6바이트 [rows(u16), cols(u16), copies(u16)]
#        (d) 전송 후 진행률(0xA3)을 폴링해 '실제 인쇄 완료'를 확인한 뒤에만
#            PrintEnd 전송. 일찍 보내면 인쇄가 11mm 근처에서 끊깁니다.
#        참고: https://printers.niim.blue/interfacing/print-tasks/
#   v11 - ★★★ B1 Pro (300dpi) 대응 ★★★
#        B1(203dpi, 8px/mm, 헤드 384dot) 전제로 박혀 있던 값들을 전부
#        해상도에서 유도하도록 바꿨습니다.
#        (a) PRINTER_DPI 한 줄로 203 <-> 300 전환. PX_PER_MM 은 여기서 계산.
#            300dpi → 11.811 px/mm (정수가 아닙니다. 8 이 아닙니다)
#        (b) MODEL_MAX_W_PX 에 'b1pro': 576 추가 (2인치 300dpi 헤드 = 48.8mm)
#        (c) 여백 / 캡션 높이 / 캡션 폰트 / 자 눈금 두께를 전부 mm 또는
#            헤드 폭 비례로 계산. 예전엔 px 로 박혀 있어서 해상도를 올리면
#            여백이 사라지고 글씨가 콩알만 해졌습니다.
#        (d) 줄 헤더 흑화소 카운트를 0~255 로 clip. 헤드가 넓어지면
#            구간 합이 255 를 넘어 uint8 이 조용히 wrap 될 수 있었습니다.
#        (e) _PRESETS 키/값 불일치 수정 ('54x86' 키에 (50,90,90) 이 들어가
#            있었고, LABEL_PRESET='50x90' 은 아예 KeyError 였습니다)
#        (f) 회전각 오타 수정: 54x86 프리셋의 세 번째 값은 '회전각'입니다.
#            86 이 들어가 있어서 사진이 86도 기울어졌습니다 → 90.
#        ★ 아직 실측으로 확정하지 못한 값: 헤드 폭 576dot.
#          --ruler 를 한 번 찍어 실제 mm 를 재고, 아래 '해상도 검증' 참고.

#   v12 - ★★★ B1 Pro 인쇄 시퀀스(v4 태스크) 대응 ★★★
#        v11 은 B1 Pro 에서 PrintStart 부터 오류 응답(type 219)을 받고 죽었습니다.
#        원인: B1 Pro 는 B1 과 '명령 형식 자체'가 다릅니다.
#        (a) PRINT_TASK 도입. 'b1'(2024년 기종) / 'v4'(2025년 기종)
#            - PrintStart  : 7바이트 → 9바이트 (speed, flag 추가)
#            - SetPageSize : 6바이트 → 13바이트 (절단/분할 필드 7바이트 추가)
#            - PageStart(0x03) 를 아예 보내지 않음
#            - SetPageSize 뒤에 응답 안 기다리는 PrintStatus
#            - PrintEnd 뒤에 응답 안 기다리는 Heartbeat
#        (b) 헤드 폭 정정: 576 → 567 dot. (300dpi x 48mm = 566.9)
#            유효 인쇄폭은 B1 과 같은 48mm 입니다. 54mm 용지 기준 좌우 3mm 여백.
#        (c) 흑화소 카운트 3분할 기준을 '이미지 폭의 1/3' 에서
#            '헤드 바이트 수 // 3' 으로 변경. 384dot 에서는 둘이 같았지만
#            567dot 에서는 어긋나 세로 줄무늬가 생길 수 있습니다.
#        (d) --check 에서 모델 ID(0x40)와 프로토콜 버전(0xa5)을 직접 조회해
#            PRINT_TASK / DPI / 헤드 폭 설정이 맞는지 검증합니다.
#        (e) type 219 오류 메시지에 오류 코드와 원인 후보를 함께 출력.
#        출처: https://printers.niim.blue/interfacing/print-tasks/
#              https://printers.niim.blue/interfacing/proto/

# 명령
#   python3 niimbot_photo.py --btscan [초]    ★ 클래식(BR/EDR) 검색 — 안 잡힐 때
#   python3 niimbot_photo.py --btpair <주소>  페어링 + trust 자동 실행
#   python3 niimbot_photo.py --bt             블루투스 기기 목록 (종이 안 씀)
#   python3 niimbot_photo.py --check          연결 진단 (종이 안 씀)
#   python3 niimbot_photo.py --ruler [속도]   자 패턴 인쇄 → 길이/해상도 측정
#   python3 niimbot_photo.py --solve S1 mm1 S2 mm2   안전 속도 계산
#   python3 niimbot_photo.py [사진.jpg]       실제 인화 시험

# 해상도 규칙
#   - 203dpi 모델(B1/B18/B21) : 7.992 px/mm, 헤드 384 dot (48 mm)
#   - 300dpi 모델(B1 Pro)     : 11.811 px/mm, 헤드 576 dot (48.8 mm)
#   - 이미지의 '가로'가 헤드 폭, '세로'가 용지가 나가는 길이입니다.
#   - 54x86 mm @ 300dpi → 638 x 1016 px 를 요청하지만 가로는 헤드 상한
#     576 으로 잘립니다. 즉 54mm 용지에 48.8mm 폭으로 인쇄되고 좌우에
#     2.6mm 씩 흰 여백이 남습니다. 이건 정상입니다.

# 해상도 검증 (--ruler 인쇄물을 자로 재세요)
#   - 세로: 눈금 80 자리까지가 실제로 80mm 면 OK.
#           X mm 로 나왔다면 실제 해상도 = (86 * PX_PER_MM) / X px/mm
#   - 가로: 왼쪽 끝 ~ 오른쪽 파선 띠 바깥쪽까지가 약 46mm 면 576dot 가 맞습니다.
#           더 짧으면 MODEL_MAX_W_PX['b1pro'] 를 줄이세요.
# """

# import enum
# import glob
# import math
# import os
# import queue
# import shutil
# import socket
# import struct
# import subprocess
# import sys
# import threading
# import time
# import traceback
# import wave
# from datetime import datetime

# import cv2
# import numpy as np
# from PIL import Image, ImageEnhance, ImageFilter, ImageOps

# # ============================================================================
# # 1. 설정  (여기만 바꾸면 됩니다)
# # ============================================================================

# # --- 프린터 ---------------------------------------------------------------
# PRINTER_ENABLED = True       # False 면 PNG 로만 저장하고 인쇄는 건너뜁니다(테스트용)
# PRINTER_MODEL   = 'b1pro'    # 'b1pro' | 'b1' | 'b18' | 'b21' | 'd11' | 'd110'

# # ★★ 해상도. 이 한 줄이 아래 모든 픽셀 계산의 기준입니다 ★★
# #   B1 Pro  → 300
# #   B1/B21/B18/D11 → 203
# PRINTER_DPI     = 300

# # ★★ 인쇄 시퀀스(태스크). 모델마다 명령 형식이 다릅니다 ★★
# #   'b1' : 2024년 출시 기종 (B1, B21, D11 ...)
# #          PrintStart 7바이트 / PageStart 사용 / SetPageSize 6바이트
# #   'v4' : 2025년 출시 기종 (B1 Pro, B21 Pro, D110_M, D11_H ...)
# #          PrintStart 9바이트 / PageStart 안 씀 / SetPageSize 13바이트
# #          + SetPageSize 뒤에 응답 안 기다리는 PrintStatus
# #          + PrintEnd 뒤에 응답 안 기다리는 Heartbeat
# #
# #   ★ 'b1' 형식을 B1 Pro 에 보내면 PrintStart 에서 오류 응답(type 219)이
# #     돌아오고 인쇄가 시작조차 되지 않습니다. 그게 이 값이 필요한 이유입니다.
# #   출처: https://printers.niim.blue/interfacing/print-tasks/
# PRINT_TASK      = 'v4'       # 'v4' | 'b1'

# PRINT_SPEED     = 0          # v4 태스크의 PrintStart 에만 들어갑니다.
#                              #   0 = 느리고 화질 좋게 (사진은 이쪽)
#                              #   1 = 빠르고 화질 낮게

# PRINT_BT_DUMMY_PACKETS = True
#                              # 일부 Pro 기종은 블루투스에서 PrintStart /
#                              # PrintEnd 직후 패킷 하나를 통째로 흘려버립니다.
#                              # 그 자리에 '응답을 안 기다리는' 더미 패킷을 넣어
#                              # 진짜 명령이 희생되지 않게 합니다. (부작용 없음)

# # --- 연결 방식 -------------------------------------------------------------
# PRINTER_CONN    = 'bluetooth'   # 'bluetooth' | 'usb'
#                                 # 공식 앱과 같은 경로(블루투스)를 기본값으로 둡니다.

# # 블루투스
# PRINTER_BT_ADDR = 'auto'     # 'auto' = 페어링된 기기 중에서 자동 선택
#                              # 직접 적으려면 'E2:E1:08:03:09:87' 형식
#                              # ★ B1 계열은 주소가 두 개입니다 (앞 3바이트가 회전된 형태).
#                              #   'Serial Port' UUID 를 가진 쪽만 인쇄가 됩니다.
#                              #   `python3 niimbot_photo.py --bt` 로 확인하세요.
# PRINTER_BT_CHANNEL = 0       # 0 = 자동 탐색 (1번부터 순서대로 시도)

# # USB (PRINTER_CONN='usb' 일 때만)
# PRINTER_PORT    = 'auto'     # '/dev/ttyACM0' 처럼 직접 지정해도 됩니다.
#                              # 'auto' 는 ttyACM* → ttyUSB* 순으로 찾고,
#                              # Jetson 온보드 UART(ttyTHS*)는 후보에서 제외합니다.

# PRINT_DENSITY   = 3          # 1~5
#                              # ★ 300dpi 는 도트가 작아서 같은 농도라도 203dpi 보다
#                              #   연하게 나옵니다. B1(203dpi)에서 2로 낮춰 쓰셨더라도
#                              #   B1 Pro 에서는 3부터 다시 잡으시는 게 맞습니다.
#                              #   너무 진하면 2, 흐리면 4로.

# PRINT_LABEL_TYPE = 2        # 용지 종류. 프린터가 이 값으로 갭 감지 방식을 정합니다.
#                              #   1  간격지(갭)   ← 일반 라벨. 기본값
#                              #   2  블랙마크
#                              #   3  연속지(갭 없음)
#                              #   4  타공지
#                              #   5  투명지
#                              #   6  PVC 태그
#                              #   10 블랙마크 갭
#                              #   11 열수축 튜브
#                              # ★ 실제 용지와 안 맞으면 PrintStart 에서
#                              #   PaperOutException(코드 8) 이 납니다.
#                              #   맞는 값을 모르면 1 → 3 → 2 순으로 바꿔 가며 시험하세요.

# # --- 전송 속도 제한 --------------------------------------------------------
# #
# #   v10 부터 기본 해제(0) 입니다. 신형 프로토콜에서는 속도 제한 없이
# #   한 장이 완주하는 것을 실측으로 확인했습니다.
# #
# #   ★ 다만 300dpi 로 올리면 데이터량이 (11.811/8)^2 ≈ 2.2배로 늘어납니다.
# #     54x86 한 장 = 1016줄 x 72바이트 ≈ 73KB.
# #     앞부분만 찍히고 잘리는 증상이 재발하면 `--ruler 150` 부터 시험하세요.
# #
# #   과거 기록 (B1 203dpi 실측):
# #     - 프린터 소비속도 약 100~120줄/초 (= 13~15 mm/s), 수신 버퍼 약 2.6~4.3KB
# #     - 300dpi 로 환산하면 대략 150~180줄/초가 소비속도, 안전 구간은 그 75%
# PRINT_LINES_PER_SEC = 0      # 초당 몇 줄씩 흘려보낼지 (0 이면 제한 없음)
# PRINT_CHUNK_LINES   = 0      # 한 번에 몇 줄씩 write 할지.
#                              # 0 = 연결 방식에 맞춰 자동
#                              #     블루투스 → 1줄 (패킷 경계로 정확히 자름)
#                              #     USB      → 8줄
#                              # ★ 블루투스(RFCOMM)는 프레임 단위로 전달되는데,
#                              #   여러 패킷을 이어 붙여 보내면 프린터 펌웨어가
#                              #   프레임 경계에서 패킷을 못 맞춰 이미지 줄을
#                              #   통째로 버리는 경우가 있습니다. (빈 라벨 증상)

# # --- 페이지 분할 (실험용) --------------------------------------------------
# #   신형 프로토콜에서는 단일 페이지가 정상 동작하므로 기본 False.
# #   ★ 켜면 totalPages 의미상 라벨이 페이지 수만큼 따로따로 나뉘어 나올 수
# #     있으니 켜지 마세요.
# PRINT_MULTIPAGE      = False
# PRINT_MAX_PAGE_LINES = 448   # 한 페이지 최대 줄수
# PRINT_PAGE_Y_RESET   = True  # 페이지마다 줄 번호를 0부터 다시 매길지
# PRINT_PAGE_GAP_SEC   = 0.05  # 페이지 사이 간격

# PRINT_USE_STATUS   = False   # 전송 '중' 진행률 브레이크 (v10 에서는 불필요.
#                              # 전송 '후' 완료 대기 폴링은 항상 동작합니다)
# PRINT_MAX_LEAD_MM  = 3       # 프린터보다 최대 몇 mm 앞서 나갈지.

# # --- niimprint 패키지 위치 (선택) -----------------------------------------
# #   이 파일에 프로토콜이 내장돼 있습니다. niimprint 가 설치돼 있어도
# #   **내장 구현을 씁니다.**
# NIIMPRINT_PATH  = ''

# # --- 용지 / 이미지 ---------------------------------------------------------
# # ── 인쇄 영역 프리셋 (한 줄만 바꾸면 전환됩니다) ──────────────────────────
# #   세 번째 값은 '사진 회전각(도)' 입니다. 용지 길이가 아닙니다.
# #   세로로 긴 라벨은 16:9 카메라 화면을 90도 돌려 넣는 게 맞습니다.
# LABEL_PRESET    = '54x86'
# _PRESETS = {           # 프리셋 이름: (가로mm, 세로mm, 사진 회전각)
#     '54x86': (54, 86, 90),      # B1 Pro 포토 용지
#     '50x90': (50, 90, 90),      # 구 B1 포토 용지
#     '50x60': (50, 60, 0),
# }
# if LABEL_PRESET not in _PRESETS:
#     raise KeyError(f"LABEL_PRESET '{LABEL_PRESET}' 이(가) _PRESETS 에 없습니다. "
#                    f"가능한 값: {list(_PRESETS)}")
# LABEL_W_MM, LABEL_H_MM, _PRESET_ROT = _PRESETS[LABEL_PRESET]

# # ★★ 실제로 찍을 수 있는 길이는 용지 길이보다 짧습니다 ★★
# #   프린터는 갭 센서로 라벨 시작을 감지한 뒤, 센서에서 프린트 헤드까지의
# #   거리만큼 종이를 더 보낸 다음 찍기 시작합니다. 그래서 인쇄는 항상
# #   라벨 위 가장자리보다 몇 mm 아래에서 시작하고, 용지 길이 전체를 보내면
# #   남는 만큼이 그대로 다음 장으로 넘어갑니다. (라벨 2장이 나오는 증상)
# #
# #   여기에 '용지 길이 - 실제 인쇄 가능 길이' 를 넣으세요.
# #   측정법: --ruler 를 찍고 첫 장에 온전히 나온 마지막 눈금이 N 이면
# #           LABEL_TRIM_MM = LABEL_H_MM - N + 2  (2는 안전 여유)
# LABEL_TRIM_MM   = 0.0

# # ★ 해상도에서 유도. 300dpi = 11.811 px/mm (정수가 아닙니다)
# PX_PER_MM       = PRINTER_DPI / 25.4

# # 모델별 프린트 헤드 폭 (도트 수)
# #   b1/b18/b21 : 203dpi, 384 dot = 48.0 mm
# #   b1pro      : 300dpi, 567 dot = 48.0 mm   ← 576 아니고 567 입니다.
# #                (300dpi x 48mm = 566.9 → 567. 유효 인쇄폭은 B1 과 같은 48mm)
# MODEL_MAX_W_PX  = {'b1pro': 567, 'b1': 384, 'b18': 384, 'b21': 384,
#                    'd11': 96, 'd110': 96}

# PHOTO_ROTATE_DEG = _PRESET_ROT   # 프리셋이 정해 줍니다. 직접 90/180/270 으로
#                                  # 덮어써도 됩니다. 인화물이 거꾸로면 +180.
# PHOTO_FIT       = 'cover'    # 'cover' = 꽉 채우고 남는 부분 잘라냄
#                              # 'contain' = 다 보이게 넣고 여백은 흰색

# # ★ v11: 여백/캡션은 px 가 아니라 mm 로 지정합니다.
# #   해상도를 바꿔도 물리적 크기가 그대로 유지됩니다.
# LABEL_MARGIN_MM = 0.8        # 라벨 가장자리 흰 여백
# CAPTION_H_MM    = 4.0        # 하단 캡션 띠 높이

# LABEL_MARGIN_PX = int(round(LABEL_MARGIN_MM * PX_PER_MM))
# CAPTION_H_PX    = int(round(CAPTION_H_MM * PX_PER_MM))

# # --- 흑백 변환 튜닝 (열전사는 회색이 없어서 디더링이 전부입니다) ------------
# GAMMA           = 0.70
# AUTO_CONTRAST   = False
# CONTRAST_GAIN   = 1.00       # 1.0 = 그대로
# BRIGHTNESS_GAIN = 1.00
# UNSHARP_PERCENT = 60         # 0 이면 샤프닝 안 함. 디더링 전 윤곽을 살려줍니다.
#                              # ★ 300dpi 는 도트가 촘촘해 디더링 노이즈가 덜 보입니다.
#                              #   결과가 거칠면 100 정도로 낮춰 보세요.

# # --- 캡션(하단 글자) -------------------------------------------------------
# CAPTION_ENABLED = True
# CAPTION_FORMAT  = '%Y-%m-%d  %H:%M'     # strftime 형식
# CAPTION_TEXT    = ''                    # 비우면 날짜/시간, 채우면 그 문구 고정

# # --- 저장 / 사운드 ---------------------------------------------------------
# SAVE_DIR        = 'captures'  # 원본 캡처와 라벨 이미지를 여기에 남깁니다.
# SAVE_RAW        = True
# SOUND_ENABLED   = True
# SOUND_DIR       = 'sounds'

# # --- 문제 추적용 -----------------------------------------------------------
# #   True 로 두면 프린터에 보낸 명령과 받은 응답을 전부 찍습니다.
# PROTOCOL_DEBUG  = False


# def mm2px(mm):
#     """밀리미터 -> 픽셀(도트). 해상도가 바뀌어도 이 함수만 쓰면 안전합니다."""
#     return int(round(mm * PX_PER_MM))


# # ============================================================================
# # 2. Niimbot 프로토콜  — 내장 구현 (niimprint 없이도 동작)
# #    원본 niimprint(MIT) 를 기반으로 하되 아래를 고쳤습니다.
# #      (a) _packed_rows 를 numpy.packbits 로     (145ms -> 4ms)
# #      (b) _recv 의 무한 루프 방어               (v9 주석 참고)
# #      (c) 신형(2024+) 펌웨어 프로토콜            (v10 주석 참고)
# #      (d) 흑화소 카운트 clip                     (v11 주석 참고)
# # ============================================================================
# try:
#     import serial
#     from serial.tools.list_ports import comports as _list_comports
#     _SERIAL_OK = True
#     _SERIAL_ERR = ''
# except Exception as e:                                    # noqa: BLE001
#     serial = None
#     _list_comports = lambda: []                           # noqa: E731
#     _SERIAL_OK = False
#     _SERIAL_ERR = str(e)


# class RequestCodeEnum(enum.IntEnum):
#     GET_INFO = 64            # 0x40
#     HEARTBEAT = 220          # 0xDC
#     SET_LABEL_TYPE = 35      # 0x23
#     SET_LABEL_DENSITY = 33   # 0x21
#     START_PRINT = 1          # 0x01
#     END_PRINT = 243          # 0xF3
#     START_PAGE_PRINT = 3     # 0x03
#     END_PAGE_PRINT = 227     # 0xE3
#     SET_DIMENSION = 19       # 0x13
#     GET_PRINT_STATUS = 163   # 0xA3
#     GET_RFID = 26            # 0x1A
#     CANCEL_PRINT = 218       # 0xDA


# # 프린터가 type 219(0xDB) 로 돌려주는 오류 코드.
# # 출처: niimbluelib src/packets/payloads.ts PrinterErrorCode
# PRINTER_ERROR_CODES = {
#     0x01: '커버 열림',
#     0x02: '용지 없음',
#     0x03: '배터리 부족',
#     0x04: '배터리 이상',
#     0x05: '사용자 취소',
#     0x06: '데이터 오류',
#     0x07: '과열',
#     0x08: '용지 배출 이상 (용지가 감지되지 않거나 용지 종류가 안 맞음)',
#     0x09: '프린터 사용 중',
#     0x0a: '프린트 헤드 없음',
#     0x0b: '온도 낮음',
#     0x0c: '프린트 헤드 헐거움',
#     0x0d: '리본 없음',
#     0x0e: '리본 종류 오류',
#     0x0f: '사용한 리본',
#     0x10: '용지 종류 불일치',
#     0x11: '용지 설정 실패',
#     0x12: '인쇄 모드 설정 실패',
#     0x13: '농도 설정 실패',
#     0x14: 'RFID 쓰기 실패',
#     0x15: '여백 설정 실패',
#     0x16: '통신 이상',
#     0x17: '연결 끊김',
#     0x18: '캔버스 파라미터 오류',
#     0x19: '회전 파라미터 이상',
#     0x1a: 'JSON 파라미터 이상',
#     0x1c: '용지 확인 필요',
#     0x1d: 'RFID 태그 미기록',
#     0x1e: '농도 설정 미지원',
#     0x1f: '인쇄 모드 미지원',
#     0x20: '라벨 재질 설정 오류',
#     0x21: '라벨 재질 설정 미지원',
#     0x22: 'RFID 쓰기 미지원',
#     0x32: '잘못된 페이지',
#     0x33: '잘못된 리본 페이지',
#     0x34: '수신 타임아웃',
#     0x35: '비정품 리본',
# }

# LABEL_TYPE_NAMES = {
#     1: '간격지(갭)', 2: '블랙마크', 3: '연속지', 4: '타공지',
#     5: '투명지', 6: 'PVC 태그', 10: '블랙마크 갭', 11: '열수축 튜브',
# }


# class _Packet:
#     """55 55 | type | len | data... | checksum | AA AA"""

#     __slots__ = ('type', 'data')

#     def __init__(self, type_, data):
#         self.type = type_
#         self.data = data

#     @classmethod
#     def from_bytes(cls, pkt):
#         assert pkt[:2] == b"\x55\x55"
#         assert pkt[-2:] == b"\xaa\xaa"
#         type_, len_ = pkt[2], pkt[3]
#         data = pkt[4:4 + len_]
#         checksum = type_ ^ len_
#         for i in data:
#             checksum ^= i
#         assert checksum == pkt[-3]
#         return cls(type_, data)

#     def to_bytes(self):
#         checksum = self.type ^ len(self.data)
#         for i in self.data:
#             checksum ^= i
#         return bytes((0x55, 0x55, self.type, len(self.data),
#                       *self.data, checksum, 0xAA, 0xAA))

#     def __repr__(self):
#         return f'<Packet type={self.type} len={len(self.data)}>'


# class _SerialTransport:
#     def __init__(self, port):
#         if not _SERIAL_OK:
#             raise RuntimeError(f'pyserial 을 불러올 수 없습니다: {_SERIAL_ERR}\n'
#                                f'  → pip3 install pyserial')
#         self._serial = serial.Serial(port=port, baudrate=115200, timeout=0.5)

#     def read(self, length):
#         return self._serial.read(length)

#     def write(self, data):
#         n = self._serial.write(data)
#         # write_timeout 이 걸려 있으면 일부만 쓰이고 조용히 넘어갈 수 있습니다.
#         # 그러면 인쇄물 중간이 통째로 비는데 원인을 찾기가 아주 어렵습니다.
#         if n is not None and n != len(data):
#             raise IOError(f'시리얼 쓰기가 잘렸습니다: {n}/{len(data)} 바이트')
#         return n

#     def flush(self):
#         """커널 송신 버퍼가 실제로 다 나갈 때까지 기다립니다(속도 제한의 전제)."""
#         try:
#             self._serial.flush()
#         except Exception:                                 # noqa: BLE001
#             pass

#     def drain(self):
#         """수신 버퍼에 쌓인 것을 비차단으로 비웁니다. 버린 바이트 수 반환."""
#         try:
#             n = getattr(self._serial, 'in_waiting', 0)
#             if n:
#                 self._serial.read(n)
#                 return n
#         except Exception:                                 # noqa: BLE001
#             pass
#         return 0

#     def close(self):
#         try:
#             self._serial.close()
#         except Exception:                                 # noqa: BLE001
#             pass


# class _BluetoothTransport:
#     """
#     RFCOMM(SPP) 소켓. 공식 앱이 쓰는 것과 같은 경로입니다.

#     USB(CDC) 와의 차이
#       - 링크 자체가 느리고, send() 가 상대 버퍼에 맞춰 실제로 블로킹됩니다.
#         즉 흐름 제어가 링크 레벨에서 걸리므로 USB 처럼 데이터가 통째로
#         사라지는 일이 잘 없습니다.
#       - 대신 무선이라 간섭에 약합니다. 인쇄 도중 끊기면 라벨이 반만 나옵니다.
#     """

#     def __init__(self, address, channel=0, timeout=0.6, connect_timeout=12.0):
#         if not hasattr(socket, 'AF_BLUETOOTH'):
#             raise RuntimeError('이 파이썬은 블루투스 소켓을 지원하지 않습니다.')
#         chans = [channel] if channel else list(range(1, 11))
#         last = None
#         for ch in chans:
#             sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
#                                  socket.BTPROTO_RFCOMM)
#             try:
#                 sock.settimeout(connect_timeout)
#                 sock.connect((address, ch))
#                 sock.settimeout(timeout)
#                 self._timeout = timeout
#                 self._sock = sock
#                 self.channel = ch
#                 self.address = address
#                 return
#             except Exception as e:                        # noqa: BLE001
#                 last = e
#                 try:
#                     sock.close()
#                 except Exception:                         # noqa: BLE001
#                     pass
#         raise RuntimeError(
#             f'블루투스 연결 실패 ({address}): {last}\n'
#             f'  1) 페어링됐는지:  bluetoothctl paired-devices\n'
#             f'  2) 주소가 맞는지: python3 niimbot_photo.py --bt\n'
#             f'     (B1 계열은 주소가 2개인데 Serial Port UUID 를 가진 쪽만 됩니다)\n'
#             f'  3) 프린터 전원 / 다른 기기와 연결돼 있지 않은지')

#     def read(self, length):
#         try:
#             return self._sock.recv(length)
#         except (socket.timeout, TimeoutError):
#             return b''
#         except OSError:
#             return b''

#     def write(self, data):
#         self._sock.sendall(data)          # 전부 보내거나 예외
#         return len(data)

#     def flush(self):
#         pass                              # sendall 이 이미 다 보낸 뒤 반환

#     def drain(self):
#         """
#         ★★ 블루투스에서 '빈 라벨'이 나오는 문제 방지 장치 ★★

#         인쇄 중에 우리는 소켓을 읽지 않습니다. USB(CDC) 는 호스트 커널이
#         수신 엔드포인트를 알아서 계속 비워 주므로 문제가 없지만,
#         RFCOMM 은 크레딧 기반 흐름 제어라 **받는 쪽이 안 읽으면 크레딧이
#         바닥나고, 그러면 프린터의 송신 루틴이 블로킹**됩니다.
#         그 상태로 프린터 펌웨어가 멈추면 들어오는 이미지 줄을 처리하지
#         못하고 통째로 흘려버립니다. 종이는 명령대로 나오니 결과는 백지.

#         그래서 이미지를 보내는 동안에도 주기적으로 수신 버퍼를 비웁니다.
#         """
#         total = 0
#         try:
#             self._sock.setblocking(False)
#             while True:
#                 b = self._sock.recv(4096)
#                 if not b:
#                     break
#                 total += len(b)
#         except (BlockingIOError, InterruptedError):
#             pass
#         except (socket.timeout, TimeoutError):
#             pass
#         except OSError:
#             pass
#         finally:
#             try:
#                 self._sock.settimeout(self._timeout)
#             except Exception:                             # noqa: BLE001
#                 pass
#         return total

#     def close(self):
#         try:
#             self._sock.close()
#         except Exception:                                 # noqa: BLE001
#             pass


# class _PrinterClient:
#     def __init__(self, transport):
#         self._transport = transport
#         self._packetbuf = bytearray()

#     # ------------------------------------------------------------ 하이레벨
#     def print_image(self, image, density=3, verbose=True, rate=None):
#         """
#         라벨 한 장을 인쇄합니다. (신형 2024+ 펌웨어 프로토콜)

#         신형 인쇄 시퀀스 (https://printers.niim.blue):
#           SetDensity → SetLabelType → PrintStart[totalPages u16, 0x4, color]
#           → (페이지마다) PageStart → SetPageSize[rows, cols, copies]
#                          → 줄 데이터(흑화소 카운트 포함) → PageEnd
#           → 진행률(0xA3) 폴링으로 완료 확인 → PrintEnd
#         """
#         width = image.width
#         packed, counts = self._packed_rows(
#             image, MODEL_MAX_W_PX.get(PRINTER_MODEL.lower(), width))
#         total = int(packed.shape[0])

#         # ── 페이지 나누기 (신형 프로토콜에서는 단일 페이지로 충분) ──────
#         if PRINT_MULTIPAGE and total > PRINT_MAX_PAGE_LINES:
#             n_pages = int(math.ceil(total / float(PRINT_MAX_PAGE_LINES)))
#             base = total // n_pages
#             sizes = [base] * n_pages
#             for i in range(total - base * n_pages):
#                 sizes[i] += 1
#         else:
#             sizes = [total]

#         head_px = MODEL_MAX_W_PX.get(PRINTER_MODEL.lower(), width)

#         self.set_label_density(density)
#         self.set_label_type(PRINT_LABEL_TYPE)
#         self.start_print(len(sizes))       # 총 페이지 수를 함께 통보
#         if PRINT_TASK == 'v4' and PRINT_BT_DUMMY_PACKETS:
#             # PrintStart 직후 첫 패킷을 흘리는 기종 대비용 더미.
#             # 응답을 기다리지 않으므로 지원하지 않는 모델에서도 무해합니다.
#             self._send_no_wait(RequestCodeEnum.GET_PRINT_STATUS, b'\x01')

#         if PRINT_CHUNK_LINES:
#             chunk = max(1, int(PRINT_CHUNK_LINES))
#         else:
#             # 블루투스는 1줄씩(=패킷 하나씩), USB 는 8줄씩 묶어 보냅니다.
#             chunk = 1 if isinstance(self._transport, _BluetoothTransport) else 8
#         lps = float(rate if rate is not None else (PRINT_LINES_PER_SEC or 0))
#         use_status = bool(PRINT_USE_STATUS)
#         lead = max(8, mm2px(PRINT_MAX_LEAD_MM))
#         status_fail = 0
#         brake_time = 0.0
#         drained = 0

#         if verbose and len(sizes) > 1:
#             print(f'[Print] {total}줄을 {len(sizes)}페이지로 분할: '
#                   + ' + '.join(f'{n}줄({n / PX_PER_MM:.0f}mm)' for n in sizes))

#         t_all = time.time()
#         offset = 0
#         for pi, n_lines in enumerate(sizes):
#             # ★ v4 태스크는 PageStart(0x03) 를 쓰지 않습니다.
#             if PRINT_TASK != 'v4':
#                 self.start_page_print()
#             # 페이지마다 그 페이지의 줄수로 크기를 다시 알려 줍니다.
#             self.set_dimension(n_lines, width)
#             if PRINT_TASK == 'v4':
#                 # 공식 앱과 같은 순서: SetPageSize 직후 응답을 기다리지 않는
#                 # PrintStatus 를 한 번 던집니다.
#                 self._send_no_wait(RequestCodeEnum.GET_PRINT_STATUS, b'\x01')

#             t0 = time.time()
#             for i in range(0, n_lines, chunk):
#                 hi = min(i + chunk, n_lines)
#                 parts = []
#                 for k in range(i, hi):
#                     y = k if PRINT_PAGE_Y_RESET else offset + k
#                     # 흑화소 카운트(3분할)를 실제 값으로 보냅니다.
#                     # 신형 펌웨어는 이 값으로 발열량을 정하므로 0 이면 백지.
#                     c0, c1, c2 = counts[offset + k]
#                     parts.append(_Packet(
#                         0x85,
#                         struct.pack('>H3BB', y, int(c0), int(c1), int(c2), 1)
#                         + packed[offset + k].tobytes()).to_bytes())
#                 self._transport.write(b''.join(parts))
#                 if hasattr(self._transport, 'flush'):
#                     self._transport.flush()
#                 # 프린터가 보내오는 것을 계속 비워 줍니다.
#                 # 안 비우면 블루투스에서 프린터가 막혀 이미지를 버립니다.
#                 if hasattr(self._transport, 'drain'):
#                     drained += self._transport.drain()

#                 # (1) 고정 속도 페이싱 (기본 꺼짐. 잘릴 때만 켜세요)
#                 if lps > 0:
#                     wait = t0 + hi / lps - time.time()
#                     if wait > 0:
#                         time.sleep(wait)

#                 # (2) 진행 상황 기반 브레이크 (느리게만 조절)
#                 if use_status and hi % (chunk * 2) == 0 and hi < n_lines:
#                     printed = self._printed_lines(n_lines)
#                     if printed is None:
#                         status_fail += 1
#                         if status_fail >= 2:
#                             use_status = False
#                             if verbose:
#                                 print('[Print] 이 모델은 진행상황 조회(0xA3)를 '
#                                       '지원하지 않아 고정 속도만 씁니다.')
#                         continue
#                     status_fail = 0
#                     t_brake = time.time()
#                     while hi - printed > lead:
#                         time.sleep(0.05)
#                         if time.time() - t_brake > 15.0:      # 안전 탈출
#                             use_status = False
#                             break
#                         nxt = self._printed_lines(n_lines)
#                         if nxt is None:
#                             break
#                         printed = nxt
#                     brake_time += time.time() - t_brake

#             self.end_page_print()
#             offset += n_lines
#             if pi < len(sizes) - 1 and PRINT_PAGE_GAP_SEC > 0:
#                 time.sleep(PRINT_PAGE_GAP_SEC)

#         dt = time.time() - t_all
#         if verbose:
#             extra = f', 대기 {brake_time:.1f}s' if brake_time > 0.05 else ''
#             print(f'[Print] 이미지 전송 {dt:.1f}s  '
#                   f'({total}줄 / {len(sizes)}페이지, '
#                   f'실측 {total / max(dt, 1e-6):.0f}줄/초'
#                   + (f', 설정 {lps:.0f}줄/초' if lps > 0 else ', 속도 무제한')
#                   + f'{extra})')
#             print(f'[Print] 전송 중 프린터가 보낸 데이터: {drained} 바이트'
#                   + ('  ← 안 비우면 여기서 막힙니다' if drained else
#                      '  (프린터가 아무것도 안 보냄)'))

#         # ── 인쇄가 '실제로' 끝날 때까지 진행률(0xA3)을 폴링한 뒤에만
#         #    PrintEnd 를 보냅니다. 전송 완료 != 인쇄 완료입니다.
#         #    (일찍 종료 명령을 보내면 신형 펌웨어는 인쇄를 그 자리에서
#         #     끊어 버립니다. '11mm 만 찍히고 배출' 증상이 그것이었습니다)
#         if verbose:
#             print('[Print] 인쇄 완료 대기 중 (진행률 폴링)...')
#         t_poll = time.time()
#         miss = 0
#         done_seen = 0
#         last_state = None
#         while time.time() - t_poll < 60.0:
#             time.sleep(0.3)
#             st = self.get_print_status()
#             if st is None:
#                 miss += 1
#                 if miss >= 5:
#                     break               # 조회 미지원 → 그냥 종료 시퀀스로
#                 continue
#             miss = 0

#             if st['state'] is not None and st['state'] != last_state:
#                 last_state = st['state']
#                 if last_state not in (0, 1):
#                     name = PRINTER_ERROR_CODES.get(last_state, '알 수 없음')
#                     print(f'[Print] ★ 프린터 상태 {last_state} — {name}')
#                     if last_state == 0x08:
#                         print('[Print]   보낸 줄 수가 라벨 길이를 넘었습니다. '
#                               'LABEL_TRIM_MM 을 늘리세요 '
#                               f'(지금 {LABEL_TRIM_MM}mm, '
#                               f'인쇄 길이 {label_print_h_mm():.0f}mm).')

#             # ★★ v13: 완료 판정은 progress1 만 봅니다 ★★
#             #   예전에는 progress1 과 progress2 가 둘 다 100 이 되기를 기다렸는데,
#             #   이 모델은 progress2 를 아예 쓰지 않고 항상 0 입니다.
#             #   그래서 인쇄가 다 끝나도 루프가 안 끝나고 타임아웃까지
#             #   GET_PRINT_STATUS 를 계속 던지는 상태가 됐습니다.
#             if st['progress1'] >= 100 or st['progress2'] >= 100:
#                 done_seen += 1
#                 if done_seen >= 2:      # 두 번 연속 확인되면 확실합니다
#                     break
#             else:
#                 done_seen = 0

#         deadline = time.time() + 30.0
#         while not self.end_print():
#             if time.time() > deadline:
#                 raise TimeoutError('end_print 응답이 오지 않습니다')
#             time.sleep(0.1)

#         if PRINT_TASK == 'v4' and PRINT_BT_DUMMY_PACKETS:
#             # PrintEnd 직후에도 패킷 하나를 흘리는 기종이 있습니다.
#             self._send_no_wait(RequestCodeEnum.HEARTBEAT, b'\x01')

#     @staticmethod
#     def _packed_rows(image, head_px=None):
#         """1비트 이미지 -> (비트팩 배열, 줄별 흑화소 카운트).

#         반환:
#           packed : (h, ceil(w/8)) uint8  — 줄 데이터 비트팩
#           counts : (h, 3) uint8          — 3분할 구간별 검은 픽셀 수

#         신형 펌웨어는 줄 헤더의 흑화소 카운트를 실제로 사용합니다(발열량 결정).
#         0 으로 보내면 '찍을 게 없다'로 판단해 발열을 건너뛰고 용지만 이송합니다.

#         ★ v12: 분할 기준을 '이미지 폭의 1/3' 이 아니라
#           **헤드 바이트 수 // 3** 으로 바꿨습니다. 위키(niimbluelib) 기준이
#           `printhead size / 8 / 3` 바이트이고, 384dot 에서는 둘이 우연히
#           같았지만(16바이트=128px) 567dot 에서는 달라집니다.
#             567dot → 71바이트 → 청크 23바이트(184px) / 23 / 나머지 199px
#           경계가 어긋나면 발열량이 구간별로 틀어져 세로 줄무늬가 생깁니다.
#         """
#         img = ImageOps.invert(image.convert('L')).convert('1')
#         arr = np.array(img, dtype=bool)             # (h, w)  True = 인쇄
#         packed = np.packbits(arr, axis=1)           # (h, ceil(w/8))

#         w = arr.shape[1]
#         head_px = int(head_px or w)
#         head_bytes = (head_px + 7) // 8
#         chunk_px = max(8, (head_bytes // 3) * 8)    # 바이트 경계에 맞춘 구간 폭

#         b0 = min(chunk_px, w)
#         b1 = min(2 * chunk_px, w)
#         counts = np.column_stack([
#             arr[:, 0:b0].sum(axis=1),
#             arr[:, b0:b1].sum(axis=1),
#             arr[:, b1:].sum(axis=1),
#         ])
#         # 헤더에서 각 1바이트이므로 255 가 상한입니다. wrap 되면 그 줄만
#         # 발열량이 엉뚱해져 흐리게 나옵니다.
#         counts = np.clip(counts, 0, 255).astype(np.uint8)
#         return packed, counts

#     # ------------------------------------------------------------ 저수준
#     def _send(self, packet):
#         self._transport.write(packet.to_bytes())

#     def _recv(self):
#         """
#         수신 스트림에서 패킷을 꺼냅니다.

#         ★★ 여기에 심각한 버그가 있었습니다 (v9 에서 수정) ★★
#           예전 코드는 버퍼 맨 앞이 패킷 시작이라고 **가정하고** 4번째 바이트를
#           길이로 읽었습니다. 앞에 잡음이 섞이면 엉뚱한 값을 길이로 읽고,
#           그 값이 남은 바이트보다 크면 'break' 로 빠져나옵니다.
#           그러면 버퍼가 영원히 그 상태로 남아 **그 뒤의 모든 응답을 못 봅니다.**

#         지금은 먼저 헤더(55 55)를 찾아 앞의 잡음을 버리고 나서 길이를 읽습니다.
#         """
#         packets = []
#         buf = self._packetbuf
#         buf.extend(self._transport.read(1024))

#         while True:
#             i = buf.find(b'\x55\x55')
#             if i < 0:
#                 # 헤더가 없음. 다음 조각에서 55 55 가 이어질 수 있으니
#                 # 마지막 1바이트만 남기고 버립니다.
#                 if len(buf) > 1:
#                     del buf[:len(buf) - 1]
#                 break
#             if i > 0:
#                 del buf[:i]                  # 헤더 앞의 잡음 제거
#             if len(buf) < 5:
#                 break                        # 길이 바이트가 아직 안 왔음
#             pkt_len = buf[3] + 7
#             if len(buf) < pkt_len:
#                 break                        # 패킷이 아직 덜 왔음
#             try:
#                 pkt = _Packet.from_bytes(bytes(buf[:pkt_len]))
#             except AssertionError:
#                 del buf[:2]                  # 가짜 헤더 → 건너뛰고 다시 찾음
#                 continue
#             self._log_recv(pkt)
#             packets.append(pkt)
#             del buf[:pkt_len]

#         # 버퍼가 비정상적으로 커지면(응답을 계속 못 맞추는 상황) 잘라 냅니다.
#         if len(buf) > 4096:
#             del buf[:len(buf) - 512]
#         return packets

#     def _log_recv(self, pkt):
#         if PROTOCOL_DEBUG:
#             print(f'  [proto] <- type={pkt.type}(0x{pkt.type:02x}) '
#                   f'{pkt.data.hex()}')

#     def _send_no_wait(self, reqcode, data):
#         """응답을 기다리지 않고 던지기만 하는 명령.

#         v4 태스크에서 공식 앱이 이렇게 보내는 자리가 몇 군데 있습니다.
#         실패해도 인쇄를 막으면 안 되므로 예외를 삼킵니다.
#         """
#         try:
#             if PROTOCOL_DEBUG:
#                 print(f'  [proto] -> (no-wait) 0x{int(reqcode):02x} {data.hex()}')
#             self._send(_Packet(int(reqcode), data))
#         except Exception:                                 # noqa: BLE001
#             pass

#     def _transceive(self, reqcode, data, respoffset=1, retries=6):
#         respcode = respoffset + reqcode
#         if PROTOCOL_DEBUG:
#             name = getattr(RequestCodeEnum(reqcode), 'name', hex(reqcode)) \
#                 if reqcode in [int(x) for x in RequestCodeEnum] else hex(reqcode)
#             print(f'  [proto] -> {name} {data.hex()}')
#         self._send(_Packet(reqcode, data))
#         for _ in range(retries):
#             for packet in self._recv():
#                 if packet.type == 219:
#                     code = packet.data[0] if packet.data else -1
#                     name = PRINTER_ERROR_CODES.get(code, '알 수 없는 코드')
#                     msg = (f'프린터가 오류를 반환했습니다 — {name} '
#                            f'(코드 {code}/0x{code:02x}, 명령 0x{int(reqcode):02x})')
#                     if code in (0x01, 0x02, 0x08, 0x10, 0x1c, 0x11):
#                         msg += (
#                             '\n  이건 프로토콜 문제가 아니라 용지/기구 문제입니다.'
#                             '\n  1) 커버를 열었다가 딱 소리 나게 다시 닫으세요.'
#                             ' (닫을 때 갭 보정이 다시 돕니다)'
#                             '\n  2) 라벨 한 장을 배출구 밖으로 완전히 빼 두세요.'
#                             '\n  3) 감열면(인쇄면)이 아래를 향하게 물렸는지 확인.'
#                             f'\n  4) PRINT_LABEL_TYPE 이 {PRINT_LABEL_TYPE}'
#                             f'({LABEL_TYPE_NAMES.get(PRINT_LABEL_TYPE, "?")})'
#                             ' 인데 실제 용지와 다를 수 있습니다.'
#                             '\n     1,3,2 순으로 바꿔 가며 시험해 보세요.'
#                             '\n  5) 전원을 껐다 켠 뒤 다시 시도.')
#                     raise ValueError(msg)
#                 if packet.type == 0:
#                     raise NotImplementedError('지원하지 않는 명령 (type 0)')
#                 if packet.type == respcode:
#                     return packet
#             time.sleep(0.1)
#         if PROTOCOL_DEBUG:
#             print(f'  [proto] <- (응답 없음, {retries}회 재시도)')
#         return None

#     def _expect(self, name, packet):
#         if packet is None:
#             raise TimeoutError(f'{name}: 프린터가 응답하지 않습니다 '
#                                f'(전원/용지/케이블 확인)')
#         return packet

#     # ------------------------------------------------------------ 명령들
#     def set_label_type(self, n):
#         p = self._expect('set_label_type',
#                          self._transceive(RequestCodeEnum.SET_LABEL_TYPE,
#                                           bytes((n,)), 16))
#         return bool(p.data[0])

#     def set_label_density(self, n):
#         p = self._expect('set_label_density',
#                          self._transceive(RequestCodeEnum.SET_LABEL_DENSITY,
#                                           bytes((n,)), 16))
#         return bool(p.data[0])

#     def start_print(self, total_pages=1):
#         """PrintStart. ★ 페이로드 길이가 모델마다 다릅니다 ★

#           1바이트 : D11, B21, D110 (구형)
#           7바이트 : [totalPages(u16), 0 x4, pageColor(u8)]
#                     → 2024년 기종 (B1, B21, D11)          PRINT_TASK='b1'
#           9바이트 : [totalPages(u16), 0 x4, pageColor(u8), speed(u8), flag(u8)]
#                     → 2025년 기종 (B1 Pro, B21 Pro, D110_M)  PRINT_TASK='v4'

#         형식이 안 맞으면 프린터가 오류 응답(type 219)을 돌려주고 인쇄가
#         시작조차 되지 않습니다. B1 Pro 에 7바이트를 보내면 정확히 그렇습니다.

#         pageColor 는 0 = 흑백. B1 Pro 는 적/흑 2색을 지원하므로 이 바이트가
#         실제 의미를 가지지만, 흑백 인화만 할 거면 0 으로 둡니다.
#         speed 는 0 = 느리고 화질 좋게 / 1 = 빠르고 화질 낮게.
#         """
#         if PRINT_TASK == 'v4':
#             data = struct.pack('>HBBBBBBB',
#                                int(total_pages),        # totalPages
#                                0, 0, 0, 0,              # always 0
#                                0,                       # pageColor
#                                int(PRINT_SPEED) & 0xFF,  # speed
#                                0)                       # some flag
#         else:
#             data = struct.pack('>HBBBBB', int(total_pages), 0, 0, 0, 0, 0)
#         p = self._expect('start_print',
#                          self._transceive(RequestCodeEnum.START_PRINT, data))
#         return bool(p.data[0])

#     def end_print(self):
#         p = self._transceive(RequestCodeEnum.END_PRINT, b'\x01')
#         return bool(p.data[0]) if p else False

#     def start_page_print(self):
#         p = self._expect('start_page_print',
#                          self._transceive(RequestCodeEnum.START_PAGE_PRINT, b'\x01'))
#         return bool(p.data[0])

#     def end_page_print(self):
#         p = self._expect('end_page_print',
#                          self._transceive(RequestCodeEnum.END_PAGE_PRINT, b'\x01'))
#         return bool(p.data[0])

#     def set_dimension(self, rows, cols, copies=1):
#         """SetPageSize. ★ 이것도 길이가 모델마다 다릅니다 ★

#           4바이트  : [rows(u16), cols(u16)]                      (구형)
#           6바이트  : [rows, cols, copies]                        PRINT_TASK='b1'
#           13바이트 : [rows, cols, copies, cutHeight(u16), cutType(u8),
#                      0(u8), sendAll(u8), partHeight(u16)]        PRINT_TASK='v4'

#         cols(가로 도트 수)는 반드시 헤드 폭 이하여야 합니다.
#         v4 의 뒤쪽 7바이트는 절단/분할 관련이고 전부 0 이면 '통짜 한 장'입니다.
#         """
#         base = struct.pack('>HHH', rows, cols, copies)
#         data = base + b'\x00' * 7 if PRINT_TASK == 'v4' else base
#         p = self._expect('set_dimension',
#                          self._transceive(RequestCodeEnum.SET_DIMENSION, data))
#         return bool(p.data[0])

#     def get_print_status(self):
#         """
#         현재 인쇄 진행 상황. 지원하지 않으면 None.

#         ★★ 절대 예외를 밖으로 내보내면 안 됩니다 ★★
#           이 조회는 '있으면 좋은' 보조 기능인데, _transceive 가 프린터의
#           오류 응답(type 219)에 ValueError 를 던지도록 되어 있습니다.
#           그게 print_image 밖으로 튀어나가면 인쇄가 그 자리에서 중단됩니다.
#         """
#         try:
#             p = self._transceive(RequestCodeEnum.GET_PRINT_STATUS, b'\x01',
#                                  respoffset=16, retries=2)
#         except Exception:                                 # noqa: BLE001
#             return None
#         if p is None or len(p.data) < 4:
#             return None
#         page, pr1, pr2 = struct.unpack('>HBB', p.data[:4])
#         # ★ v13: 7번째 바이트는 프린터 상태/오류 코드입니다.
#         #   정상 인쇄 중에는 1, 라벨 끝을 넘어가면 8(용지 배출 이상)로 바뀝니다.
#         state = p.data[6] if len(p.data) >= 7 else None
#         return {'page': page, 'progress1': pr1, 'progress2': pr2,
#                 'state': state, 'raw': p.data.hex()}

#     def _printed_lines(self, total):
#         """
#         '지금까지 몇 줄 찍었나'를 추정합니다.
#         값이 0~100 범위를 벗어나면 이 모델은 다른 의미로 쓰는 것이므로
#         신뢰하지 않고 None 을 돌려줘서 브레이크를 꺼 버립니다.
#         """
#         st = self.get_print_status()
#         if st is None:
#             return None
#         pr = max(st['progress1'], st['progress2'])
#         if not (0 <= pr <= 100):
#             return None
#         return int(pr / 100.0 * total)

#     def get_rfid(self):
#         """
#         롤에 붙은 RFID 태그를 읽습니다. 종이를 전혀 쓰지 않습니다.

#         ★ 주의: total_len / used_len 은 **밀리미터가 아니라 '라벨 장수'** 입니다.
#           라벨의 물리적 크기(mm)는 RFID 로 알 수 없습니다. 자로 재야 합니다.
#         """
#         try:
#             p = self._transceive(RequestCodeEnum.GET_RFID, b'\x01', retries=3)
#         except Exception:                                 # noqa: BLE001
#             return None
#         if p is None or not p.data or p.data[0] == 0:
#             return None
#         try:
#             d = p.data
#             out = {'uuid': d[0:8].hex()}
#             i = 8
#             n = d[i]; i += 1
#             out['barcode'] = d[i:i + n].decode(errors='replace'); i += n
#             n = d[i]; i += 1
#             out['serial'] = d[i:i + n].decode(errors='replace'); i += n
#             total, used, type_ = struct.unpack('>HHB', d[i:i + 5])
#             out.update(total_len=total, used_len=used, type=type_)
#             return out
#         except Exception:                                 # noqa: BLE001
#             return {'raw': p.data.hex()}

#     def get_printer_identity(self):
#         """
#         모델 ID 와 프로토콜 버전을 물어봅니다. (종이 안 씀)

#         어느 인쇄 태스크를 써야 하는지 추측 대신 확인할 수 있는 유일한 방법입니다.
#           PrinterInfo(0x40, [0x08])   → 모델 ID (big-endian u16)
#           PrinterStatusData(0xa5,[1]) → 응답 data[11]*100 + data[12] = 프로토콜 버전

#         알려진 모델 ID
#           0x1000 B1      protocol 3  → PRINT_TASK 'b1'   203dpi  384dot
#           0x1001 B1 Pro  protocol 5  → PRINT_TASK 'v4'   300dpi  567dot
#           0x1002 B1 SE   protocol 3  → 'b1'
#           0x1200 M2-H    protocol 4  → 'b1'              300dpi  567dot
#         """
#         out = {'model_id': None, 'protocol': None}
#         try:
#             p = self._transceive(RequestCodeEnum.GET_INFO, b'\x08',
#                                  respoffset=8, retries=3)
#             if p is not None and p.data:
#                 d = p.data
#                 out['model_id'] = (d[0] << 8) if len(d) == 1 else \
#                     int.from_bytes(d[:2], 'big')
#         except Exception:                                 # noqa: BLE001
#             pass
#         try:
#             p = self._transceive(0xA5, b'\x01', respoffset=16, retries=3)
#             if p is not None and len(p.data) >= 13:
#                 out['protocol'] = p.data[11] * 100 + p.data[12]
#         except Exception:                                 # noqa: BLE001
#             pass
#         return out

#     def heartbeat(self):
#         """연결 확인용. 응답이 오면 프린터가 살아 있는 것입니다."""
#         p = self._transceive(RequestCodeEnum.HEARTBEAT, b'\x01', retries=4)
#         if p is None:
#             return None
#         d = p.data
#         out = {'raw_len': len(d), 'closingstate': None,
#                'powerlevel': None, 'paperstate': None}
#         if len(d) == 20:
#             out['paperstate'] = d[18]
#         elif len(d) == 13:
#             out.update(closingstate=d[9], powerlevel=d[10], paperstate=d[11])
#         elif len(d) == 19:
#             out.update(closingstate=d[15], powerlevel=d[16], paperstate=d[17])
#         elif len(d) == 10:
#             out.update(closingstate=d[8], powerlevel=d[9])
#         elif len(d) == 9:
#             out['closingstate'] = d[8]
#         return out


# # --- 참고: 외부 niimprint 가 설치돼 있는지 (정보 표시용) ---------------------
# #   실제 인쇄는 항상 위의 내장 구현을 씁니다.
# _BACKEND = 'builtin'
# _NIIMPRINT_PRESENT = False

# if NIIMPRINT_PATH and NIIMPRINT_PATH not in sys.path:
#     sys.path.insert(0, NIIMPRINT_PATH)

# try:
#     import niimprint as _niimprint_mod                    # noqa: F401
#     _NIIMPRINT_PRESENT = True
# except Exception:                                         # noqa: BLE001
#     pass


# # ============================================================================
# # 3. 포트 찾기
# # ============================================================================
# def list_serial_ports():
#     """
#     사용 가능한 시리얼 포트 목록. (경로, 설명) 튜플의 리스트.
#     Jetson 온보드 UART(ttyTHS*)와 콘솔(ttyS*)은 프린터가 아니므로 제외합니다.
#     """
#     found = {}
#     for p in _list_comports():
#         found[p.device] = p.description
#     for pat in ('/dev/ttyACM*', '/dev/ttyUSB*', '/dev/niimbot'):
#         for path in glob.glob(pat):
#             found.setdefault(path, '')
#     out = []
#     for path, desc in sorted(found.items()):
#         base = os.path.basename(path)
#         if base.startswith(('ttyTHS', 'ttyS', 'ttyprintk')):
#             continue                       # 온보드 UART / 커널 콘솔
#         out.append((path, desc))
#     return out


# def resolve_port(port=None):
#     """'auto' 면 후보 중 하나를 고르고, 아니면 그대로 돌려줍니다."""
#     port = port or PRINTER_PORT
#     if port and port != 'auto':
#         return port
#     cands = list_serial_ports()
#     if not cands:
#         raise RuntimeError(
#             "시리얼 포트를 찾지 못했습니다.\n"
#             "  1) 프린터 전원을 켜고 USB 케이블을 꽂았는지\n"
#             "  2) 충전 전용 케이블이 아닌지 (가장 흔한 원인)\n"
#             "  3) ls /dev/ttyACM*  로 직접 확인해 보세요")
#     # /dev/niimbot(udev 심볼릭 링크) > ttyACM > ttyUSB 순으로 선호
#     for pref in ('/dev/niimbot', 'ttyACM', 'ttyUSB'):
#         for path, _ in cands:
#             if path == pref or os.path.basename(path).startswith(pref):
#                 return path
#     return cands[0][0]


# # ---------------------------------------------------------------- 블루투스
# def _bluetoothctl(*args, timeout=12):
#     try:
#         r = subprocess.run(['bluetoothctl', *args], capture_output=True,
#                            text=True, timeout=timeout)
#         return r.stdout or ''
#     except Exception:                                     # noqa: BLE001
#         return ''


# def bt_candidates():
#     """
#     페어링된 기기 목록에서 프린터 후보를 찾습니다.
#     반환: [{'addr','name','spp','connected'}, ...]  spp=True 인 쪽이 정답입니다.

#     ★ B1 계열은 블루투스 주소가 두 개 보입니다. 앞 3바이트가 회전된 형태
#       (AA:BB:CC:.. 와 CC:AA:BB:..) 인데, 'Serial Port' UUID 를 가진 쪽으로만
#       인쇄가 됩니다. 그래서 UUID 까지 확인합니다.
#     """
#     out = _bluetoothctl('devices')
#     if not out:
#         out = _bluetoothctl('paired-devices')
#     devs = []
#     for line in out.splitlines():
#         parts = line.split(None, 2)
#         if len(parts) >= 2 and parts[0] == 'Device':
#             devs.append((parts[1], parts[2] if len(parts) > 2 else ''))

#     result = []
#     for addr, name in devs:
#         info = _bluetoothctl('info', addr)
#         result.append({
#             'addr': addr,
#             'name': name,
#             'spp': 'Serial Port' in info,
#             'connected': 'Connected: yes' in info,
#             'paired': 'Paired: yes' in info,
#         })
#     return result


# def resolve_bt_addr(addr=None):
#     """'auto' 면 페어링 목록에서 Serial Port 를 가진 기기를 고릅니다."""
#     addr = addr or PRINTER_BT_ADDR
#     if addr and addr.lower() != 'auto':
#         return addr.upper()

#     cands = bt_candidates()
#     if not cands:
#         raise RuntimeError(
#             '페어링된 블루투스 기기가 없습니다.\n'
#             '  bluetoothctl 로 먼저 페어링하세요:\n'
#             '    bluetoothctl\n'
#             '    scan on          (B1... 같은 이름을 찾습니다)\n'
#             '    pair  <주소>\n'
#             '    trust <주소>\n'
#             '    scan off / exit')

#     def score(d):
#         n = (d['name'] or '').upper()
#         s = 0
#         if d['spp']:
#             s += 100                      # Serial Port 를 가진 쪽이 최우선
#         if any(k in n for k in ('B1', 'B21', 'B18', 'D11', 'D110', 'NIIMBOT',
#                                 'PRO')):
#             s += 10
#         if d['paired']:
#             s += 1
#         return s

#     best = max(cands, key=score)
#     if not best['spp']:
#         names = ', '.join(f"{d['addr']}({d['name']})" for d in cands)
#         raise RuntimeError(
#             'Serial Port UUID 를 가진 기기를 못 찾았습니다.\n'
#             f'  후보: {names}\n'
#             '  python3 niimbot_photo.py --bt  로 확인한 뒤\n'
#             '  PRINTER_BT_ADDR 에 직접 적어 주세요.')
#     return best['addr'].upper()


# # ---------------------------------------------------------------- 통합 연결
# def open_transport(conn=None):
#     """PRINTER_CONN 에 따라 알맞은 transport 를 열고 (transport, 설명) 반환."""
#     conn = (conn or PRINTER_CONN).lower()
#     if conn == 'bluetooth':
#         addr = resolve_bt_addr()
#         tr = _BluetoothTransport(addr, PRINTER_BT_CHANNEL)
#         return tr, f'bluetooth {addr} (ch {tr.channel})'
#     port = resolve_port()
#     return _SerialTransport(port), f'usb {port}'


# def connection_summary():
#     """화면/로그에 한 줄로 띄울 연결 설정 요약."""
#     if (PRINTER_CONN or '').lower() == 'bluetooth':
#         return f'bluetooth {PRINTER_BT_ADDR}'
#     return f'usb {PRINTER_PORT}'


# # ============================================================================
# # 4. 사운드 (numpy 로 wav 를 만들고 aplay 로 재생)
# # ============================================================================
# _SOUND_PLAYER = None
# _SOUND_PATHS = {}
# _SOUND_PROCS = []


# def _write_wav(path, samples, rate=44100):
#     data = np.clip(samples, -1.0, 1.0)
#     data = (data * 32000).astype('<i2')
#     with wave.open(path, 'wb') as w:
#         w.setnchannels(1)
#         w.setsampwidth(2)
#         w.setframerate(rate)
#         w.writeframes(data.tobytes())


# def _make_beep(rate=44100):
#     """카운트다운용 짧은 전자음 (1000 Hz, 90 ms)."""
#     n = int(rate * 0.09)
#     t = np.arange(n) / rate
#     tone = np.sin(2 * np.pi * 1000 * t) * 0.6
#     tone += np.sin(2 * np.pi * 2000 * t) * 0.15
#     env = np.minimum(1.0, np.minimum(t / 0.004, (t[-1] - t) / 0.02))
#     return tone * env


# def _make_shutter(rate=44100):
#     """'찰칵' — 미러가 올라갔다 내려오는 두 번의 클릭."""
#     rng = np.random.default_rng(7)

#     def click(dur, amp, decay):
#         n = int(rate * dur)
#         t = np.arange(n) / rate
#         noise = np.diff(rng.standard_normal(n), prepend=0.0)   # 간단한 하이패스
#         body = np.sin(2 * np.pi * 2600 * t) * 0.35
#         return (noise * 0.8 + body) * amp * np.exp(-t / decay)

#     total = np.zeros(int(rate * 0.22))
#     a = click(0.05, 1.00, 0.010)
#     b = click(0.06, 0.65, 0.014)
#     total[:len(a)] += a
#     off = int(rate * 0.065)
#     total[off:off + len(b)] += b
#     return total * 0.9


# def _make_done(rate=44100):
#     """인쇄 완료 알림 (두 음)."""
#     seg = []
#     for freq, dur in ((880, 0.10), (1320, 0.16)):
#         n = int(rate * dur)
#         t = np.arange(n) / rate
#         env = np.minimum(1.0, np.minimum(t / 0.005, (t[-1] - t) / 0.04))
#         seg.append(np.sin(2 * np.pi * freq * t) * 0.5 * env)
#     return np.concatenate(seg)


# def ensure_sounds(directory=SOUND_DIR):
#     """wav 파일이 없으면 만들고, 재생기(aplay/paplay)를 찾아 둡니다."""
#     global _SOUND_PLAYER
#     if not SOUND_ENABLED:
#         return
#     os.makedirs(directory, exist_ok=True)
#     makers = {'beep': _make_beep, 'shutter': _make_shutter, 'done': _make_done}
#     for name, fn in makers.items():
#         path = os.path.join(directory, f'{name}.wav')
#         if not os.path.exists(path):
#             try:
#                 _write_wav(path, fn())
#             except Exception as e:                        # noqa: BLE001
#                 print(f'[Sound] {name}.wav 생성 실패: {e}')
#                 continue
#         _SOUND_PATHS[name] = path

#     for cand in ('aplay', 'paplay', 'afplay'):
#         if shutil.which(cand):
#             _SOUND_PLAYER = cand
#             break
#     if _SOUND_PLAYER is None:
#         print('[Sound] aplay/paplay 를 찾지 못했습니다 → 무음으로 동작합니다.')
#         print('        (설치: sudo apt install alsa-utils)')
#     else:
#         print(f'[Sound] 준비 완료 ({_SOUND_PLAYER})')


# def play(name):
#     """논블로킹 재생. 소리가 안 나도 프로그램은 절대 멈추지 않습니다."""
#     if not SOUND_ENABLED or _SOUND_PLAYER is None:
#         return
#     path = _SOUND_PATHS.get(name)
#     if not path:
#         return
#     _SOUND_PROCS[:] = [p for p in _SOUND_PROCS if p.poll() is None]
#     if len(_SOUND_PROCS) > 6:
#         return
#     try:
#         cmd = ([_SOUND_PLAYER, '-q', path] if _SOUND_PLAYER == 'aplay'
#                else [_SOUND_PLAYER, path])
#         _SOUND_PROCS.append(subprocess.Popen(
#             cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
#     except Exception:                                     # noqa: BLE001
#         pass


# # ============================================================================
# # 5. 프레임 -> 라벨 이미지
# # ============================================================================
# def label_print_h_mm():
#     """실제로 찍을 길이(mm). 용지 길이에서 헤드 오프셋만큼 뺀 값입니다."""
#     return max(5.0, LABEL_H_MM - LABEL_TRIM_MM)


# def label_size_px(model=None):
#     """
#     (가로, 세로) 픽셀 크기. 가로는 프린트 헤드 상한으로 잘립니다.

#     54mm x 11.811 = 638 px 를 요청해도 b1pro 헤드는 576 dot 이므로
#     576 이 반환됩니다. 즉 용지 좌우에 흰 여백이 남습니다.
#     잘린 사실은 label_size_requested_px() 와 비교하면 알 수 있습니다.
#     """
#     model = (model or PRINTER_MODEL).lower()
#     max_w = MODEL_MAX_W_PX.get(model, 384)
#     w = min(mm2px(LABEL_W_MM), max_w)
#     h = mm2px(label_print_h_mm())
#     return w, h


# def label_size_requested_px():
#     """헤드 상한을 적용하기 '전'의 크기. 진단 출력용."""
#     return mm2px(LABEL_W_MM), mm2px(label_print_h_mm())


# def frame_to_label(bgr, model=None):
#     """
#     OpenCV BGR 프레임 -> 인쇄용 1비트 PIL 이미지.
#     (54x86 @ 300dpi 기준 576 x 1016)

#     처리 순서
#       회전 -> (여백/캡션 제외한) 내부 영역에 맞춰 fit -> 흰 캔버스에 합성
#       -> 그레이 -> 오토콘트라스트 -> 대비/밝기 -> 언샤프 -> 캡션 -> 디더링
#     """
#     W, H = label_size_px(model)

#     img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
#     if PHOTO_ROTATE_DEG % 360:
#         # PIL 은 반시계 방향이라 부호를 뒤집어 '시계 방향'으로 맞춥니다.
#         img = img.rotate(-PHOTO_ROTATE_DEG, expand=True)

#     m = max(0, LABEL_MARGIN_PX)
#     cap_h = CAPTION_H_PX if CAPTION_ENABLED else 0
#     inner_w = max(1, W - 2 * m)
#     inner_h = max(1, H - 2 * m - cap_h)

#     if PHOTO_FIT == 'contain':
#         fitted = ImageOps.pad(img, (inner_w, inner_h), method=Image.LANCZOS,
#                               color=(255, 255, 255), centering=(0.5, 0.5))
#     else:
#         fitted = ImageOps.fit(img, (inner_w, inner_h), method=Image.LANCZOS,
#                               centering=(0.5, 0.5))

#     canvas = Image.new('RGB', (W, H), (255, 255, 255))
#     canvas.paste(fitted, (m, m))

#     gray = canvas.convert('L')
#     if GAMMA != 1.0:
#         lut = [round((i / 255.0) ** GAMMA * 255) for i in range(256)]
#         gray = gray.point(lut)
#     if AUTO_CONTRAST:
#         gray = ImageOps.autocontrast(gray, cutoff=1)
#     if CONTRAST_GAIN != 1.0:
#         gray = ImageEnhance.Contrast(gray).enhance(CONTRAST_GAIN)
#     if BRIGHTNESS_GAIN != 1.0:
#         gray = ImageEnhance.Brightness(gray).enhance(BRIGHTNESS_GAIN)
#     if UNSHARP_PERCENT > 0:
#         # ★ 언샤프 반경도 해상도에 비례해야 같은 정도로 보입니다.
#         radius = max(1.0, 1.4 * (PX_PER_MM / 8.0))
#         gray = gray.filter(ImageFilter.UnsharpMask(
#             radius=radius, percent=UNSHARP_PERCENT, threshold=3))

#     if CAPTION_ENABLED and cap_h > 0:
#         # 캡션은 cv2 로 그립니다. PIL 기본 폰트는 라벨에 비해 너무 작습니다.
#         # ★ v11: 폰트 크기/두께를 헤드 폭에 비례시킵니다.
#         #   예전엔 scale=0.62, thickness=1 이 박혀 있어서 576px 에서는
#         #   글씨가 캡션 띠에 비해 터무니없이 작게 나왔습니다.
#         arr = np.array(gray)
#         arr[H - m - cap_h:H, :] = 255                     # 흰 띠
#         text = CAPTION_TEXT or datetime.now().strftime(CAPTION_FORMAT)
#         k = W / 384.0
#         scale = 0.62 * k
#         thick = max(1, int(round(1.0 * k)))
#         (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX,
#                                       scale, thick)
#         tx = max(2, (W - tw) // 2)
#         ty = H - m - (cap_h - th) // 2 - max(2, int(round(2 * k)))
#         cv2.putText(arr, text, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX,
#                     scale, 0, thick, cv2.LINE_AA)
#         gray = Image.fromarray(arr)

#     # convert('1') 은 기본이 Floyd-Steinberg 디더링입니다. 사진에는 이게 정답입니다.
#     return gray.convert('1')


# # ============================================================================
# # 6. 백그라운드 인쇄 워커
# # ============================================================================
# class NiimbotPrintWorker(threading.Thread):
#     """
#     submit(bgr) 로 프레임을 던져 넣으면 알아서
#       PNG 저장 -> 라벨 이미지 변환 -> 프린터 전송
#     까지 처리합니다. 메인 루프는 status/message 만 읽어서 화면에 띄우면 됩니다.

#     status : 'idle' | 'printing' | 'ok' | 'error'

#     ★ 주의: 이 클래스는 threading.Thread 를 상속합니다.
#       내부 변수 이름을 `self._stop` 으로 쓰면 안 됩니다.
#       Thread._stop() 이 실제 메서드라서, join() 이 그것을 호출할 때
#       "TypeError: 'Event' object is not callable" 로 죽습니다.
#     """

#     def __init__(self, stop_event=None):
#         super().__init__(daemon=True, name='PrintWorker')
#         self._q = queue.Queue(maxsize=2)
#         self._stopping = stop_event or threading.Event()   # ← _stop 금지!
#         self._lock = threading.Lock()
#         self._status = 'idle'
#         self._message = ''
#         self._stamp = 0.0
#         self._count = 0
#         self._transport = None
#         self._client = None
#         self._port = None

#     # ------------------------------------------------------------ 상태 조회
#     def _set(self, status, message=''):
#         with self._lock:
#             self._status = status
#             self._message = message
#             self._stamp = time.time()

#     @property
#     def status(self):
#         with self._lock:
#             return self._status

#     @property
#     def message(self):
#         with self._lock:
#             return self._message

#     @property
#     def status_age(self):
#         with self._lock:
#             return time.time() - self._stamp

#     @property
#     def busy(self):
#         return self.status == 'printing'

#     @property
#     def printed_count(self):
#         with self._lock:
#             return self._count

#     # ------------------------------------------------------------ 작업 투입
#     def submit(self, bgr):
#         """큐가 꽉 차 있으면 조용히 무시합니다(연타 방지)."""
#         try:
#             self._q.put_nowait(bgr.copy())
#             return True
#         except queue.Full:
#             print('[Print] 이미 인쇄 대기 중이라 이번 요청은 건너뜁니다.')
#             return False

#     # ------------------------------------------------------------ 연결 관리
#     def _connect(self):
#         if self._client is not None:
#             return self._client
#         self._transport, desc = open_transport()
#         self._client = _PrinterClient(self._transport)
#         self._port = desc
#         lim = (f'{PRINT_LINES_PER_SEC}줄/초' if PRINT_LINES_PER_SEC
#                else '무제한')
#         print(f'[Print] 프린터 연결됨: {desc}  (전송 속도 {lim})')
#         return self._client

#     def _disconnect(self):
#         try:
#             if self._transport is not None:
#                 if hasattr(self._transport, 'close'):
#                     self._transport.close()
#                 else:
#                     self._transport._serial.close()
#         except Exception:                                 # noqa: BLE001
#             pass
#         self._transport = None
#         self._client = None

#     # ------------------------------------------------------------ 메인 루프
#     def run(self):
#         os.makedirs(SAVE_DIR, exist_ok=True)
#         while not self._stopping.is_set():
#             try:
#                 bgr = self._q.get(timeout=0.3)
#             except queue.Empty:
#                 continue
#             if bgr is None:
#                 break
#             self._handle(bgr)
#         self._disconnect()
#         print('[Print] 종료')

#     def _handle(self, bgr):
#         self._set('printing', '이미지 변환 중')
#         tag = datetime.now().strftime('%Y%m%d_%H%M%S')
#         try:
#             if SAVE_RAW:
#                 cv2.imwrite(os.path.join(SAVE_DIR, f'{tag}_raw.png'), bgr)
#             label = frame_to_label(bgr)
#             label_path = os.path.join(SAVE_DIR, f'{tag}_label.png')
#             label.save(label_path)
#         except Exception as e:                            # noqa: BLE001
#             self._set('error', f'이미지 변환 실패: {e}')
#             print(f'[Print] 이미지 변환 실패: {e}')
#             return

#         if not PRINTER_ENABLED:
#             self._set('ok', f'저장만 함 → {os.path.basename(label_path)}')
#             print(f'[Print] (인쇄 비활성) 저장 완료: {label_path}')
#             return

#         density = PRINT_DENSITY
#         if PRINTER_MODEL.lower() in ('b18', 'd11', 'd110') and density > 3:
#             density = 3

#         last_err = None
#         for attempt in (1, 2):
#             try:
#                 self._set('printing', f'인쇄 중 ({attempt}/2)')
#                 t0 = time.time()
#                 client = self._connect()
#                 client.print_image(label, density=density)
#                 dt = time.time() - t0
#                 with self._lock:
#                     self._count += 1
#                 self._set('ok', f'{dt:.1f}s 만에 인쇄 완료')
#                 print(f'[Print] 완료 ({dt:.1f}s)  {label_path}')
#                 play('done')
#                 return
#             except Exception as e:                        # noqa: BLE001
#                 last_err = e
#                 print(f'[Print] 실패({attempt}/2): {type(e).__name__}: {e}')
#                 traceback.print_exc()      # ★ 어느 명령에서 죽었는지 보이게
#                 self._disconnect()                        # 다음 시도는 새 연결로
#                 time.sleep(0.6)

#         self._set('error', f'{type(last_err).__name__}: {last_err}')

#     def close(self):
#         self._stopping.set()
#         try:
#             self._q.put_nowait(None)
#         except queue.Full:
#             pass


# # ============================================================================
# # 7. 진단 / 단독 실행
# # ============================================================================
# def bt_sibling_addrs(addr):
#     """
#     B1 계열은 블루투스 주소가 여러 개로 보입니다.
#     마지막 3바이트는 같고 앞 3바이트만 회전된 형태입니다.
#       AA:BB:CC:.. / CC:AA:BB:.. / BB:CC:AA:..
#     그중 'Serial Port' UUID 를 가진 하나로만 인쇄가 됩니다.
#     """
#     parts = addr.upper().split(':')
#     if len(parts) != 6:
#         return [addr.upper()]
#     a, b, c = parts[0], parts[1], parts[2]
#     tail = ':'.join(parts[3:])
#     out = []
#     for head in ((a, b, c), (c, a, b), (b, c, a)):
#         cand = ':'.join(head) + ':' + tail
#         if cand not in out:
#             out.append(cand)
#     return out


# def bt_pair(addr, timeout=45):
#     """
#     페어링 + trust 를 자동으로 실행하고 결과를 판정합니다.
#       python3 niimbot_photo.py --btpair 06:07:02:17:9B:5B
#     """
#     if not shutil.which('bluetoothctl'):
#         print('bluetoothctl 이 없습니다 → sudo apt install bluez')
#         return False

#     addr = addr.upper()
#     sibs = bt_sibling_addrs(addr)
#     print('=' * 62)
#     print(f' 페어링: {addr}')
#     print('=' * 62)
#     if len(sibs) > 1:
#         print('이 프린터는 주소가 여러 개로 보일 수 있습니다(앞 3바이트 회전).')
#         print('  회전 형태: ' + ', '.join(sibs[1:]))
#         print('  지금 주소가 안 되면 위 주소들도 같은 방법으로 시도하세요.\n')

#     script = ('power on\n'
#               'agent on\n'
#               'default-agent\n'
#               f'pair {addr}\n')
#     try:
#         proc = subprocess.Popen(['bluetoothctl'], stdin=subprocess.PIPE,
#                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
#                                 text=True)
#         proc.stdin.write(script)
#         proc.stdin.flush()
#         time.sleep(min(timeout, 20))
#         proc.stdin.write(f'trust {addr}\ninfo {addr}\n')
#         proc.stdin.flush()
#         time.sleep(4)
#         proc.stdin.write('exit\n')
#         proc.stdin.flush()
#         out = proc.communicate(timeout=10)[0] or ''
#     except Exception as e:                                # noqa: BLE001
#         print(f'실행 실패: {e}')
#         return False

#     interesting = [ln.strip() for ln in out.splitlines()
#                    if any(k in ln for k in ('Pairing', 'Paired', 'Trusted',
#                                             'UUID', 'Failed', 'successful',
#                                             'AlreadyExists', 'Connected'))]
#     for ln in dict.fromkeys(interesting):
#         print('  ' + ln)

#     print()
#     info = _bluetoothctl('info', addr)
#     paired = 'Paired: yes' in info
#     spp = 'Serial Port' in info
#     print(f'  페어링됨      : {"예" if paired else "아니오"}')
#     print(f'  Serial Port   : {"예 ★ 이 주소로 인쇄합니다" if spp else "아니오"}')

#     if paired and spp:
#         print('\n준비 완료. 다음을 실행하세요:')
#         print('    python3 niimbot_photo.py --check')
#         print('    python3 niimbot_photo.py --ruler')
#         return True
#     if paired and not spp:
#         print('\n페어링은 됐지만 Serial Port UUID 가 없습니다.')
#         print('회전 주소들도 페어링해 보세요:')
#         for a in sibs[1:]:
#             print(f'    python3 niimbot_photo.py --btpair {a}')
#         return False
#     print('\n페어링 실패.')
#     print('  - 프린터 전원이 켜져 있는지')
#     print('  - PC/핸드폰이 프린터를 잡고 있지 않은지 (한 번에 한 기기만)')
#     print('  - 먼저 검색을 돌려 보세요: python3 niimbot_photo.py --btscan 30')
#     return False


# def bt_scan(seconds=20):
#     """
#     ★ BR/EDR(클래식) 검색.

#     'PC 에서는 보이는데 Jetson 에서는 안 보인다' 의 가장 흔한 원인:
#       Niimbot B1 계열은 **클래식(BR/EDR)** 기기인데, BlueZ 의 기본 검색은
#       설정에 따라 **BLE 만** 훑는 경우가 있습니다. 그러면 아무리 기다려도
#       목록에 안 뜹니다.

#     그래서 검색 전에 transport 를 bredr 로 명시합니다.
#     """
#     if not shutil.which('bluetoothctl'):
#         print('bluetoothctl 이 없습니다 → sudo apt install bluez')
#         return False

#     print(f'BR/EDR(클래식) 모드로 {seconds}초 검색합니다...')
#     print('※ 프린터 전원을 켜 두고, 핸드폰/PC 와 연결돼 있지 않은지 확인하세요.\n')

#     script = ('power on\n'
#               'agent on\n'
#               'menu scan\n'
#               'transport bredr\n'
#               'back\n'
#               'scan on\n')
#     try:
#         proc = subprocess.Popen(['bluetoothctl'], stdin=subprocess.PIPE,
#                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
#                                 text=True)
#         proc.stdin.write(script)
#         proc.stdin.flush()
#         t_end = time.time() + seconds
#         seen = {}
#         while time.time() < t_end:
#             time.sleep(1.0)
#             left = int(t_end - time.time())
#             print(f'\r  검색 중... {left:2d}초 남음   발견 {len(seen)}개', end='')
#             for d in bt_candidates():
#                 if d['addr'] not in seen:
#                     seen[d['addr']] = d
#                     print(f"\r  + {d['addr']}  {d['name']}" + ' ' * 20)
#         print('\r' + ' ' * 50 + '\r', end='')
#         try:
#             proc.stdin.write('scan off\nexit\n')
#             proc.stdin.flush()
#             proc.wait(timeout=5)
#         except Exception:                                 # noqa: BLE001
#             proc.kill()
#     except Exception as e:                                # noqa: BLE001
#         print(f'검색 실패: {e}')
#         return False

#     print()
#     return bt_list()


# def bt_list():
#     """페어링된 블루투스 기기와 프린터 후보를 보여 줍니다. (종이 안 씀)"""
#     print('=' * 62)
#     print(' 블루투스 기기 목록')
#     print('=' * 62)
#     if not shutil.which('bluetoothctl'):
#         print('bluetoothctl 이 없습니다 → sudo apt install bluez')
#         return False
#     cands = bt_candidates()
#     if not cands:
#         print('페어링된 기기가 없습니다.\n')
#         print('  bluetoothctl')
#         print('    power on')
#         print('    scan on            ← B1... 같은 이름이 뜰 때까지 기다립니다')
#         print('    pair  <주소>')
#         print('    trust <주소>')
#         print('    scan off')
#         print('    exit')
#         return False
#     print(f"{'주소':<20} {'이름':<22} {'SerialPort':<11} {'연결됨'}")
#     print('-' * 62)
#     for d in cands:
#         print(f"{d['addr']:<20} {(d['name'] or '')[:20]:<22} "
#               f"{'예 ★' if d['spp'] else '아니오':<11} "
#               f"{'예' if d['connected'] else '아니오'}")
#     print()
#     spp = [d for d in cands if d['spp']]
#     if spp:
#         print(f"→ 인쇄에 쓸 주소: {spp[0]['addr']}")
#         print("   PRINTER_BT_ADDR 는 'auto' 그대로 두면 이걸 자동으로 씁니다.")
#     else:
#         print('★ Serial Port UUID 를 가진 기기가 없습니다.')
#         print('  B1 계열은 주소가 두 개 보입니다(앞 3바이트가 회전된 형태).')
#         print('  둘 다 페어링한 뒤 다시 확인해 보세요.')
#     return bool(spp)


# def check():
#     print('=' * 62)
#     print(' Niimbot 연결 진단')
#     print('=' * 62)

#     W, H = label_size_px()
#     RW, RH = label_size_requested_px()
#     print(f'프리셋        : {LABEL_PRESET}  '
#           f'(용지 {LABEL_W_MM} x {LABEL_H_MM} mm, '
#           f'인쇄 {LABEL_W_MM} x {label_print_h_mm():.0f} mm, '
#           f'트림 {LABEL_TRIM_MM}mm, 회전 {PHOTO_ROTATE_DEG}도)')
#     print(f'해상도        : {PRINTER_DPI} dpi  =  {PX_PER_MM:.3f} px/mm')
#     print(f'라벨 이미지   : {W} x {H} px')
#     if W < RW:
#         print(f'  ※ 가로 요청 {RW}px 이 헤드 상한 {W}px 으로 잘렸습니다.')
#         print(f'     용지 {LABEL_W_MM}mm 중 {W / PX_PER_MM:.1f}mm 만 인쇄되고 '
#               f'좌우에 {(LABEL_W_MM - W / PX_PER_MM) / 2:.1f}mm 씩 여백이 남습니다.')
#     print(f'프린터 모델   : {PRINTER_MODEL}   농도 {PRINT_DENSITY}')
#     _task_desc = ('PrintStart 9B / PageStart 안씀 / SetPageSize 13B'
#                   if PRINT_TASK == 'v4' else
#                   'PrintStart 7B / PageStart 사용 / SetPageSize 6B')
#     print(f'인쇄 태스크   : {PRINT_TASK}  ({_task_desc})')
#     print(f'여백/캡션     : {LABEL_MARGIN_PX}px / {CAPTION_H_PX}px '
#           f'({LABEL_MARGIN_MM}mm / {CAPTION_H_MM}mm)')
#     print(f'연결 방식     : {PRINTER_CONN}')
#     print(f'프로토콜      : v13 (2024/2025 펌웨어 양쪽, 300dpi 대응)')
#     print(f'한 장 데이터  : {H * ((W + 7) // 8) / 1024:.0f} KB  ({H}줄)')
#     if PRINT_LINES_PER_SEC:
#         print(f'전송 속도제한 : {PRINT_LINES_PER_SEC}줄/초 → '
#               f'{H / max(PRINT_LINES_PER_SEC, 1):.1f}초 예상')
#     else:
#         print('전송 속도제한 : 없음')

#     conn = (PRINTER_CONN or '').lower()
#     if conn == 'bluetooth':
#         print()
#         if not bt_list():
#             return False
#         try:
#             addr = resolve_bt_addr()
#         except Exception as e:                            # noqa: BLE001
#             print(f'\n{e}')
#             return False
#         print(f'\n선택된 주소   : {addr}')
#         print('연결을 시도합니다...')
#         try:
#             tr = _BluetoothTransport(addr, PRINTER_BT_CHANNEL)
#         except Exception as e:                            # noqa: BLE001
#             print(f'★ {e}')
#             return False
#         print(f'RFCOMM 연결됨 (채널 {tr.channel})')
#     else:
#         print(f'pyserial      : '
#               f'{"OK" if _SERIAL_OK else "없음 → pip3 install pyserial"}')
#         ports = list_serial_ports()
#         print(f'\n후보 포트     : {len(ports)}개')
#         for pth, d in ports:
#             ok = os.access(pth, os.R_OK | os.W_OK)
#             print(f'  - {pth:20} {d[:34]:34} {"쓰기가능" if ok else "★권한없음"}')
#         if not ports:
#             print('  (없음) 프린터 전원 / USB 케이블(데이터용인지) 을 확인하세요.')
#             return False
#         try:
#             port = resolve_port()
#             tr = _SerialTransport(port)
#         except Exception as e:                            # noqa: BLE001
#             print(f'\n연결 실패: {e}')
#             return False
#         print(f'\n선택된 포트   : {port}')

#     print('\n프린터에 heartbeat 를 보냅니다...')
#     try:
#         cl = _PrinterClient(tr)
#         hb = cl.heartbeat()
#         ident = cl.get_printer_identity()
#         st = cl.get_print_status()
#         rf = cl.get_rfid()
#     finally:
#         tr.close()

#     if hb is None:
#         print('★ 응답이 없습니다.')
#         if conn == 'bluetooth':
#             print('   - 프린터가 다른 기기(핸드폰 앱)와 연결돼 있지 않은지 확인하세요.')
#             print('     한 번에 한 기기만 붙을 수 있습니다.')
#             print('   - 주소가 두 개 중 틀린 쪽일 수 있습니다 (--bt 로 확인).')
#         else:
#             print('   - sudo systemctl disable --now ModemManager')
#         return False

#     print(f'응답 OK  {hb}')
#     if hb.get('powerlevel') is not None:
#         print(f'   배터리 레벨 : {hb["powerlevel"]}')
#     if hb.get('paperstate') is not None:
#         print(f'   용지 상태   : {hb["paperstate"]}')
#     print(f'   진행상황 조회(0xA3): {"지원  " + str(st) if st else "미지원"}')

#     # ── 모델 ID 로 인쇄 태스크 설정이 맞는지 확인 ────────────────────
#     _KNOWN = {
#         0x1000: ('B1', 'b1', 203, 384),
#         0x1001: ('B1 Pro', 'v4', 300, 567),
#         0x1002: ('B1 SE', 'b1', 203, 384),
#         0x1200: ('M2-H', 'b1', 300, 567),
#     }
#     mid, proto = ident.get('model_id'), ident.get('protocol')
#     print()
#     if mid is None:
#         print('모델 ID       : 조회 실패 (설정값을 그대로 씁니다)')
#     else:
#         known = _KNOWN.get(mid)
#         label = known[0] if known else '알 수 없는 모델'
#         print(f'모델 ID       : 0x{mid:04x} ({label})'
#               + (f'  프로토콜 v{proto}' if proto else ''))
#         if known:
#             _, want_task, want_dpi, want_head = known
#             head_now = MODEL_MAX_W_PX.get(PRINTER_MODEL.lower())
#             if want_task != PRINT_TASK:
#                 print(f'  ★ PRINT_TASK 가 {PRINT_TASK!r} 인데 이 모델은 '
#                       f'{want_task!r} 를 써야 합니다.')
#                 print(f'     그대로 두면 PrintStart 에서 type 219 오류가 납니다.')
#             if want_dpi != PRINTER_DPI:
#                 print(f'  ★ PRINTER_DPI 가 {PRINTER_DPI} 인데 이 모델은 '
#                       f'{want_dpi} 입니다.')
#             if head_now != want_head:
#                 print(f'  ★ 헤드 폭이 {head_now} 로 설정돼 있는데 이 모델은 '
#                       f'{want_head} dot 입니다.')
#             if (want_task == PRINT_TASK and want_dpi == PRINTER_DPI
#                     and head_now == want_head):
#                 print('  설정이 모델과 일치합니다.')

#     # ── 롤에 물려 있는 '진짜' 라벨 규격 (종이 안 씀) ──────────────────
#     print()
#     if rf is None:
#         print('라벨 롤       : RFID 를 못 읽음 (구형 롤이거나 미지원)')
#     else:
#         tot, used = rf.get('total_len'), rf.get('used_len')
#         print(f'라벨 롤       : 바코드 {rf.get("barcode")}  '
#               f'시리얼 {rf.get("serial")}')
#         if isinstance(tot, int) and isinstance(used, int):
#             print(f'  남은 라벨   : {tot - used}장  (전체 {tot}장 중 {used}장 사용)')
#         print('  ※ 이 숫자는 장수이지 mm 가 아닙니다.')
#         print(f'  라벨의 실제 크기는 자로 재서 LABEL_PRESET 을 맞추세요. '
#               f'(지금: {LABEL_PRESET})')

#     print('\n모든 점검 통과.')
#     print('  python3 niimbot_photo.py --ruler   ← 자 눈금으로 해상도 검증')
#     print('\n※ 인쇄가 이상하면 PROTOCOL_DEBUG = True 로 두고 --ruler 를 한 번')
#     print('  돌린 뒤 로그 전체를 보여 주세요. 명령/응답이 다 찍힙니다.')
#     return True


# def _self_test(path=None):
#     ensure_sounds()
#     W, H = label_size_px()
#     lim = f'{PRINT_LINES_PER_SEC}줄/초' if PRINT_LINES_PER_SEC else '무제한'
#     print(f'라벨 크기: {W} x {H} px  '
#           f'({LABEL_W_MM} x {label_print_h_mm():.0f} mm @ {PX_PER_MM:.3f} px/mm, '
#           f'{PRINTER_DPI}dpi)  전송 속도 {lim}')

#     if path:
#         bgr = cv2.imread(path)
#         if bgr is None:
#             print(f'이미지를 읽을 수 없습니다: {path}')
#             return
#     else:
#         # 16:9 테스트 패턴 (그라데이션 + 격자 + 문구)
#         bgr = np.zeros((720, 1280, 3), np.uint8)
#         bgr[:] = np.linspace(0, 255, 1280, dtype=np.uint8)[None, :, None]
#         for x in range(0, 1280, 80):
#             cv2.line(bgr, (x, 0), (x, 720), (0, 0, 0), 2)
#         for y in range(0, 720, 80):
#             cv2.line(bgr, (0, y), (1280, y), (0, 0, 0), 2)
#         cv2.putText(bgr, 'NIIMBOT TEST', (120, 400),
#                     cv2.FONT_HERSHEY_DUPLEX, 3.0, (255, 255, 255), 8, cv2.LINE_AA)
#         cv2.putText(bgr, 'NIIMBOT TEST', (120, 400),
#                     cv2.FONT_HERSHEY_DUPLEX, 3.0, (0, 0, 0), 3, cv2.LINE_AA)

#     worker = NiimbotPrintWorker()
#     worker.start()
#     worker.submit(bgr)
#     time.sleep(0.5)
#     while worker.busy:
#         print(f'  ... {worker.message}')
#         time.sleep(1.0)
#     print(f'결과: {worker.status} / {worker.message}')
#     worker.close()
#     worker.join(timeout=5.0)


# def ruler_label():
#     """
#     ★ 해상도 검증 / '어디서 잘렸는지' 측정용 라벨.
#       10 mm 마다 굵은 선과 숫자, 5 mm 마다 중간선을 넣고
#       오른쪽에는 5mm 간격 파선 띠를 둡니다.

#       ★ v11: 선 두께, 폰트 크기, 띠 위치를 전부 해상도/헤드폭 비례로
#         계산합니다. 예전엔 384px 기준 픽셀값이 박혀 있어서 576px 에서
#         눈금이 어색하게 나왔습니다.
#     """
#     W, H = label_size_px()
#     arr = np.full((H, W), 255, np.uint8)

#     k = W / 384.0                       # 헤드 폭 비례 계수
#     bar_w = max(8, mm2px(3.8))          # 오른쪽 파선 띠 폭
#     bar_r = max(4, mm2px(2.0))          # 오른쪽 여백
#     bar_x1 = W - bar_r - bar_w
#     bar_x2 = W - bar_r
#     long_line_end = max(1, bar_x1 - max(4, int(round(6 * k))))

#     thick_major = max(2, int(round(3 * k)))
#     thick_mid = max(1, int(round(2 * k)))
#     thick_minor = max(1, int(round(1 * k)))
#     font_scale = 1.0 * k
#     font_thick = max(2, int(round(3 * k)))

#     # 오른쪽: 5 mm 간격 파선. 통짜 검정으로 하면 헤드가 과하게 가열됩니다.
#     ruler_h = int(label_print_h_mm())
#     for mm in range(0, ruler_h, 10):
#         y1 = min(H - 1, mm2px(mm))
#         y2 = min(H - 1, mm2px(mm + 5))
#         cv2.rectangle(arr, (bar_x1, y1), (bar_x2, y2), 0, -1)

#     for mm in range(0, ruler_h + 1):
#         y = min(H - 1, mm2px(mm))
#         if mm % 10 == 0:
#             cv2.line(arr, (0, y), (long_line_end, y), 0, thick_major)
#             # 마지막 눈금은 아래로 쓸 자리가 없으니 위쪽에 씁니다.
#             dy_up = int(round(12 * k))
#             dy_dn = int(round(30 * k))
#             ty = y - dy_up if y + int(round(32 * k)) >= H else y + dy_dn
#             cv2.putText(arr, f'{mm}', (int(round(14 * k)), ty),
#                         cv2.FONT_HERSHEY_SIMPLEX, font_scale, 0,
#                         font_thick, cv2.LINE_AA)
#         elif mm % 5 == 0:
#             cv2.line(arr, (0, y), (W // 3, y), 0, thick_mid)
#         else:
#             cv2.line(arr, (0, y), (W // 8, y), 0, thick_minor)
#     return Image.fromarray(arr).convert('1')


# def ruler_test(rate=None):
#     """
#     자 패턴을 인쇄해서 해상도와 전송 완주 여부를 측정합니다.
#       python3 niimbot_photo.py --ruler        (설정값 사용)
#       python3 niimbot_photo.py --ruler 150    (이번만 150줄/초로)
#     """
#     os.makedirs(SAVE_DIR, exist_ok=True)
#     label = ruler_label()
#     path = os.path.join(SAVE_DIR, 'ruler_label.png')
#     label.save(path)
#     print(f'자 패턴 저장: {path}')

#     if not PRINTER_ENABLED:
#         print('PRINTER_ENABLED=False 라 인쇄는 건너뜁니다.')
#         return True

#     lps = rate if rate is not None else PRINT_LINES_PER_SEC
#     W, H = label_size_px()
#     # 진단용 명령이므로 명령/응답 로그를 항상 켭니다.
#     global PROTOCOL_DEBUG
#     _dbg_save, PROTOCOL_DEBUG = PROTOCOL_DEBUG, True
#     tr, desc = open_transport()
#     eta = f'{H / lps:.1f}초 예상' if lps else '속도 무제한'
#     print(f'{desc} / {H}줄 ({eta}) 로 인쇄합니다...')
#     try:
#         _PrinterClient(tr).print_image(label, density=PRINT_DENSITY, rate=rate)
#     finally:
#         tr.close()
#         PROTOCOL_DEBUG = _dbg_save

#     print()
#     print('─' * 62)
#     print(' 인쇄물을 자로 재세요')
#     print('─' * 62)
#     print(f'  [길이] 첫 장에 온전히 나온 마지막 눈금이 N 이면')
#     print(f'         LABEL_TRIM_MM = {LABEL_H_MM} - N + 2  로 고치세요.')
#     print(f'         (지금 트림 {LABEL_TRIM_MM}mm → {H}줄 = '
#           f'{label_print_h_mm():.0f}mm 인쇄)')
#     print(f'         라벨 2장에 걸쳐 나오면 트림이 부족한 것입니다.')
#     print(f'  [해상도] 눈금 사이가 실제 10mm 인지 확인하세요.')
#     print(f'         전체가 X mm 로 나왔다면 실제 해상도는 {H} / X px/mm 입니다.')
#     print(f'  [가로] 왼쪽 끝 ~ 오른쪽 파선 띠 바깥까지가 '
#           f'약 {W / PX_PER_MM:.1f}mm 면 헤드 폭 {W}dot 가 맞습니다.')
#     print()
#     print('  그 밖의 증상')
#     print('    · 중간에 가로줄이 비거나 뭉갬 → 무선 간섭. 프린터를 가까이 두세요.')
#     print('    · 흐릿함 → PRINT_DENSITY 를 4~5 로 올리세요.')
#     print('    · 특정 지점에서 매번 끊김 → PRINT_LINES_PER_SEC 를 150 부터 시험.')
#     return True


# def solve(s1, mm1, s2, mm2):
#     """
#     서로 다른 두 전송속도에서의 '잘린 위치'로 프린터의
#     소비속도 C(줄/초)와 수신버퍼 B(줄)를 계산합니다.

#       P = S*B/(S-C)   →   B = P*(S-C)/S
#       두 식을 연립하면 C 가 하나로 정해집니다.
#     """
#     W, H = label_size_px()
#     row_bytes = (W + 7) // 8 + 6         # 줄 데이터 + 패킷 오버헤드
#     p1, p2 = mm1 * PX_PER_MM, mm2 * PX_PER_MM
#     print(f'입력: {s1}줄/초 → {mm1}mm({p1:.0f}줄) / {s2}줄/초 → {mm2}mm({p2:.0f}줄)')
#     if mm1 >= label_print_h_mm() or mm2 >= label_print_h_mm():
#         print('\n한쪽이 끝까지 인쇄됐습니다. 그 속도가 이미 안전 구간입니다.')
#         ok = s1 if mm1 >= label_print_h_mm() else s2
#         print(f'→ PRINT_LINES_PER_SEC = {ok} 로 두세요.')
#         return True
#     # B = p1*(s1-C)/s1 = p2*(s2-C)/s2  →  C 에 대해 정리
#     den = p1 / s1 - p2 / s2
#     if abs(den) < 1e-9:
#         print('\n두 측정값이 같은 비율이라 분리되지 않습니다. 속도 차이를 더 크게 주세요.')
#         return False
#     C = (p1 - p2) / den
#     B = p1 * (s1 - C) / s1
#     if C <= 0 or B <= 0:
#         print('\n계산 결과가 물리적으로 말이 안 됩니다(측정 오차일 수 있습니다).')
#         print('두 측정 모두 같은 밀도/같은 용지로 다시 재 보세요.')
#         return False
#     safe = C * 0.75
#     print(f'\n프린터 소비속도 C = {C:.0f}줄/초  ({C / PX_PER_MM:.1f} mm/s)')
#     print(f'프린터 수신버퍼 B = {B:.0f}줄  ({B * row_bytes / 1024:.1f} KB)')
#     print(f'\n→ 권장  PRINT_LINES_PER_SEC = {int(safe // 5 * 5)}  '
#           f'(C의 75%, 한 장 {H / max(safe, 1):.1f}초)')
#     return True


# if __name__ == '__main__':
#     argv = sys.argv[1:]
#     arg = argv[0] if argv else None
#     if arg in ('--check', '-c', 'check'):
#         sys.exit(0 if check() else 1)
#     if arg in ('--bt', '--bluetooth', 'bt'):
#         sys.exit(0 if bt_list() else 1)
#     if arg in ('--btscan', 'btscan', '--scan'):
#         sec = int(argv[1]) if len(argv) > 1 else 20
#         sys.exit(0 if bt_scan(sec) else 1)
#     if arg in ('--btpair', 'btpair', '--pair'):
#         if len(argv) < 2:
#             print('사용법: --btpair 06:07:02:17:9B:5B')
#             sys.exit(2)
#         sys.exit(0 if bt_pair(argv[1]) else 1)
#     if arg in ('--ruler', '-r', 'ruler'):
#         r = float(argv[1]) if len(argv) > 1 else None
#         sys.exit(0 if ruler_test(r) else 1)
#     if arg in ('--solve', 'solve'):
#         if len(argv) != 5:
#             print('사용법: --solve <속도1> <mm1> <속도2> <mm2>')
#             print('  예:   --solve 300 27 150 55')
#             sys.exit(2)
#         sys.exit(0 if solve(*map(float, argv[1:5])) else 1)
#     _self_test(arg)