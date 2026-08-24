`timescale 1ns / 1ps

// Selects which processing engine owns the external ping-pong feature buffer.
//
// select_pool=0: Conv engine owns the buffer.
// select_pool=1: Standalone_MaxPool owns the buffer.
//
// The unselected engine receives zero read data and cannot issue a write.
module Feature_Buffer_Mux #(
    parameter integer DATA_WIDTH = 8,
    parameter integer ADDR_WIDTH = 32
) (
    input logic select_pool,

    // Conv engine side.
    input  logic [ADDR_WIDTH-1:0]         conv_rAddr,
    output logic signed [DATA_WIDTH-1:0] conv_rData,
    input  logic                          conv_we,
    input  logic                          conv_w_sel,
    input  logic [ADDR_WIDTH-1:0]         conv_wAddr,
    input  logic signed [DATA_WIDTH-1:0] conv_wData,

    // Standalone MaxPool engine side.
    input  logic [ADDR_WIDTH-1:0]         pool_rAddr,
    output logic signed [DATA_WIDTH-1:0] pool_rData,
    input  logic                          pool_we,
    input  logic                          pool_w_sel,
    input  logic [ADDR_WIDTH-1:0]         pool_wAddr,
    input  logic signed [DATA_WIDTH-1:0] pool_wData,

    // External pingpongBuffer side.
    output logic [ADDR_WIDTH-1:0]         buffer_rAddr,
    input  logic signed [DATA_WIDTH-1:0] buffer_rData,
    output logic                          buffer_we,
    output logic                          buffer_w_sel,
    output logic [ADDR_WIDTH-1:0]         buffer_wAddr,
    output logic signed [DATA_WIDTH-1:0] buffer_wData
);

    always_comb begin
        buffer_rAddr = conv_rAddr;
        buffer_we    = conv_we;
        buffer_w_sel = conv_w_sel;
        buffer_wAddr = conv_wAddr;
        buffer_wData = conv_wData;

        conv_rData = buffer_rData;
        pool_rData = '0;

        if (select_pool) begin
            buffer_rAddr = pool_rAddr;
            buffer_we    = pool_we;
            buffer_w_sel = pool_w_sel;
            buffer_wAddr = pool_wAddr;
            buffer_wData = pool_wData;

            conv_rData = '0;
            pool_rData = buffer_rData;
        end
    end

endmodule
