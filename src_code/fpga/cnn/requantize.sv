`timescale 1ns / 1ps

module requantize #(
    parameter [4:0] SHIFT_VAL_L1 = 5'd20,
    parameter [4:0] SHIFT_VAL_L2 = 5'd20,
    parameter [4:0] SHIFT_VAL_L3 = 5'd20,
    parameter [4:0] SHIFT_VAL_L4 = 5'd20,
    parameter [4:0] SHIFT_VAL_L5 = 5'd20,
    parameter signed [31:0] MULT_FACTOR_L1 = 32'sd2734,
    parameter signed [31:0] MULT_FACTOR_L2 = 32'sd2845,
    parameter signed [31:0] MULT_FACTOR_L3 = 32'sd3104,
    parameter signed [31:0] MULT_FACTOR_L4 = 32'sd4128,
    parameter signed [31:0] MULT_FACTOR_L5 = 32'sd8257,
    parameter signed [7:0] ZERO_POINT_L1 = 8'sd0,
    parameter signed [7:0] ZERO_POINT_L2 = 8'sd0,
    parameter signed [7:0] ZERO_POINT_L3 = 8'sd0,
    parameter signed [7:0] ZERO_POINT_L4 = 8'sd0,
    parameter signed [7:0] ZERO_POINT_L5 = 8'sd0
) (
    input  logic        [ 2:0] layer,
    input  logic signed [31:0] acc_in,
    output logic signed [ 7:0] quant_out
);

    logic [4:0] shift_val;
    logic signed [31:0] mult_factor;
    logic signed [7:0] zero_point;
    logic signed [63:0] scaled_acc;
    logic signed [63:0] rounded_acc;
    logic signed [63:0] shifted_acc;
    logic signed [63:0] round_bias;
    logic signed [63:0] quantized_acc;
    logic bypass_requantize;

    always_comb begin
        // Layer encoding from CNN_acc_controller.
        // pool4/pool5:0, conv1:1, conv2:2, conv3:3, fc1:4, fc2:5
        // XLSX exports use the form:
        //   quantized = (acc * MULT_FACTOR) >> SHIFT_VAL + ZERO_POINT
        // so the HDL parameters are set to match that convention.
        bypass_requantize = 1'b0;
        case (layer)
            3'd0: begin
                // Pool4/Pool5 receive and produce int8 feature data.
                // No accumulator-domain scaling is required.
                shift_val = 5'd0;
                mult_factor = 32'sd1;
                zero_point = 8'sd0;
                bypass_requantize = 1'b1;
            end
            3'd1: begin
                shift_val   = SHIFT_VAL_L1;
                mult_factor = MULT_FACTOR_L1;
                zero_point  = ZERO_POINT_L1;
            end
            3'd2: begin
                shift_val   = SHIFT_VAL_L2;
                mult_factor = MULT_FACTOR_L2;
                zero_point  = ZERO_POINT_L2;
            end
            3'd3: begin
                shift_val   = SHIFT_VAL_L3;
                mult_factor = MULT_FACTOR_L3;
                zero_point  = ZERO_POINT_L3;
            end
            3'd4: begin
                shift_val   = SHIFT_VAL_L4;
                mult_factor = MULT_FACTOR_L4;
                zero_point  = ZERO_POINT_L4;
            end
            3'd5: begin
                shift_val   = SHIFT_VAL_L5;
                mult_factor = MULT_FACTOR_L5;
                zero_point  = ZERO_POINT_L5;
            end
            default: begin
                shift_val   = SHIFT_VAL_L1;
                mult_factor = MULT_FACTOR_L1;
                zero_point  = ZERO_POINT_L1;
            end
        endcase

        scaled_acc = $signed(acc_in) * mult_factor;

        if (shift_val == 0) begin
            rounded_acc = scaled_acc;
        end else begin
            round_bias = 64'sd1 <<< (shift_val - 1);

            if (scaled_acc >= 0) rounded_acc = scaled_acc + round_bias;
            else rounded_acc = scaled_acc - round_bias;
        end

        shifted_acc   = rounded_acc >>> shift_val;
        quantized_acc = shifted_acc + zero_point;

        if (bypass_requantize) quant_out = acc_in[7:0];
        else if (quantized_acc > 64'sd127) quant_out = 8'sd127;
        else if (quantized_acc < -64'sd128) quant_out = -8'sd128;
        else quant_out = quantized_acc[7:0];
    end

endmodule

module requantize_pipeline #(
    parameter [4:0] SHIFT_VAL_L1 = 5'd20,
    parameter [4:0] SHIFT_VAL_L2 = 5'd20,
    parameter [4:0] SHIFT_VAL_L3 = 5'd20,
    parameter [4:0] SHIFT_VAL_L4 = 5'd20,
    parameter [4:0] SHIFT_VAL_L5 = 5'd20,
    parameter signed [31:0] MULT_FACTOR_L1 = 32'sd2396,
    parameter signed [31:0] MULT_FACTOR_L2 = 32'sd3027,
    parameter signed [31:0] MULT_FACTOR_L3 = 32'sd1735,
    parameter signed [31:0] MULT_FACTOR_L4 = 32'sd4128,
    parameter signed [31:0] MULT_FACTOR_L5 = 32'sd13210,  // 1651
    parameter signed [7:0] ZERO_POINT_L1 = 8'sd0,
    parameter signed [7:0] ZERO_POINT_L2 = 8'sd0,
    parameter signed [7:0] ZERO_POINT_L3 = 8'sd0,
    parameter signed [7:0] ZERO_POINT_L4 = 8'sd0,
    parameter signed [7:0] ZERO_POINT_L5 = 8'sd0
) (
    input  logic               clk,
    input  logic               rst_n,
    input  logic               in_valid,
    input  logic        [ 2:0] layer,
    input  logic signed [31:0] acc_in,
    output logic               out_valid,
    output logic signed [ 7:0] quant_out
);

    logic [4:0] cfg_shift_val;
    logic signed [31:0] cfg_mult_factor;
    logic signed [7:0] cfg_zero_point;
    logic cfg_bypass;

    logic signed [31:0] acc_stage1;
    logic signed [31:0] mult_stage1;
    logic [4:0] shift_stage1;
    logic signed [7:0] zero_stage1;
    logic bypass_stage1;
    logic valid_stage1;

    logic signed [63:0] product_stage2;
    logic [4:0] shift_stage2;
    logic signed [7:0] zero_stage2;
    logic bypass_stage2;
    logic signed [31:0] bypass_data_stage2;
    logic valid_stage2;

    logic signed [63:0] round_bias;
    logic signed [63:0] rounded_acc;
    logic signed [63:0] shifted_acc;
    logic signed [63:0] quantized_acc;
    logic signed [7:0] quantized_next;
    logic signed [63:0] rounded_stage3;
    logic [4:0] shift_stage3;
    logic signed [7:0] zero_stage3;
    logic bypass_stage3;
    logic signed [31:0] bypass_data_stage3;
    logic signed [7:0] quant_stage4;
    logic valid_stage3;
    logic valid_stage4;

    always_comb begin
        cfg_shift_val = SHIFT_VAL_L1;
        cfg_mult_factor = MULT_FACTOR_L1;
        cfg_zero_point = ZERO_POINT_L1;
        cfg_bypass = 1'b0;

        case (layer)
            3'd0: begin
                cfg_shift_val = 5'd0;
                cfg_mult_factor = 32'sd1;
                cfg_zero_point = 8'sd0;
                cfg_bypass = 1'b1;
            end
            3'd1: begin
                cfg_shift_val   = SHIFT_VAL_L1;
                cfg_mult_factor = MULT_FACTOR_L1;
                cfg_zero_point  = ZERO_POINT_L1;
            end
            3'd2: begin
                cfg_shift_val   = SHIFT_VAL_L2;
                cfg_mult_factor = MULT_FACTOR_L2;
                cfg_zero_point  = ZERO_POINT_L2;
            end
            3'd3: begin
                cfg_shift_val   = SHIFT_VAL_L3;
                cfg_mult_factor = MULT_FACTOR_L3;
                cfg_zero_point  = ZERO_POINT_L3;
            end
            3'd4: begin
                cfg_shift_val   = SHIFT_VAL_L4;
                cfg_mult_factor = MULT_FACTOR_L4;
                cfg_zero_point  = ZERO_POINT_L4;
            end
            3'd5: begin
                cfg_shift_val   = SHIFT_VAL_L5;
                cfg_mult_factor = MULT_FACTOR_L5;
                cfg_zero_point  = ZERO_POINT_L5;
            end
            default: begin
            end
        endcase
    end

    always_comb begin
        shifted_acc   = rounded_stage3 >>> shift_stage3;
        quantized_acc = shifted_acc + zero_stage3;

        if (bypass_stage3) quantized_next = bypass_data_stage3[7:0];
        else if (quantized_acc > 64'sd127) quantized_next = 8'sd127;
        else if (quantized_acc < -64'sd128) quantized_next = -8'sd128;
        else quantized_next = quantized_acc[7:0];
    end

    always_comb begin
        if (shift_stage2 == 0) begin
            rounded_acc = product_stage2;
        end else begin
            round_bias = 64'sd1 <<< (shift_stage2 - 1'b1);
            rounded_acc = (product_stage2 >= 0)
                ? product_stage2 + round_bias
                : product_stage2 - round_bias;
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            acc_stage1 <= '0;
            mult_stage1 <= '0;
            shift_stage1 <= '0;
            zero_stage1 <= '0;
            bypass_stage1 <= 1'b0;
            valid_stage1 <= 1'b0;
            product_stage2 <= '0;
            shift_stage2 <= '0;
            zero_stage2 <= '0;
            bypass_stage2 <= 1'b0;
            bypass_data_stage2 <= '0;
            valid_stage2 <= 1'b0;
            rounded_stage3 <= '0;
            shift_stage3 <= '0;
            zero_stage3 <= '0;
            bypass_stage3 <= 1'b0;
            bypass_data_stage3 <= '0;
            valid_stage3 <= 1'b0;
            quant_stage4 <= '0;
            valid_stage4 <= 1'b0;
            quant_out <= '0;
            out_valid <= 1'b0;
        end else begin
            valid_stage1 <= in_valid;
            if (in_valid) begin
                acc_stage1 <= acc_in;
                mult_stage1 <= cfg_mult_factor;
                shift_stage1 <= cfg_shift_val;
                zero_stage1 <= cfg_zero_point;
                bypass_stage1 <= cfg_bypass;
            end

            valid_stage2 <= valid_stage1;
            if (valid_stage1) begin
                product_stage2 <= $signed(acc_stage1) * mult_stage1;
                shift_stage2 <= shift_stage1;
                zero_stage2 <= zero_stage1;
                bypass_stage2 <= bypass_stage1;
                bypass_data_stage2 <= acc_stage1;
            end

            valid_stage3 <= valid_stage2;
            if (valid_stage2) begin
                rounded_stage3 <= rounded_acc;
                shift_stage3 <= shift_stage2;
                zero_stage3 <= zero_stage2;
                bypass_stage3 <= bypass_stage2;
                bypass_data_stage3 <= bypass_data_stage2;
            end

            valid_stage4 <= valid_stage3;
            if (valid_stage3) quant_stage4 <= quantized_next;

            out_valid <= valid_stage4;
            if (valid_stage4) quant_out <= quant_stage4;
        end
    end

endmodule
