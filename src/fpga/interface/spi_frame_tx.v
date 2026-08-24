`timescale 1ns / 1ps
//============================================================
// spi_frame_tx : gray_128x128 버퍼 → Jetson SPI 프레임 송신
//  - Jetson이 마스터(mode 0). 커맨드 없음.
//  - "CS 잡고 16384바이트 클럭킹" = 프레임 1장 (총 길이는 절대 불변!)
//
//  ★★★ 전송 길이가 왜 정확히 16384바이트여야 하는가 ★★★
//    Tegra(Jetson)의 SPI 드라이버는 16KiB(16384) 경계에서 전송을 쪼개고
//    그 사이 CS를 토글합니다 (spidev bufsiz와 무관한 드라이버 내부 동작).
//    CS가 풀리는 순간 이 모듈은 카운터를 리셋하므로, 16384를 넘는 단일
//    전송은 절대 성립하지 않습니다. 실측으로 확인된 제약입니다.
//
//  ★ 테일(텔레메트리)은 그래서 프레임 '뒤'가 아니라 '안'에 넣습니다:
//    이미지의 마지막 4바이트(우하단 구석 4픽셀)를 희생합니다.
//      [    0..16379] 128x128 gray 픽셀 (마지막 4픽셀 제외)
//      [16380]        0xA5 테일 매직 (구버전 비트스트림/정렬 오류 검출용)
//      [16381]        probability_data (CNN 최종 로짓, s8)
//      [16382]        {person_cnt[6:0], result}  ← 연속감지 진행률 + 감지 1비트
//      [16383]        0xA5 ^ prob ^ [16382]  (XOR 체크섬)
//  - ★ 테일 값은 CS 세션이 '시작되는 순간' 래치되어 세션 중 안 바뀝니다.
//    (프레임과 함께 일관된 스냅샷)
//  - ★ 구버전 뷰어로 읽으면 이미지 구석 4픽셀에 잡티가 보이는 것 외에
//    완전히 동일하게 동작합니다 (하위 호환).
//  - ★ 테일 바이트는 이 모듈 안에서 mux 됩니다. gray_128x128의 메모리
//    주소는 14비트 그대로 — 버퍼는 CNN 값을 알 필요가 없습니다.
//  - tx_taken마다 바이트 카운터 증가, CS 유휴 동안 0으로 복귀
//  - spi_active : CS 세션 동안 1 → gray_128x128 캡처 동결에 사용
//  - BD에서 Add Module로 추가, sclk/mosi/miso/cs_n은 Make External
//    (외부 포트 이름을 XDC와 동일하게 sclk/mosi/miso/cs_n으로 맞출 것)
//============================================================
module spi_frame_tx (
    input wire clk,   // gray_128x128의 aclk과 반드시 같은 클럭 넷
    input wire rst_n, // gray_128x128의 aresetn과 같은 넷

    // Jetson SPI 핀 (Pmod JE)
    input  wire sclk,
    input  wire mosi,  // 사용 안 함 (Jetson은 더미 0x00 송신)
    output wire miso,
    input  wire cs_n,

    // gray_128x128 SPI 읽기 포트
    output wire [13:0] spi_rd_addr,
    input  wire [ 7:0] spi_rd_data,   // 주소 후 1클럭 뒤 데이터 (동기 BRAM 읽기)
    output wire spi_active,

    // ★ 추가: 프레임 테일로 내보낼 CNN 상태값 (clk와 같은 도메인)
    input  wire [7:0] probability_data,
    input  wire       result,           // CNN_accelerator의 result 출력 직결
    input  wire [6:0] person_cnt        // jetson_controller의 연속감지 카운트
);
    localparam integer FRAME_BYTES = 16384;
    localparam integer TAIL_START  = 16380;   // 마지막 4바이트 = 테일
    localparam [7:0]   TAIL_MAGIC  = 8'hA5;

    wire       tx_taken;
    wire [7:0] rx_byte;  // 미사용
    wire       byte_done;  // 미사용
    wire       spi_busy;  // 미사용

    // 다음에 시프터로 로드될 바이트 번호 (0..16383, 원래 프로토콜 길이 그대로)
    reg  [13:0] byte_cnt;
    reg  [ 7:0] prob_lat;
    reg         result_lat;
    reg  [ 6:0] pcnt_lat;

    wire [7:0] cnn_byte = {pcnt_lat, result_lat};
    wire [7:0] tail_chk = TAIL_MAGIC ^ prob_lat ^ cnn_byte;

    // 이미지 구간은 BRAM 출력, 마지막 4바이트는 래치된 테일.
    // 16380..16383의 하위 2비트가 정확히 0..3이라 그대로 인덱스로 씁니다.
    // (16380 = 4의 배수)
    reg [7:0] tx_mux;
    always @* begin
        if (byte_cnt < TAIL_START) tx_mux = spi_rd_data;
        else begin
            case (byte_cnt[1:0])
                2'd0:    tx_mux = TAIL_MAGIC;
                2'd1:    tx_mux = prob_lat;
                2'd2:    tx_mux = cnn_byte;
                default: tx_mux = tail_chk;
            endcase
        end
    end

    spi_slave u_spi (
        .clk        (clk),
        .rst_n      (rst_n),
        .cpol       (1'b0),         // mode 0 고정
        .cpha       (1'b0),
        .tx_data    (tx_mux),
        .tx_taken   (tx_taken),
        .rx_data    (rx_byte),
        .done       (byte_done),
        .busy       (spi_busy),
        .cs_active_o(spi_active),
        .sclk       (sclk),
        .mosi       (mosi),
        .miso       (miso),
        .cs_n       (cs_n)
    );

    // 테일 구간(16380..16383)에서도 mem을 읽지만 tx_mux가 무시하므로 무해.
    // CS 유휴 복귀 시 byte_cnt=0 → mem[0]이 다음 프레임 첫 바이트로 준비됨.
    assign spi_rd_addr = byte_cnt;

    // 바이트 카운터 + 테일 래치
    //  - CS 유휴: 카운터 0 유지, 테일 값은 최신값을 계속 따라감
    //  - CS 세션: 테일 값 동결
    //  - tx_taken: 다음 바이트 프리페치 시작 (원본과 동일한 0..16383 순환)
    //    (다음 소비 시점은 8 SPI 비트 뒤이므로 BRAM 1클럭 레이턴시는 여유 충분)
    always @(posedge clk) begin
        if (!rst_n) begin
            byte_cnt   <= 14'd0;
            prob_lat   <= 8'd0;
            result_lat <= 1'b0;
            pcnt_lat   <= 7'd0;
        end else if (!spi_active) begin
            byte_cnt   <= 14'd0;
            prob_lat   <= probability_data;
            result_lat <= result;
            pcnt_lat   <= person_cnt;
        end else if (tx_taken) begin
            if (byte_cnt == 14'd16383) byte_cnt <= 14'd0;
            else byte_cnt <= byte_cnt + 1'b1;
        end
    end

endmodule

`timescale 1ns / 1ps
//============================================================
// SPI Slave : 4개 CPOL/CPHA 모드 전부 지원 (master와 대칭)
//  - reset 규약: active-low, synchronous (rst_n)
//  - [수정] CS를 잡은 채 여러 바이트 연속 전송(스트리밍) 지원
//    * 바이트 경계(2nd 엣지, bit_cnt==7)에서 tx_data 즉시 재로드
//    * tx_taken : tx_data가 시프트 레지스터로 넘어간 순간 1클럭 펄스
//    * cs_active_o : CS 세션 동안 1 (프레임 전송 중 표시)
//  - [수정] miso tri-state 제거 (단일 슬레이브 + BD Module Reference)
//============================================================
module spi_slave (
    input  wire       clk,
    input  wire       rst_n,        // master와 동일: 동기, active-low
    input  wire       cpol,
    input  wire       cpha,
    input  wire [7:0] tx_data,
    output reg        tx_taken,     // ★ 추가
    output reg  [7:0] rx_data,
    output reg        done,
    output reg        busy,
    output wire       cs_active_o,  // ★ 추가
    input  wire       sclk,
    input  wire       mosi,
    output wire       miso,
    input  wire       cs_n
);
    // ---- 입력 동기화(2~3 FF) + 엣지 검출 ----
    reg [2:0] sclk_sync;
    reg [2:0] cs_n_sync;
    reg [1:0] mosi_sync;
    always @(posedge clk) begin
        if (!rst_n) begin
            sclk_sync <= {3{cpol}};   // 유휴 레벨 = cpol (거짓 엣지 방지)
            cs_n_sync <= 3'b111;
            mosi_sync <= 2'b11;
        end else begin
            sclk_sync <= {sclk_sync[1:0], sclk};
            cs_n_sync <= {cs_n_sync[1:0], cs_n};
            mosi_sync <= {mosi_sync[0], mosi};
        end
    end
    wire sclk_rise = (sclk_sync[2:1] == 2'b01);
    wire sclk_fall = (sclk_sync[2:1] == 2'b10);
    wire cs_n_fall = (cs_n_sync[2:1] == 2'b10);
    wire cs_n_rise = (cs_n_sync[2:1] == 2'b01);
    wire cs_active = ~cs_n_sync[1];
    wire mosi_in = mosi_sync[1];

    assign cs_active_o = cs_active;

    // CPOL에 따른 1st(leading)/2nd(trailing) 엣지 매핑 (master와 동일)
    wire edge1 = (!cpol) ? sclk_rise : sclk_fall;  // leading
    wire edge2 = (!cpol) ? sclk_fall : sclk_rise;  // trailing

    reg [7:0] tx_shift_reg, rx_shift_reg;
    reg [2:0] bit_cnt;
    reg       step;
    reg       miso_r;

    // 단일 슬레이브 구성이므로 tri-state 대신 직결
    // (BD Module Reference 경유 시 1'bz는 경고/흡수 이슈가 있음)
    assign miso = miso_r;

    always @(posedge clk) begin
        if (!rst_n) begin
            tx_shift_reg <= 8'd0;
            rx_shift_reg <= 8'd0;
            bit_cnt      <= 3'd0;
            step         <= 1'b0;
            rx_data      <= 8'd0;
            done         <= 1'b0;
            busy         <= 1'b0;
            miso_r       <= 1'b1;
            tx_taken     <= 1'b0;
        end else begin
            done     <= 1'b0;
            tx_taken <= 1'b0;
            if (cs_n_fall) begin
                // 프레임(세션) 시작 : 첫 바이트 로드
                bit_cnt <= 3'd0;
                step    <= 1'b0;
                busy    <= 1'b1;
                if (!cpha) begin
                    // CPHA=0 : 1st 엣지 전에 첫 비트 미리 출력 (master의 START 대응)
                    miso_r       <= tx_data[7];
                    tx_shift_reg <= {tx_data[6:0], 1'b0};
                end else begin
                    // CPHA=1 : 로드만, 1st 엣지에서 출력
                    tx_shift_reg <= tx_data;
                    miso_r       <= 1'b1;
                end
                tx_taken <= 1'b1;
            end else if (cs_active) begin
                if (edge1 || edge2) begin
                    if (step == 1'b0) begin
                        // ---- 1st(leading) 엣지 ----
                        step <= 1'b1;
                        if (!cpha) begin
                            // CPHA=0 : 1st = 샘플
                            rx_shift_reg <= {rx_shift_reg[6:0], mosi_in};
                        end else begin
                            // CPHA=1 : 1st = 드라이브
                            miso_r       <= tx_shift_reg[7];
                            tx_shift_reg <= {tx_shift_reg[6:0], 1'b0};
                        end
                    end else begin
                        // ---- 2nd(trailing) 엣지 ----
                        step <= 1'b0;
                        if (bit_cnt < 7) begin
                            bit_cnt <= bit_cnt + 1;
                            if (!cpha) begin
                                miso_r       <= tx_shift_reg[7];
                                tx_shift_reg <= {tx_shift_reg[6:0], 1'b0};
                            end else begin
                                rx_shift_reg <= {rx_shift_reg[6:0], mosi_in};
                            end
                        end else begin
                            // ---- 바이트 완료 : CS 유지 상태에서 다음 바이트로 즉시 연결 ----
                            bit_cnt <= 3'd0;
                            done    <= 1'b1;
                            if (!cpha) begin
                                rx_data      <= rx_shift_reg;
                                // ★ 다음 바이트 MSB를 이 2nd 엣지에서 바로 드라이브
                                //   (master는 다음 1st 엣지에서 이 비트를 샘플)
                                miso_r       <= tx_data[7];
                                tx_shift_reg <= {tx_data[6:0], 1'b0};
                            end else begin
                                rx_data      <= {rx_shift_reg[6:0], mosi_in};
                                // ★ 재로드만, 드라이브는 다음 1st 엣지에서
                                tx_shift_reg <= tx_data;
                            end
                            tx_taken <= 1'b1;
                        end
                    end
                end
            end else if (cs_n_rise) begin
                busy   <= 1'b0;
                miso_r <= 1'b1;
            end
        end
    end

endmodule
