`timescale 1ns / 1ps

// Controls one physical NUM_CH-wide output-channel group.
//
// CH results are stored by CH_Result_Buffer as four spatial vectors. When
// MaxPool_en is high, one value per channel is written. Otherwise all four
// convolution positions are written without spatial downsampling.
module Conv_Controller #(
    parameter integer NUM_CH       = 8,
    parameter integer ADDR_WIDTH   = 32,
    parameter integer CONFIG_WIDTH = 16,
    parameter integer CH_SELECT_WIDTH =
        (NUM_CH <= 1) ? 1 : $clog2(NUM_CH)
) (
    input logic clk,
    input logic rst_n,

    input  logic                    start,
    input  logic                    source_bank,
    input  logic                    MaxPool_en,
    input  logic [CONFIG_WIDTH-1:0] cfg_image_width,
    input  logic [CONFIG_WIDTH-1:0] cfg_image_height,
    input  logic [CONFIG_WIDTH-1:0] cfg_input_channels,
    input  logic [ADDR_WIDTH-1:0]   cfg_write_pixels_per_channel,
    input  logic [ADDR_WIDTH-1:0]   cfg_write_group_base_addr,
    output logic                    busy,
    output logic                    done,

    // External Weight Buffer request and CNN-local register status.
    output logic                  weight_load_start,
    output logic                  weight_prefetch_start,
    output logic                  weight_promote_prefetch,
    input  logic                  weight_load_ready,
    input  logic                  weight_valid,
    input  logic                  weight_prefetch_busy,
    input  logic                  weight_prefetch_valid,

    // External Data Buffer request and Shift_Buffer status.
    output logic                  data_read_enable,
    input  logic                  data_read_ready,
    output logic                  data_read_bank,
    output logic [ADDR_WIDTH-1:0] data_read_addr,
    output logic [7:0]            pad_tile_index,
    output logic [7:0]            pad_row,
    output logic [7:0]            pad_col,
    input  logic                  shift_pixel_ready,
    input  logic                  shift_window_valid,
    input  logic                  shift_window_ready,
    input  logic                  shift_tile_done,
    output logic                  shift_clear,
    output logic                  shift_reuse_horizontal,
    output logic                  shift_reuse_vertical,
    output logic [7:0]            shift_tile_x_index,

    // CH_wrapper control.
    output logic [NUM_CH-1:0] ch_enable,
    output logic              acc_clear,
    output logic              first_ic,
    output logic              last_ic,

    // CH_Result_Buffer control and serialized output status.
    output logic result_buffer_clear,
    input  logic result_output_valid,
    output logic result_output_ready,
    input  logic result_output_done,
    input  logic [CH_SELECT_WIDTH-1:0] result_output_channel,
    input  logic [1:0] result_output_position,

    // External Data Buffer write control. Write data comes directly from
    // CH_Result_Buffer.
    output logic                  data_write_enable,
    output logic                  data_write_bank,
    output logic [ADDR_WIDTH-1:0] data_write_addr,

    // Debug/status indices.
    output logic [CONFIG_WIDTH-1:0] input_channel_index,
    output logic [CONFIG_WIDTH-1:0] pool_x_index,
    output logic [CONFIG_WIDTH-1:0] pool_y_index
);

    // Nine signed 8-bit weights and one signed 32-bit bias per channel.
    localparam integer WEIGHT_PACKET_BYTES = 13 * NUM_CH;

    typedef enum logic [3:0] {
        IDLE,
        SET_WEIGHT_DATA,
        CONV,
        POST_PROCESS,
        PUSH_DATA,
        WRITE_DRAIN,
        DONE
    } state_t;

    state_t state;

    logic setup_issued;
    logic waiting_for_prefetch;
    logic [4:0] data_request_count;
    logic [2:0] write_drain_count;

    logic [CONFIG_WIDTH-1:0] input_channel_reg;
    logic [CONFIG_WIDTH-1:0] pool_x_reg;
    logic [CONFIG_WIDTH-1:0] pool_y_reg;

    logic source_bank_reg;
    logic MaxPool_en_reg;
    logic [CONFIG_WIDTH-1:0] image_width_reg;
    logic [CONFIG_WIDTH-1:0] image_height_reg;
    logic [CONFIG_WIDTH-1:0] pool_x_last_reg;
    logic [CONFIG_WIDTH-1:0] pool_y_last_reg;
    logic [CONFIG_WIDTH-1:0] input_channels_reg;

    logic [ADDR_WIDTH-1:0] conv_width;
    logic [ADDR_WIDTH-1:0] conv_height;
    logic [ADDR_WIDTH-1:0] write_pixels_per_channel_reg;
    logic [ADDR_WIDTH-1:0] write_group_base_addr;
    logic [ADDR_WIDTH-1:0] write_channel_base_addr;
    logic [ADDR_WIDTH-1:0] write_spatial_addr;
    logic [ADDR_WIDTH-1:0] data_read_addr_reg;
    logic [ADDR_WIDTH-1:0] data_read_row_step_reg;
    logic [ADDR_WIDTH-1:0] data_read_plane_step_reg;
    logic [ADDR_WIDTH-1:0] data_read_tile_row_step_reg;
    logic [ADDR_WIDTH-1:0] data_read_channel_base_addr_reg;
    logic [ADDR_WIDTH-1:0] data_read_row_base_addr_reg;
    logic [ADDR_WIDTH-1:0] data_read_tile_base_addr_reg;
    logic [ADDR_WIDTH-1:0] data_read_row_wrap_step_reg;
    logic [CONFIG_WIDTH-1:0] pad_row_calc;
    logic [CONFIG_WIDTH-1:0] pad_col_calc;
    logic [7:0] pool_x_coord;
    logic [7:0] pool_y_coord;
    logic tile_at_row_end_reg;
    logic reuse_horizontal;
    logic reuse_vertical;
    logic [4:0] reads_per_tile;

    assign input_channel_index = input_channel_reg;
    assign pool_x_index        = pool_x_reg;
    assign pool_y_index        = pool_y_reg;

    always_comb begin
        // Coordinate generation for padding never needs bits above 7:0.
        pool_x_coord = pool_x_reg[7:0];
        pool_y_coord = pool_y_reg[7:0];
        conv_width    = image_width_reg - 2;
        conv_height   = image_height_reg - 2;
        reuse_horizontal = (input_channels_reg == 1) && (pool_x_coord != 0);
        reuse_vertical = (input_channels_reg == 1)
            && (pool_x_coord == 0)
            && (pool_y_coord != 0);
        reads_per_tile = (reuse_horizontal || reuse_vertical) ? 5'd8 : 5'd16;
        pad_row_calc  = (pool_y_coord << 1)
            + (reuse_horizontal ? data_request_count[2:1]
               : reuse_vertical ? (2 + data_request_count[2])
                                : data_request_count[3:2]);
        pad_col_calc  = (pool_x_coord << 1)
            + (reuse_horizontal ? (2 + data_request_count[0])
               : reuse_vertical ? data_request_count[1:0]
               : data_request_count[1:0]);
    end

    always_comb begin
        // DONE is a one-clock completion state entered only after the final
        // synchronous Data Buffer write edge.
        busy = (state != IDLE) && (state != DONE);
        done = (state == DONE);

        weight_load_start = 1'b0;
        weight_prefetch_start = 1'b0;
        weight_promote_prefetch = 1'b0;

        data_read_enable = 1'b0;
        data_read_bank   = source_bank_reg;
        pad_tile_index   = input_channel_reg[7:0];
        pad_row          = pad_row_calc[7:0];
        pad_col          = pad_col_calc[7:0];
        data_read_addr   = data_read_addr_reg;

        shift_clear = 1'b0;
        shift_reuse_horizontal = reuse_horizontal;
        shift_reuse_vertical = reuse_vertical;
        shift_tile_x_index = pool_x_reg[7:0];

        ch_enable = {NUM_CH{1'b0}};
        acc_clear = 1'b0;
        first_ic  = (input_channel_reg == 0);
        last_ic   = (input_channel_reg == input_channels_reg-1);

        result_buffer_clear = 1'b0;
        result_output_ready = 1'b0;

        data_write_enable = 1'b0;
        data_write_bank   = !source_bank_reg;
        data_write_addr   = write_channel_base_addr + write_spatial_addr;

        case (state)
            IDLE: begin
                if (start) begin
                    acc_clear          = 1'b1;
                    result_buffer_clear = 1'b1;
                    shift_clear        = 1'b1;
                end
            end

            SET_WEIGHT_DATA: begin
                ch_enable = {NUM_CH{1'b1}};

                if (waiting_for_prefetch) begin
                    if (weight_prefetch_valid)
                        weight_promote_prefetch = 1'b1;
                end else if (!setup_issued) begin
                    weight_load_start = 1'b1;
                    shift_clear       = 1'b1;
                end else if (weight_valid
                             && (data_request_count < reads_per_tile)
                             && shift_pixel_ready) begin
                    data_read_enable = 1'b1;
                end
            end

            CONV: begin
                ch_enable = {NUM_CH{1'b1}};
                if ((input_channel_reg != input_channels_reg-1)
                    && !weight_prefetch_valid
                    && !weight_prefetch_busy)
                    weight_prefetch_start = 1'b1;
            end

            POST_PROCESS: begin
                // Wait until CH_Result_Buffer has captured all four vectors.
            end

            PUSH_DATA: begin
                result_output_ready = 1'b1;
                data_write_enable   = result_output_valid;
            end

            DONE: begin
                // One-clock done pulse. All write controls remain inactive.
            end

            default: begin
                // Safe inactive defaults.
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            data_read_addr_reg <= '0;
            data_read_row_step_reg <= '0;
            data_read_plane_step_reg <= '0;
            data_read_tile_row_step_reg <= '0;
            data_read_channel_base_addr_reg <= '0;
            data_read_row_base_addr_reg <= '0;
            data_read_tile_base_addr_reg <= '0;
            data_read_row_wrap_step_reg <= '0;
            tile_at_row_end_reg <= 1'b0;
        end else if (state == IDLE && start) begin
            data_read_row_step_reg <= cfg_image_width - 3;
            data_read_plane_step_reg <= cfg_image_width * cfg_image_height;
            data_read_tile_row_step_reg <= cfg_image_width << 1;
            data_read_row_wrap_step_reg <= (cfg_image_width << 1)
                - ((((cfg_image_width - 2) >> 1) - 1'b1) << 1);
            data_read_channel_base_addr_reg <= '0;
            data_read_row_base_addr_reg <= '0;
            data_read_tile_base_addr_reg <= '0;
            data_read_addr_reg <= '0;
            tile_at_row_end_reg <= (((cfg_image_width - 2) >> 1) == 1);
        end else begin
            if ((state == CONV) && shift_tile_done
                && (input_channel_reg != input_channels_reg-1)) begin
                // Move to the next input-channel plane for the same tile.
                data_read_channel_base_addr_reg <=
                    data_read_channel_base_addr_reg + data_read_plane_step_reg;
                data_read_tile_base_addr_reg <=
                    data_read_tile_base_addr_reg + data_read_plane_step_reg;
                data_read_addr_reg <=
                    data_read_tile_base_addr_reg + data_read_plane_step_reg;
            end else if ((state == PUSH_DATA) && result_output_done
                && !((pool_x_reg == pool_x_last_reg)
                     && (pool_y_reg == pool_y_last_reg))) begin
                if (tile_at_row_end_reg) begin
                    data_read_channel_base_addr_reg <= '0;
                    data_read_row_base_addr_reg <=
                        data_read_row_base_addr_reg + data_read_tile_row_step_reg;
                    data_read_tile_base_addr_reg <=
                        data_read_row_base_addr_reg + data_read_tile_row_step_reg;
                    data_read_addr_reg <=
                        data_read_row_base_addr_reg
                        + data_read_tile_row_step_reg
                        + ((input_channels_reg == 1)
                           ? (image_width_reg << 1) : '0);
                    tile_at_row_end_reg <= (pool_x_last_reg == 0);
                end else begin
                    data_read_channel_base_addr_reg <= '0;
                    data_read_tile_base_addr_reg <=
                        data_read_row_base_addr_reg + ((pool_x_reg + 1'b1) << 1);
                    data_read_addr_reg <=
                        data_read_row_base_addr_reg + ((pool_x_reg + 1'b1) << 1)
                        + ((input_channels_reg == 1) ? 2 : 0);
                    tile_at_row_end_reg <= (pool_x_reg + 1'b1 == pool_x_last_reg);
                end
            end else if (!setup_issued && weight_load_start && weight_load_ready) begin
                // setup_issued==0 occurs for channel0 at tile start. Address
                // bases are already prepared by the state transitions above.
                data_read_addr_reg <= data_read_tile_base_addr_reg;
            end else if (data_read_enable && data_read_ready) begin
                if (reuse_horizontal && data_request_count[0])
                    data_read_addr_reg <=
                        data_read_addr_reg + image_width_reg - 1'b1;
                else if (reuse_vertical && (data_request_count[1:0] == 2'd3))
                    data_read_addr_reg <=
                        data_read_addr_reg + data_read_row_step_reg;
                else if (!reuse_horizontal && (data_request_count[1:0] == 2'd3))
                    data_read_addr_reg <=
                        data_read_addr_reg + data_read_row_step_reg;
                else
                    data_read_addr_reg <= data_read_addr_reg + 1'b1;
            end
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state                   <= IDLE;
            setup_issued            <= 1'b0;
            waiting_for_prefetch    <= 1'b0;
            data_request_count      <= 5'd0;
            write_drain_count       <= 2'd0;
            input_channel_reg       <= '0;
            pool_x_reg              <= '0;
            pool_y_reg              <= '0;
            source_bank_reg         <= 1'b0;
            MaxPool_en_reg          <= 1'b0;
            image_width_reg         <= '0;
            image_height_reg        <= '0;
            pool_x_last_reg         <= '0;
            pool_y_last_reg         <= '0;
            input_channels_reg      <= '0;
            write_group_base_addr   <= '0;
            write_channel_base_addr <= '0;
            write_spatial_addr      <= '0;
            write_pixels_per_channel_reg <= '0;
        end else begin
            case (state)
                IDLE: begin
                    if (start) begin
                        source_bank_reg         <= source_bank;
                        MaxPool_en_reg          <= MaxPool_en;
                        image_width_reg         <= cfg_image_width;
                        image_height_reg        <= cfg_image_height;
                        pool_x_last_reg         <=
                            ((cfg_image_width - 2) >> 1) - 1'b1;
                        pool_y_last_reg         <=
                            ((cfg_image_height - 2) >> 1) - 1'b1;
                        input_channels_reg      <= cfg_input_channels;
                        write_group_base_addr   <= cfg_write_group_base_addr;
                        write_channel_base_addr <= cfg_write_group_base_addr;
                        write_pixels_per_channel_reg <=
                            cfg_write_pixels_per_channel;
                        write_spatial_addr      <= '0;
                        input_channel_reg       <= '0;
                        pool_x_reg              <= '0;
                        pool_y_reg              <= '0;
                        setup_issued            <= 1'b0;
                        waiting_for_prefetch    <= 1'b0;
                        data_request_count      <= 5'd0;
                        write_drain_count       <= 2'd0;
                        state                   <= SET_WEIGHT_DATA;
                    end
                end

                SET_WEIGHT_DATA: begin
                    if (waiting_for_prefetch) begin
                        if (weight_prefetch_valid) begin
                            waiting_for_prefetch <= 1'b0;
                            setup_issued <= 1'b1;
                        end
                    end else if (!setup_issued) begin
                        if (weight_load_start && weight_load_ready) begin
                            setup_issued <= 1'b1;
                        end
                    end else begin
                        if (data_read_enable && data_read_ready) begin
                            data_request_count <=
                                data_request_count + 1'b1;
                        end

                        if (shift_window_valid && shift_window_ready)
                            state <= CONV;
                    end
                end

                CONV: begin
                    if (shift_tile_done) begin
                        if (input_channel_reg
                            == input_channels_reg-1) begin
                            state <= POST_PROCESS;
                        end else begin
                            input_channel_reg <=
                                input_channel_reg + 1'b1;
                            waiting_for_prefetch <=
                                !weight_prefetch_valid && weight_prefetch_busy;
                            setup_issued       <= weight_prefetch_valid;
                            data_request_count <= 5'd0;
                            state              <= SET_WEIGHT_DATA;
                        end
                    end
                end

                POST_PROCESS: begin
                    if (result_output_valid)
                        state <= PUSH_DATA;
                end

                PUSH_DATA: begin
                    if (data_write_enable) begin
                        if (result_output_channel == NUM_CH-1) begin
                            write_channel_base_addr <=
                                write_group_base_addr;

                            if (MaxPool_en_reg) begin
                                write_spatial_addr <=
                                    write_spatial_addr + 1'b1;
                            end else begin
                                case (result_output_position)
                                    2'd0: write_spatial_addr <=
                                        write_spatial_addr + 1'b1;
                                    2'd1: write_spatial_addr <=
                                        write_spatial_addr + conv_width - 1'b1;
                                    2'd2: write_spatial_addr <=
                                        write_spatial_addr + 1'b1;
                                    default: begin
                                        if (pool_x_reg == pool_x_last_reg) begin
                                            write_spatial_addr <=
                                                write_spatial_addr
                                                + conv_width + 2;
                                        end else begin
                                            write_spatial_addr <=
                                                write_spatial_addr + 2;
                                        end
                                    end
                                endcase
                            end
                        end else begin
                            write_channel_base_addr <=
                                write_channel_base_addr
                                + write_pixels_per_channel_reg;
                        end
                    end

                    if (result_output_done) begin
                        if ((pool_x_reg == pool_x_last_reg)
                            && (pool_y_reg == pool_y_last_reg)) begin
                            // The registered requantizer and conv write
                            // command delay the final buffer write by five
                            // cycles.
                            write_drain_count <= 3'd5;
                            state <= WRITE_DRAIN;
                        end else begin
                            if (pool_x_reg == pool_x_last_reg) begin
                                pool_x_reg <= '0;
                                pool_y_reg <= pool_y_reg + 1'b1;
                            end else begin
                                pool_x_reg <= pool_x_reg + 1'b1;
                            end

                            input_channel_reg  <= '0;
                            // Conv1 has one input channel, so its loaded
                            // kernel and bias remain valid for the next
                            // spatial tile. Later layers reload for input
                            // channel zero after finishing a tile.
                            setup_issued       <= (input_channels_reg == 1);
                            waiting_for_prefetch <= 1'b0;
                            data_request_count <= 5'd0;
                            state              <= SET_WEIGHT_DATA;
                        end
                    end
                end

                WRITE_DRAIN: begin
                    if (write_drain_count == 0) begin
                        state <= DONE;
                    end else begin
                        write_drain_count <= write_drain_count - 1'b1;
                    end
                end

                DONE: begin
                    state <= IDLE;
                end

                default: begin
                    state <= IDLE;
                end
            endcase
        end
    end

endmodule
