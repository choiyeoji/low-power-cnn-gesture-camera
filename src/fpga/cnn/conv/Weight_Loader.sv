`timescale 1ns / 1ps

// Reads one 3x3-weight set and one bias word from an external synchronous
// 64-bit weight buffer.
//
// External memory layout for one layer:
//
//   Weight words:
//     output-group -> input-channel -> kernel-position
//
//     weight address =
//       group_base_address + input_channel_index * 9 + kernel_index
//
//   Bias words:
//     four 64-bit words per eight-output-channel group, after every weight
//     block. Each word holds two signed 32-bit biases.
//
//     bias address =
//       group_base_address + input_channels * 9 + bias_pair_index
//
// One 64-bit word contains eight signed 8-bit channel values:
//   [ 7: 0] = CH0, [15: 8] = CH1, ... [63:56] = CH7
//
// The external buffer is not modified by this module. wt_rdata is assumed to
// return one clock after wt_raddr is issued.
//
module Weight_Loader #(
    parameter integer NUM_CH       = 8,
    parameter integer ADDR_WIDTH   = 16,
    parameter integer CONFIG_WIDTH = 16,
    parameter integer WEIGHT_WORD_WIDTH = 64
) (
    input  logic clk,
    input  logic rst_n,

    // One request loads weights for one output group and one input channel.
    input  logic                    load_start,
    input  logic                    prefetch_start,
    input  logic                    promote_prefetch,
    output logic                    load_ready,
    output logic                    busy,
    output logic                    weight_valid,
    output logic                    prefetch_busy,
    output logic                    prefetch_valid,

    // Active-layer configuration and current indices.
    input  logic [CONFIG_WIDTH-1:0] input_channels,
    input  logic [ADDR_WIDTH-1:0]   group_base_address,
    input  logic [CONFIG_WIDTH-1:0] input_channel_index,
    input  logic [CONFIG_WIDTH-1:0] prefetch_channel_index,

    // External synchronous-read 64-bit weight-buffer interface.
    output logic                    wt_ren,
    output logic [ADDR_WIDTH-1:0]   wt_raddr,
    input  logic [WEIGHT_WORD_WIDTH-1:0] wt_rdata,

    // Stored values supplied to CH_wrapper.
    output logic signed [7:0]
        weight_out [0:NUM_CH-1][0:8],
    output logic signed [31:0]
        bias_out [0:NUM_CH-1]
);

    localparam integer NUM_KERNEL_WORDS = 9;
    localparam integer WEIGHT_WORD_CH = WEIGHT_WORD_WIDTH / 8;
    localparam integer SUBGROUP_COUNT = NUM_CH / WEIGHT_WORD_CH;
    localparam integer SUBGROUP_COUNT_WIDTH =
        (SUBGROUP_COUNT <= 1) ? 1 : $clog2(SUBGROUP_COUNT);
    localparam integer BIAS_WORDS_PER_SUBGROUP = WEIGHT_WORD_CH / 2;
    localparam integer TOTAL_KERNEL_WORDS = NUM_KERNEL_WORDS * SUBGROUP_COUNT;
    localparam integer TOTAL_BIAS_WORDS =
        BIAS_WORDS_PER_SUBGROUP * SUBGROUP_COUNT;
    localparam integer KERNEL_COUNT_WIDTH =
        (TOTAL_KERNEL_WORDS <= 1) ? 1 : $clog2(TOTAL_KERNEL_WORDS + 1);
    localparam integer BIAS_COUNT_WIDTH =
        (TOTAL_BIAS_WORDS <= 1) ? 1 : $clog2(TOTAL_BIAS_WORDS + 1);
    localparam integer CALC_WIDTH =
        (2 * CONFIG_WIDTH) + 4;

    typedef enum logic [1:0] {
        IDLE,
        LOAD_WEIGHT,
        LOAD_BIAS
    } state_t;

    state_t state;
    logic   load_prefetch_bank;

    logic [KERNEL_COUNT_WIDTH-1:0] kernel_issue_count;
    logic [KERNEL_COUNT_WIDTH-1:0] kernel_capture_count;
    logic [BIAS_COUNT_WIDTH-1:0] bias_issue_count;
    logic [BIAS_COUNT_WIDTH-1:0] bias_capture_count;
    logic [$clog2(NUM_KERNEL_WORDS)-1:0] kernel_issue_word_index;
    logic [$clog2(NUM_KERNEL_WORDS)-1:0] kernel_capture_word_index;
    logic [SUBGROUP_COUNT_WIDTH-1:0] kernel_issue_subgroup_index;
    logic [SUBGROUP_COUNT_WIDTH-1:0] kernel_capture_subgroup_index;
    logic [$clog2(BIAS_WORDS_PER_SUBGROUP)-1:0] bias_issue_word_index;
    logic [$clog2(BIAS_WORDS_PER_SUBGROUP)-1:0] bias_capture_word_index;
    logic [SUBGROUP_COUNT_WIDTH-1:0] bias_issue_subgroup_index;
    logic [SUBGROUP_COUNT_WIDTH-1:0] bias_capture_subgroup_index;
    logic [ADDR_WIDTH-1:0] weight_base_addr_reg;
    logic [ADDR_WIDTH-1:0] bias_base_addr_reg;
    logic [ADDR_WIDTH-1:0] subgroup_stride_reg;
    logic [ADDR_WIDTH-1:0] weight_request_addr;
    logic [ADDR_WIDTH-1:0] bias_request_addr;
    logic                  weight_data_pending;
    logic                  bias_data_pending;

    logic [CALC_WIDTH-1:0] input_channels_calc;
    logic [CALC_WIDTH-1:0] input_channel_index_calc;
    logic [CALC_WIDTH-1:0] weight_start_calc_wide;
    logic [CALC_WIDTH-1:0] bias_addr_calc_wide;
    logic [ADDR_WIDTH-1:0] weight_start_calc;
    logic [ADDR_WIDTH-1:0] bias_addr_calc;
    logic [CALC_WIDTH-1:0] prefetch_input_channel_index_calc;
    logic [CALC_WIDTH-1:0] prefetch_weight_start_calc_wide;
    logic [ADDR_WIDTH-1:0] prefetch_weight_start_calc;
    logic [CALC_WIDTH-1:0] subgroup_stride_calc_wide;
    logic [ADDR_WIDTH-1:0] subgroup_stride_calc;
    logic [ADDR_WIDTH-1:0] subgroup_jump;
    logic signed [7:0] active_weight_out [0:NUM_CH-1][0:8];
    logic signed [31:0] active_bias_out [0:NUM_CH-1];
    logic signed [7:0] prefetched_weight_out [0:NUM_CH-1][0:8];
    logic signed [31:0] prefetched_bias_out [0:NUM_CH-1];

    integer ch;
    integer kernel;

    always_comb begin
        input_channels_calc =
            {{(CALC_WIDTH-CONFIG_WIDTH){1'b0}}, input_channels};
        input_channel_index_calc =
            {{(CALC_WIDTH-CONFIG_WIDTH){1'b0}}, input_channel_index};
        prefetch_input_channel_index_calc =
            {{(CALC_WIDTH-CONFIG_WIDTH){1'b0}}, prefetch_channel_index};

        weight_start_calc_wide =
            {{(CALC_WIDTH-ADDR_WIDTH){1'b0}}, group_base_address}
            + (input_channel_index_calc * NUM_KERNEL_WORDS);
        prefetch_weight_start_calc_wide =
            {{(CALC_WIDTH-ADDR_WIDTH){1'b0}}, group_base_address}
            + (prefetch_input_channel_index_calc * NUM_KERNEL_WORDS);

        bias_addr_calc_wide =
            {{(CALC_WIDTH-ADDR_WIDTH){1'b0}}, group_base_address}
            + (input_channels_calc * NUM_KERNEL_WORDS);
        subgroup_stride_calc_wide =
            (input_channels_calc * NUM_KERNEL_WORDS)
            + BIAS_WORDS_PER_SUBGROUP;

        weight_start_calc =
            weight_start_calc_wide[ADDR_WIDTH-1:0];
        prefetch_weight_start_calc =
            prefetch_weight_start_calc_wide[ADDR_WIDTH-1:0];
        bias_addr_calc =
            bias_addr_calc_wide[ADDR_WIDTH-1:0];
        subgroup_stride_calc =
            subgroup_stride_calc_wide[ADDR_WIDTH-1:0];
        subgroup_jump = subgroup_stride_calc - NUM_KERNEL_WORDS + 1'b1;
    end

    always_comb begin
        load_ready = (state == IDLE);
        busy       = (state != IDLE);
        prefetch_busy = (state != IDLE) && load_prefetch_bank;
        wt_ren     = 1'b0;
        wt_raddr   = weight_request_addr;

        case (state)
            LOAD_WEIGHT: begin
                if (kernel_issue_count < TOTAL_KERNEL_WORDS)
                    wt_ren   = 1'b1;
            end

            LOAD_BIAS: begin
                if (bias_issue_count < TOTAL_BIAS_WORDS) begin
                    wt_ren   = 1'b1;
                    wt_raddr = bias_request_addr;
                end
            end

            default: begin end
        endcase
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state             <= IDLE;
            load_prefetch_bank <= 1'b0;
            kernel_issue_count <= '0;
            kernel_capture_count <= '0;
            bias_issue_count <= '0;
            bias_capture_count <= '0;
            kernel_issue_word_index <= '0;
            kernel_capture_word_index <= '0;
            kernel_issue_subgroup_index <= '0;
            kernel_capture_subgroup_index <= '0;
            bias_issue_word_index <= '0;
            bias_capture_word_index <= '0;
            bias_issue_subgroup_index <= '0;
            bias_capture_subgroup_index <= '0;
            weight_base_addr_reg <= '0;
            bias_base_addr_reg <= '0;
            subgroup_stride_reg <= '0;
            weight_request_addr <= '0;
            bias_request_addr <= '0;
            weight_data_pending <= 1'b0;
            bias_data_pending <= 1'b0;
            weight_valid      <= 1'b0;
            prefetch_valid    <= 1'b0;

            for (ch = 0; ch < NUM_CH; ch = ch + 1) begin
                active_bias_out[ch] <= 32'sd0;
                prefetched_bias_out[ch] <= 32'sd0;
                bias_out[ch] <= 32'sd0;

                for (kernel = 0; kernel < NUM_KERNEL_WORDS;
                     kernel = kernel + 1) begin
                    active_weight_out[ch][kernel] <= 8'sd0;
                    prefetched_weight_out[ch][kernel] <= 8'sd0;
                    weight_out[ch][kernel] <= 8'sd0;
                end
            end
        end else begin
            if (promote_prefetch && prefetch_valid) begin
                for (ch = 0; ch < NUM_CH; ch = ch + 1) begin
                    bias_out[ch] <= prefetched_bias_out[ch];
                    active_bias_out[ch] <= prefetched_bias_out[ch];
                    for (kernel = 0; kernel < NUM_KERNEL_WORDS;
                         kernel = kernel + 1) begin
                        weight_out[ch][kernel] <=
                            prefetched_weight_out[ch][kernel];
                        active_weight_out[ch][kernel] <=
                            prefetched_weight_out[ch][kernel];
                    end
                end
                weight_valid   <= 1'b1;
                prefetch_valid <= 1'b0;
            end

            case (state)
                IDLE: begin
                    if (load_start && load_ready) begin
                        load_prefetch_bank <= 1'b0;
                        weight_base_addr_reg <= weight_start_calc;
                        bias_base_addr_reg <= bias_addr_calc;
                        subgroup_stride_reg <= subgroup_stride_calc;
                        kernel_issue_count <= '0;
                        kernel_capture_count <= '0;
                        bias_issue_count <= '0;
                        bias_capture_count <= '0;
                        kernel_issue_word_index <= '0;
                        kernel_capture_word_index <= '0;
                        kernel_issue_subgroup_index <= '0;
                        kernel_capture_subgroup_index <= '0;
                        bias_issue_word_index <= '0;
                        bias_capture_word_index <= '0;
                        bias_issue_subgroup_index <= '0;
                        bias_capture_subgroup_index <= '0;
                        weight_request_addr <= weight_start_calc;
                        bias_request_addr <= bias_addr_calc;
                        weight_data_pending <= 1'b0;
                        bias_data_pending <= 1'b0;
                        weight_valid      <= 1'b0;
                        state             <= LOAD_WEIGHT;
                    end else if (prefetch_start && !prefetch_valid) begin
                        load_prefetch_bank <= 1'b1;
                        weight_base_addr_reg <= prefetch_weight_start_calc;
                        bias_base_addr_reg <= bias_addr_calc;
                        subgroup_stride_reg <= subgroup_stride_calc;
                        kernel_issue_count <= '0;
                        kernel_capture_count <= '0;
                        bias_issue_count <= '0;
                        bias_capture_count <= '0;
                        kernel_issue_word_index <= '0;
                        kernel_capture_word_index <= '0;
                        kernel_issue_subgroup_index <= '0;
                        kernel_capture_subgroup_index <= '0;
                        bias_issue_word_index <= '0;
                        bias_capture_word_index <= '0;
                        bias_issue_subgroup_index <= '0;
                        bias_capture_subgroup_index <= '0;
                        weight_request_addr <= prefetch_weight_start_calc;
                        bias_request_addr <= bias_addr_calc;
                        weight_data_pending <= 1'b0;
                        bias_data_pending <= 1'b0;
                        state             <= LOAD_WEIGHT;
                    end
                end

                LOAD_WEIGHT: begin
                    if (weight_data_pending) begin
                        integer capture_subgroup_channel_base;
                        capture_subgroup_channel_base =
                            kernel_capture_subgroup_index * WEIGHT_WORD_CH;
                        for (ch = 0; ch < NUM_CH; ch = ch + 1) begin
                            if ((ch >= capture_subgroup_channel_base)
                                && (ch < capture_subgroup_channel_base + WEIGHT_WORD_CH)) begin
                                if (load_prefetch_bank)
                                    prefetched_weight_out[ch][kernel_capture_word_index] <=
                                        $signed(wt_rdata[(ch - capture_subgroup_channel_base)*8 +: 8]);
                                else
                                    active_weight_out[ch][kernel_capture_word_index] <=
                                        $signed(wt_rdata[(ch - capture_subgroup_channel_base)*8 +: 8]);
                            end
                        end

                        kernel_capture_count <= kernel_capture_count + 1'b1;
                        if (kernel_capture_word_index == NUM_KERNEL_WORDS-1) begin
                            kernel_capture_word_index <= '0;
                            kernel_capture_subgroup_index <=
                                kernel_capture_subgroup_index + 1'b1;
                        end else begin
                            kernel_capture_word_index <=
                                kernel_capture_word_index + 1'b1;
                        end
                    end

                    if (kernel_issue_count < TOTAL_KERNEL_WORDS) begin
                        kernel_issue_count <= kernel_issue_count + 1'b1;
                        weight_data_pending <= 1'b1;
                        if (kernel_issue_word_index == NUM_KERNEL_WORDS-1) begin
                            kernel_issue_word_index <= '0;
                            if (kernel_issue_count != TOTAL_KERNEL_WORDS-1)
                                weight_request_addr <=
                                    weight_request_addr + subgroup_jump;
                            kernel_issue_subgroup_index <=
                                kernel_issue_subgroup_index + 1'b1;
                        end else begin
                            kernel_issue_word_index <=
                                kernel_issue_word_index + 1'b1;
                            weight_request_addr <= weight_request_addr + 1'b1;
                        end
                    end else begin
                        weight_data_pending <= 1'b0;
                        if (weight_data_pending
                            && (kernel_capture_count == TOTAL_KERNEL_WORDS-1)) begin
                            bias_issue_count <= '0;
                            bias_capture_count <= '0;
                            bias_issue_word_index <= '0;
                            bias_capture_word_index <= '0;
                            bias_issue_subgroup_index <= '0;
                            bias_capture_subgroup_index <= '0;
                            bias_request_addr <= bias_base_addr_reg;
                            bias_data_pending <= 1'b0;
                            state            <= LOAD_BIAS;
                        end
                    end
                end

                LOAD_BIAS: begin
                    if (bias_data_pending) begin
                        integer capture_bias_channel_base;
                        capture_bias_channel_base =
                            bias_capture_subgroup_index * WEIGHT_WORD_CH;
                        if (load_prefetch_bank) begin
                            prefetched_bias_out[capture_bias_channel_base
                                + (bias_capture_word_index * 2)] <=
                                $signed(wt_rdata[31:0]);
                            prefetched_bias_out[capture_bias_channel_base
                                + (bias_capture_word_index * 2) + 1] <=
                                $signed(wt_rdata[63:32]);
                        end else begin
                            active_bias_out[capture_bias_channel_base
                                + (bias_capture_word_index * 2)] <=
                                $signed(wt_rdata[31:0]);
                            active_bias_out[capture_bias_channel_base
                                + (bias_capture_word_index * 2) + 1] <=
                                $signed(wt_rdata[63:32]);
                        end

                        bias_capture_count <= bias_capture_count + 1'b1;
                        if (bias_capture_word_index == BIAS_WORDS_PER_SUBGROUP-1) begin
                            bias_capture_word_index <= '0;
                            bias_capture_subgroup_index <=
                                bias_capture_subgroup_index + 1'b1;
                        end else begin
                            bias_capture_word_index <=
                                bias_capture_word_index + 1'b1;
                        end
                    end

                    if (bias_issue_count < TOTAL_BIAS_WORDS) begin
                        bias_issue_count <= bias_issue_count + 1'b1;
                        bias_data_pending <= 1'b1;
                        if (bias_issue_word_index == BIAS_WORDS_PER_SUBGROUP-1) begin
                            bias_issue_word_index <= '0;
                            if (bias_issue_count != TOTAL_BIAS_WORDS-1)
                                bias_request_addr <=
                                    bias_request_addr
                                    + (subgroup_stride_reg
                                       - BIAS_WORDS_PER_SUBGROUP + 1'b1);
                            bias_issue_subgroup_index <=
                                bias_issue_subgroup_index + 1'b1;
                        end else begin
                            bias_issue_word_index <=
                                bias_issue_word_index + 1'b1;
                            bias_request_addr <= bias_request_addr + 1'b1;
                        end
                    end else begin
                        bias_data_pending <= 1'b0;
                        if (bias_data_pending
                            && (bias_capture_count == TOTAL_BIAS_WORDS-1)) begin
                            if (load_prefetch_bank) begin
                                prefetch_valid <= 1'b1;
                            end else begin
                                weight_valid <= 1'b1;
                                for (ch = 0; ch < NUM_CH; ch = ch + 1) begin
                                    bias_out[ch] <= active_bias_out[ch];
                                    for (kernel = 0; kernel < NUM_KERNEL_WORDS;
                                         kernel = kernel + 1)
                                        weight_out[ch][kernel] <=
                                            active_weight_out[ch][kernel];
                                end
                            end
                            state        <= IDLE;
                        end
                    end

                end

                default: begin
                    state        <= IDLE;
                end
            endcase
        end
    end

endmodule
