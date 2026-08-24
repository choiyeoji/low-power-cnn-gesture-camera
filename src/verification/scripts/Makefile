# ==========================================
# VCS Makefile for CNN Accelerator Project
# ==========================================

# --- Variables ---
VCS      = vcs
SIMV     = ./simv
FILELIST = filelist.f

# --- VCS Compile Options ---
# -sverilog: SystemVerilog 문법 지원
# -full64: 64비트 모드로 컴파일
# -debug_access+all: 디버깅 및 파형 추출(Waveform) 권한 부여
# -timescale: 시뮬레이션 시간 단위/정밀도 설정
# -l: 로그 파일 저장
VCS_FLAGS  = -sverilog
VCS_FLAGS += -full64
VCS_FLAGS += -f $(FILELIST)
VCS_FLAGS += -debug_access+all
VCS_FLAGS += -timescale=1ns/1ps
VCS_FLAGS += -l compile.log

# --- Simulation Options ---
SIM_FLAGS  = -l sim.log

# --- Targets ---
.PHONY: all build run clean wave verdi

# 기본 명령어 (make 입력 시 컴파일 후 바로 실행)
all: build run

# 1. 컴파일 (Compile)
build:
	$(VCS) $(VCS_FLAGS)

# 2. 시뮬레이션 실행 (Run)
run:
	$(SIMV) $(SIM_FLAGS)

# 3. 찌꺼기 파일 삭제 (Clean)
clean:
	rm -rf $(SIMV) $(SIMV).daidir csrc
	rm -rf *.log *.key *.vpd *.fsdb 
	rm -rf DVEfiles novas* verdi* ucli.key

# 4. 파형 뷰어 실행 (DVE 사용 시)
wave:
	dve -vpd vcdplus.vpd &

# 5. 파형 뷰어 실행 (Verdi 사용 시)
verdi:
	verdi -ssf novas.fsdb &