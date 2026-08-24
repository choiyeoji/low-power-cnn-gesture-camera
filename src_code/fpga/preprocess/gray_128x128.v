`timescale 1ns / 1ps

module gray_128x128 #(
    parameter GW = 128,
    parameter GH = 128
) (
    input wire aclk,
    input wire aresetn,

    input  wire [7:0] cap_tdata,
    input  wire       cap_tvalid,
    output wire       cap_tready,
    input  wire       cap_tlast,
    input  wire       cap_tuser,

    input  wire              busy,
    output reg               start,
    input  wire       [13:0] cnn_rd_addr,
    output reg signed [ 7:0] cnn_rd_data,
    input  wire              cnn_enable,

    // ★ 테일(prob/cnn) 바이트는 spi_frame_tx 내부에서 mux 되므로
    //   이 포트는 이미지 16384바이트용 14비트 그대로 둡니다.
    input  wire [13:0] spi_rd_addr,
    output reg  [ 7:0] spi_rd_data,
    input  wire        spi_active

    // output reg frame_dropped,

    // Display-side AXI stream removed: only capture + CNN/SPI memory access remain.
);

    // ★★ SIZE는 반드시 16384 (= 프레임 크기 = wr_addr 14비트 범위) ★★
    //   +2 등으로 늘리면 wr_addr(14비트)가 SIZE-1에 도달할 수 없게 되어
    //   합성기가 last_pix→start를 통째로 상수 0(GND)으로 제거합니다.
    //   → CNN이 영원히 시작 못 함 (경고도 안 나옴). 실제로 겪었던 사고입니다.
    //   SPI 추가 데이터(테일)는 spi_frame_tx가 처리하므로 여기 건드릴 필요 없음.
    localparam integer SIZE = GW * GH;  // 16384

    (* ram_style = "block" *)reg [ 7:0] mem       [0:SIZE-1];
    reg [13:0] wr_addr;
    reg        capturing;

    assign cap_tready = 1'b1;

    wire sof = cap_tvalid && cap_tuser;
    wire wr_en = ((sof && !busy) || (capturing && cap_tvalid)) && !spi_active;
    wire [13:0] wr_addr_w = sof ? 14'd0 : wr_addr;  // sof 픽셀은 0번지

    always @(posedge aclk) begin
        if (wr_en) mem[wr_addr_w] <= cap_tdata;
    end

    always @(posedge aclk) begin
        if (!aresetn) begin
            wr_addr   <= 14'd0;
            capturing <= 1'b0;
            // frame_dropped <= 1'b0;
        end else begin
            // frame_dropped <= 1'b0;
            if (spi_active) begin
                capturing <= 1'b0;
            end else if (sof) begin
                if (!busy) begin
                    capturing <= 1'b1;
                    wr_addr   <= 14'd1;
                end else begin
                    capturing <= 1'b0;
                    // frame_dropped <= 1'b1;
                end
            end else if (capturing && cap_tvalid) begin
                if (wr_addr == (SIZE - 1))
                    capturing <= 1'b0;  // 16383 = 마지막 픽셀
                else wr_addr <= wr_addr + 1'b1;
            end
        end
    end

    wire last_pix = capturing && cap_tvalid && !sof && (wr_addr == (SIZE - 1));
    reg  last_pix_reg;

    always @(posedge aclk) begin
        if (!aresetn) last_pix_reg <= 1'b0;
        else if (start) last_pix_reg <= 1'b0;
        else if (last_pix) last_pix_reg <= 1'b1;
    end

    always @(posedge aclk) begin
        if (!aresetn) start <= 1'b0;
        else if (busy) start <= 1'b0;
        else if (last_pix_reg && cnn_enable) start <= 1'b1;
    end

    always @(posedge aclk) begin
        cnn_rd_data <= mem[cnn_rd_addr] - 8'd128;
    end

    always @(posedge aclk) begin
        spi_rd_data <= mem[spi_rd_addr];
    end

endmodule

// `timescale 1ns / 1ps

// module gray_128x128 #(
//     parameter GW = 128,
//     parameter GH = 128
// ) (
//     input wire aclk,
//     input wire aresetn,

