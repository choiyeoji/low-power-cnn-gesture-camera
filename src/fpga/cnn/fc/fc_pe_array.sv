`timescale 1ns / 1ps

module fc_pe_array (
    input  logic                     clk,
    input  logic                     rst_n,
    input  logic signed [7:0]        activation_u8,
    input  logic                     bias_load,
    input  logic                     mac_valid,
    input  logic        [63:0]       weight_word,
    input  logic signed [31:0]       bias_0_s32,
    input  logic signed [31:0]       bias_1_s32,
    input  logic signed [31:0]       bias_2_s32,
    input  logic signed [31:0]       bias_3_s32,
    input  logic signed [31:0]       bias_4_s32,
    input  logic signed [31:0]       bias_5_s32,
    input  logic signed [31:0]       bias_6_s32,
    input  logic signed [31:0]       bias_7_s32,
    input  logic        [7:0]        lane_valid,
    output logic signed [31:0]       accumulator_0_s32,
    output logic signed [31:0]       accumulator_1_s32,
    output logic signed [31:0]       accumulator_2_s32,
    output logic signed [31:0]       accumulator_3_s32,
    output logic signed [31:0]       accumulator_4_s32,
    output logic signed [31:0]       accumulator_5_s32,
    output logic signed [31:0]       accumulator_6_s32,
    output logic signed [31:0]       accumulator_7_s32
);

    logic signed [7:0] weight_0_s8;
    logic signed [7:0] weight_1_s8;
    logic signed [7:0] weight_2_s8;
    logic signed [7:0] weight_3_s8;
    logic signed [7:0] weight_4_s8;
    logic signed [7:0] weight_5_s8;
    logic signed [7:0] weight_6_s8;
    logic signed [7:0] weight_7_s8;

    logic        [7:0] pe_bias_load;

    // 64-bit weight word의 각 byte를 PE별 signed int8 weight로 해석한다.
    assign weight_0_s8 = $signed(weight_word[7:0]);
    assign weight_1_s8 = $signed(weight_word[15:8]);
    assign weight_2_s8 = $signed(weight_word[23:16]);
    assign weight_3_s8 = $signed(weight_word[31:24]);
    assign weight_4_s8 = $signed(weight_word[39:32]);
    assign weight_5_s8 = $signed(weight_word[47:40]);
    assign weight_6_s8 = $signed(weight_word[55:48]);
    assign weight_7_s8 = $signed(weight_word[63:56]);

    // 무효 lane은 bias load를 포함해 accumulator를 갱신하지 않는다.
    assign pe_bias_load = {8{bias_load}} & lane_valid;

    fc_pe u_fc_pe_0 (
        .clk             (clk),
        .rst_n           (rst_n),
        .activation_u8   (activation_u8),
        .weight_s8       (weight_0_s8),
        .bias_s32        (bias_0_s32),
        .bias_load       (pe_bias_load[0]),
        .mac_valid       (mac_valid),
        .lane_valid      (lane_valid[0]),
        .accumulator_s32 (accumulator_0_s32)
    );

    fc_pe u_fc_pe_1 (
        .clk             (clk),
        .rst_n           (rst_n),
        .activation_u8   (activation_u8),
        .weight_s8       (weight_1_s8),
        .bias_s32        (bias_1_s32),
        .bias_load       (pe_bias_load[1]),
        .mac_valid       (mac_valid),
        .lane_valid      (lane_valid[1]),
        .accumulator_s32 (accumulator_1_s32)
    );

    fc_pe u_fc_pe_2 (
        .clk             (clk),
        .rst_n           (rst_n),
        .activation_u8   (activation_u8),
        .weight_s8       (weight_2_s8),
        .bias_s32        (bias_2_s32),
        .bias_load       (pe_bias_load[2]),
        .mac_valid       (mac_valid),
        .lane_valid      (lane_valid[2]),
        .accumulator_s32 (accumulator_2_s32)
    );

    fc_pe u_fc_pe_3 (
        .clk             (clk),
        .rst_n           (rst_n),
        .activation_u8   (activation_u8),
        .weight_s8       (weight_3_s8),
        .bias_s32        (bias_3_s32),
        .bias_load       (pe_bias_load[3]),
        .mac_valid       (mac_valid),
        .lane_valid      (lane_valid[3]),
        .accumulator_s32 (accumulator_3_s32)
    );

    fc_pe u_fc_pe_4 (
        .clk             (clk),
        .rst_n           (rst_n),
        .activation_u8   (activation_u8),
        .weight_s8       (weight_4_s8),
        .bias_s32        (bias_4_s32),
        .bias_load       (pe_bias_load[4]),
        .mac_valid       (mac_valid),
        .lane_valid      (lane_valid[4]),
        .accumulator_s32 (accumulator_4_s32)
    );

    fc_pe u_fc_pe_5 (
        .clk             (clk),
        .rst_n           (rst_n),
        .activation_u8   (activation_u8),
        .weight_s8       (weight_5_s8),
        .bias_s32        (bias_5_s32),
        .bias_load       (pe_bias_load[5]),
        .mac_valid       (mac_valid),
        .lane_valid      (lane_valid[5]),
        .accumulator_s32 (accumulator_5_s32)
    );

    fc_pe u_fc_pe_6 (
        .clk             (clk),
        .rst_n           (rst_n),
        .activation_u8   (activation_u8),
        .weight_s8       (weight_6_s8),
        .bias_s32        (bias_6_s32),
        .bias_load       (pe_bias_load[6]),
        .mac_valid       (mac_valid),
        .lane_valid      (lane_valid[6]),
        .accumulator_s32 (accumulator_6_s32)
    );

    fc_pe u_fc_pe_7 (
        .clk             (clk),
        .rst_n           (rst_n),
        .activation_u8   (activation_u8),
        .weight_s8       (weight_7_s8),
        .bias_s32        (bias_7_s32),
        .bias_load       (pe_bias_load[7]),
        .mac_valid       (mac_valid),
        .lane_valid      (lane_valid[7]),
        .accumulator_s32 (accumulator_7_s32)
    );

endmodule
