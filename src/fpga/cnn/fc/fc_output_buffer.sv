`timescale 1ns / 1ps

module fc_output_buffer (
    input  logic                     clk,
    input  logic                     rst_n,
    input  logic                     capture_en,
    input  logic                     bias_add_en,
    input  logic        [1:0]        bias_word_index,
    input  logic        [63:0]       bias_word,
    input  logic        [7:0]        lane_valid,
    input  logic        [2:0]        read_lane_index,
    input  logic signed [31:0]       accumulator_0_s32,
    input  logic signed [31:0]       accumulator_1_s32,
    input  logic signed [31:0]       accumulator_2_s32,
    input  logic signed [31:0]       accumulator_3_s32,
    input  logic signed [31:0]       accumulator_4_s32,
    input  logic signed [31:0]       accumulator_5_s32,
    input  logic signed [31:0]       accumulator_6_s32,
    input  logic signed [31:0]       accumulator_7_s32,
    output logic signed [31:0]       buffered_accumulator_0_s32,
    output logic signed [31:0]       buffered_accumulator_1_s32,
    output logic signed [31:0]       buffered_accumulator_2_s32,
    output logic signed [31:0]       buffered_accumulator_3_s32,
    output logic signed [31:0]       buffered_accumulator_4_s32,
    output logic signed [31:0]       buffered_accumulator_5_s32,
    output logic signed [31:0]       buffered_accumulator_6_s32,
    output logic signed [31:0]       buffered_accumulator_7_s32,
    output logic signed [31:0]       selected_accumulator_s32
);

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            buffered_accumulator_0_s32 <= 32'sd0;
            buffered_accumulator_1_s32 <= 32'sd0;
            buffered_accumulator_2_s32 <= 32'sd0;
            buffered_accumulator_3_s32 <= 32'sd0;
            buffered_accumulator_4_s32 <= 32'sd0;
            buffered_accumulator_5_s32 <= 32'sd0;
            buffered_accumulator_6_s32 <= 32'sd0;
            buffered_accumulator_7_s32 <= 32'sd0;
        end
        else if (capture_en) begin
            // 마지막 MAC 갱신 완료 후 유효 lane의 accumulator를 동시에 저장한다.
            if (lane_valid[0]) begin
                buffered_accumulator_0_s32 <= accumulator_0_s32;
            end
            else begin
                // 무효 lane은 이후 출력 처리에 사용되지 않도록 0을 저장한다.
                buffered_accumulator_0_s32 <= 32'sd0;
            end

            if (lane_valid[1]) begin
                buffered_accumulator_1_s32 <= accumulator_1_s32;
            end
            else begin
                buffered_accumulator_1_s32 <= 32'sd0;
            end

            if (lane_valid[2]) begin
                buffered_accumulator_2_s32 <= accumulator_2_s32;
            end
            else begin
                buffered_accumulator_2_s32 <= 32'sd0;
            end

            if (lane_valid[3]) begin
                buffered_accumulator_3_s32 <= accumulator_3_s32;
            end
            else begin
                buffered_accumulator_3_s32 <= 32'sd0;
            end

            if (lane_valid[4]) begin
                buffered_accumulator_4_s32 <= accumulator_4_s32;
            end
            else begin
                buffered_accumulator_4_s32 <= 32'sd0;
            end

            if (lane_valid[5]) begin
                buffered_accumulator_5_s32 <= accumulator_5_s32;
            end
            else begin
                buffered_accumulator_5_s32 <= 32'sd0;
            end

            if (lane_valid[6]) begin
                buffered_accumulator_6_s32 <= accumulator_6_s32;
            end
            else begin
                buffered_accumulator_6_s32 <= 32'sd0;
            end

            if (lane_valid[7]) begin
                buffered_accumulator_7_s32 <= accumulator_7_s32;
            end
            else begin
                buffered_accumulator_7_s32 <= 32'sd0;
            end
        end
        else if (bias_add_en) begin
            // 64-bit word의 두 Bias를 signed int32로 해석해 해당 lane pair에 더한다.
            // Overflow가 발생하면 32-bit two's-complement 하위 비트를 유지한다.
            case (bias_word_index)
                2'd0: begin
                    if (lane_valid[0]) begin
                        buffered_accumulator_0_s32 <= $signed(buffered_accumulator_0_s32) + $signed(bias_word[31:0]);
                    end
                    if (lane_valid[1]) begin
                        buffered_accumulator_1_s32 <= $signed(buffered_accumulator_1_s32) + $signed(bias_word[63:32]);
                    end
                end

                2'd1: begin
                    if (lane_valid[2]) begin
                        buffered_accumulator_2_s32 <= $signed(buffered_accumulator_2_s32) + $signed(bias_word[31:0]);
                    end
                    if (lane_valid[3]) begin
                        buffered_accumulator_3_s32 <= $signed(buffered_accumulator_3_s32) + $signed(bias_word[63:32]);
                    end
                end

                2'd2: begin
                    if (lane_valid[4]) begin
                        buffered_accumulator_4_s32 <= $signed(buffered_accumulator_4_s32) + $signed(bias_word[31:0]);
                    end
                    if (lane_valid[5]) begin
                        buffered_accumulator_5_s32 <= $signed(buffered_accumulator_5_s32) + $signed(bias_word[63:32]);
                    end
                end

                2'd3: begin
                    if (lane_valid[6]) begin
                        buffered_accumulator_6_s32 <= $signed(buffered_accumulator_6_s32) + $signed(bias_word[31:0]);
                    end
                    if (lane_valid[7]) begin
                        buffered_accumulator_7_s32 <= $signed(buffered_accumulator_7_s32) + $signed(bias_word[63:32]);
                    end
                end

                default: begin
                end
            endcase
        end
    end

    always_comb begin
        selected_accumulator_s32 = 32'sd0;

        case (read_lane_index)
            3'd0: begin
                selected_accumulator_s32 = buffered_accumulator_0_s32;
            end

            3'd1: begin
                selected_accumulator_s32 = buffered_accumulator_1_s32;
            end

            3'd2: begin
                selected_accumulator_s32 = buffered_accumulator_2_s32;
            end

            3'd3: begin
                selected_accumulator_s32 = buffered_accumulator_3_s32;
            end

            3'd4: begin
                selected_accumulator_s32 = buffered_accumulator_4_s32;
            end

            3'd5: begin
                selected_accumulator_s32 = buffered_accumulator_5_s32;
            end

            3'd6: begin
                selected_accumulator_s32 = buffered_accumulator_6_s32;
            end

            3'd7: begin
                selected_accumulator_s32 = buffered_accumulator_7_s32;
            end

            default: begin
                selected_accumulator_s32 = 32'sd0;
            end
        endcase
    end

endmodule
