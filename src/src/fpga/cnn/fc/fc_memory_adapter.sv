`timescale 1ns / 1ps

module fc_memory_adapter #(
    parameter WT_ADDR_WIDTH   = 10 * 128,
    parameter WT_DATA_WIDTH   = 64,
    parameter DATA_ADDR_WIDTH = 126 * 126 * 16,
    parameter DATA_DATA_WIDTH = 8
) (
    input  logic [$clog2(WT_ADDR_WIDTH)-1:0]
        core_weight_read_offset,
    input  logic [$clog2(DATA_ADDR_WIDTH)-1:0]
        core_data_read_offset,
    input  logic
        core_data_write_en,
    input  logic [$clog2(DATA_ADDR_WIDTH)-1:0]
        core_data_write_offset,
    input  logic [DATA_DATA_WIDTH-1:0]
        core_data_write_data,

    output logic [WT_DATA_WIDTH-1:0]
        core_weight_read_data,
    output logic [DATA_DATA_WIDTH-1:0]
        core_data_read_data,

    output logic [$clog2(WT_ADDR_WIDTH)-1:0]
        wt_raddr,
    input  logic [WT_DATA_WIDTH-1:0]
        wt_rdata,

    output logic
        DATA_we,
    output logic [$clog2(DATA_ADDR_WIDTH)-1:0]
        DATA_waddr,
    output logic [DATA_DATA_WIDTH-1:0]
        DATA_wdata,
    output logic [$clog2(DATA_ADDR_WIDTH)-1:0]
        DATA_raddr,
    input  logic [DATA_DATA_WIDTH-1:0]
        DATA_rdata
);

    // FC 내부 logical offset을 base address 변환 없이 외부 주소로 전달한다.
    assign wt_raddr   = core_weight_read_offset;
    assign DATA_raddr = core_data_read_offset;
    assign DATA_waddr = core_data_write_offset;
    assign DATA_we    = core_data_write_en;
    assign DATA_wdata = core_data_write_data;

    // 외부 synchronous read data를 추가 가공 없이 core로 전달한다.
    assign core_weight_read_data = wt_rdata;
    assign core_data_read_data   = DATA_rdata;

endmodule
