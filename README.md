# 📸 저전력 CNN 가속기 기반 제스처 사진기

> FPGA 기반 저전력 사람 감지 CNN 가속기와 Jetson 기반 제스처 인식을 결합한 **비접촉식 AI 사진 촬영 시스템**
> **Team. 하나둘셋찰칵** | 대한상공회의소 서울기술교육센터 | 2026.08.20

<br>

## 📌 프로젝트 개요

**저전력 CNN 가속기 기반 제스처 사진기**는 무인 시스템의 상시 대기 전력과 비접촉 조작 문제를 개선하기 위해, **FPGA 기반 사람 감지 CNN 가속기**와 **Jetson Orin Nano 기반 제스처 인식 AI**를 역할별로 분리한 시스템입니다.

대기 상태에서는 Pcam 영상을 Z7-20 FPGA에서 입력받아 **128×128 영상으로 전처리한 뒤 INT8 CNN 가속기를 이용해 사람의 유무를 판단**합니다.

사람이 감지되면 GPIO Handshake를 통해 Jetson의 제스처 인식 시스템을 활성화하고, Jetson에서는 Webcam과 MediaPipe로 손 관절 정보를 추출한 뒤 **LSTM 모델을 이용해 사용자의 동작을 인식**합니다.

인식된 제스처에 따라 화면 확대, 필터 선택, 사진 촬영 등을 수행하며, 촬영한 이미지는 Bluetooth로 Photo Printer에 전달하여 즉시 출력합니다.

사용자가 자리를 떠나 `Idle` 상태가 지속되면 Jetson의 제스처 인식을 종료하고 다시 FPGA CNN 가속기를 활성화하여 **고성능 AI 연산이 필요한 구간과 저전력 대기 구간을 분리**했습니다.

<br>

## 🎯 프로젝트 목표

무인 사진 시스템은 이용자가 없는 시간에도 카메라와 연산 장치를 계속 구동하는 경우가 많아 장시간 운영 시 대기 전력이 누적됩니다.

또한 촬영 위치와 조작 위치가 분리되어 있거나 터치 장치를 사용하는 시스템은 별도의 리모컨·조작 장치가 필요하고, 무인 환경에서는 장치의 파손 및 관리 부담이 발생할 수 있습니다.

본 프로젝트에서는 이를 개선하기 위해 다음과 같은 구조를 적용했습니다.

* FPGA CNN 가속기를 이용한 상시 저전력 사람 감지
* 사람 감지 시에만 Jetson의 고성능 제스처 AI 활성화
* 손 제스처 기반 비접촉 사진기 조작
* FPGA와 Jetson 간 SPI 영상 통신 및 GPIO Handshake
* 촬영 이미지의 Bluetooth 기반 즉시 인화

<br>

## 👥 프로젝트 형태

**6인 팀 프로젝트**

### 주요 담당 분야

* Z7-20 · Jetson 시스템 통합 및 최적화
* CNN 가속기 통합
* 인공지능 신경망 최적화
* Fully Connected Layer 설계
* Convolution Layer 설계
* **Pcam 영상 처리 및 FPGA–Jetson 이미지 통신**

<br>

## 🙋 주요 담당

### Pcam 영상 처리 & 이미지 통신

* Pcam 5C 영상 입력 및 Z7-20 연동
* MIPI CSI-2 기반 영상 입력 Pipeline 구성
* RGB 영상의 Grayscale 변환
* CNN 입력을 위한 128×128 영상 Downscale
* CNN 입력 영상용 Frame Buffer 연결
* FPGA의 128×128 Grayscale 영상을 Jetson으로 전송하는 SPI 통신 구현
* CNN 추론 결과와 연속 사람 감지 상태를 SPI Frame에 포함하여 전달
* FPGA–Jetson GPIO Handshake 연동
* 실제 Pcam–FPGA–Jetson 시스템 통합 및 영상 통신 검증

<br>

## 🛠 사용 기술

### FPGA & HDL

* Digilent Zybo Z7-20
* Xilinx Zynq-7000
* Verilog HDL
* SystemVerilog
* Vivado 2020.2
* BRAM
* FSM
* AXI4-Stream

