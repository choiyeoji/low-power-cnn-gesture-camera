`timescale 1ns / 1ps

// One independent 2x2 max-pooling unit for every output channel.
//
// All channels may finish together, while pool_ready can acknowledge them one
// at a time through the shared Data Buffer write path. Each channel keeps its
// pool_data stable until its corresponding pool_ready bit is accepted.
module MaxPool_wrapper #(
    parameter integer NUM_CH     = 8,
    parameter integer DATA_WIDTH = 8
) (
    input logic clk,
    input logic rst_n,
    input logic clear,

    // CH_wrapper interface.
    input  logic signed [DATA_WIDTH-1:0] conv_data [0:NUM_CH-1],
    input  logic [NUM_CH-1:0]            conv_valid,
    output logic [NUM_CH-1:0]            conv_ready,

    // Conv Controller / Data Buffer write interface.
    // Only the ReLU-applied value leaves the wrapper.
    output logic signed [DATA_WIDTH-1:0] relu_data [0:NUM_CH-1],
    output logic [NUM_CH-1:0]            pool_valid,
    input  logic [NUM_CH-1:0]            pool_ready
);

    // Raw signed maxima remain internal for waveform debugging.
    logic signed [DATA_WIDTH-1:0] raw_pool_data [0:NUM_CH-1];

    genvar ch;

    generate
        for (ch = 0; ch < NUM_CH; ch = ch + 1) begin : GEN_MAXPOOL
            MaxPool_2x2 #(
                .DATA_WIDTH(DATA_WIDTH)
            ) U_MAXPOOL (
                .clk       (clk),
                .rst_n     (rst_n),
                .clear     (clear),
                .conv_data (conv_data[ch]),
                .conv_valid(conv_valid[ch]),
                .conv_ready(conv_ready[ch]),
                .pool_data (raw_pool_data[ch]),
                .relu_data (relu_data[ch]),
                .pool_valid(pool_valid[ch]),
                .pool_ready(pool_ready[ch])
            );
        end
    endgenerate

endmodule
