`timescale 1ns / 1ps

module fc #(
    parameter WT_ADDR_WIDTH   = 8292,
    parameter WT_DATA_WIDTH   = 64,
    parameter DATA_ADDR_WIDTH = 126 * 126 * 16,
    parameter DATA_DATA_WIDTH = 8
) (
    input  logic                               clk,
    input  logic                               rst_n,
    input  logic [3:0]                         threshold_sel,

    // CNN controller
    input  logic                               start,
    input  logic [2:0]                         layer,

    // 실제 입력 개수가 N개이면 input_length는 N-1이다.
    input  logic [$clog2(1024)-1:0]            input_length,

    // 실제 출력 개수가 N개이면 output_length는 N-1이다.
    input  logic [$clog2(1024)-1:0]            output_length,
    input  logic                               finish_en,
    output logic                               Done,

    // Weight/Bias Buffer read
    output logic [$clog2(WT_ADDR_WIDTH)-1:0]   wt_raddr,
    input  logic [WT_DATA_WIDTH-1:0]           wt_rdata,

    // Data Buffer write
    output logic                               DATA_we,
    output logic [$clog2(DATA_ADDR_WIDTH)-1:0] DATA_waddr,
    output logic [DATA_DATA_WIDTH-1:0]         DATA_wdata,

    // Data Buffer read
    output logic [$clog2(DATA_ADDR_WIDTH)-1:0] DATA_raddr,
    input  logic [DATA_DATA_WIDTH-1:0]         DATA_rdata,

    // Final FC result
    output logic                               result,
    output logic signed [7:0]                 quantized_write_s8,
    output logic signed [8:0]                 quantized_write_s9
);

    localparam WT_ADDR_BITS = $clog2(WT_ADDR_WIDTH);
    localparam DATA_ADDR_BITS = $clog2(DATA_ADDR_WIDTH);

    logic [   WT_ADDR_BITS-1:0] core_weight_read_offset;
    logic [  WT_DATA_WIDTH-1:0] core_weight_read_data;

    logic [ DATA_ADDR_BITS-1:0] core_data_read_offset;
    logic [DATA_DATA_WIDTH-1:0] core_data_read_data;

    logic                       core_data_write_en;
    logic [ DATA_ADDR_BITS-1:0] core_data_write_offset;
    logic [DATA_DATA_WIDTH-1:0] core_data_write_data;

    logic                       core_done;
    logic                       core_result;

    // Done과 result는 core의 registered 결과를 추가 지연 없이 전달한다.
    assign Done   = core_done;
    assign result = core_result;

    // 현재 datapath는 64-bit Weight word와 8-bit Data를 전제로 한다.
    // 다른 폭을 위한 padding이나 truncation은 이 wrapper에서 수행하지 않는다.

    // 최대 1024 입력/출력 구성에서는 기본 Weight depth가 부족할 수 있다.
    // 실제 통합 시 전체 group을 수용하는 외부 memory depth가 필요하다.

    fc_core #(
        .WT_ADDR_WIDTH    (WT_ADDR_WIDTH),
        .WT_DATA_WIDTH    (WT_DATA_WIDTH),
        .DATA_ADDR_WIDTH  (DATA_ADDR_WIDTH),
        .DATA_DATA_WIDTH  (DATA_DATA_WIDTH),
        .MAX_INPUT_LENGTH (1024),
        .MAX_OUTPUT_LENGTH(1024)
    ) u_fc_core (
        .clk                    (clk),
        .rst_n                  (rst_n),
        .threshold_sel          (threshold_sel),
        .start                  (start),
        .finish_en              (finish_en),
        .layer                  (layer),
        .input_length           (input_length),
        .output_length          (output_length),
        .Done                   (core_done),
        .result                 (core_result),
        .core_weight_read_offset(core_weight_read_offset),
        .core_weight_read_data  (core_weight_read_data),
        .core_data_read_offset  (core_data_read_offset),
        .core_data_read_data    (core_data_read_data),
        .core_data_write_en     (core_data_write_en),
        .core_data_write_offset (core_data_write_offset),
        .core_data_write_data   (core_data_write_data),
        .quantized_write_s8     (quantized_write_s8),
        .quantized_write_s9     (quantized_write_s9)
    );

    fc_memory_adapter #(
        .WT_ADDR_WIDTH  (WT_ADDR_WIDTH),
        .WT_DATA_WIDTH  (WT_DATA_WIDTH),
        .DATA_ADDR_WIDTH(DATA_ADDR_WIDTH),
        .DATA_DATA_WIDTH(DATA_DATA_WIDTH)
    ) u_fc_memory_adapter (
        .core_weight_read_offset(core_weight_read_offset),
        .core_data_read_offset  (core_data_read_offset),
        .core_data_write_en     (core_data_write_en),
        .core_data_write_offset (core_data_write_offset),
        .core_data_write_data   (core_data_write_data),
        .core_weight_read_data  (core_weight_read_data),
        .core_data_read_data    (core_data_read_data),
        .wt_raddr               (wt_raddr),
        .wt_rdata               (wt_rdata),
        .DATA_we                (DATA_we),
        .DATA_waddr             (DATA_waddr),
        .DATA_wdata             (DATA_wdata),
        .DATA_raddr             (DATA_raddr),
        .DATA_rdata             (DATA_rdata)
    );

endmodule