### Camera & Image Processing

* Digilent Pcam 5C
* MIPI CSI-2
* MIPI D-PHY
* Bayer to RGB
* RGB to Grayscale
* Center Crop
* Downscale
* 128×128 Image Buffer

### AI

* INT8 CNN Accelerator
* Convolution
* Max Pooling
* Fully Connected Layer
* Requantization
* MediaPipe Hands
* LSTM
* TensorFlow / Keras

### Communication

* SPI
* GPIO Handshake
* Bluetooth

### Software

* Python
* OpenCV
* NumPy
* Jetson.GPIO
* spidev

### Hardware

* NVIDIA Jetson Orin Nano
* Webcam
* Photo Printer

<br>

# ⚙️ 시스템 구성

전체 시스템은 **사람 감지용 FPGA CNN 가속기**와 **제스처 인식용 Jetson AI**로 역할을 분리했습니다.

```text
                [ 사람 감지용 카메라 ]
                       Pcam 5C
                          │
                     AXI4-Stream
                          ▼
┌─────────────────────────────────────────────┐
│                Zybo Z7-20                   │
│                                             │
│   Camera Image                              │
│        ↓                                    │
│   Grayscale                                 │
│        ↓                                    │
│   128×128 Downscale                         │
│        ↓                                    │
│   INT8 CNN Accelerator                      │
│        ↓                                    │
│   Person / Non-Person                       │
└──────────────┬──────────────────────────────┘
               │
        SPI + GPIO Handshake
               │
               ▼
┌─────────────────────────────────────────────┐
│             Jetson Orin Nano                │
│                                             │
│   Webcam                                    │
│      ↓                                      │
│   MediaPipe Hands                           │
│      ↓                                      │
│   21 Hand Landmarks                         │
│      ↓                                      │
│   78-D Feature                              │
│      ↓                                      │
│   30 Frame Sequence                         │
│      ↓                                      │
│   LSTM Gesture Recognition                  │
│      ↓                                      │
│   Zoom / Select / Print / Idle              │
└──────────────┬──────────────────────────────┘
               │
            Bluetooth
               ▼
          Photo Printer
               │
               ▼
          Photo Output
```

<br>

## 🔄 저전력 제어 Loop

고성능 Jetson AI를 항상 동작시키지 않고 시스템 상태에 따라 FPGA와 Jetson의 역할을 전환합니다.

```text
[Standby]

FPGA CNN Accelerator : ON
Jetson Gesture AI     : OFF
        │
        │ Person Detection
        ▼
GPIO Handshake
        │
        ▼
[Active]

FPGA CNN Accelerator : OFF
Jetson Gesture AI     : ON
        │
        │ Gesture Recognition
        ▼
Photo / Zoom / Filter
        │
        │ Idle 3 sec
        ▼
[Standby]

FPGA CNN Accelerator : ON
Jetson Gesture AI     : OFF
```

이를 통해 사람이 없는 대기 구간에서는 FPGA의 경량 CNN 가속기만 동작시키고, 실제 사용자가 있을 때만 Jetson의 제스처 인식 AI를 활성화했습니다.

<br>

# 🖼 Pcam 영상 처리

## 1. Pcam 영상 입력

Pcam 5C에서 입력되는 영상은 MIPI D-PHY와 CSI-2 Receiver를 통해 FPGA 내부로 전달됩니다.

```text
Pcam 5C
   ↓
MIPI D-PHY
   ↓
MIPI CSI-2
   ↓
Bayer to RGB
   ↓
RGB Image Stream
```

<br>

## 2. RGB → Grayscale

CNN의 입력 데이터량과 연산량을 줄이기 위해 RGB 영상을 Grayscale로 변환했습니다.

```text
Gray ≈ 0.299R + 0.587G + 0.114B
```

RTL에서는 부동소수점 연산 대신 정수 기반 연산을 사용합니다.

```text
Gray = (77R + 150G + 29B) / 256
```

<br>

## 3. 128×128 CNN 입력 생성

