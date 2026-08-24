`timescale 1ns / 1ps

// Configurable one-to-three-layer convolution accelerator.
//
// The input channel count of Layer 0 is one. Each later layer receives the
// output channels of the preceding layer. For example:
//   NUM_LAYERS=1: 1 -> 8
//   NUM_LAYERS=2: 1 -> 16 -> 32
//   NUM_LAYERS=3: 1 -> 32 -> 64 -> 128
//
// One physical NUM_CH-wide datapath is reused across output-channel groups.
// Every active output-channel count must therefore be a multiple of NUM_CH.
module conv #(
    parameter integer NUM_CH            = 8,
    parameter integer NUM_LAYERS         = 3,
    parameter integer LAYER0_OUTPUT_CHANNELS = 16,
    parameter integer LAYER1_OUTPUT_CHANNELS = 32,
    parameter integer LAYER2_OUTPUT_CHANNELS = 64,
    // Logical image size before the external one-pixel zero border.
    // The external buffer supplies every Conv input already padded:
    //   128 -> Layer inputs 130, 66, 34
    //    64 -> Layer inputs  66, 34, 18
    parameter integer INPUT_WIDTH       = 128,
    parameter integer INPUT_HEIGHT      = 128,
    parameter integer DATA_ADDR_WIDTH   = 32,
    parameter integer BUFFER_DATA_WIDTH = 8,
    parameter integer WEIGHT_ADDR_WIDTH = 32,
    parameter integer WEIGHT_WORD_WIDTH = 64,
    parameter logic LAYER0_MAXPOOL_EN = 1'b1,
    parameter logic LAYER1_MAXPOOL_EN = 1'b1,
    parameter logic LAYER2_MAXPOOL_EN = 1'b1,
    parameter logic LAYER0_RELU_EN    = 1'b1,
    parameter logic LAYER1_RELU_EN    = 1'b1,
    parameter logic LAYER2_RELU_EN    = 1'b1,
    parameter logic PACKED_WRITE_EN   = 1'b0,
    // Final 128x128 architecture executes Pool4 and Pool5 after Layer 3.
    // This may be disabled only by focused Conv-only testbenches.
    parameter logic ENABLE_STANDALONE_POOL = 1'b1,

    // The external controller reloads the Weight Buffer after each layer.
    // Therefore every layer starts reading its newly loaded weights at zero.
    parameter logic [WEIGHT_ADDR_WIDTH-1:0] WEIGHT_BASE_LAYER0 = '0,
    parameter logic [WEIGHT_ADDR_WIDTH-1:0] WEIGHT_BASE_LAYER1 = '0,
    parameter logic [WEIGHT_ADDR_WIDTH-1:0] WEIGHT_BASE_LAYER2 = '0
) (
    input  logic clk,
    input  logic rst_n,

    // CNN ACC controller interface.
    input  logic start,
    output logic done,
    output logic busy,

    // External asynchronous-read pingpongBuffer. Its read bank is selected
    // internally as r_sel=~w_sel and rData follows rAddr combinationally.
    output logic [DATA_ADDR_WIDTH-1:0]   rAddr,
    input  logic signed [BUFFER_DATA_WIDTH-1:0] rData,
    output logic [7:0]                   tile_index,
    output logic [7:0]                   pad_row,
    output logic [7:0]                   pad_col,
    output logic                         we,
    output logic                         w_sel,
    output logic [DATA_ADDR_WIDTH-1:0]   wAddr,
    output logic signed [BUFFER_DATA_WIDTH-1:0] wData,
    output logic                         packed_we,
    output logic                         packed_w_sel,
    output logic [DATA_ADDR_WIDTH-1:0]   packed_wAddr,
    output logic [NUM_CH*BUFFER_DATA_WIDTH-1:0] packed_wData,

    // External asynchronous-read 64-bit Weight Ping-Pong Buffer.
    // weight_addr is a 64-bit word address, not a byte address.
    output logic                         weight_ren,
    output logic [WEIGHT_ADDR_WIDTH-1:0] weight_addr,
    input  logic [WEIGHT_WORD_WIDTH-1:0] weight_rdata
);

    // Physical padded input of Conv Layer 1.
    localparam integer LAYER1_PAD_WIDTH  = INPUT_WIDTH + 2;
    localparam integer LAYER1_PAD_HEIGHT = INPUT_HEIGHT + 2;

    // Logical feature-map size produced by Layer 1.
    localparam integer LAYER1_OUTPUT_WIDTH =
        LAYER0_MAXPOOL_EN ? (INPUT_WIDTH/2) : INPUT_WIDTH;
    localparam integer LAYER1_OUTPUT_HEIGHT =
        LAYER0_MAXPOOL_EN ? (INPUT_HEIGHT/2) : INPUT_HEIGHT;

    // Physical padded input of Conv Layer 2.
    localparam integer LAYER2_PAD_WIDTH  = LAYER1_OUTPUT_WIDTH + 2;
    localparam integer LAYER2_PAD_HEIGHT = LAYER1_OUTPUT_HEIGHT + 2;

    // Logical feature-map size produced by Layer 2.
    localparam integer LAYER2_OUTPUT_WIDTH =
        LAYER1_MAXPOOL_EN
            ? (LAYER1_OUTPUT_WIDTH/2) : LAYER1_OUTPUT_WIDTH;
    localparam integer LAYER2_OUTPUT_HEIGHT =
        LAYER1_MAXPOOL_EN
            ? (LAYER1_OUTPUT_HEIGHT/2) : LAYER1_OUTPUT_HEIGHT;

    // Physical padded input of Conv Layer 3.
    localparam integer LAYER3_PAD_WIDTH  = LAYER2_OUTPUT_WIDTH + 2;
    localparam integer LAYER3_PAD_HEIGHT = LAYER2_OUTPUT_HEIGHT + 2;

    // Logical feature-map size produced by Layer 3. These values are also
    // the input dimensions of the later standalone Pool4 engine.
    localparam integer LAYER3_OUTPUT_WIDTH =
        LAYER2_MAXPOOL_EN
            ? (LAYER2_OUTPUT_WIDTH/2) : LAYER2_OUTPUT_WIDTH;
    localparam integer LAYER3_OUTPUT_HEIGHT =
        LAYER2_MAXPOOL_EN
            ? (LAYER2_OUTPUT_HEIGHT/2) : LAYER2_OUTPUT_HEIGHT;

    localparam integer LAYER0_WRITE_PIXELS =
        LAYER0_MAXPOOL_EN
            ? (LAYER1_OUTPUT_WIDTH * LAYER1_OUTPUT_HEIGHT)
            : (INPUT_WIDTH * INPUT_HEIGHT);
    localparam integer LAYER1_WRITE_PIXELS =
        LAYER1_MAXPOOL_EN
            ? (LAYER2_OUTPUT_WIDTH * LAYER2_OUTPUT_HEIGHT)
            : (LAYER1_OUTPUT_WIDTH * LAYER1_OUTPUT_HEIGHT);
    localparam integer LAYER2_WRITE_PIXELS =
        LAYER2_MAXPOOL_EN
            ? (LAYER3_OUTPUT_WIDTH * LAYER3_OUTPUT_HEIGHT)
            : (LAYER2_OUTPUT_WIDTH * LAYER2_OUTPUT_HEIGHT);
    localparam integer LAYER0_WRITE_GROUP_STRIDE =
        NUM_CH * LAYER0_WRITE_PIXELS;
    localparam integer LAYER1_WRITE_GROUP_STRIDE =
        NUM_CH * LAYER1_WRITE_PIXELS;
    localparam integer LAYER2_WRITE_GROUP_STRIDE =
        NUM_CH * LAYER2_WRITE_PIXELS;
    localparam integer WEIGHT_WORD_CH = WEIGHT_WORD_WIDTH / 8;
    localparam integer WEIGHT_GROUP_SUBGROUPS = NUM_CH / WEIGHT_WORD_CH;
    localparam integer LEGACY_BIAS_WORDS_PER_GROUP = WEIGHT_WORD_CH / 2;
    localparam integer LAYER0_WEIGHT_GROUP_STRIDE =
        WEIGHT_GROUP_SUBGROUPS * ((1 * 9) + LEGACY_BIAS_WORDS_PER_GROUP);
    localparam integer LAYER1_WEIGHT_GROUP_STRIDE =
        WEIGHT_GROUP_SUBGROUPS
        * ((LAYER0_OUTPUT_CHANNELS * 9) + LEGACY_BIAS_WORDS_PER_GROUP);
    localparam integer LAYER2_WEIGHT_GROUP_STRIDE =
        WEIGHT_GROUP_SUBGROUPS
        * ((LAYER1_OUTPUT_CHANNELS * 9) + LEGACY_BIAS_WORDS_PER_GROUP);
    localparam logic LAYER0_WRITE_GROUP_STRIDE_IS_POWER_OF_TWO =
        (LAYER0_WRITE_GROUP_STRIDE > 0)
        && ((LAYER0_WRITE_GROUP_STRIDE
             & (LAYER0_WRITE_GROUP_STRIDE - 1)) == 0);
    localparam logic LAYER1_WRITE_GROUP_STRIDE_IS_POWER_OF_TWO =
        (LAYER1_WRITE_GROUP_STRIDE > 0)
        && ((LAYER1_WRITE_GROUP_STRIDE
             & (LAYER1_WRITE_GROUP_STRIDE - 1)) == 0);
    localparam logic LAYER2_WRITE_GROUP_STRIDE_IS_POWER_OF_TWO =
        (LAYER2_WRITE_GROUP_STRIDE > 0)
        && ((LAYER2_WRITE_GROUP_STRIDE
             & (LAYER2_WRITE_GROUP_STRIDE - 1)) == 0);

    localparam integer CH_SELECT_WIDTH =
        (NUM_CH <= 1) ? 1 : $clog2(NUM_CH);
    localparam integer CONFIG_WIDTH = 16;
    typedef enum logic [3:0] {
        CNN_IDLE,
        PREPARE_GROUP,
        START_GROUP,
        RUN_GROUP,
        STAGE_DONE,
        WAIT_STAGE_START,
        START_POOL4,
        RUN_POOL4,
        START_POOL5,
        RUN_POOL5
    } cnn_state_t;

    cnn_state_t cnn_state;

    localparam logic [1:0] NEXT_CONV  = 2'd0;
    localparam logic [1:0] NEXT_POOL4 = 2'd1;
    localparam logic [1:0] NEXT_POOL5 = 2'd2;

    logic [1:0] next_stage;
    logic       final_stage_done;

    // Visible in the integration waveform.
    logic [1:0] layer_index;
    logic [CONFIG_WIDTH-1:0] output_group_index;

    logic conv_start;
    logic conv_done;
    logic conv_busy;
    logic pool_start;
    logic pool_done;
    logic pool_busy;
    logic select_pool;

    // Conv engine side of Feature_Buffer_Mux.
    logic [DATA_ADDR_WIDTH-1:0] conv_rAddr;
    logic signed [BUFFER_DATA_WIDTH-1:0] conv_rData;
    logic conv_we;
    logic conv_w_sel;
    logic [DATA_ADDR_WIDTH-1:0] conv_wAddr;
    logic signed [BUFFER_DATA_WIDTH-1:0] conv_wData;
    logic conv_write_we_reg;
    logic conv_write_bank_reg;
    logic [DATA_ADDR_WIDTH-1:0] conv_write_addr_reg;
    logic signed [BUFFER_DATA_WIDTH-1:0] conv_write_data_reg;
    logic packed_write_we_reg;
    logic [NUM_CH*BUFFER_DATA_WIDTH-1:0] packed_write_data_reg;
    logic conv_quant_valid;
    logic conv_meta_valid_stage1;
    logic conv_meta_valid_stage2;
    logic conv_meta_valid_stage3;
    logic conv_meta_valid_stage4;
    logic conv_meta_valid_stage5;
    logic conv_meta_bank_stage1;
    logic conv_meta_bank_stage2;
    logic conv_meta_bank_stage3;
    logic conv_meta_bank_stage4;
    logic conv_meta_bank_stage5;
    logic [DATA_ADDR_WIDTH-1:0] conv_meta_addr_stage1;
    logic [DATA_ADDR_WIDTH-1:0] conv_meta_addr_stage2;
    logic [DATA_ADDR_WIDTH-1:0] conv_meta_addr_stage3;
    logic [DATA_ADDR_WIDTH-1:0] conv_meta_addr_stage4;
    logic [DATA_ADDR_WIDTH-1:0] conv_meta_addr_stage5;

    // Standalone MaxPool side of Feature_Buffer_Mux.
    logic [DATA_ADDR_WIDTH-1:0] pool_rAddr;
    logic signed [BUFFER_DATA_WIDTH-1:0] pool_rData;
    logic pool_we;
    logic pool_w_sel;
    logic [DATA_ADDR_WIDTH-1:0] pool_wAddr;
    logic signed [BUFFER_DATA_WIDTH-1:0] pool_wData;

    logic pool_source_bank;
    logic [CONFIG_WIDTH-1:0] pool_input_width;
    logic [CONFIG_WIDTH-1:0] pool_input_height;
    logic [CONFIG_WIDTH-1:0] pool_channel_count;

    logic                    cfg_source_bank;
    logic [CONFIG_WIDTH-1:0] cfg_image_width;
    logic [CONFIG_WIDTH-1:0] cfg_image_height;
    logic [CONFIG_WIDTH-1:0] cfg_input_channels;
    logic [CONFIG_WIDTH-1:0] cfg_output_groups;
    logic cfg_MaxPool_en;
    logic cfg_Relu_en;

    logic                    active_cfg_source_bank;
    logic [CONFIG_WIDTH-1:0] active_cfg_image_width;
    logic [CONFIG_WIDTH-1:0] active_cfg_image_height;
    logic [CONFIG_WIDTH-1:0] active_cfg_input_channels;
    logic [CONFIG_WIDTH-1:0] active_cfg_output_groups;
    logic                    active_cfg_MaxPool_en;
    logic                    active_cfg_Relu_en;
    logic [DATA_ADDR_WIDTH-1:0] active_cfg_write_pixels_per_channel;
    logic [DATA_ADDR_WIDTH-1:0] active_cfg_write_group_base_addr;
    logic [WEIGHT_ADDR_WIDTH-1:0] active_cfg_weight_group_base_addr;

    // Conv_Controller ↔ external Weight Buffer register bridge.
    logic controller_weight_load_start;
    logic controller_weight_prefetch_start;
    logic controller_weight_promote_prefetch;
    logic weight_load_ready;
    logic weight_valid;
    logic weight_prefetch_busy;
    logic weight_prefetch_valid;
    logic signed [7:0] weight_reg [0:NUM_CH-1][0:8];
    logic signed [31:0] bias_reg  [0:NUM_CH-1];
    logic [CONFIG_WIDTH-1:0] next_input_channel;
    logic controller_weight_load_start_r;
    logic controller_weight_prefetch_start_r;
    logic controller_weight_promote_prefetch_r;
    logic [CONFIG_WIDTH-1:0] current_input_channel_r;
    logic [CONFIG_WIDTH-1:0] next_input_channel_r;

    // Conv_Controller ↔ external Data Buffer register bridge.
    logic controller_data_read_enable;
    logic controller_data_read_bank;
    logic [DATA_ADDR_WIDTH-1:0] controller_data_read_addr;
    logic [7:0] controller_pad_tile_index;
    logic [7:0] controller_pad_row;
    logic [7:0] controller_pad_col;
    logic data_read_ready;
    logic data_read_req_fire;
    logic data_read_fire;
    logic data_read_pending;
    logic data_read_pending_d;
    logic data_read_pending_d2;
    logic signed [7:0] data_reg;
    logic data_reg_valid;

    logic shift_pixel_ready;
    logic shift_clear;
    logic shift_reuse_horizontal;
    logic shift_reuse_vertical;
    logic [7:0] shift_tile_x_index;
    logic signed [7:0] pixel_window [0:8];
    logic shift_window_valid;
    logic shift_window_ready;
    logic [1:0] shift_window_index;
    logic shift_tile_done;
    logic ch_consumed_tile_done;
    logic zero_extend_input;

    // CH and MaxPool path.
    logic [NUM_CH-1:0] ch_enable;
    logic acc_clear;
    logic first_ic;
    logic last_ic;
    logic ch_pixel_ready;
    logic signed [31:0] conv_result [0:NUM_CH-1];
    logic [NUM_CH-1:0] conv_result_valid;

    logic [NUM_CH-1:0] result_conv_ready;
    logic result_buffer_clear;
    logic signed [31:0] result_output_data;
    logic signed [31:0] result_output_data_vec [0:NUM_CH-1];
    logic result_output_valid;
    logic result_output_ready;
    logic result_output_done;
    logic [CH_SELECT_WIDTH-1:0] result_output_channel;
    logic [1:0] result_output_position;
    logic signed [31:0] ch_wdata;
    logic signed [7:0] conv_wdata_quantized;
    logic [NUM_CH-1:0] packed_quant_valid_lane;
    logic packed_quant_valid;
    logic signed [7:0] packed_wdata_quantized [0:NUM_CH-1];
    logic [NUM_CH*BUFFER_DATA_WIDTH-1:0] packed_write_data_next;
    logic [2:0] requantize_layer;

    logic controller_data_write_enable;
    logic controller_data_write_bank;
    logic [DATA_ADDR_WIDTH-1:0] controller_data_write_addr;

    logic [CONFIG_WIDTH-1:0] current_input_channel;
    logic [CONFIG_WIDTH-1:0] current_pool_x;
    logic [CONFIG_WIDTH-1:0] current_pool_y;
    integer packed_ch;
    initial begin
        if ((NUM_CH <= 0)
            || (BUFFER_DATA_WIDTH < 8)
            || (INPUT_WIDTH <= 0)
            || (INPUT_HEIGHT <= 0)
            || (NUM_LAYERS < 1)
            || (NUM_LAYERS > 3)
            || ((WEIGHT_WORD_WIDTH % 8) != 0)
            || ((NUM_CH % WEIGHT_WORD_CH) != 0)
            || (LAYER0_OUTPUT_CHANNELS <= 0)
            || ((LAYER0_OUTPUT_CHANNELS % NUM_CH) != 0)
            || ((NUM_LAYERS >= 2)
                && ((LAYER1_OUTPUT_CHANNELS <= 0)
                    || ((LAYER1_OUTPUT_CHANNELS % NUM_CH) != 0)))
            || ((NUM_LAYERS >= 3)
                && ((LAYER2_OUTPUT_CHANNELS <= 0)
                    || ((LAYER2_OUTPUT_CHANNELS % NUM_CH) != 0)))) begin
            $error(
                "NUM_LAYERS must be 1..3, BUFFER_DATA_WIDTH must be >=8, WEIGHT_WORD_WIDTH must be byte-aligned, NUM_CH must be a multiple of the weight-word channel count, and NUM_CH must divide every active output-channel count"
            );
        end
    end

    // Each accepted start executes exactly one buffer-processing stage.
    // done pulses after that stage's final synchronous write. The external
    // controller may then update Data/Weight Buffers and issue the next start.
    assign busy =
        (cnn_state != CNN_IDLE)
        && (cnn_state != STAGE_DONE)
        && (cnn_state != WAIT_STAGE_START);
    assign done = (cnn_state == STAGE_DONE);

    // The external Data Buffer read is synchronous: address accepted at cycle N
    // returns data at cycle N+1.
    assign data_read_req_fire =
        controller_data_read_enable && data_read_ready;
    assign data_read_fire = data_read_pending_d2;
    assign conv_rAddr = controller_data_read_addr;
    assign tile_index = controller_pad_tile_index;
    assign pad_row    = controller_pad_row;
    assign pad_col    = controller_pad_col;

    // Pipeline the complete write command so layer configuration, result
    // selection, and requantization do not directly drive the feature BRAM.
    assign conv_we    = PACKED_WRITE_EN ? 1'b0 : conv_write_we_reg;
    assign conv_w_sel = conv_write_bank_reg;
    assign conv_wAddr = conv_write_addr_reg;
    assign conv_wData = conv_write_data_reg;
    assign packed_we    = PACKED_WRITE_EN ? packed_write_we_reg : 1'b0;
    assign packed_w_sel = conv_write_bank_reg;
    assign packed_wAddr = conv_write_addr_reg;
    assign packed_wData = packed_write_data_reg;

    always_comb begin
        packed_write_data_next = '0;
        for (packed_ch = 0; packed_ch < NUM_CH; packed_ch = packed_ch + 1)
            packed_write_data_next[packed_ch*BUFFER_DATA_WIDTH +:
                                   BUFFER_DATA_WIDTH] =
                packed_wdata_quantized[packed_ch];
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            conv_write_we_reg   <= 1'b0;
            conv_write_bank_reg <= 1'b0;
            conv_write_addr_reg <= '0;
            conv_write_data_reg <= '0;
            packed_write_we_reg <= 1'b0;
            packed_write_data_reg <= '0;
            conv_meta_valid_stage1 <= 1'b0;
            conv_meta_valid_stage2 <= 1'b0;
            conv_meta_valid_stage3 <= 1'b0;
            conv_meta_valid_stage4 <= 1'b0;
            conv_meta_valid_stage5 <= 1'b0;
            conv_meta_bank_stage1 <= 1'b0;
            conv_meta_bank_stage2 <= 1'b0;
            conv_meta_bank_stage3 <= 1'b0;
            conv_meta_bank_stage4 <= 1'b0;
            conv_meta_bank_stage5 <= 1'b0;
            conv_meta_addr_stage1 <= '0;
            conv_meta_addr_stage2 <= '0;
            conv_meta_addr_stage3 <= '0;
            conv_meta_addr_stage4 <= '0;
            conv_meta_addr_stage5 <= '0;
        end else begin
            conv_meta_valid_stage1 <= controller_data_write_enable;
            conv_meta_valid_stage2 <= conv_meta_valid_stage1;
            conv_meta_valid_stage3 <= conv_meta_valid_stage2;
            conv_meta_valid_stage4 <= conv_meta_valid_stage3;
            conv_meta_valid_stage5 <= conv_meta_valid_stage4;
            conv_meta_bank_stage1 <= controller_data_write_bank;
            conv_meta_bank_stage2 <= conv_meta_bank_stage1;
            conv_meta_bank_stage3 <= conv_meta_bank_stage2;
            conv_meta_bank_stage4 <= conv_meta_bank_stage3;
            conv_meta_bank_stage5 <= conv_meta_bank_stage4;
            conv_meta_addr_stage1 <= controller_data_write_addr;
            conv_meta_addr_stage2 <= conv_meta_addr_stage1;
            conv_meta_addr_stage3 <= conv_meta_addr_stage2;
            conv_meta_addr_stage4 <= conv_meta_addr_stage3;
            conv_meta_addr_stage5 <= conv_meta_addr_stage4;

            conv_write_we_reg   <= conv_quant_valid && conv_meta_valid_stage5;
            conv_write_bank_reg <= conv_meta_bank_stage5;
            conv_write_addr_reg <= conv_meta_addr_stage5;
            conv_write_data_reg <= conv_wdata_quantized;
            packed_write_we_reg <=
                packed_quant_valid && conv_meta_valid_stage5;
            if (packed_quant_valid && conv_meta_valid_stage5)
                packed_write_data_reg <= packed_write_data_next;
        end
    end

    always_comb begin
        case (layer_index)
            2'd0: requantize_layer = 3'd1;
            2'd1: requantize_layer = 3'd2;
            default: requantize_layer = 3'd3;
        endcase
    end

    always_comb begin
        next_input_channel = current_input_channel;
        if (current_input_channel + 1'b1 < active_cfg_input_channels)
            next_input_channel = current_input_channel + 1'b1;
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            controller_weight_load_start_r <= 1'b0;
            controller_weight_prefetch_start_r <= 1'b0;
            controller_weight_promote_prefetch_r <= 1'b0;
            current_input_channel_r <= '0;
            next_input_channel_r <= '0;
        end else begin
            // Register this module boundary to shorten controller->loader
            // setup paths through the Weight_Loader combinational logic.
            controller_weight_load_start_r <= controller_weight_load_start;
            controller_weight_prefetch_start_r <=
                controller_weight_prefetch_start;
            controller_weight_promote_prefetch_r <=
                controller_weight_promote_prefetch;
            current_input_channel_r <= current_input_channel;
            next_input_channel_r <= next_input_channel;
        end
    end

    requantize_pipeline U_REQUANTIZE_CONV_WRITE (
        .clk     (clk),
        .rst_n   (rst_n),
        .in_valid(controller_data_write_enable),
        .layer   (requantize_layer),
        .acc_in  (ch_wdata),
        .out_valid(conv_quant_valid),
        .quant_out(conv_wdata_quantized)
    );

    generate
        genvar packed_quant_ch;
        for (packed_quant_ch = 0; packed_quant_ch < NUM_CH;
             packed_quant_ch = packed_quant_ch + 1) begin : GEN_PACKED_REQUANT
            requantize_pipeline U_REQUANTIZE_PACKED_WRITE (
                .clk     (clk),
                .rst_n   (rst_n),
                .in_valid(controller_data_write_enable),
                .layer   (requantize_layer),
                .acc_in  (result_output_data_vec[packed_quant_ch]),
                .out_valid(packed_quant_valid_lane[packed_quant_ch]),
                .quant_out(packed_wdata_quantized[packed_quant_ch])
            );
        end
    endgenerate

    assign packed_quant_valid = packed_quant_valid_lane[0];

    // Select the run-time configuration for the active layer.
    always_comb begin
        cfg_source_bank        = 1'b0;
        cfg_image_width        = LAYER1_PAD_WIDTH;
        cfg_image_height       = LAYER1_PAD_HEIGHT;
        cfg_input_channels     = 16'd1;
        cfg_output_groups      = LAYER0_OUTPUT_CHANNELS / NUM_CH;
        cfg_MaxPool_en = LAYER0_MAXPOOL_EN;
        cfg_Relu_en    = LAYER0_RELU_EN;

        case (layer_index)
            2'd0: begin
                cfg_source_bank     = 1'b0;
                cfg_image_width     = LAYER1_PAD_WIDTH;
                cfg_image_height    = LAYER1_PAD_HEIGHT;
                cfg_MaxPool_en      = LAYER0_MAXPOOL_EN;
                cfg_Relu_en         = LAYER0_RELU_EN;
            end

            2'd1: begin
                cfg_source_bank     = 1'b1;
                cfg_image_width     = LAYER2_PAD_WIDTH;
                cfg_image_height    = LAYER2_PAD_HEIGHT;
                cfg_input_channels  = LAYER0_OUTPUT_CHANNELS;
                cfg_output_groups   =
                    LAYER1_OUTPUT_CHANNELS / NUM_CH;
                cfg_MaxPool_en      = LAYER1_MAXPOOL_EN;
                cfg_Relu_en         = LAYER1_RELU_EN;
            end

            default: begin
                cfg_source_bank     = 1'b0;
                cfg_image_width     = LAYER3_PAD_WIDTH;
                cfg_image_height    = LAYER3_PAD_HEIGHT;
                cfg_input_channels  = LAYER1_OUTPUT_CHANNELS;
                cfg_output_groups   =
                    LAYER2_OUTPUT_CHANNELS / NUM_CH;
                cfg_MaxPool_en      = LAYER2_MAXPOOL_EN;
                cfg_Relu_en         = LAYER2_RELU_EN;
            end
        endcase
    end

    // Pool4 and Pool5 reuse one standalone engine. Layer 3 writes its compact
    // 64x16x16 result to Bank B; Pool4 reads B/writes A, then Pool5 reads
    // A/writes B.
    always_comb begin
        select_pool =
            (cnn_state == START_POOL4)
            || (cnn_state == RUN_POOL4)
            || (cnn_state == START_POOL5)
            || (cnn_state == RUN_POOL5);

        pool_source_bank  = 1'b1;
        pool_input_width  = LAYER3_OUTPUT_WIDTH;
        pool_input_height = LAYER3_OUTPUT_HEIGHT;
        pool_channel_count = LAYER2_OUTPUT_CHANNELS;

        if ((cnn_state == START_POOL5)
            || (cnn_state == RUN_POOL5)) begin
            pool_source_bank  = 1'b0;
            pool_input_width  = LAYER3_OUTPUT_WIDTH/2;
            pool_input_height = LAYER3_OUTPUT_HEIGHT/2;
        end
    end

    // The external ping-pong buffer must contain the current stage input
    // before start. One start executes one Conv layer, Pool4, or Pool5.
    // After every stage, done pulses and the FSM waits for the next start so
    // that the external controller can update the ping-pong/weight buffers.
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            cnn_state           <= CNN_IDLE;
            layer_index         <= 2'd0;
            output_group_index  <= '0;
            active_cfg_source_bank <= 1'b0;
            active_cfg_image_width <= '0;
            active_cfg_image_height <= '0;
            active_cfg_input_channels <= '0;
            active_cfg_output_groups <= '0;
            active_cfg_MaxPool_en <= 1'b0;
            active_cfg_Relu_en <= 1'b0;
            active_cfg_write_pixels_per_channel <= '0;
            active_cfg_write_group_base_addr <= '0;
            active_cfg_weight_group_base_addr <= '0;
            conv_start          <= 1'b0;
            pool_start          <= 1'b0;
            next_stage          <= NEXT_CONV;
            final_stage_done    <= 1'b0;
        end else begin
            conv_start <= 1'b0;
            pool_start <= 1'b0;

            case (cnn_state)
                CNN_IDLE: begin
                    if (start) begin
                        layer_index         <= 2'd0;
                        output_group_index  <= '0;
                        next_stage          <= NEXT_CONV;
                        final_stage_done    <= 1'b0;
                        cnn_state           <= PREPARE_GROUP;
                    end
                end

                PREPARE_GROUP: begin
                    active_cfg_source_bank <= cfg_source_bank;
                    active_cfg_image_width <= cfg_image_width;
                    active_cfg_image_height <= cfg_image_height;
                    active_cfg_input_channels <= cfg_input_channels;
                    active_cfg_output_groups <= cfg_output_groups;
                    active_cfg_MaxPool_en <= cfg_MaxPool_en;
                    active_cfg_Relu_en <= cfg_Relu_en;
                    case (layer_index)
                        2'd0: begin
                            active_cfg_write_pixels_per_channel <=
                                LAYER0_WRITE_PIXELS;
                            active_cfg_weight_group_base_addr <=
                                output_group_index
                                * LAYER0_WEIGHT_GROUP_STRIDE;
                            if (LAYER0_WRITE_GROUP_STRIDE_IS_POWER_OF_TWO)
                                active_cfg_write_group_base_addr <=
                                    output_group_index
                                    << $clog2(LAYER0_WRITE_GROUP_STRIDE);
                            else
                                active_cfg_write_group_base_addr <=
                                    output_group_index
                                    * LAYER0_WRITE_GROUP_STRIDE;
                        end

                        2'd1: begin
                            active_cfg_write_pixels_per_channel <=
                                LAYER1_WRITE_PIXELS;
                            active_cfg_weight_group_base_addr <=
                                output_group_index
                                * LAYER1_WEIGHT_GROUP_STRIDE;
                            if (LAYER1_WRITE_GROUP_STRIDE_IS_POWER_OF_TWO)
                                active_cfg_write_group_base_addr <=
                                    output_group_index
                                    << $clog2(LAYER1_WRITE_GROUP_STRIDE);
                            else
                                active_cfg_write_group_base_addr <=
                                    output_group_index
                                    * LAYER1_WRITE_GROUP_STRIDE;
                        end

                        default: begin
                            active_cfg_write_pixels_per_channel <=
                                LAYER2_WRITE_PIXELS;
                            active_cfg_weight_group_base_addr <=
                                output_group_index
                                * LAYER2_WEIGHT_GROUP_STRIDE;
                            if (LAYER2_WRITE_GROUP_STRIDE_IS_POWER_OF_TWO)
                                active_cfg_write_group_base_addr <=
                                    output_group_index
                                    << $clog2(LAYER2_WRITE_GROUP_STRIDE);
                            else
                                active_cfg_write_group_base_addr <=
                                    output_group_index
                                    * LAYER2_WRITE_GROUP_STRIDE;
                        end
                    endcase
                    cnn_state <= START_GROUP;
                end

                START_GROUP: begin
                    conv_start <= 1'b1;
                    cnn_state  <= RUN_GROUP;
                end

                RUN_GROUP: begin
                    if (conv_done) begin
                        if (output_group_index
                            == active_cfg_output_groups-1) begin
                            output_group_index <= '0;

                            if (layer_index < NUM_LAYERS-1) begin
                                layer_index      <= layer_index + 1'b1;
                                next_stage       <= NEXT_CONV;
                                final_stage_done <= 1'b0;
                            end else if (ENABLE_STANDALONE_POOL
                                         && (NUM_LAYERS == 3)) begin
                                next_stage       <= NEXT_POOL4;
                                final_stage_done <= 1'b0;
                            end else begin
                                final_stage_done <= 1'b1;
                            end

                            cnn_state <= STAGE_DONE;
                        end else begin
                            output_group_index <=
                                output_group_index + 1'b1;
                            cnn_state <= PREPARE_GROUP;
                        end
                    end
                end

                STAGE_DONE: begin
                    // done is high throughout this state. For a non-final
                    // stage, wait until the external controller has updated
                    // its buffers and supplies another start pulse.
                    if (final_stage_done) begin
                        layer_index         <= 2'd0;
                        output_group_index  <= '0;
                        next_stage          <= NEXT_CONV;
                        final_stage_done    <= 1'b0;
                        cnn_state           <= CNN_IDLE;
                    end else begin
                        cnn_state <= WAIT_STAGE_START;
                    end
                end

                WAIT_STAGE_START: begin
                    if (start) begin
                        case (next_stage)
                            NEXT_POOL4: cnn_state <= START_POOL4;
                            NEXT_POOL5: cnn_state <= START_POOL5;
                            default:    cnn_state <= PREPARE_GROUP;
                        endcase
                    end
                end

                START_POOL4: begin
                    pool_start <= 1'b1;
                    cnn_state  <= RUN_POOL4;
                end

                RUN_POOL4: begin
                    if (pool_done) begin
                        next_stage       <= NEXT_POOL5;
                        final_stage_done <= 1'b0;
                        cnn_state        <= STAGE_DONE;
                    end
                end

                START_POOL5: begin
                    pool_start <= 1'b1;
                    cnn_state  <= RUN_POOL5;
                end

                RUN_POOL5: begin
                    if (pool_done) begin
                        final_stage_done <= 1'b1;
                        cnn_state        <= STAGE_DONE;
                    end
                end

                default: begin
                    cnn_state <= CNN_IDLE;
                end
            endcase
        end
    end

    // Reads nine 64-bit Weight words and the layer-end Bias word from the
    // external buffer. Conv_Controller waits for weight_valid before it
    // starts requesting feature-map pixels.
    Weight_Loader #(
        .NUM_CH      (NUM_CH),
        .ADDR_WIDTH  (WEIGHT_ADDR_WIDTH),
        .CONFIG_WIDTH(CONFIG_WIDTH),
        .WEIGHT_WORD_WIDTH(WEIGHT_WORD_WIDTH)
    ) U_WEIGHT_LOADER (
        .clk                (clk),
        .rst_n              (rst_n),
        .load_start         (controller_weight_load_start_r),
        .prefetch_start     (controller_weight_prefetch_start_r),
        .promote_prefetch   (controller_weight_promote_prefetch_r),
        .load_ready         (weight_load_ready),
        .busy               (),
        .weight_valid       (weight_valid),
        .prefetch_busy      (weight_prefetch_busy),
        .prefetch_valid     (weight_prefetch_valid),
        .input_channels     (active_cfg_input_channels),
        .group_base_address (active_cfg_weight_group_base_addr),
        .input_channel_index(current_input_channel_r),
        .prefetch_channel_index(next_input_channel_r),
        .wt_ren             (weight_ren),
        .wt_raddr           (weight_addr),
        .wt_rdata           (weight_rdata),
        .weight_out         (weight_reg),
        .bias_out           (bias_reg)
    );

    Conv_Controller #(
        .NUM_CH      (NUM_CH),
        .ADDR_WIDTH  (DATA_ADDR_WIDTH),
        .CONFIG_WIDTH(CONFIG_WIDTH)
    ) U_CONV_CONTROLLER (
        .clk                    (clk),
        .rst_n                  (rst_n),
        .start                  (conv_start),
        .source_bank            (active_cfg_source_bank),
        .MaxPool_en             (active_cfg_MaxPool_en),
        .cfg_image_width        (active_cfg_image_width),
        .cfg_image_height       (active_cfg_image_height),
        .cfg_input_channels     (active_cfg_input_channels),
        .cfg_write_pixels_per_channel(active_cfg_write_pixels_per_channel),
        .cfg_write_group_base_addr(active_cfg_write_group_base_addr),
        .busy                   (conv_busy),
        .done                   (conv_done),
        .weight_load_start      (controller_weight_load_start),
        .weight_prefetch_start  (controller_weight_prefetch_start),
        .weight_promote_prefetch(controller_weight_promote_prefetch),
        .weight_load_ready      (weight_load_ready),
        .weight_valid           (weight_valid),
        .weight_prefetch_busy   (weight_prefetch_busy),
        .weight_prefetch_valid  (weight_prefetch_valid),
        .data_read_enable       (controller_data_read_enable),
        .data_read_ready        (data_read_ready),
        .data_read_bank         (controller_data_read_bank),
        .data_read_addr         (controller_data_read_addr),
        .pad_tile_index         (controller_pad_tile_index),
        .pad_row                (controller_pad_row),
        .pad_col                (controller_pad_col),
        .shift_pixel_ready      (shift_pixel_ready),
        .shift_window_valid     (shift_window_valid),
        .shift_window_ready     (shift_window_ready),
        .shift_tile_done        (ch_consumed_tile_done),
        .shift_clear            (shift_clear),
        .shift_reuse_horizontal (shift_reuse_horizontal),
        .shift_reuse_vertical   (shift_reuse_vertical),
        .shift_tile_x_index     (shift_tile_x_index),
        .ch_enable              (ch_enable),
        .acc_clear              (acc_clear),
        .first_ic               (first_ic),
        .last_ic                (last_ic),
        .result_buffer_clear    (result_buffer_clear),
        .result_output_valid    (result_output_valid),
        .result_output_ready    (result_output_ready),
        .result_output_done     (result_output_done),
        .result_output_channel  (result_output_channel),
        .result_output_position (result_output_position),
        .data_write_enable      (controller_data_write_enable),
        .data_write_bank        (controller_data_write_bank),
        .data_write_addr        (controller_data_write_addr),
        .input_channel_index    (current_input_channel),
        .pool_x_index           (current_pool_x),
        .pool_y_index           (current_pool_y)
    );

    Standalone_MaxPool #(
        .DATA_WIDTH  (BUFFER_DATA_WIDTH),
        .ADDR_WIDTH  (DATA_ADDR_WIDTH),
        .CONFIG_WIDTH(CONFIG_WIDTH)
    ) U_STANDALONE_MAXPOOL (
        .clk          (clk),
        .rst_n        (rst_n),
        .start        (pool_start),
        .source_bank  (pool_source_bank),
        .input_width  (pool_input_width),
        .input_height (pool_input_height),
        .channel_count(pool_channel_count),
        .busy         (pool_busy),
        .done         (pool_done),
        .rAddr        (pool_rAddr),
        .rData        (pool_rData),
        .we           (pool_we),
        .w_sel        (pool_w_sel),
        .wAddr        (pool_wAddr),
        .wData        (pool_wData)
    );

    Feature_Buffer_Mux #(
        .DATA_WIDTH(BUFFER_DATA_WIDTH),
        .ADDR_WIDTH(DATA_ADDR_WIDTH)
    ) U_FEATURE_BUFFER_MUX (
        .select_pool (select_pool),
        .conv_rAddr  (conv_rAddr),
        .conv_rData  (conv_rData),
        .conv_we     (conv_we),
        .conv_w_sel  (conv_w_sel),
        .conv_wAddr  (conv_wAddr),
        .conv_wData  (conv_wData),
        .pool_rAddr  (pool_rAddr),
        .pool_rData  (pool_rData),
        .pool_we     (pool_we),
        .pool_w_sel  (pool_w_sel),
        .pool_wAddr  (pool_wAddr),
        .pool_wData  (pool_wData),
        .buffer_rAddr(rAddr),
        .buffer_rData(rData),
        .buffer_we   (we),
        .buffer_w_sel(w_sel),
        .buffer_wAddr(wAddr),
        .buffer_wData(wData)
    );

    Shift_Buffer #(
        .PIXEL_WIDTH(8),
        .TILE_X_WIDTH(8),
        .MAX_TILE_COLUMNS(INPUT_WIDTH)
    ) U_SHIFT_BUFFER (
        .clk         (clk),
        .rst_n       (rst_n),
        .clear       (shift_clear),
        .reuse_horizontal(shift_reuse_horizontal),
        .reuse_vertical(shift_reuse_vertical),
        .tile_x_index(shift_tile_x_index),
        .pixel_data  (data_reg),
        .pixel_valid (data_reg_valid),
        .pixel_ready (shift_pixel_ready),
        .pixel_window(pixel_window),
        .window_valid(shift_window_valid),
        .window_ready(shift_window_ready),
        .window_index(shift_window_index),
        .tile_done   (shift_tile_done)
    );

    CH_wrapper #(
        .NUM_CH      (NUM_CH),
        .OUTPUT_SHIFT(8)
    ) U_CH_WRAPPER (
        .clk         (clk),
        .rst_n       (rst_n),
        .ch_enable   (ch_enable),
        .acc_clear   (acc_clear),
        .first_ic    (first_ic),
        .last_ic     (last_ic),
        .pixel_valid (shift_window_valid),
        .pixel_ready (ch_pixel_ready),
        .window_index(shift_window_index),
        .zero_extend_input(zero_extend_input),
        .pixel_in    (pixel_window),
        .weight_valid(weight_valid),
        .weight_in   (weight_reg),
        .bias_in     (bias_reg),
        .result_out  (conv_result),
        .result_valid(conv_result_valid),
        .result_ready(result_conv_ready),
        .consumed_tile_done(ch_consumed_tile_done)
    );

    CH_Result_Buffer #(
        .NUM_CH    (NUM_CH),
        .DATA_WIDTH(32),
        .PACKED_OUTPUT(PACKED_WRITE_EN)
    ) U_CH_RESULT_BUFFER (
        .clk            (clk),
        .rst_n          (rst_n),
        .clear          (result_buffer_clear),
        .MaxPool_en     (active_cfg_MaxPool_en),
        .Relu_en        (active_cfg_Relu_en),
        .conv_data      (conv_result),
        .conv_valid     (conv_result_valid),
        .conv_ready     (result_conv_ready),
        .output_data    (result_output_data),
        .output_data_vec(result_output_data_vec),
        .output_valid   (result_output_valid),
        .output_ready   (result_output_ready),
        .output_channel (result_output_channel),
        .output_position(result_output_position),
        .output_done    (result_output_done)
    );

    assign shift_window_ready   = (|ch_enable) && ch_pixel_ready;
    assign ch_wdata             = result_output_data;
    assign zero_extend_input    = 1'b0;

    // Capture the asynchronous external Data Buffer output in a CNN-local
    // register. A new value may replace the current value on the same clock
    // that Shift_Buffer consumes it.
    assign data_read_ready = !data_reg_valid || shift_pixel_ready;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            data_read_pending <= 1'b0;
            data_read_pending_d <= 1'b0;
            data_read_pending_d2 <= 1'b0;
            data_reg       <= 8'd0;
            data_reg_valid <= 1'b0;
        end else begin
            data_read_pending <= data_read_req_fire;
            data_read_pending_d <= data_read_pending;
            data_read_pending_d2 <= data_read_pending_d;

            if (data_reg_valid && shift_pixel_ready)
                data_reg_valid <= 1'b0;

            if (data_read_fire) begin
                data_reg       <= conv_rData[7:0];
                data_reg_valid <= 1'b1;
            end
        end
    end

endmodule