//     input  wire [7:0] cap_tdata,
//     input  wire       cap_tvalid,
//     output wire       cap_tready,
//     input  wire       cap_tlast,
//     input  wire       cap_tuser,

//     input  wire               busy,
//     output reg                start,
//     input  wire        [13:0] cnn_rd_addr,
//     output wire signed [ 7:0] cnn_rd_data,
//     input  wire               cnn_enable,

//     // ★ 테일(prob/cnn) 바이트는 spi_frame_tx 내부에서 mux 되므로
//     //   이 포트는 이미지 16384바이트용 14비트 그대로 둡니다.
//     input  wire [13:0] spi_rd_addr,
//     output reg  [ 7:0] spi_rd_data,
//     input  wire        spi_active,

//     // output reg frame_dropped,

//     input  wire [23:0] s_axis_tdata,
//     input  wire        s_axis_tvalid,
//     output wire        s_axis_tready,
//     input  wire        s_axis_tlast,
//     input  wire        s_axis_tuser,

//     output wire [23:0] m_axis_tdata,
//     output wire        m_axis_tvalid,
//     input  wire        m_axis_tready,
//     output wire        m_axis_tlast,
//     output wire        m_axis_tuser
// );

//     // ★★ SIZE는 반드시 16384 (= 프레임 크기 = wr_addr 14비트 범위) ★★
//     //   +2 등으로 늘리면 wr_addr(14비트)가 SIZE-1에 도달할 수 없게 되어
//     //   합성기가 last_pix→start를 통째로 상수 0(GND)으로 제거합니다.
//     //   → CNN이 영원히 시작 못 함 (경고도 안 나옴). 실제로 겪었던 사고입니다.
//     //   SPI 추가 데이터(테일)는 spi_frame_tx가 처리하므로 여기 건드릴 필요 없음.
//     localparam integer SIZE = GW * GH;  // 16384

//     // ★ LUTRAM 금지. 이 배열이 분산 RAM으로 가면 읽기 포트 3개 × 16KB가
//     //   LUT ~6천 개로 복제되어 쓰기 데이터 fanout이 1500+가 되고, 이것이
//     //   칩 전체 라우팅 혼잡과 타이밍 위반(WNS -0.7ns)의 주범이었습니다.
//     //   BRAM이면 포트당 4개 × 3 = 12개 RAMB36으로 끝납니다.
//     (* ram_style = "block" *)
//     reg [ 7:0] mem       [0:SIZE-1];
//     reg [13:0] wr_addr;
//     reg        capturing;

//     // ★ 50MHz 리셋 블록에서 오는 aresetn을 이 모듈 클럭으로 2단 재동기화.
//     //   원본 aresetn을 그대로 쓰면 50M→100M 크로싱 경로가 수천 개 리셋 핀으로
//     //   퍼지면서 타이밍 위반(-0.6ns대)이 발생합니다. 여기서 한 번 끊어주면
//     //   크로싱은 rst_meta 한 점으로 줄어듭니다.
//     (* ASYNC_REG = "TRUE" *) reg rst_meta, rst_sync;
//     reg rst_n_i;
//     always @(posedge aclk) begin
//         rst_meta <= aresetn;
//         rst_sync <= rst_meta;
//         rst_n_i  <= rst_sync;
//     end

//     assign cap_tready = 1'b1;

//     wire sof = cap_tvalid && cap_tuser;
//     wire wr_en = ((sof && !busy) || (capturing && cap_tvalid)) && !spi_active;
//     wire [13:0] wr_addr_w = sof ? 14'd0 : wr_addr;  // sof 픽셀은 0번지

//     always @(posedge aclk) begin
//         if (wr_en) mem[wr_addr_w] <= cap_tdata;
//     end