Pcam 영상 전체를 CNN에 입력하지 않고 중앙 영역을 Crop한 뒤 Downscale합니다.

```text
1280 × 720
     ↓
Center Crop
512 × 512
     ↓
4 Pixel Sampling
     ↓
128 × 128
```

최종 128×128 Grayscale 영상은 FPGA 내부 Frame Buffer에 저장되어 CNN 입력과 SPI 영상 전송에 사용됩니다.

<br>

# 🧠 사람 탐지 CNN 가속기

사람 유무만 판단하는 목적에 맞춰 비교적 경량화된 CNN 구조를 FPGA에 구현했습니다.

```text
Input Image
128 × 128

    ↓

Conv 1 + Pool 1
16 Channel

    ↓

Conv 2 + Pool 2
32 Channel

    ↓

Conv 3
64 Channel

    ↓

Pooling Layers

    ↓

Fully Connected

    ↓

Classifier

    ↓

Person / Non-Person
```

CNN 내부 연산은 INT8 기반으로 구성하여 Feature Map과 Weight의 Memory 사용량을 줄였습니다.

Convolution에서 생성된 32-bit 누적 결과는 Requantization을 거쳐 다시 8-bit Feature Map으로 저장합니다.

<br>

# 💾 CNN 가속기 최적화

## 1. 비대칭 Cache 구조

각 CNN Layer가 요구하는 Memory 크기가 서로 다르기 때문에 동일 크기의 두 Memory를 사용하는 구조에서는 사용하지 않는 공간이 발생했습니다.

기존 대칭형 구조에서는 두 Memory를 각각 64 KB로 구성해 총 **128 KB**가 필요했습니다.

```text
Symmetric Cache

Memory A : 64 KB
Memory B : 64 KB
----------------
Total    : 128 KB
```

이를 Layer별 최대 요구량에 맞춘 비대칭 구조로 변경했습니다.

```text
Asymmetric Cache

Memory A : 64 KB
Memory B : 32 KB
----------------
Total    : 96 KB
```

이를 통해 Cache Memory 사용량을 **128 KB → 96 KB**로 감소시켰습니다.

<br>

## 2. 이전 Tile 재사용

Convolution을 수행할 때 인접한 연산 영역은 일부 Pixel을 공유합니다.

기존 방식에서는 다음 연산 시 겹치는 데이터까지 다시 Memory에서 읽었습니다.

```text
1st Tile : 16 Pixel Read
2nd Tile : 16 Pixel Read
```

이전 Tile에서 이미 읽은 데이터를 재사용하도록 구조를 변경하여 두 번째 연산에서 필요한 신규 Memory Access를 줄였습니다.

```text
1st Tile : 16 Pixel Read
2nd Tile :  8 Pixel Read
```

이를 통해 중복 Memory 접근을 절반 수준으로 줄이고 CNN 처리 속도를 개선했습니다.

<br>

## 3. Requantization Shift-Add

Convolution의 MAC 연산 결과는 32-bit로 누적되며 다음 Layer에서 다시 INT8 데이터가 필요합니다.

일반적인 곱셈 연산을 그대로 사용하면 하드웨어 자원과 Critical Path 부담이 증가하기 때문에 Requantization 과정에서 **Shift와 Add 연산을 조합**했습니다.

```text
32-bit Accumulator
        ↓
Requantization
Shift + Add
        ↓
8-bit Feature
        ↓
Feature Buffer
```

곱셈 연산을 Shift/Add 구조로 변경하여 Timing과 전력 소모에 대한 부담을 줄였습니다.

<br>

# 📊 CNN 가속기 성능

Z7-20 CNN 가속기와 Jetson Orin Nano에서 사람 감지를 수행했을 때의 결과를 비교했습니다.

| 항목       | Z7-20 CNN 가속기 | Jetson Orin Nano |
| -------- | ------------: | ---------------: |
| 병렬 연산 유닛 |            16 |             1024 |
| 하드웨어 활용률 |           74% |               7% |
| 상시 감지 전력 |     **2.3 W** |  **6.5 ~ 7.0 W** |
| 정확도      |       **98%** |          **99%** |

