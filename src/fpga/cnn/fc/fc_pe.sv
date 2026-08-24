`timescale 1ns / 1ps

module fc_pe (
    input  logic                     clk,
    input  logic                     rst_n,
    input  logic signed [7:0]        activation_u8,
    input  logic signed [7:0]        weight_s8,
    input  logic signed [31:0]       bias_s32,
    input  logic                     bias_load,
    input  logic                     mac_valid,
    input  logic                     lane_valid,
    output logic signed [31:0]       accumulator_s32
);

    logic signed [8:0]  activation_s9;
    // Vivado hint: map PE multiply to DSP blocks.
    (* use_dsp = "yes" *) logic signed [16:0] product_s17;
    logic signed [31:0] product_s32;
    logic signed [31:0] product_reg;
    logic               product_valid_reg;

    // signed int8 activation의 부호를 유지하도록 signed 9-bit로 확장한다.
    assign activation_s9 = $signed({activation_u8[7], activation_u8});
    assign product_s17   = activation_s9 * weight_s8;

    // signed 17-bit product를 accumulator 폭으로 명시적으로 부호 확장한다.
    assign product_s32 = {{15{product_s17[16]}}, product_s17};

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            accumulator_s32 <= 32'sd0;
            product_reg     <= 32'sd0;
            product_valid_reg <= 1'b0;
        end
        else begin
            product_valid_reg <= mac_valid && lane_valid;
            if (mac_valid && lane_valid)
                product_reg <= product_s32;

            if (bias_load) begin
                // bias load는 같은 클럭의 MAC 요청보다 우선한다.
                accumulator_s32 <= bias_s32;
            end
            else if (product_valid_reg) begin
                accumulator_s32 <= accumulator_s32 + product_reg;
            end
        end
    end

endmodule
