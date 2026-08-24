`timescale 1ns / 1ps

module fc_core #(
    parameter WT_ADDR_WIDTH     = 10 * 128,
    parameter WT_DATA_WIDTH     = 64,
    parameter DATA_ADDR_WIDTH   = 126 * 126 * 16,
    parameter DATA_DATA_WIDTH   = 8,
    parameter MAX_INPUT_LENGTH  = 128,
    parameter MAX_OUTPUT_LENGTH = 128,
    parameter ACC_WIDTH         = 32
) (
    input logic clk,
    input logic rst_n,
    input logic [3:0] threshold_sel,
    input logic start,
    input logic finish_en,
    input logic [2:0] layer,
    input logic [$clog2(MAX_INPUT_LENGTH)-1:0] input_length,
    input logic [$clog2(MAX_OUTPUT_LENGTH)-1:0] output_length,

    output logic Done,
    output logic result,

    output logic [$clog2(WT_ADDR_WIDTH)-1:0] core_weight_read_offset,
    input logic [WT_DATA_WIDTH-1:0] core_weight_read_data,

    output logic [$clog2(DATA_ADDR_WIDTH)-1:0] core_data_read_offset,
    input logic [DATA_DATA_WIDTH-1:0] core_data_read_data,

    output logic core_data_write_en,
    output logic [$clog2(DATA_ADDR_WIDTH)-1:0] core_data_write_offset,
    output logic [DATA_DATA_WIDTH-1:0] core_data_write_data,
    output logic signed [7:0] quantized_write_s8,
    output logic signed [8:0] quantized_write_s9
);

    localparam INPUT_INDEX_WIDTH = $clog2(MAX_INPUT_LENGTH);
    localparam OUTPUT_INDEX_WIDTH = $clog2(MAX_OUTPUT_LENGTH);
    localparam WT_ADDR_BITS = $clog2(WT_ADDR_WIDTH);
    localparam DATA_ADDR_BITS = $clog2(DATA_ADDR_WIDTH);

    // Final FC output is classified in the int8 requantized domain.
    // threshold_sel maps to 16 equal bins over int8 range:
    // 0 -> -128, 1 -> -112, ..., 8 -> 0, ..., 15 -> +112.
    logic signed [8:0] output_threshold_s9;

    logic               start_accept;
    logic               controller_busy;
    logic               pe_acc_clear;
    logic               output_bias_add_en;
    logic        [ 1:0] output_bias_word_index;
    logic               pe_mac_enable;
    logic               pe_array_mac_valid;
    logic        [ 7:0] lane_valid;
    (* max_fanout = 8 *)
    logic signed [ 7:0] activation_reg;
    logic        [63:0] weight_word_reg;
    logic        [ 7:0] mac_lane_valid_reg;
    logic               output_capture_en;
    logic        [ 2:0] output_read_lane_index;
    logic               quant_acc_capture_en;
    logic               quant_result_capture_en;
    logic               result_capture_en;
    logic               controller_data_write_en;
    logic [DATA_ADDR_BITS-1:0] controller_data_write_offset;
    logic [DATA_DATA_WIDTH-1:0] controller_data_write_data;
    logic signed [ 7:0] activation_s8;
    logic               quantized_write_valid;
    logic signed [31:0] quant_accumulator_s32;
    logic signed [ 7:0] quantized_result_s8;
    logic signed [ 7:0] fc_write_s8;

    logic signed [31:0] accumulator_0_s32;
    logic signed [31:0] accumulator_1_s32;
    logic signed [31:0] accumulator_2_s32;
    logic signed [31:0] accumulator_3_s32;
    logic signed [31:0] accumulator_4_s32;
    logic signed [31:0] accumulator_5_s32;
    logic signed [31:0] accumulator_6_s32;
    logic signed [31:0] accumulator_7_s32;

    logic signed [31:0] buffered_accumulator_0_s32;
    logic signed [31:0] buffered_accumulator_1_s32;
    logic signed [31:0] buffered_accumulator_2_s32;
    logic signed [31:0] buffered_accumulator_3_s32;
    logic signed [31:0] buffered_accumulator_4_s32;
    logic signed [31:0] buffered_accumulator_5_s32;
    logic signed [31:0] buffered_accumulator_6_s32;
    logic signed [31:0] buffered_accumulator_7_s32;
    logic signed [31:0] selected_accumulator_s32;

    // Register both memory operands before the PE MAC. The controller drains
    // the final registered operand before capturing the accumulators.
    assign pe_array_mac_valid = mac_lane_valid_reg != 8'b0;

    assign activation_s8 = $signed(core_data_read_data[7:0]);
    assign fc_write_s8 = (layer == 3'd4 && quantized_result_s8 < 0)
        ? 8'sd0 : quantized_result_s8;
    assign controller_data_write_data = fc_write_s8;
    assign output_threshold_s9 =
        $signed({1'b0, threshold_sel, 4'b0000}) - 9'sd128;
    assign quantized_write_s9 = {quantized_write_s8[7], quantized_write_s8};

    // Keep the controller FSM and requantize mux off the Data Buffer write
    // path. The synchronous RAM receives this stable registered command.
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            activation_reg         <= 8'sd0;
            weight_word_reg        <= 64'd0;
            mac_lane_valid_reg     <= 8'b0;
            core_data_write_en     <= 1'b0;
            core_data_write_offset <= '0;
            core_data_write_data   <= '0;
            quant_accumulator_s32  <= 32'sd0;
            quantized_result_s8    <= 8'sd0;
        end else begin
            mac_lane_valid_reg <= pe_mac_enable ? lane_valid : 8'b0;
            if (pe_mac_enable) begin
                activation_reg  <= $signed(core_data_read_data[7:0]);
                weight_word_reg <= core_weight_read_data;
            end

            if (quant_acc_capture_en)
                quant_accumulator_s32 <= selected_accumulator_s32;

            if (quant_result_capture_en)
                quantized_result_s8 <= quantized_write_s8;

            core_data_write_en     <= controller_data_write_en;
            core_data_write_offset <= controller_data_write_offset;
            core_data_write_data   <= controller_data_write_data;
        end
    end

    // 기본 Weight depth 1280은 최대 128x128 구성의 2112 words보다 작다.
    // 외부 통합 전 실제 Buffer depth로 override하거나 팀 사양을 확정해야 한다.

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            result <= 1'b0;
        end else if (start_accept) begin
            // 새 연산을 수락하면 이전 최종 결과를 지운다.
            result <= 1'b0;
        end else if (result_capture_en) begin
            result <= (quantized_write_s9 >= output_threshold_s9);
        end
    end

    requantize_pipeline U_REQUANTIZE_FC_WRITE (
        .clk      (clk),
        .rst_n    (rst_n),
        .in_valid (quant_acc_capture_en),
        .layer    (layer),
        .acc_in   (selected_accumulator_s32),
        .out_valid(quantized_write_valid),
        .quant_out(quantized_write_s8)
    );

    fc_controller #(
        .MAX_INPUT_LENGTH (MAX_INPUT_LENGTH),
        .MAX_OUTPUT_LENGTH(MAX_OUTPUT_LENGTH),
        .WT_ADDR_WIDTH    (WT_ADDR_WIDTH),
        .DATA_ADDR_WIDTH  (DATA_ADDR_WIDTH)
    ) u_fc_controller (
        .clk                    (clk),
        .rst_n                  (rst_n),
        .start                  (start),
        .input_length           (input_length),
        .output_length          (output_length),
        .finish_en              (finish_en),
        .start_accept           (start_accept),
        .busy                   (controller_busy),
        .Done                   (Done),
        .core_weight_read_offset(core_weight_read_offset),
        .core_data_read_offset  (core_data_read_offset),
        .core_data_write_en     (controller_data_write_en),
        .core_data_write_offset (controller_data_write_offset),
        .pe_acc_clear           (pe_acc_clear),
        .output_bias_add_en     (output_bias_add_en),
        .output_bias_word_index (output_bias_word_index),
        .pe_mac_enable          (pe_mac_enable),
        .lane_valid             (lane_valid),
        .output_capture_en      (output_capture_en),
        .output_read_lane_index (output_read_lane_index),
        .quant_acc_capture_en   (quant_acc_capture_en),
        .quant_result_capture_en(quant_result_capture_en),
        .quant_result_valid     (quantized_write_valid),
        .result_capture_en      (result_capture_en)
    );

    fc_pe_array u_fc_pe_array (
        .clk              (clk),
        .rst_n            (rst_n),
        .activation_u8    (activation_reg),
        .bias_load        (pe_acc_clear),
        .mac_valid        (pe_array_mac_valid),
        .weight_word      (weight_word_reg),
        .bias_0_s32       (32'sd0),
        .bias_1_s32       (32'sd0),
        .bias_2_s32       (32'sd0),
        .bias_3_s32       (32'sd0),
        .bias_4_s32       (32'sd0),
        .bias_5_s32       (32'sd0),
        .bias_6_s32       (32'sd0),
        .bias_7_s32       (32'sd0),
        .lane_valid       (pe_acc_clear ? lane_valid : mac_lane_valid_reg),
        .accumulator_0_s32(accumulator_0_s32),
        .accumulator_1_s32(accumulator_1_s32),
        .accumulator_2_s32(accumulator_2_s32),
        .accumulator_3_s32(accumulator_3_s32),
        .accumulator_4_s32(accumulator_4_s32),
        .accumulator_5_s32(accumulator_5_s32),
        .accumulator_6_s32(accumulator_6_s32),
        .accumulator_7_s32(accumulator_7_s32)
    );

    fc_output_buffer u_fc_output_buffer (
        .clk                       (clk),
        .rst_n                     (rst_n),
        .capture_en                (output_capture_en),
        .bias_add_en               (output_bias_add_en),
        .bias_word_index           (output_bias_word_index),
        .bias_word                 (core_weight_read_data),
        .lane_valid                (lane_valid),
        .read_lane_index           (output_read_lane_index),
        .accumulator_0_s32         (accumulator_0_s32),
        .accumulator_1_s32         (accumulator_1_s32),
        .accumulator_2_s32         (accumulator_2_s32),
        .accumulator_3_s32         (accumulator_3_s32),
        .accumulator_4_s32         (accumulator_4_s32),
        .accumulator_5_s32         (accumulator_5_s32),
        .accumulator_6_s32         (accumulator_6_s32),
        .accumulator_7_s32         (accumulator_7_s32),
        .buffered_accumulator_0_s32(buffered_accumulator_0_s32),
        .buffered_accumulator_1_s32(buffered_accumulator_1_s32),
        .buffered_accumulator_2_s32(buffered_accumulator_2_s32),
        .buffered_accumulator_3_s32(buffered_accumulator_3_s32),
        .buffered_accumulator_4_s32(buffered_accumulator_4_s32),
        .buffered_accumulator_5_s32(buffered_accumulator_5_s32),
        .buffered_accumulator_6_s32(buffered_accumulator_6_s32),
        .buffered_accumulator_7_s32(buffered_accumulator_7_s32),
        .selected_accumulator_s32  (selected_accumulator_s32)
    );

endmodule