사람 유무를 판별하는 비교적 단순한 상시 감지 작업에서는 Z7-20 CNN 가속기가 Jetson Orin Nano와 유사한 정확도를 유지하면서 **약 3배 낮은 전력**으로 동작하는 것을 확인했습니다.

이를 이용하여 상시 감지는 FPGA가 담당하고, 복잡한 제스처 추론이 필요한 경우에만 Jetson을 활성화하도록 시스템을 구성했습니다.

<br>

# 🔌 FPGA–Jetson SPI 영상 통신

FPGA CNN 가속기에서 실제 입력으로 사용하는 **128×128 Grayscale 영상**을 Jetson의 대기 화면에서도 확인할 수 있도록 SPI 통신을 구현했습니다.

```text
Z7-20 FPGA
SPI Slave
    │
    │ 128×128 Grayscale Frame
    │ CNN Result
    ▼
Jetson Orin Nano
SPI Master
```

SPI는 Mode 0, 5 MHz로 설정했으며 하나의 Frame을 **16,384 Byte**로 구성했습니다.

### SPI Frame

|        Byte | Data                            |
| ----------: | ------------------------------- |
| `0 ~ 16379` | 128×128 Grayscale Image         |
|     `16380` | Tail Magic `0xA5`               |
|     `16381` | CNN Score                       |
|     `16382` | Person Count + Detection Result |
|     `16383` | XOR Checksum                    |

Jetson의 SPI Driver가 16 KiB보다 큰 Transaction을 분리하는 특성을 고려하여 전체 Frame 크기를 정확히 16,384 Byte로 유지했습니다.

마지막 4 Byte에는 CNN 추론 결과와 연속 사람 감지 상태를 포함해 별도의 추가 Packet 없이 영상과 상태 데이터를 함께 전달하도록 구성했습니다.

<br>

# 🤝 FPGA–Jetson Handshake

FPGA에서 사람을 감지하면 GPIO 신호를 이용해 Jetson의 제스처 인식 시스템을 활성화합니다.

순간적인 오검출에 의해 시스템이 실행되는 것을 줄이기 위해 CNN이 **사람을 연속 15회 감지**했을 때 Trigger를 발생시키도록 구성했습니다.

```text
CNN Person Detection
        ↓
15 Consecutive Detections
        ↓
FPGA Trigger HIGH
        ↓
Jetson Gesture AI ON
        ↓
FPGA CNN Accelerator OFF
```

Jetson에서 `Idle` 제스처가 3초간 유지되면 세션을 종료하고 다시 CNN 가속기를 활성화합니다.

```text
Idle 3 sec
    ↓
Jetson Gesture AI OFF
    ↓
GPIO RUN LOW
    ↓
FPGA CNN Accelerator ON
```

<br>

# ✋ AI 제스처 인식

Jetson Orin Nano에서는 Webcam 영상을 입력받아 MediaPipe Hands와 LSTM을 이용해 사용자의 손 동작을 인식합니다.

## 1. 제스처 데이터 수집

Webcam의 **480×270 영상**에서 MediaPipe Hands를 이용하여 손의 21개 Landmark를 추출합니다.

```text
Webcam Image
480 × 270

     ↓

MediaPipe Hands

     ↓

21 Hand Landmarks

     ↓

30 Frame Sequence
15 FPS × 2 sec
```

<br>

## 2. Feature Extraction

21개 Landmark의 XYZ 좌표에서 총 63개의 좌표 Feature를 생성합니다.

여기에 뼈 사이의 관절 각도 15개를 추가합니다.

```text
Coordinate Feature
21 × 3 = 63

       +

Joint Angle
15

       ↓

78-D Feature
```

손의 위치, 크기 및 화면비에 의한 영향을 줄이기 위해 좌표 정규화를 적용했습니다.

<br>

## 3. LSTM

30 Frame의 연속적인 손 움직임을 입력받아 4가지 동작을 분류합니다.

