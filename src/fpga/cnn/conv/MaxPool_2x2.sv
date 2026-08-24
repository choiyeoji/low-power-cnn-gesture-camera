`timescale 1ns / 1ps

// Collects four signed convolution results and keeps their maximum.
//
// Expected CH result order:
//   0: top-left
//   1: top-right
//   2: bottom-left
//   3: bottom-right
//
// pool_valid remains high and both outputs remain stable until pool_ready is
// accepted. pool_data exposes the signed maximum for debugging, while
// relu_data is the non-negative value that must be sent to the output MUX.
module MaxPool_2x2 #(
    parameter integer DATA_WIDTH = 8
) (
    input logic clk,
    input logic rst_n,
    input logic clear,

    // CH result input.
    input  logic signed [DATA_WIDTH-1:0] conv_data,
    input  logic                         conv_valid,
    output logic                         conv_ready,

    // Stored max-pooling result.
    output logic signed [DATA_WIDTH-1:0] pool_data,
    output logic signed [DATA_WIDTH-1:0] relu_data,
    output logic                         pool_valid,
    input  logic                         pool_ready
);

    logic signed [DATA_WIDTH-1:0] max_reg;
    logic [1:0]                   conv_count;
    logic                         conv_fire;

    assign conv_ready = !pool_valid;
    assign conv_fire  = conv_valid && conv_ready;

    // ReLU is placed directly after the registered max-pooling result.
    assign relu_data =
        pool_data[DATA_WIDTH-1] ? {DATA_WIDTH{1'b0}} : pool_data;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            max_reg   <= '0;
            conv_count <= 2'd0;
            pool_data <= '0;
            pool_valid <= 1'b0;
        end else if (clear) begin
            max_reg   <= '0;
            conv_count <= 2'd0;
            pool_data <= '0;
            pool_valid <= 1'b0;
        end else begin
            if (pool_valid) begin
                // Hold pool_data until the shared write path accepts it.
                if (pool_ready)
                    pool_valid <= 1'b0;
            end else if (conv_fire) begin
                case (conv_count)
                    2'd0: begin
                        max_reg   <= conv_data;
                        conv_count <= 2'd1;
                    end

                    2'd1: begin
                        if ($signed(conv_data) > $signed(max_reg))
                            max_reg <= conv_data;

                        conv_count <= 2'd2;
                    end

                    2'd2: begin
                        if ($signed(conv_data) > $signed(max_reg))
                            max_reg <= conv_data;

                        conv_count <= 2'd3;
                    end

                    default: begin
                        if ($signed(conv_data) > $signed(max_reg))
                            pool_data <= conv_data;
                        else
                            pool_data <= max_reg;

                        pool_valid <= 1'b1;
                        conv_count <= 2'd0;
                    end
                endcase
            end
        end
    end

endmodule
