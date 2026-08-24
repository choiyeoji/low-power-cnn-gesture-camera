`timescale 1ns / 1ps

module CH #(
    parameter integer OUTPUT_SHIFT = 8
) (
    input logic clk,
    input logic rst_n,

    // Conv_ctrl → CH
    input logic ch_enable,  // 현재 CH 활성화 
    input logic acc_clear,  // 기존 누적 결과 초기화 
    input logic first_ic,  // 현재 입력이 첫번째 입력널채널인지 
    input logic last_ic,   // 현재 입력이 마지막 입력 채널인지 여부

    // Shift Buffer → CH
    input  logic              pixel_valid,
    output logic              pixel_ready,
    input  logic        [1:0] window_index,
    input  logic              zero_extend_input,
    input  logic signed [7:0] pixel_in    [0:8],

    // Weight Buffer → CH
    input logic               weight_valid,
    input logic signed [ 7:0] weight_in   [0:8],
    input logic signed [31:0] bias_in,

    // CH → ReLU
    output logic signed [31:0] result_out,

    // CH → MaxPool
    output logic result_valid,
    input  logic result_ready
    ,
    output logic mac_tile_done
);

    // --------------------------------------------------------
    // 내부 신호
    // --------------------------------------------------------

    // signed 8-bit Pixel을 signed 9-bit로 부호 확장한 값
    logic signed [8:0] pixel_signed[0:8];

    // signed 8-bit Weight를 signed 9-bit로 부호 확장한 값
    logic signed [8:0] weight_signed[0:8];

    // Stage 1 captures products, stage 2 captures pair sums, and stage 3
    // finishes reduction before updating the spatial accumulator.
    // Vivado hint: map MAC multipliers to DSP blocks.
    (* use_dsp = "yes" *) logic signed [17:0] product_calc[0:8];
    logic signed [17:0] product_reg[0:8];
    logic                stage1_valid;
    logic                stage2_valid;
    logic                stage3_valid;
    logic                stage1_first_ic;
    logic                stage1_last_ic;
    logic [1:0]          stage1_window_index;
    logic signed [31:0]  stage1_bias;
    logic                stage2_first_ic;
    logic                stage2_last_ic;
    logic [1:0]          stage2_window_index;
    logic signed [31:0]  stage2_bias;
    logic                stage3_first_ic;
    logic                stage3_last_ic;
    logic [1:0]          stage3_window_index;
    logic signed [31:0]  stage3_bias;

    logic signed [18:0] sum01_reg;
    logic signed [18:0] sum23_reg;
    logic signed [18:0] sum45_reg;
    logic signed [18:0] sum67_reg;
    logic signed [17:0] product8_reg;
    logic signed [19:0] sum0123;
    logic signed [19:0] sum4567;
    logic signed [20:0] sum01234567;
    logic signed [21:0] product_sum;
    logic signed [21:0] product_sum_reg;
    logic signed [31:0] conv_sum;

    // Four independent spatial accumulators for the four convolution windows
    // that form one 2x2 max-pooling region.
    logic signed [31:0] accumulator[0:3]; // 하나의 2x2 영역에 포함되는 4개 Convolution 위치의미 

    /*
    | `window_index` | 사용 레지스터 |
    |---:|---|
    | `00` | `accumulator[0]` |
    | `01` | `accumulator[1]` |
    | `10` | `accumulator[2]` |
    | `11` | `accumulator[3]` |
    */
    

    // 모든 입력 채널 누적과 Bias 덧셈이 끝난 결과
    // 시뮬레이션과 디버깅을 위해 저장
    logic signed [31:0] full_result;

    // Pixel, Weight, CH enable이 모두 유효할 때 1
    logic input_fire;
    logic stage3_execute;
    logic pipeline_stall;

    integer i;
    integer spatial;

    // --------------------------------------------------------
    // 입력 유효 조건
    // --------------------------------------------------------

    // The three MAC stages accept a new window every cycle. They all stall
    // only when a final-channel result cannot leave the one-entry result
    // register.
    assign pipeline_stall = stage3_valid && stage3_last_ic
        && result_valid && !result_ready;
    assign pixel_ready =
        !ch_enable
        || (weight_valid && !pipeline_stall);

    assign input_fire = ch_enable && pixel_valid && pixel_ready; // CH가 입력을 실제로 처리하는 조건 
    assign stage3_execute = stage3_valid && !pipeline_stall;
    assign mac_tile_done = stage3_execute && (stage3_window_index == 2'd3);

    /*
    ch_enable   = 1
    pixel_valid = 1
    weight_valid= 1
    pixel_ready = 1 
    => 이 조건이 만족되는 Clock Edge에서만 누적 레지스터가 변경된다 
    */

    // --------------------------------------------------------
    // Pixel/Weight 확장, 곱셈 9개 및 3×3 합산
    // --------------------------------------------------------

    always_comb begin
        // 조합논리 기본값
        for (i = 0; i < 9; i = i + 1) begin
            // All feature and image inputs use signed int8 two's-complement
            // values. zero_extend_input remains for interface compatibility.
            pixel_signed[i] = zero_extend_input
                ? {1'b0, pixel_in[i]}
                : $signed({pixel_in[i][7], pixel_in[i]});

            // signed 8-bit Weight의 부호 Bit를 복사하여
            // signed 9-bit로 부호 확장
            weight_signed[i] = $signed({weight_in[i][7], weight_in[i]});

            // signed 9-bit × signed 9-bit
            // 결과는 signed 18-bit
            product_calc[i] = pixel_signed[i] * weight_signed[i];
        end

        sum0123     = sum01_reg + sum23_reg;
        sum4567     = sum45_reg + sum67_reg;
        sum01234567 = sum0123 + sum4567;
        product_sum = sum01234567 + product8_reg;
        conv_sum = {{10{product_sum_reg[21]}}, product_sum_reg};
    end

    // --------------------------------------------------------
    // 입력 채널 누적, Bias 추가 및 8-bit 출력 생성
    // --------------------------------------------------------

    // --------------------------------------------------------
    // result_out   유지
    // result_valid 유지

    // CH_Result_Buffer가 받을 준비가 되면 result_ready=1을 보냅니다.
    // --------------------------------------------------------
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            // 비동기 Active-Low Reset
            full_result  <= 32'sd0;
            result_out   <= 32'sd0;
            result_valid <= 1'b0;
            stage1_valid <= 1'b0;
            stage2_valid <= 1'b0;
            stage3_valid <= 1'b0;
            product_sum_reg <= 22'sd0;
            stage1_first_ic <= 1'b0;
            stage1_last_ic  <= 1'b0;
            stage1_window_index <= 2'd0;
            stage1_bias <= 32'sd0;
            stage2_first_ic <= 1'b0;
            stage2_last_ic  <= 1'b0;
            stage2_window_index <= 2'd0;
            stage2_bias <= 32'sd0;
            stage3_first_ic <= 1'b0;
            stage3_last_ic  <= 1'b0;
            stage3_window_index <= 2'd0;
            stage3_bias <= 32'sd0;

            for (spatial = 0; spatial < 4; spatial = spatial + 1)
            accumulator[spatial] <= 32'sd0;

            for (spatial = 0; spatial < 9; spatial = spatial + 1)
                product_reg[spatial] <= 18'sd0;
            sum01_reg <= 19'sd0;
            sum23_reg <= 19'sd0;
            sum45_reg <= 19'sd0;
            sum67_reg <= 19'sd0;
            product8_reg <= 18'sd0;

        end else begin
            if (acc_clear) begin
                // Layer 시작 또는 강제 초기화
                //
                // acc_clear와 input_fire가 동시에 1이면
                // acc_clear가 우선하므로 입력 계산은 수행하지 않음
                full_result  <= 32'sd0;
                result_out   <= 32'sd0;
                result_valid <= 1'b0;
                stage1_valid <= 1'b0;
                stage2_valid <= 1'b0;
                stage3_valid <= 1'b0;

                for (spatial = 0; spatial < 4; spatial = spatial + 1)
                accumulator[spatial] <= 32'sd0;

            end else begin
                // Hold result_out/result_valid while the downstream MaxPool
                // applies backpressure.
                if (result_valid && result_ready) result_valid <= 1'b0;

                    if (!pipeline_stall) begin
                        if (stage3_execute) begin
                            if (stage3_first_ic && stage3_last_ic) begin
                                full_result <= conv_sum + stage3_bias;
                                result_out <= conv_sum + stage3_bias;
                                result_valid <= 1'b1;
                                accumulator[stage3_window_index] <= 32'sd0;
                            end else if (stage3_first_ic) begin
                                accumulator[stage3_window_index] <= conv_sum;
                            end else if (stage3_last_ic) begin
                                full_result <= accumulator[stage3_window_index]
                                    + conv_sum + stage3_bias;
                                result_out <= accumulator[stage3_window_index]
                                    + conv_sum + stage3_bias;
                                result_valid <= 1'b1;
                                accumulator[stage3_window_index] <= 32'sd0;
                            end else begin
                                accumulator[stage3_window_index] <=
                                    accumulator[stage3_window_index] + conv_sum;
                            end
                        end

                        stage3_valid <= stage2_valid;
                        stage3_first_ic <= stage2_first_ic;
                        stage3_last_ic <= stage2_last_ic;
                        stage3_window_index <= stage2_window_index;
                        stage3_bias <= stage2_bias;
                        if (stage2_valid)
                            product_sum_reg <= product_sum;

                        stage2_valid <= stage1_valid;
                        stage2_first_ic <= stage1_first_ic;
                        stage2_last_ic <= stage1_last_ic;
                        stage2_window_index <= stage1_window_index;
                        stage2_bias <= stage1_bias;
                        if (stage1_valid) begin
                            sum01_reg <= product_reg[0] + product_reg[1];
                            sum23_reg <= product_reg[2] + product_reg[3];
                            sum45_reg <= product_reg[4] + product_reg[5];
                            sum67_reg <= product_reg[6] + product_reg[7];
                            product8_reg <= product_reg[8];
                        end

                        stage1_valid <= input_fire;
                        if (input_fire) begin
                            for (spatial = 0; spatial < 9; spatial = spatial + 1)
                                product_reg[spatial] <= product_calc[spatial];
                            stage1_first_ic <= first_ic;
                            stage1_last_ic <= last_ic;
                            stage1_window_index <= window_index;
                            stage1_bias <= bias_in;
                        end
                    end
            end
        end
    end

endmodule