```text
Input
30 × 78

   ↓

LSTM 64
Dropout 0.3

   ↓

LSTM 64
Dropout 0.3

   ↓

Dense 32
ReLU
Dropout 0.2

   ↓

Softmax 4
```

### Gesture Class

| Gesture  | 기능         |
| -------- | ---------- |
| `idle`   | 대기 / 세션 종료 |
| `print`  | 사진 촬영      |
| `zoom`   | 화면 확대·축소   |
| `select` | 필터 선택      |

<br>

# 📷 사진 촬영 및 출력

제스처 인식 결과를 이용해 사진기의 주요 기능을 비접촉으로 제어합니다.

### Zoom

`zoom` 동작을 일정 시간 유지하면 화면 확대 상태를 ON/OFF 합니다.

### Filter

`select` 동작을 통해 Filter 메뉴를 활성화하고 손의 위치에 따라 Filter를 선택합니다.

### Print

`print` 동작을 유지하면 Countdown 후 사진을 촬영합니다.

```text
Print Gesture

      ↓

     3
     2
     1

      ↓

Photo Capture

      ↓

Bluetooth

      ↓

Photo Printer
```

촬영된 이미지는 Bluetooth를 통해 Photo Printer로 전달하여 즉시 출력합니다.

<br>

# ⚠️ 문제 해결

## 1. CNN Critical Path - TNS 해결

CNN MAC 연산의 Memory Read부터 곱셈·누산까지 긴 조합 경로가 형성되면서 Timing Violation이 발생했습니다.

### 개선 전

```text
WNS : -2.45 ns
TNS : -9323.07 ns
```

Memory, Multiply, Add 연산 사이에 Pipeline Register를 추가하여 긴 Critical Path를 여러 Stage로 분리했습니다.

### 개선 후

```text
WNS : +0.124 ns
TNS : 0 ns
```

Timing Violation을 제거하여 FPGA에서 안정적으로 동작할 수 있도록 개선했습니다.

<br>

## 2. 제스처 인식 FPS 개선

초기 제스처 인식에서는 무거운 추론 동작이 전체 영상 처리 FPS를 떨어뜨렸으며, Webcam이 비압축 YUYV Mode로 동작하면서 고해상도 입력의 FPS에도 제한이 발생했습니다.

영상 Capture/Render와 AI 추론 흐름을 분리하여 카메라 영상 출력이 추론 과정에 직접적으로 영향을 받지 않도록 변경했습니다.

### 개선 결과

```text
Capture : 15.0 FPS
Render  : 15.0 FPS
Recog   : 14.9 FPS
```

<br>

## 3. SPI 16 KiB 전송 문제

Jetson에서 16 KiB보다 큰 SPI 데이터를 한 번에 읽을 경우 Tegra SPI Driver가 Transaction을 내부적으로 분할하면서 CS가 변경되는 문제가 발생했습니다.

FPGA에서는 CS가 해제될 때 Address Counter가 초기화되기 때문에 Frame 데이터가 처음부터 반복되는 현상이 발생했습니다.

### 해결

* 전체 SPI Frame 크기를 16,384 Byte로 고정
* 마지막 4개의 Pixel 영역을 CNN Telemetry 데이터로 활용
* Magic Byte와 XOR Checksum으로 Frame 정렬 및 데이터 정상 여부 확인

이를 통해 별도의 Frame 크기 증가 없이 영상과 CNN 상태 데이터를 안정적으로 전달했습니다.

<br>

# 🧪 검증 내용

## CNN 기능 검증

SystemVerilog Testbench를 이용하여 Person / Non-Person Dataset에 대한 CNN 동작을 확인했습니다.

```text
Person Dataset      : 100 images
Non-Person Dataset  : 100 images
--------------------------------
Total               : 200 images
Image Size          : 128 × 128
```

### 검증 항목

* 128×128 Image Memory Load
* CNN `start / busy / done` Handshake
* Person / Non-Person 결과 비교
* Signed INT8 최종 Score 확인
* Timeout 기반 Deadlock 검출
* Person / Non-Person 개별 Pass Rate 계산
* Threshold `-128 ~ 127` Sweep
* TP / TN / FP / FN 기반 Threshold 비교

