`timescale 1ns / 1ps

// Stores the four NUM_CH-wide convolution result vectors that belong to one
// 2x2 output region, then serializes either:
//   MaxPool_en=1 : one pooled value per channel      (NUM_CH writes)
//   MaxPool_en=0 : four unpooled values per channel  (4*NUM_CH writes)
//
// ReLU is independently selectable in both paths. Output data and indices
// remain stable until output_valid && output_ready.
module CH_Result_Buffer #(
    parameter integer NUM_CH = 8,
    parameter integer DATA_WIDTH = 8,
    parameter logic PACKED_OUTPUT = 1'b0,
    parameter integer CH_INDEX_WIDTH =
        (NUM_CH <= 1) ? 1 : $clog2(NUM_CH)
) (
    input logic clk,
    input logic rst_n,
    input logic clear,

    input logic MaxPool_en,
    input logic Relu_en,

    input  logic signed [DATA_WIDTH-1:0] conv_data [0:NUM_CH-1],
    input  logic [NUM_CH-1:0]            conv_valid,
    output logic [NUM_CH-1:0]            conv_ready,

    output logic signed [DATA_WIDTH-1:0]  output_data,
    output logic signed [DATA_WIDTH-1:0]  output_data_vec [0:NUM_CH-1],
    output logic                          output_valid,
    input  logic                          output_ready,
    output logic [CH_INDEX_WIDTH-1:0]     output_channel,
    output logic [1:0]                    output_position,
    output logic                          output_done
);

    typedef enum logic [1:0] {
        CAPTURE_RESULTS,
        OUTPUT_RESULTS
    } state_t;

    state_t state;

    logic signed [DATA_WIDTH-1:0] conv_mem [0:3][0:NUM_CH-1];
    logic signed [DATA_WIDTH-1:0] pool_max_mem [0:NUM_CH-1];
    logic [1:0] capture_position;
    logic [1:0] output_position_reg;
    logic [CH_INDEX_WIDTH-1:0] output_channel_reg;
    logic MaxPool_en_reg;
    logic Relu_en_reg;

    logic all_conv_valid;
    logic capture_fire;
    logic output_fire;
    logic signed [DATA_WIDTH-1:0] selected_data;
    localparam logic [CH_INDEX_WIDTH-1:0] LAST_OUTPUT_CHANNEL =
        NUM_CH - 1;

    integer capture_ch;
    integer reset_ch;
    integer reset_pos;
    integer output_ch;

    assign all_conv_valid = &conv_valid;
    assign conv_ready =
        ((state == CAPTURE_RESULTS) && all_conv_valid)
            ? {NUM_CH{1'b1}} : {NUM_CH{1'b0}};
    assign capture_fire =
        (state == CAPTURE_RESULTS) && all_conv_valid;

    assign output_valid    = (state == OUTPUT_RESULTS);
    assign output_channel  = PACKED_OUTPUT
        ? LAST_OUTPUT_CHANNEL : output_channel_reg;
    assign output_position = output_position_reg;
    assign output_fire     = output_valid && output_ready;

    assign output_done =
        output_fire
        && (PACKED_OUTPUT
            || (output_channel_reg == NUM_CH-1))
        && (MaxPool_en_reg || (output_position_reg == 2'd3));

    // Select either one stored convolution result or the signed maximum of
    // all four results for the currently selected output channel.
    always_comb begin
        for (output_ch = 0; output_ch < NUM_CH; output_ch = output_ch + 1) begin
            selected_data = conv_mem[output_position_reg][output_ch];

            if (MaxPool_en_reg)
                selected_data = pool_max_mem[output_ch];

            if (Relu_en_reg && selected_data[DATA_WIDTH-1])
                output_data_vec[output_ch] = '0;
            else
                output_data_vec[output_ch] = selected_data;
        end

        output_data = output_data_vec[output_channel_reg];
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state              <= CAPTURE_RESULTS;
            capture_position   <= 2'd0;
            output_position_reg <= 2'd0;
            output_channel_reg <= '0;
            MaxPool_en_reg     <= 1'b0;
            Relu_en_reg        <= 1'b0;

            for (reset_pos = 0; reset_pos < 4;
                 reset_pos = reset_pos + 1) begin
                for (reset_ch = 0; reset_ch < NUM_CH;
                     reset_ch = reset_ch + 1) begin
                    conv_mem[reset_pos][reset_ch] <= '0;
                    if (reset_pos == 0)
                        pool_max_mem[reset_ch] <= '0;
                end
            end
        end else if (clear) begin
            state               <= CAPTURE_RESULTS;
            capture_position    <= 2'd0;
            output_position_reg <= 2'd0;
            output_channel_reg  <= '0;
            MaxPool_en_reg      <= MaxPool_en;
            Relu_en_reg         <= Relu_en;
        end else begin
            case (state)
                CAPTURE_RESULTS: begin
                    if (capture_fire) begin
                        for (capture_ch = 0; capture_ch < NUM_CH;
                             capture_ch = capture_ch + 1) begin
                            conv_mem[capture_position][capture_ch]
                                <= conv_data[capture_ch];
                            if (capture_position == 2'd0)
                                pool_max_mem[capture_ch]
                                    <= conv_data[capture_ch];
                            else if ($signed(conv_data[capture_ch])
                                     > $signed(pool_max_mem[capture_ch]))
                                pool_max_mem[capture_ch]
                                    <= conv_data[capture_ch];
                        end

                        if (capture_position == 2'd0) begin
                            MaxPool_en_reg <= MaxPool_en;
                            Relu_en_reg    <= Relu_en;
                        end

                        if (capture_position == 2'd3) begin
                            capture_position    <= 2'd0;
                            output_position_reg <= 2'd0;
                            output_channel_reg  <= '0;
                            state               <= OUTPUT_RESULTS;
                        end else begin
                            capture_position <= capture_position + 1'b1;
                        end
                    end
                end

                OUTPUT_RESULTS: begin
                    if (output_fire) begin
                        if (PACKED_OUTPUT) begin
                            if (MaxPool_en_reg
                                || (output_position_reg == 2'd3)) begin
                                output_position_reg <= 2'd0;
                                state <= CAPTURE_RESULTS;
                            end else begin
                                output_position_reg <=
                                    output_position_reg + 1'b1;
                                state <= OUTPUT_RESULTS;
                            end
                        end else if (output_channel_reg == NUM_CH-1) begin
                            output_channel_reg <= '0;

                            if (MaxPool_en_reg
                                || (output_position_reg == 2'd3)) begin
                                output_position_reg <= 2'd0;
                                state <= CAPTURE_RESULTS;
                            end else begin
                                output_position_reg
                                    <= output_position_reg + 1'b1;
                                state <= OUTPUT_RESULTS;
                            end
                        end else begin
                            output_channel_reg
                                <= output_channel_reg + 1'b1;
                            state <= OUTPUT_RESULTS;
                        end
                    end
                end

                default: begin
                    state               <= CAPTURE_RESULTS;
                    capture_position    <= 2'd0;
                    output_position_reg <= 2'd0;
                    output_channel_reg  <= '0;
                end
            endcase
        end
    end
    
endmodule
