`timescale 1ns / 1ps

// Selects one ReLU result from MaxPool_wrapper for the shared Data Buffer
// write port. When the selected result is valid and select_enable is high,
// ch_wvalid and exactly one pool_ready bit are asserted together.
module Output_Mux #(
    parameter integer NUM_CH       = 8,
    parameter integer DATA_WIDTH   = 8,
    parameter integer SELECT_WIDTH =
        (NUM_CH <= 1) ? 1 : $clog2(NUM_CH)
) (
    // MaxPool_wrapper interface.
    input logic signed [DATA_WIDTH-1:0] relu_data [0:NUM_CH-1],
    input logic [NUM_CH-1:0]     pool_valid,

    // Conv Controller interface.
    input logic [SELECT_WIDTH-1:0] channel_select,
    input logic                    select_enable,

    // Shared Data Buffer write-data interface.
    output logic signed [DATA_WIDTH-1:0] ch_wdata,
    output logic                  ch_wvalid,
    input  logic                  ch_wready,

    // One-hot acknowledgement back to MaxPool_wrapper.
    output logic [NUM_CH-1:0] pool_ready
);

    always_comb begin
        ch_wdata   = {DATA_WIDTH{1'b0}};
        ch_wvalid  = 1'b0;
        pool_ready = {NUM_CH{1'b0}};

        // The range check also handles NUM_CH values that are not powers of 2.
        if (channel_select < NUM_CH) begin
            ch_wdata = relu_data[channel_select];

            if (select_enable && pool_valid[channel_select]) begin
                ch_wvalid = 1'b1;

                if (ch_wready)
                    pool_ready[channel_select] = 1'b1;
            end
        end
    end

endmodule