최종 시스템에서는 사람 탐지 정확도 **98%**를 확인했습니다.

<br>

## 시스템 통합 검증

* Pcam 영상 입력 확인
* AXI4-Stream 영상 전달 확인
* RGB → Grayscale 변환 확인
* 128×128 CNN 입력 영상 생성 확인
* FPGA CNN Person Detection 확인
* SPI 기반 FPGA → Jetson 영상 전달 확인
* GPIO Handshake 동작 확인
* 사람 감지 시 Jetson 제스처 AI 활성화 확인
* `Idle` 3초 후 CNN 가속기 재활성화 확인
* MediaPipe Landmark 추출 확인
* LSTM Gesture Classification 확인
* 사진 촬영 및 Bluetooth Printer 출력 확인

<br>

# 🎥 시스템 동작

시연에서는 다음과 같은 전체 동작을 확인했습니다.

1. Pcam을 통한 사람 감지 영상 입력
2. FPGA에서 128×128 영상 전처리
3. Z7-20 CNN 가속기를 이용한 Person Detection
4. 대기 화면에서 SPI로 CNN 입력 영상 확인
5. 사람 감지 시 GPIO Handshake 발생
6. FPGA CNN 가속기 OFF
7. Jetson 제스처 인식 AI ON
8. MediaPipe + LSTM을 이용한 손 제스처 인식
9. Zoom / Filter / Photo 기능 제어
10. 촬영 이미지 Bluetooth 인화
11. `Idle` 상태 3초 유지 시 제스처 인식 종료
12. FPGA CNN 가속기 재활성화 및 저전력 대기 상태 복귀

<br>

## 📂 소스코드 구조

FPGA 기반 사람 탐지 CNN 가속기와 Jetson 기반 제스처 인식 소스를 구분하고, CNN 기능 검증을 위한 Testbench 및 검증 데이터를 별도로 구성했습니다.

```text
src/
├── fpga/
│   ├── cnn/
│   │   ├── CNN_accelerator.v
│   │   ├── CNN_acc_controller.sv
│   │   ├── Buffer.sv
│   │   ├── padding.sv
│   │   ├── requantize.sv
│   │   │
│   │   ├── conv/
│   │   │   ├── CH.sv
│   │   │   ├── CH_Result_Buffer.sv
│   │   │   ├── CH_wrapper.sv
│   │   │   ├── conv.sv
│   │   │   ├── Conv_Controller.sv
│   │   │   ├── Feature_Buffer_Mux.sv
│   │   │   ├── MaxPool_2x2.sv
│   │   │   ├── MaxPool_wrapper.sv
│   │   │   ├── Output_Mux.sv
│   │   │   ├── Shift_Buffer.sv
│   │   │   ├── Standalone_MaxPool.sv
│   │   │   └── Weight_Loader.sv
│   │   │
│   │   └── fc/
│   │       ├── fc.sv
│   │       ├── fc_controller.sv
│   │       ├── fc_core.sv
│   │       ├── fc_memory_adapter.sv
│   │       ├── fc_output_buffer.sv
│   │       ├── fc_pe.sv
│   │       ├── fc_pe_array.sv
│   │       └── fc_quantizer.sv
│   │
│   ├── preprocess/
│   │   ├── rgb2gray.v
│   │   ├── ds_128.v
│   │   └── gray_128x128.v
│   │
│   ├── interface/
│   │   ├── jetson_controller.v
│   │   └── spi_frame_tx.v
│   │
│   ├── memory/
│   │   └── int8_weights_conv_fc_be64.mem
│   │
│   ├── bd/
│   │   ├── system.bd
│   │   └── system_wrapper.vhd
│   │
│   └── constraints/
│       └── *.xdc
│
└── jetson/
    ├── gesture_print_b1pro.py
    ├── cnn_image.py
    ├── hand_feature.py
    ├── create_dataset.py
    ├── train_6people.py
    └── niimbot_b1pro.py

verification/
└── cnn/
    ├── tb/
    │   └── tb_CNN_all_imge.sv
    │
    ├── scripts/
    │   ├── img2mem.py
    │   ├── filelist.f
    │   └── Makefile
    │
    └── test_data/
        ├── person/
        │   └── person*.mem
        └── non_person/
            └── non_person*.mem
```