//     always @(posedge aclk) begin
//         if (!rst_n_i) begin
//             wr_addr   <= 14'd0;
//             capturing <= 1'b0;
//             // frame_dropped <= 1'b0;
//         end else begin
//             // frame_dropped <= 1'b0;
//             if (spi_active) begin
//                 capturing <= 1'b0;
//             end else if (sof) begin
//                 if (!busy) begin
//                     capturing <= 1'b1;
//                     wr_addr   <= 14'd1;
//                 end else begin
//                     capturing <= 1'b0;
//                     // frame_dropped <= 1'b1;
//                 end
//             end else if (capturing && cap_tvalid) begin
//                 if (wr_addr == (SIZE - 1))
//                     capturing <= 1'b0;  // 16383 = 마지막 픽셀
//                 else wr_addr <= wr_addr + 1'b1;
//             end
//         end
//     end

//     wire last_pix = capturing && cap_tvalid && !sof && (wr_addr == (SIZE - 1));
//     reg  last_pix_reg;

//     always @(posedge aclk) begin
//         if (!rst_n_i) last_pix_reg <= 1'b0;
//         else if (start) last_pix_reg <= 1'b0;
//         else if (last_pix) last_pix_reg <= 1'b1;
//     end

//     always @(posedge aclk) begin
//         if (!rst_n_i) start <= 1'b0;
//         else if (busy) start <= 1'b0;
//         else if (last_pix_reg && cnn_enable) start <= 1'b1;
//     end

//     reg [11:0] sx_q, sy_q;
//     wire beat = s_axis_tvalid && m_axis_tready;

//     wire [11:0] sx = s_axis_tuser ? 12'd0 : sx_q;
//     wire [11:0] sy = s_axis_tuser ? 12'd0 : sy_q;

//     always @(posedge aclk) begin
//         if (!rst_n_i) begin
//             sx_q <= 12'd0;
//             sy_q <= 12'd0;
//         end else if (beat) begin
//             if (s_axis_tlast) begin
//                 sx_q <= 12'd0;
//                 sy_q <= sy + 12'd1;
//             end else begin
//                 sx_q <= sx + 12'd1;
//                 sy_q <= sy;
//             end
//         end
//     end

//     wire in_win = (sx < GW) && (sy < GH);
//     wire [13:0] disp_addr = {sy[6:0], sx[6:0]};
//     wire [13:0] port_addr = disp_addr;

//     reg [13:0] port_addr_q;
//     reg [7:0] port_data_q;
//     reg [7:0] port_data_qq;

//     always @(posedge aclk) begin
//         port_addr_q  <= port_addr;
//         port_data_q  <= mem[port_addr_q];
//         port_data_qq <= port_data_q;
//     end

//     // ★ 빼기(-128)를 레지스터 앞에 두면 BRAM 추론이 안 되므로,
//     //   BRAM 동기 읽기(cnn_rd_raw) 후에 조합으로 뺍니다. 레이턴시는
//     //   기존과 동일하게 주소 후 1사이클입니다.
//     reg [7:0] cnn_rd_raw;
//     always @(posedge aclk) begin
//         cnn_rd_raw <= mem[cnn_rd_addr];
//     end
//     assign cnn_rd_data = cnn_rd_raw - 8'd128;

//     always @(posedge aclk) begin
//         spi_rd_data <= mem[spi_rd_addr];
//     end

//     reg vld_d1, vld_d2;
//     reg lst_d1, lst_d2;
//     reg usr_d1, usr_d2;
//     reg win_d1, win_d2;

//     always @(posedge aclk) begin
//         if (!rst_n_i) begin
//             vld_d1 <= 1'b0;
//             vld_d2 <= 1'b0;
//         end else if (m_axis_tready) begin
//             vld_d1 <= s_axis_tvalid;
//             lst_d1 <= s_axis_tlast;
//             usr_d1 <= s_axis_tuser;
//             win_d1 <= in_win;

//             vld_d2 <= vld_d1;
//             lst_d2 <= lst_d1;
//             usr_d2 <= usr_d1;
//             win_d2 <= win_d1;
//         end
//     end

//     assign m_axis_tdata  = win_d2 ? {port_data_qq, port_data_qq, port_data_qq} : 24'd0;
//     assign m_axis_tvalid = vld_d2;
//     assign m_axis_tlast = lst_d2;
//     assign m_axis_tuser = usr_d2;
//     assign s_axis_tready = m_axis_tready;

// endmodule

