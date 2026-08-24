`timescale 1ns / 1ps

module fc_controller #(
    parameter MAX_INPUT_LENGTH  = 128,
    parameter MAX_OUTPUT_LENGTH = 128,
    parameter WT_ADDR_WIDTH     = 10 * 128,
    parameter DATA_ADDR_WIDTH   = 126 * 126 * 16
) (
    input logic clk,
    input logic rst_n,
    input logic start,
    input logic [$clog2(MAX_INPUT_LENGTH)-1:0] input_length,
    input logic [$clog2(MAX_OUTPUT_LENGTH)-1:0] output_length,
    input logic finish_en,

    output logic start_accept,
    output logic busy,
    output logic Done,

    output logic [$clog2(WT_ADDR_WIDTH)-1:0] core_weight_read_offset,
    output logic [$clog2(DATA_ADDR_WIDTH)-1:0] core_data_read_offset,
    output logic core_data_write_en,
    output logic [$clog2(DATA_ADDR_WIDTH)-1:0] core_data_write_offset,

    output logic pe_acc_clear,
    output logic output_bias_add_en,
    output logic [1:0] output_bias_word_index,

    output logic pe_mac_enable,
    output logic [7:0] lane_valid,

    output logic output_capture_en,
    output logic [2:0] output_read_lane_index,
    output logic quant_acc_capture_en,
    output logic quant_result_capture_en,
    input  logic quant_result_valid,

    output logic result_capture_en
);

    localparam INPUT_INDEX_WIDTH = $clog2(MAX_INPUT_LENGTH);
    localparam OUTPUT_INDEX_WIDTH = $clog2(MAX_OUTPUT_LENGTH);
    localparam WT_ADDR_BITS = $clog2(WT_ADDR_WIDTH);
    localparam DATA_ADDR_BITS = $clog2(DATA_ADDR_WIDTH);

    // 출력 index에 lane 번호 0~7을 더할 때 overflow가 없도록 확장한다.
    localparam OUTPUT_BASE_WIDTH = (DATA_ADDR_BITS > OUTPUT_INDEX_WIDTH) ? DATA_ADDR_BITS : OUTPUT_INDEX_WIDTH;
    localparam OUTPUT_ARITH_WIDTH = OUTPUT_BASE_WIDTH + 3;

    localparam [INPUT_INDEX_WIDTH-1:0] INPUT_INDEX_ONE = {{(INPUT_INDEX_WIDTH - 1) {1'b0}}, 1'b1};
    localparam [WT_ADDR_BITS-1:0] WEIGHT_ADDRESS_ONE = {{(WT_ADDR_BITS - 1) {1'b0}}, 1'b1};
    localparam [DATA_ADDR_BITS-1:0] DATA_ADDRESS_ONE = {{(DATA_ADDR_BITS - 1) {1'b0}}, 1'b1};
    localparam [OUTPUT_ARITH_WIDTH-1:0] OUTPUT_GROUP_STEP = {{(OUTPUT_ARITH_WIDTH - 4) {1'b0}}, 4'd8};

    typedef enum logic [3:0] {
        S_IDLE,
        S_ACC_CLEAR,
        S_MAC,
        S_MAC_DRAIN,
        S_PRODUCT_DRAIN,
        S_CAPTURE,
        S_BIAS_PREFETCH,
        S_BIAS_ADD,
        S_QUANT_CAPTURE,
        S_QUANTIZE,
        S_QUANT_WAIT,
        S_QUANT_ISSUE,
        S_QUANT_DRAIN,
        S_NEXT_GROUP,
        S_FINAL_RESULT,
        S_DONE
    } state_t;

    state_t                          current_state;
    state_t                          next_state;

    logic   [ INPUT_INDEX_WIDTH-1:0] latched_input_length;
    logic   [OUTPUT_INDEX_WIDTH-1:0] latched_output_length;
    logic                            latched_finish_en;

    logic   [                   1:0] bias_word_index;
    logic   [ INPUT_INDEX_WIDTH-1:0] input_index;
    (* max_fanout = 1 *)
    logic   [      WT_ADDR_BITS-1:0] weight_read_offset;
    logic   [OUTPUT_INDEX_WIDTH-1:0] current_group_output_start;
    logic   [                   2:0] quant_lane_index;

    logic   [    DATA_ADDR_BITS-1:0] input_index_data;

    logic   [OUTPUT_ARITH_WIDTH-1:0] current_group_output_start_extended;
    logic   [OUTPUT_ARITH_WIDTH-1:0] latched_output_length_extended;
    logic   [OUTPUT_ARITH_WIDTH-1:0] quant_lane_index_extended;
    logic   [OUTPUT_ARITH_WIDTH-1:0] current_output_index_extended;
    logic   [OUTPUT_ARITH_WIDTH-1:0] next_group_output_start_extended;
    logic   [                   1:0] bias_last_word_index;

    // Data memory 주소 폭에 맞춰 input index를 명시적으로 확장한다.
    assign input_index_data = {{(DATA_ADDR_BITS - INPUT_INDEX_WIDTH) {1'b0}}, input_index};

    // output 비교는 넓은 intermediate에서 수행하여 lane 덧셈을 보존한다.
    assign current_group_output_start_extended = {{(OUTPUT_ARITH_WIDTH - OUTPUT_INDEX_WIDTH) {1'b0}}, current_group_output_start};
    assign latched_output_length_extended = {{(OUTPUT_ARITH_WIDTH - OUTPUT_INDEX_WIDTH) {1'b0}}, latched_output_length};
    assign quant_lane_index_extended = {{(OUTPUT_ARITH_WIDTH - 3) {1'b0}}, quant_lane_index};
    assign current_output_index_extended = current_group_output_start_extended + quant_lane_index_extended;
    assign next_group_output_start_extended = current_group_output_start_extended + OUTPUT_GROUP_STEP;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            current_state               <= S_IDLE;
            latched_input_length        <= {INPUT_INDEX_WIDTH{1'b0}};
            latched_output_length       <= {OUTPUT_INDEX_WIDTH{1'b0}};
            latched_finish_en           <= 1'b0;
            bias_word_index             <= 2'd0;
            input_index                 <= {INPUT_INDEX_WIDTH{1'b0}};
            weight_read_offset          <= {WT_ADDR_BITS{1'b0}};
            current_group_output_start  <= {OUTPUT_INDEX_WIDTH{1'b0}};
            quant_lane_index            <= 3'd0;
        end else begin
            current_state <= next_state;

            if ((current_state == S_IDLE) && start) begin
                // accepted start edge에서 현재 연산 설정을 controller에만 저장한다.
                latched_input_length        <= input_length;
                latched_output_length       <= output_length;
                latched_finish_en           <= finish_en;
                bias_word_index             <= 2'd0;
                input_index                 <= {INPUT_INDEX_WIDTH{1'b0}};
                weight_read_offset          <= {WT_ADDR_BITS{1'b0}};
                current_group_output_start  <= {OUTPUT_INDEX_WIDTH{1'b0}};
                quant_lane_index            <= 3'd0;
            end else begin
                case (current_state)
                    S_ACC_CLEAR: begin
                        input_index <= {INPUT_INDEX_WIDTH{1'b0}};
                        // Address zero is issued during S_ACC_CLEAR; keep
                        // the register on the next request address for S_MAC.
                        weight_read_offset <=
                            weight_read_offset + WEIGHT_ADDRESS_ONE;
                    end

                    S_MAC: begin
                        // 현재 Weight word를 소비한 뒤 다음 순차 주소로 이동한다.
                        weight_read_offset <= weight_read_offset + WEIGHT_ADDRESS_ONE;

                        if (input_index != latched_input_length) begin
                            input_index <= input_index + INPUT_INDEX_ONE;
                        end
                    end

                    S_CAPTURE: begin
                        bias_word_index  <= 2'd0;
                        quant_lane_index <= 3'd0;
                        // Restore the bias-zero address after the final MAC
                        // prefetch advanced the sequential request register.
                        weight_read_offset <=
                            weight_read_offset - WEIGHT_ADDRESS_ONE;
                    end

                    S_BIAS_PREFETCH: begin
                        // Issue bias word zero, then advance so S_BIAS_ADD
                        // can consume it while requesting the next word.
                        weight_read_offset <=
                            weight_read_offset + WEIGHT_ADDRESS_ONE;
                    end

                    S_BIAS_ADD: begin
                        if (bias_word_index != 2'd3) begin
                            bias_word_index <= bias_word_index + 2'd1;
                            weight_read_offset <= weight_read_offset + WEIGHT_ADDRESS_ONE;
                        end
                    end

                    S_QUANT_ISSUE: begin
                        if ((current_output_index_extended !=
                             latched_output_length_extended)
                            && (quant_lane_index != 3'd7)) begin
                            quant_lane_index <= quant_lane_index + 3'd1;
                        end
                    end

                    S_NEXT_GROUP: begin
                        current_group_output_start <= next_group_output_start_extended[OUTPUT_INDEX_WIDTH-1:0];
                        bias_word_index <= 2'd0;
                        input_index <= {INPUT_INDEX_WIDTH{1'b0}};
                        quant_lane_index <= 3'd0;
                    end

                    default: begin
                    end
                endcase
            end
        end
    end

    always_comb begin
        next_state = current_state;

        start_accept = 1'b0;
        busy = 1'b1;
        Done = 1'b0;
        // Memory를 사용하지 않는 상태에서는 마지막 순차 주소를 유지한다.
        core_weight_read_offset = weight_read_offset;
        core_data_read_offset = {DATA_ADDR_BITS{1'b0}};
        core_data_write_en = 1'b0;
        core_data_write_offset = {DATA_ADDR_BITS{1'b0}};
        pe_acc_clear = 1'b0;
        output_bias_add_en = 1'b0;
        output_bias_word_index = 2'd0;
        pe_mac_enable = 1'b0;
        lane_valid = 8'b00000000;
        output_capture_en = 1'b0;
        output_read_lane_index = 3'd0;
        quant_acc_capture_en = 1'b0;
        quant_result_capture_en = 1'b0;
        result_capture_en = 1'b0;

        // 각 lane의 전체 output index를 넓은 폭에서 직접 비교한다.
        lane_valid[0] = current_group_output_start_extended <= latched_output_length_extended;
        lane_valid[1] = (current_group_output_start_extended + {{(OUTPUT_ARITH_WIDTH-3){1'b0}}, 3'd1}) <= latched_output_length_extended;
        lane_valid[2] = (current_group_output_start_extended + {{(OUTPUT_ARITH_WIDTH-3){1'b0}}, 3'd2}) <= latched_output_length_extended;
        lane_valid[3] = (current_group_output_start_extended + {{(OUTPUT_ARITH_WIDTH-3){1'b0}}, 3'd3}) <= latched_output_length_extended;
        lane_valid[4] = (current_group_output_start_extended + {{(OUTPUT_ARITH_WIDTH-3){1'b0}}, 3'd4}) <= latched_output_length_extended;
        lane_valid[5] = (current_group_output_start_extended + {{(OUTPUT_ARITH_WIDTH-3){1'b0}}, 3'd5}) <= latched_output_length_extended;
        lane_valid[6] = (current_group_output_start_extended + {{(OUTPUT_ARITH_WIDTH-3){1'b0}}, 3'd6}) <= latched_output_length_extended;
        lane_valid[7] = (current_group_output_start_extended + {{(OUTPUT_ARITH_WIDTH-3){1'b0}}, 3'd7}) <= latched_output_length_extended;

        // One 64-bit bias word holds two output-lane biases. The final FC2
        // group has only lane 0, so it must consume only the first word.
        if (lane_valid[7] || lane_valid[6])
            bias_last_word_index = 2'd3;
        else if (lane_valid[5] || lane_valid[4])
            bias_last_word_index = 2'd2;
        else if (lane_valid[3] || lane_valid[2])
            bias_last_word_index = 2'd1;
        else
            bias_last_word_index = 2'd0;

        case (current_state)
            S_IDLE: begin
                busy       = 1'b0;
                lane_valid = 8'b00000000;

                if (start) begin
                    start_accept = 1'b1;
                    // start 수락 edge 뒤 PE accumulator clear cycle로 이동한다.
                    next_state   = S_ACC_CLEAR;
                end
            end

            S_ACC_CLEAR: begin
                // PE array의 기존 bias_load 포트를 0 load용 clear로 사용한다.
                // Invalid lane까지 포함한 PE 8개를 모두 지우기 위해 이 cycle만 전부 valid다.
                lane_valid   = 8'b11111111;
                pe_acc_clear = 1'b1;
                // Synchronous memory prefetch: first MAC uses address 0 read here.
                core_data_read_offset = input_index_data;
                core_weight_read_offset = weight_read_offset;
                next_state   = S_MAC;
            end

            S_MAC: begin
                // Synchronous memory prefetch: current MAC uses previous cycle
                // read data while this cycle issues the next address.
                core_data_read_offset = input_index_data + DATA_ADDRESS_ONE;
                core_weight_read_offset = weight_read_offset;
                pe_mac_enable = 1'b1;

                if (input_index == latched_input_length) begin
                    // The FC core registers each memory operand before its
                    // MAC. Drain the final registered operand first.
                    next_state = S_MAC_DRAIN;
                end
            end

            S_MAC_DRAIN: begin
                // No new memory request. The final registered operand is
                // captured into each PE's product pipeline on this edge.
                next_state = S_PRODUCT_DRAIN;
            end

            S_PRODUCT_DRAIN: begin
                // The final registered product is added to each accumulator
                // on this edge before the output buffer captures it.
                next_state = S_CAPTURE;
            end

            S_CAPTURE: begin
                output_capture_en = 1'b1;
                next_state = S_BIAS_PREFETCH;
            end

            S_BIAS_PREFETCH: begin
                next_state = S_BIAS_ADD;
            end

            S_BIAS_ADD: begin
                // 마지막 Weight 다음 주소의 Bias word 두 개를 순서대로 더한다.
                output_bias_add_en = 1'b1;
                output_bias_word_index = bias_word_index;
                // 현재 cycle은 이전 cycle에 요청한 Bias word를 소비하고,
                // 동시에 다음 Bias word 주소를 prefetch한다.
                core_weight_read_offset = weight_read_offset;

                if (bias_word_index == bias_last_word_index) begin
                    // Consume only the stored bias words for valid lanes.
                    next_state = S_QUANT_CAPTURE;
                end
            end

            S_QUANT_CAPTURE: begin
                output_read_lane_index = quant_lane_index;
                quant_acc_capture_en = 1'b1;
                next_state = S_QUANTIZE;
            end

            S_QUANTIZE: begin
                next_state = S_QUANT_WAIT;
            end

            S_QUANT_WAIT: begin
                if (quant_result_valid) begin
                    quant_result_capture_en = 1'b1;
                    if (latched_finish_en) begin
                        result_capture_en = 1'b1;
                        next_state = S_DONE;
                    end else begin
                        next_state = S_QUANT_ISSUE;
                    end
                end
            end

            S_QUANT_ISSUE: begin
                core_data_write_offset =
                    current_output_index_extended[DATA_ADDR_BITS-1:0];

                if (lane_valid[quant_lane_index]) begin
                    core_data_write_en = 1'b1;

                    if (current_output_index_extended ==
                        latched_output_length_extended) begin
                        // The FC core registers this command, so keep one
                        // drain cycle before reporting completion.
                        next_state = S_QUANT_DRAIN;
                    end else if (quant_lane_index == 3'd7) begin
                        next_state = S_QUANT_DRAIN;
                    end else begin
                        // Every lane needs its own selected accumulator and
                        // requantized-byte capture sequence.
                        next_state = S_QUANT_CAPTURE;
                    end
                end else begin
                    // 유효 lane은 낮은 번호부터 연속이므로 정상 경로에는 도달하지 않는다.
                    next_state = S_DONE;
                end
            end

            S_QUANT_DRAIN: begin
                // Allow the final registered FC write command to reach the
                // synchronous Data Buffer on this edge.
                if (current_output_index_extended ==
                    latched_output_length_extended) begin
                    next_state = S_DONE;
                end else begin
                    next_state = S_NEXT_GROUP;
                end
            end

            S_NEXT_GROUP: begin
                // running group 주소와 output 시작 index는 다음 edge에서 갱신된다.
                next_state = S_ACC_CLEAR;
            end

            S_FINAL_RESULT: begin
                result_capture_en = 1'b1;
                // result register 저장 edge 뒤 S_DONE cycle에 Done을 낸다.
                next_state = S_DONE;
            end

            S_DONE: begin
                lane_valid = 8'b00000000;
                Done       = 1'b1;
                next_state = S_IDLE;
            end

            default: begin
                busy       = 1'b0;
                lane_valid = 8'b00000000;
                next_state = S_IDLE;
            end
        endcase
    end

endmodule