### 폴더 설명

| 폴더                           | 내용                                                               |
| ---------------------------- | ---------------------------------------------------------------- |
| `src/fpga/cnn`               | INT8 기반 사람 탐지 CNN 가속기의 전체 제어 및 연산 RTL                            |
| `src/fpga/cnn/conv`          | Convolution, Max Pooling, Weight Loading 및 Feature Buffer 관련 RTL |
| `src/fpga/cnn/fc`            | Fully Connected Layer 연산 및 Quantization 관련 RTL                   |
| `src/fpga/preprocess`        | Pcam 입력 영상의 Grayscale 변환 및 128×128 Downscale 전처리                 |
| `src/fpga/interface`         | FPGA–Jetson SPI 영상 통신 및 GPIO Handshake 제어                        |
| `src/fpga/memory`            | CNN 연산에 사용하는 INT8 Weight Memory                                  |
| `src/fpga/bd`                | Zybo Z7-20 기반 Vivado Block Design 및 Top Wrapper                  |
| `src/fpga/constraints`       | FPGA 핀 및 Timing Constraint 설정                                    |
| `src/jetson`                 | 제스처 인식, SPI 영상 수신, 데이터셋 생성·학습 및 Photo Printer 제어 Python 코드       |
| `verification/cnn/tb`        | CNN Person / Non-Person 기능 검증용 SystemVerilog Testbench           |
| `verification/cnn/scripts`   | 이미지 Memory 변환 및 시뮬레이션 실행을 위한 Script                              |
| `verification/cnn/test_data` | CNN 검증에 사용하는 Person / Non-Person 128×128 Memory 데이터              |

> Vivado에서 자동 생성되는 `.cache`, `.gen`, `.runs`, `.sim`, `.hw`, `.Xil`, IP 생성 파일 및 Simulation Netlist는 소스코드에서 제외했습니다.

<br>

# 💡 프로젝트를 통해 배운 점

상시 동작하는 시스템에서 모든 연산을 고성능 프로세서에 맡기는 것보다, 연산의 특성에 따라 FPGA와 Jetson의 역할을 분리하는 것이 전력 효율 측면에서 효과적이라는 점을 확인했습니다.

특히 프로젝트를 통해 다음 내용을 경험했습니다.

* Pcam 5C와 Z7-20을 이용한 실시간 영상 입력 및 처리
* FPGA 기반 INT8 CNN Accelerator 구현
* 제한된 FPGA Memory를 고려한 Cache 구조 최적화
* Tile Data 재사용을 통한 Memory Access 감소
* Shift-Add 기반 Requantization
* Pipeline 적용을 통한 Critical Path 및 Timing 개선
* FPGA 기반 저전력 Person Detection
* SPI 기반 FPGA–Jetson 영상 통신
* GPIO 기반 Hardware–Software Handshake
* MediaPipe와 LSTM 기반 Gesture Recognition
* FPGA와 Jetson의 역할 전환을 이용한 저전력 제어 Loop
* 실제 Camera–FPGA–Jetson–Printer를 연결한 시스템 통합

<br>

## 📄 발표 자료

전체 시스템 설계, CNN 가속기 최적화 및 제스처 인식 과정은 프로젝트 발표 자료에서 확인할 수 있습니다.

```text
저전력 CNN 가속기 기반 제스처 사진기
Team. 하나둘셋찰칵
2026.08.20
```

<br>

## 📜 출처 및 참고

* Digilent Zybo Z7-20
* Digilent Pcam 5C
* NVIDIA Jetson Orin Nano
* MediaPipe Hands
* TensorFlow / Keras
* OpenCV
* Xilinx Vivado
* 본 프로젝트는 6인 팀 프로젝트로 수행되었습니다.

---

*FPGA 기반 저전력 사람 감지와 Jetson 제스처 AI를 결합한 비접촉식 사진 촬영 시스템*
